"""API snapshot and diff helpers for upstream SDK inspection."""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.inspection import (
    CandidatePreparationError,
    prepare_cached_candidate_environment,
)
from examples.sdk_evolution_agent.models import (
    ApiDiff,
    ApiMember,
    ApiSnapshot,
    ImplementationDefinition,
    ImplementationDiff,
    ImplementationFile,
)

DEFAULT_MODULES = {
    "claude-agent-sdk": "claude_agent_sdk",
    "openai-codex": "openai_codex",
    "openai-codex-cli-bin": "codex_cli_bin",
    "google-antigravity": "google.antigravity",
}


def snapshot_current_api(package: str, *, version: str | None = None) -> ApiSnapshot:
    """Capture public API and implementation fingerprints in the current environment."""

    module_name = DEFAULT_MODULES.get(package, package.replace("-", "_"))
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return ApiSnapshot(
            package=package,
            version=version,
            module=module_name,
            import_error=str(exc),
        )
    members: list[ApiMember] = []
    for name, value in inspect.getmembers(module):
        if name.startswith("_"):
            continue
        members.append(
            ApiMember(
                name=name,
                kind=_member_kind(value),
                signature=_signature(value),
                module=str(getattr(value, "__module__", "")),
            )
        )
    implementation = _inspect_implementation(module, package=package, module_name=module_name)
    return ApiSnapshot(
        package=package,
        version=version or str(getattr(module, "__version__", "") or ""),
        module=module_name,
        members=tuple(sorted(members, key=lambda item: item.name)),
        implementation_files=implementation[0],
        implementation_definitions=implementation[1],
        implementation_status=implementation[2],
        implementation_note=implementation[3],
    )


def diff_snapshots(before: ApiSnapshot, after: ApiSnapshot) -> ApiDiff:
    """Diff two public API snapshots."""

    before_members = {member.name: member for member in before.members}
    after_members = {member.name: member for member in after.members}
    added = tuple(sorted(set(after_members) - set(before_members)))
    removed = tuple(sorted(set(before_members) - set(after_members)))
    changed = tuple(
        sorted(
            name
            for name in set(before_members) & set(after_members)
            if before_members[name].signature != after_members[name].signature
            or before_members[name].kind != after_members[name].kind
        )
    )
    return ApiDiff(
        package=before.package,
        from_version=before.version,
        to_version=after.version,
        added=added,
        removed=removed,
        changed=changed,
    )


def diff_snapshot_groups(snapshots: Sequence[ApiSnapshot]) -> tuple[ApiDiff, ...]:
    """Diff adjacent snapshots grouped by package."""

    diffs: list[ApiDiff] = []
    grouped: dict[str, list[ApiSnapshot]] = {}
    for snapshot in snapshots:
        if snapshot.import_error:
            continue
        grouped.setdefault(snapshot.package, []).append(snapshot)
    for group in grouped.values():
        for index in range(1, len(group)):
            diffs.append(diff_snapshots(group[index - 1], group[index]))
    return tuple(diffs)


def diff_implementation_snapshots(
    before: ApiSnapshot,
    after: ApiSnapshot,
) -> ImplementationDiff:
    """Diff file and Python-definition fingerprints for two exact releases."""

    if (
        before.import_error
        or after.import_error
        or before.implementation_status == "unavailable"
        or after.implementation_status == "unavailable"
    ):
        errors = tuple(
            detail
            for detail in (
                f"{before.version}: {before.import_error}" if before.import_error else "",
                f"{after.version}: {after.import_error}" if after.import_error else "",
                (
                    f"{before.version}: "
                    f"{before.implementation_note or 'implementation unavailable'}"
                    if before.implementation_status == "unavailable"
                    else ""
                ),
                (
                    f"{after.version}: {after.implementation_note or 'implementation unavailable'}"
                    if after.implementation_status == "unavailable"
                    else ""
                ),
            )
            if detail
        )
        return ImplementationDiff(
            package=before.package,
            from_version=before.version,
            to_version=after.version,
            status="unavailable",
            limitations=errors or ("implementation snapshot was unavailable",),
        )

    before_files = {item.path: item for item in before.implementation_files}
    after_files = {item.path: item for item in after.implementation_files}
    before_python = {
        path: item for path, item in before_files.items() if item.kind in {"python", "stub"}
    }
    after_python = {
        path: item for path, item in after_files.items() if item.kind in {"python", "stub"}
    }
    before_opaque = {
        path: item for path, item in before_files.items() if item.kind not in {"python", "stub"}
    }
    after_opaque = {
        path: item for path, item in after_files.items() if item.kind not in {"python", "stub"}
    }
    before_definitions = {item.name: item for item in before.implementation_definitions}
    after_definitions = {item.name: item for item in after.implementation_definitions}
    opaque_artifacts_added = tuple(sorted(set(after_opaque) - set(before_opaque)))
    opaque_artifacts_removed = tuple(sorted(set(before_opaque) - set(after_opaque)))
    opaque_artifacts_changed = tuple(
        sorted(
            path
            for path in set(before_opaque) & set(after_opaque)
            if before_opaque[path].sha256 != after_opaque[path].sha256
        )
    )

    status = "observed"
    limitations: tuple[str, ...] = ()
    if before.package == "openai-codex-cli-bin":
        status = "opaque-runtime"
        limitations = (
            "Python packaging code is inspectable, but the bundled Codex executable "
            "is opaque in wheel artifacts.",
        )
    elif not before_python and not after_python:
        status = "opaque-runtime" if before_opaque or after_opaque else "unavailable"
        limitations = ("No inspectable Python implementation source was found.",)
    elif opaque_artifacts_added or opaque_artifacts_removed or opaque_artifacts_changed:
        limitations = (
            "Opaque or native artifacts changed; fingerprints prove artifact drift, not "
            "their internal implementation design.",
        )

    return ImplementationDiff(
        package=before.package,
        from_version=before.version,
        to_version=after.version,
        status=status,
        python_files_before=len(before_python),
        python_files_after=len(after_python),
        source_lines_before=sum(item.line_count for item in before_python.values()),
        source_lines_after=sum(item.line_count for item in after_python.values()),
        files_added=tuple(sorted(set(after_python) - set(before_python))),
        files_removed=tuple(sorted(set(before_python) - set(after_python))),
        files_changed=tuple(
            sorted(
                path
                for path in set(before_python) & set(after_python)
                if before_python[path].sha256 != after_python[path].sha256
            )
        ),
        definitions_added=tuple(sorted(set(after_definitions) - set(before_definitions))),
        definitions_removed=tuple(sorted(set(before_definitions) - set(after_definitions))),
        definitions_changed=tuple(
            sorted(
                name
                for name in set(before_definitions) & set(after_definitions)
                if before_definitions[name].sha256 != after_definitions[name].sha256
            )
        ),
        opaque_artifacts_added=opaque_artifacts_added,
        opaque_artifacts_removed=opaque_artifacts_removed,
        opaque_artifacts_changed=opaque_artifacts_changed,
        limitations=limitations,
    )


def diff_implementation_snapshot_groups(
    snapshots: Sequence[ApiSnapshot],
) -> tuple[ImplementationDiff, ...]:
    """Diff adjacent, already-ordered release snapshots for every package."""

    diffs: list[ImplementationDiff] = []
    grouped: dict[str, list[ApiSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.package, []).append(snapshot)
    for group in grouped.values():
        for index in range(1, len(group)):
            diffs.append(diff_implementation_snapshots(group[index - 1], group[index]))
    return tuple(diffs)


def isolated_env(home: Path) -> dict[str, str]:
    """A minimal environment for candidate subprocesses: PATH + a throwaway HOME.

    Deliberately omits the caller's credentials/config so freshly downloaded
    upstream code executed during inspection cannot read them. That scrub also
    drops proxy and CA overrides (HTTPS_PROXY, SSL_CERT_FILE, PIP_INDEX_URL...),
    so behind a corporate TLS-intercepting proxy or private mirror the candidate
    install may fail — an accepted trade-off of the isolation. Shared with the
    behavior probes, which install candidates the same way.
    """

    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home)}
    if sys.platform == "win32":
        env["USERPROFILE"] = str(home)
        for key in ("SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
            value = os.environ.get(key)
            if value:
                env[key] = value
    return env


def snapshot_candidate_in_venv(
    package: str,
    version: str,
    *,
    python: str = sys.executable,
    timeout: int = 300,
) -> ApiSnapshot:
    """Inspect a candidate version in an isolated temporary virtualenv."""

    module_name = DEFAULT_MODULES.get(package, package.replace("-", "_"))
    step = "virtual environment creation"
    try:
        cached = prepare_cached_candidate_environment(
            package,
            version,
            python=python,
            timeout=timeout,
            runner=subprocess.run,
            env_factory=isolated_env,
        )
        if cached is not None:
            step = "snapshot execution"
            completed = _run_snapshot_script(
                cached.python,
                package=package,
                version=version,
                module_name=module_name,
                timeout=timeout,
                env=cached.env,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="ark-sdk-snapshot-") as directory:
                venv = Path(directory) / ".venv"
                # Scrub the environment for every subprocess that touches freshly downloaded
                # upstream code: give it a throwaway HOME and only PATH, so a malicious or
                # buggy candidate package cannot read the caller's credentials/config.
                env = isolated_env(Path(directory))
                subprocess.run(
                    (python, "-m", "venv", str(venv)),
                    check=True,
                    timeout=timeout,
                    env=env,
                )
                bin_dir = "Scripts" if sys.platform == "win32" else "bin"
                venv_python = venv / bin_dir / "python"
                step = "package installation"
                subprocess.run(
                    (str(venv_python), "-m", "pip", "install", f"{package}=={version}"),
                    check=True,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    env=env,
                )
                step = "snapshot execution"
                completed = _run_snapshot_script(
                    venv_python,
                    package=package,
                    version=version,
                    module_name=module_name,
                    timeout=timeout,
                    env=env,
                )
    except CandidatePreparationError as exc:
        step = exc.step.replace("-", " ")
        cause = exc.cause
        if isinstance(cause, subprocess.TimeoutExpired):
            detail = f"{step} timed out after {cause.timeout}s"
        elif isinstance(cause, subprocess.CalledProcessError):
            output = _bounded_failure_detail(cause.stderr or cause.stdout or str(cause))
            detail = f"{step} failed: {output}"
        else:
            detail = f"{step} failed: {_bounded_failure_detail(cause)}"
        return _failed_isolated_snapshot(package, version, module_name, detail)
    except subprocess.TimeoutExpired as exc:
        return _failed_isolated_snapshot(
            package,
            version,
            module_name,
            f"{step} timed out after {exc.timeout}s",
        )
    except subprocess.CalledProcessError as exc:
        detail = _bounded_failure_detail(exc.stderr or exc.stdout or str(exc))
        return _failed_isolated_snapshot(
            package,
            version,
            module_name,
            f"{step} failed: {detail}",
        )
    except OSError as exc:
        return _failed_isolated_snapshot(
            package,
            version,
            module_name,
            f"{step} failed: {_bounded_failure_detail(exc)}",
        )

    try:
        raw = json.loads(completed.stdout)
        return ApiSnapshot(
            package=raw["package"],
            version=raw["version"],
            module=raw["module"],
            members=tuple(ApiMember(**item) for item in raw.get("members", ())),
            implementation_files=tuple(
                ImplementationFile(**item) for item in raw.get("implementation_files", ())
            ),
            implementation_definitions=tuple(
                ImplementationDefinition(**item)
                for item in raw.get("implementation_definitions", ())
            ),
            implementation_status=str(raw.get("implementation_status") or "unavailable"),
            implementation_note=str(raw.get("implementation_note") or ""),
            import_error=raw.get("import_error"),
            source="isolated-venv",
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        output = _bounded_failure_detail(completed.stdout)
        detail = f"malformed snapshot output: {exc}"
        if output:
            detail += f"; stdout={output}"
        return _failed_isolated_snapshot(package, version, module_name, detail)


def _run_snapshot_script(
    python: Path,
    *,
    package: str,
    version: str,
    module_name: str,
    timeout: int,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (str(python), "-c", _SNAPSHOT_SCRIPT, package, version, module_name),
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=dict(env),
    )


def _failed_isolated_snapshot(
    package: str,
    version: str,
    module_name: str,
    error: str,
) -> ApiSnapshot:
    return ApiSnapshot(
        package=package,
        version=version,
        module=module_name,
        import_error=_bounded_failure_detail(error, limit=560),
        source="isolated-venv",
    )


def _bounded_failure_detail(value: object, *, limit: int = 480) -> str:
    if isinstance(value, bytes):
        text = value.decode(errors="replace")
    else:
        text = str(value)
    return " ".join(text.split())[:limit]


def _member_kind(value: Any) -> str:
    if inspect.isclass(value):
        return "class"
    if inspect.isfunction(value) or inspect.ismethod(value):
        return "function"
    if inspect.ismodule(value):
        return "module"
    return type(value).__name__


def _signature(value: Any) -> str:
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return ""


def _inspect_implementation(
    module: Any,
    *,
    package: str,
    module_name: str,
) -> tuple[
    tuple[ImplementationFile, ...],
    tuple[ImplementationDefinition, ...],
    str,
    str,
]:
    roots = _module_roots(module)
    if not roots:
        return (), (), "unavailable", "imported module has no inspectable filesystem path"

    files: list[ImplementationFile] = []
    definitions: list[ImplementationDefinition] = []
    seen_paths: set[str] = set()
    for root_index, (root, single_file) in enumerate(roots, start=1):
        candidates = (root,) if single_file else tuple(sorted(root.rglob("*")))
        for path in candidates:
            if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            relative = path.name if single_file else str(path.relative_to(root))
            if len(roots) > 1:
                relative = f"root-{root_index}/{relative}"
            relative = f"{module_name.replace('.', '/')}/{relative}"
            if relative in seen_paths:
                continue
            seen_paths.add(relative)
            try:
                content = path.read_bytes()
            except OSError:
                continue
            kind = _implementation_file_kind(path, content)
            line_count = (
                content.count(b"\n") + int(bool(content) and not content.endswith(b"\n"))
                if kind in {"python", "stub"}
                else 0
            )
            files.append(
                ImplementationFile(
                    path=relative,
                    kind=kind,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size=len(content),
                    line_count=int(line_count),
                )
            )
            if kind in {"python", "stub"}:
                definitions.extend(_definition_fingerprints(content, path=relative))

    status = "opaque-runtime" if package == "openai-codex-cli-bin" else "observed"
    note = (
        "bundled executable internals are opaque; wrapper files and artifact hashes are observed"
        if status == "opaque-runtime"
        else ""
    )
    if not files:
        status = "unavailable"
        note = "module path contained no readable implementation files"
    return (
        tuple(sorted(files, key=lambda item: item.path)),
        tuple(sorted(definitions, key=lambda item: item.name)),
        status,
        note,
    )


def _module_roots(module: Any) -> tuple[tuple[Path, bool], ...]:
    roots: list[tuple[Path, bool]] = []
    module_path = getattr(module, "__path__", None)
    if module_path is not None:
        for value in module_path:
            path = Path(str(value)).resolve()
            if path.exists():
                roots.append((path, False))
    if not roots:
        module_file = getattr(module, "__file__", None)
        if module_file:
            path = Path(str(module_file)).resolve()
            if path.is_file():
                roots.append((path, True))
    return tuple(roots)


def _implementation_file_kind(path: Path, content: bytes) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix == ".pyi":
        return "stub"
    if path.suffix.lower() in {".so", ".dylib", ".dll", ".pyd"}:
        return "native-extension"
    if content.startswith((b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"MZ")):
        return "executable"
    return "opaque-artifact"


def _definition_fingerprints(
    content: bytes,
    *,
    path: str,
) -> tuple[ImplementationDefinition, ...]:
    try:
        tree = ast.parse(content.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError):
        return ()
    definitions: list[ImplementationDefinition] = []

    def visit(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = ".".join((*parents, child.name))
                kind = (
                    "class"
                    if isinstance(child, ast.ClassDef)
                    else "async-function"
                    if isinstance(child, ast.AsyncFunctionDef)
                    else "function"
                )
                fingerprint = hashlib.sha256(
                    ast.dump(child, include_attributes=False).encode("utf-8")
                ).hexdigest()
                definitions.append(
                    ImplementationDefinition(
                        name=f"{path}:{name}",
                        kind=kind,
                        path=path,
                        sha256=fingerprint,
                    )
                )
                visit(child, (*parents, child.name))
            else:
                visit(child, parents)

    visit(tree)
    return tuple(definitions)


_SNAPSHOT_SCRIPT = textwrap.dedent(
    """
    import ast
    import hashlib
    import importlib
    import inspect
    import json
    import sys
    from pathlib import Path

    def module_roots(module):
        roots = []
        module_path = getattr(module, "__path__", None)
        if module_path is not None:
            for value in module_path:
                path = Path(str(value)).resolve()
                if path.exists():
                    roots.append((path, False))
        if not roots:
            module_file = getattr(module, "__file__", None)
            if module_file:
                path = Path(str(module_file)).resolve()
                if path.is_file():
                    roots.append((path, True))
        return roots

    def file_kind(path, content):
        if path.suffix == ".py":
            return "python"
        if path.suffix == ".pyi":
            return "stub"
        if path.suffix.lower() in {".so", ".dylib", ".dll", ".pyd"}:
            return "native-extension"
        if content.startswith(
            (b"\\x7fELF", b"\\xcf\\xfa\\xed\\xfe", b"\\xca\\xfe\\xba\\xbe", b"MZ")
        ):
            return "executable"
        return "opaque-artifact"

    def definition_fingerprints(content, path):
        try:
            tree = ast.parse(content.decode("utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            return []
        definitions = []

        def visit(node, parents=()):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = ".".join((*parents, child.name))
                    if isinstance(child, ast.ClassDef):
                        kind = "class"
                    elif isinstance(child, ast.AsyncFunctionDef):
                        kind = "async-function"
                    else:
                        kind = "function"
                    fingerprint = hashlib.sha256(
                        ast.dump(child, include_attributes=False).encode("utf-8")
                    ).hexdigest()
                    definitions.append({
                        "name": f"{path}:{name}",
                        "kind": kind,
                        "path": path,
                        "sha256": fingerprint,
                    })
                    visit(child, (*parents, child.name))
                else:
                    visit(child, parents)

        visit(tree)
        return definitions

    def inspect_implementation(module, package, module_name):
        roots = module_roots(module)
        if not roots:
            return [], [], "unavailable", "imported module has no inspectable filesystem path"
        files = []
        definitions = []
        seen_paths = set()
        for root_index, (root, single_file) in enumerate(roots, start=1):
            candidates = (root,) if single_file else tuple(sorted(root.rglob("*")))
            for path in candidates:
                if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
                    continue
                if path.suffix in {".pyc", ".pyo"}:
                    continue
                relative = path.name if single_file else str(path.relative_to(root))
                if len(roots) > 1:
                    relative = f"root-{root_index}/{relative}"
                relative = f"{module_name.replace('.', '/')}/{relative}"
                if relative in seen_paths:
                    continue
                seen_paths.add(relative)
                try:
                    content = path.read_bytes()
                except OSError:
                    continue
                kind = file_kind(path, content)
                line_count = (
                    content.count(b"\\n")
                    + int(bool(content) and not content.endswith(b"\\n"))
                    if kind in {"python", "stub"}
                    else 0
                )
                files.append({
                    "path": relative,
                    "kind": kind,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                    "line_count": int(line_count),
                })
                if kind in {"python", "stub"}:
                    definitions.extend(definition_fingerprints(content, relative))
        status = "opaque-runtime" if package == "openai-codex-cli-bin" else "observed"
        note = (
            "bundled executable internals are opaque; wrapper files and artifact "
            "hashes are observed"
            if status == "opaque-runtime"
            else ""
        )
        if not files:
            status = "unavailable"
            note = "module path contained no readable implementation files"
        return (
            sorted(files, key=lambda item: item["path"]),
            sorted(definitions, key=lambda item: item["name"]),
            status,
            note,
        )

    package, version, module_name = sys.argv[1:4]
    try:
        module = importlib.import_module(module_name)
        members = []
        for name, value in inspect.getmembers(module):
            if name.startswith("_"):
                continue
            try:
                signature = str(inspect.signature(value))
            except (TypeError, ValueError):
                signature = ""
            if inspect.isclass(value):
                kind = "class"
            elif inspect.isfunction(value) or inspect.ismethod(value):
                kind = "function"
            elif inspect.ismodule(value):
                kind = "module"
            else:
                kind = type(value).__name__
            members.append({
                "name": name,
                "kind": kind,
                "signature": signature,
                "module": str(getattr(value, "__module__", "")),
            })
        (
            implementation_files,
            implementation_definitions,
            implementation_status,
            implementation_note,
        ) = inspect_implementation(module, package, module_name)
        payload = {
            "package": package,
            "version": version,
            "module": module_name,
            "members": sorted(members, key=lambda item: item["name"]),
            "implementation_files": implementation_files,
            "implementation_definitions": implementation_definitions,
            "implementation_status": implementation_status,
            "implementation_note": implementation_note,
            "import_error": None,
        }
    except Exception as exc:
        payload = {
            "package": package,
            "version": version,
            "module": module_name,
            "members": [],
            "implementation_files": [],
            "implementation_definitions": [],
            "implementation_status": "unavailable",
            "implementation_note": "implementation inspection failed",
            "import_error": str(exc),
        }
    print(json.dumps(payload, sort_keys=True))
    """
).strip()
