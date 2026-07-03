"""Behavior and adapter-contract probes for SDK evolution runs."""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import json
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_runtime_kit.adapters.antigravity import VENDOR_CONTRACT as ANTIGRAVITY_CONTRACT
from agent_runtime_kit.adapters.claude import VENDOR_CONTRACT as CLAUDE_CONTRACT
from agent_runtime_kit.adapters.codex import VENDOR_CONTRACT as CODEX_CONTRACT
from examples.sdk_evolution_agent.models import BehaviorDiff, BehaviorProbeResult
from examples.sdk_evolution_agent.snapshots import isolated_env

_CONTRACTS = {
    "claude-agent-sdk": {key: sorted(value) for key, value in CLAUDE_CONTRACT.items()},
    "openai-codex": {key: sorted(value) for key, value in CODEX_CONTRACT.items()},
    "google-antigravity": {key: sorted(value) for key, value in ANTIGRAVITY_CONTRACT.items()},
}


def collect_behavior_evidence(
    packages: Sequence[Mapping[str, object]],
    update_versions: Mapping[str, str],
    *,
    inspect_candidates: bool = False,
) -> dict[str, Any]:
    """Collect current/candidate behavior probes and compare them.

    Probing a version that is not installed means pip-installing and importing
    freshly downloaded upstream code, so those venv probes run only when the
    caller opted in via ``inspect_candidates`` (the ``--inspect-candidates`` CLI
    flag) — the same gate the API snapshots use. Without the opt-in, candidates
    get an explicit ``skip`` record instead of a silent evidence hole, and a
    drifted lockfile baseline falls back to probing the installed environment.
    """

    results: list[BehaviorProbeResult] = []
    for package in packages:
        name = str(package.get("name") or "")
        if not name:
            continue
        locked_version = _string_or_none(package.get("locked_version"))
        installed_version = _string_or_none(package.get("installed_version"))
        current_version = locked_version or installed_version
        if (
            inspect_candidates
            and locked_version
            and installed_version
            and locked_version != installed_version
        ):
            results.extend(probe_candidate_in_venv(name, locked_version, scope="current-baseline"))
        else:
            results.extend(probe_current_package(name, version=current_version))
        candidate = update_versions.get(name)
        if candidate:
            if inspect_candidates:
                results.extend(probe_candidate_in_venv(name, candidate, scope="candidate"))
            else:
                results.append(_skipped_candidate_probe(name, candidate))
    diffs = diff_behavior_results(results)
    return {
        "results": [result for result in results],
        "diffs": [diff for diff in diffs],
        "summary": summarize_behavior(diffs),
    }


def probe_current_package(
    package: str,
    *,
    version: str | None = None,
) -> tuple[BehaviorProbeResult, ...]:
    """Run behavior probes against the current Python environment."""

    return tuple(_probe_package(package, version=version, scope="current-environment"))


def probe_candidate_in_venv(
    package: str,
    version: str,
    *,
    scope: str = "candidate",
    python: str = sys.executable,
    timeout: int = 300,
) -> tuple[BehaviorProbeResult, ...]:
    """Run behavior probes against a candidate package in an isolated virtualenv."""

    with tempfile.TemporaryDirectory(prefix="ark-sdk-behavior-") as directory:
        venv = Path(directory) / ".venv"
        # Scrub the environment for every subprocess that touches freshly
        # downloaded upstream code (same scrub as the API snapshots): a
        # throwaway HOME and only PATH, so a malicious or buggy candidate
        # package cannot read the caller's credentials/config.
        env = isolated_env(Path(directory))
        venv_result = _run_probe_subprocess(
            (python, "-m", "venv", str(venv)), env=env, timeout=timeout
        )
        if venv_result is not None:
            return (venv_result(package, version, scope, "candidate-install"),)
        bin_dir = "Scripts" if sys.platform == "win32" else "bin"
        venv_python = venv / bin_dir / "python"
        install_result = _run_probe_subprocess(
            (str(venv_python), "-m", "pip", "install", f"{package}=={version}"),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        if install_result is not None:
            return (install_result(package, version, scope, "candidate-install"),)
        completed = subprocess.run(
            (
                str(venv_python),
                "-c",
                _PROBE_SCRIPT,
                package,
                version,
                scope,
                json.dumps(_CONTRACTS, sort_keys=True),
            ),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            return (
                _install_failed_probe(
                    package,
                    version,
                    scope,
                    "candidate-probe",
                    _tail(completed.stderr or completed.stdout),
                ),
            )
    raw = json.loads(completed.stdout)
    return tuple(BehaviorProbeResult(**item) for item in raw)


def diff_behavior_results(results: Sequence[BehaviorProbeResult]) -> tuple[BehaviorDiff, ...]:
    """Compare current and candidate behavior probes for each package/probe."""

    grouped: dict[tuple[str, str], dict[str, BehaviorProbeResult]] = {}
    for result in results:
        grouped.setdefault((result.package, result.probe), {})[result.scope] = result
    diffs: list[BehaviorDiff] = []
    for (package, probe), scopes in sorted(grouped.items()):
        before = scopes.get("current-baseline") or scopes.get("current-environment")
        after = scopes.get("candidate") or scopes.get("isolated-venv")
        if before is None or after is None:
            continue
        if (before.status == "skip") != (after.status == "skip"):
            # One side was not probed (candidate installs are opt-in): absence
            # of evidence is not a behavior change and must not read as one.
            continue
        if before.status == after.status and _contract_details(before) == _contract_details(after):
            severity = "none"
            summary = "No behavior contract difference detected."
        elif before.status == "pass" and after.status != "pass":
            severity = "breaking"
            summary = f"Candidate probe changed from pass to {after.status}."
        elif before.status != after.status:
            severity = "changed"
            summary = f"Probe status changed from {before.status} to {after.status}."
        else:
            severity = "changed"
            summary = "Probe details changed while status stayed the same."
        diffs.append(
            BehaviorDiff(
                package=package,
                from_version=before.version,
                to_version=after.version,
                probe=probe,
                severity=severity,
                summary=summary,
                before_status=before.status,
                after_status=after.status,
            )
        )
    return tuple(diffs)


def summarize_behavior(diffs: Sequence[BehaviorDiff]) -> dict[str, Any]:
    """Return a compact behavior summary for reports and gates."""

    breaking = [diff for diff in diffs if diff.severity == "breaking"]
    changed = [diff for diff in diffs if diff.severity == "changed"]
    return {
        "breaking_count": len(breaking),
        "changed_count": len(changed),
        "unchanged_count": len([diff for diff in diffs if diff.severity == "none"]),
        "status": "fail" if breaking else "changed" if changed else "pass",
    }


def _probe_package(
    package: str,
    *,
    version: str | None,
    scope: str,
) -> tuple[BehaviorProbeResult, ...]:
    if package == "claude-agent-sdk":
        return (_probe_claude(version=version, scope=scope),)
    if package == "openai-codex":
        return (_probe_codex(version=version, scope=scope),)
    if package == "openai-codex-cli-bin":
        return (_probe_codex_cli_bin(version=version, scope=scope),)
    if package == "google-antigravity":
        return (_probe_antigravity(version=version, scope=scope),)
    return (
        BehaviorProbeResult(
            package=package,
            version=version,
            scope=scope,
            probe="package-import",
            status="skip",
            summary="No behavior probe is defined for this package.",
        ),
    )


def _probe_claude(*, version: str | None, scope: str) -> BehaviorProbeResult:
    package = "claude-agent-sdk"
    observed = _observed_version(package)
    try:
        module = importlib.import_module("claude_agent_sdk")
        options_cls = module.ClaudeAgentOptions
    except Exception as exc:
        return _failed(package, version, scope, "adapter-contract", exc, observed_version=observed)
    fields = _fields(options_cls)
    expected = set(CLAUDE_CONTRACT["options_fields"])
    missing = sorted(expected - fields)
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe="adapter-contract",
        status="fail" if missing else "pass",
        summary=(
            "ClaudeAgentOptions exposes required adapter fields."
            if not missing
            else "ClaudeAgentOptions is missing required adapter fields."
        ),
        details={"fields": sorted(fields), "required_fields": sorted(expected), "missing": missing},
        requested_version=version,
        observed_version=observed,
        provenance=_provenance(version, observed),
    )


def _probe_codex(*, version: str | None, scope: str) -> BehaviorProbeResult:
    package = "openai-codex"
    observed = _observed_version(package)
    try:
        module = importlib.import_module("openai_codex")
        run_params = set(inspect.signature(module.AsyncThread.run).parameters)
        start_params = set(inspect.signature(module.AsyncCodex.thread_start).parameters)
    except Exception as exc:
        return _failed(package, version, scope, "adapter-contract", exc, observed_version=observed)
    expected_run = set(CODEX_CONTRACT["run_params"])
    expected_start = set(CODEX_CONTRACT["thread_start_params"])
    missing_run = sorted(expected_run - run_params)
    missing_start = sorted(expected_start - start_params)
    missing = missing_run + [f"thread_start.{item}" for item in missing_start]
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe="adapter-contract",
        status="fail" if missing else "pass",
        summary=(
            "Codex thread APIs expose required adapter parameters."
            if not missing
            else "Codex thread APIs are missing required adapter parameters."
        ),
        details={
            "run_params": sorted(run_params),
            "start_params": sorted(start_params),
            "required_run_params": sorted(expected_run),
            "required_start_params": sorted(expected_start),
            "missing": missing,
        },
        requested_version=version,
        observed_version=observed,
        provenance=_provenance(version, observed),
    )


def _probe_codex_cli_bin(*, version: str | None, scope: str) -> BehaviorProbeResult:
    package = "openai-codex-cli-bin"
    try:
        installed = importlib.metadata.version(package)
    except Exception as exc:
        return _failed(package, version, scope, "binary-distribution", exc)
    return BehaviorProbeResult(
        package=package,
        version=version or installed,
        scope=scope,
        probe="binary-distribution",
        status="pass",
        summary="Codex CLI binary distribution metadata is available.",
        details={"installed_version": installed},
        requested_version=version,
        observed_version=installed,
        provenance=_provenance(version, installed),
    )


def _probe_antigravity(*, version: str | None, scope: str) -> BehaviorProbeResult:
    package = "google-antigravity"
    observed = _observed_version(package)
    try:
        for module_name in ANTIGRAVITY_CONTRACT["required_imports"]:
            importlib.import_module(module_name)
        config_module = importlib.import_module(
            "google.antigravity.connections.local.local_connection_config"
        )
        config_cls = config_module.LocalAgentConfig
    except Exception as exc:
        return _failed(package, version, scope, "adapter-contract", exc, observed_version=observed)
    fields = _fields(config_cls)
    expected = set(ANTIGRAVITY_CONTRACT["config_fields"])
    missing = sorted(expected - fields)
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe="adapter-contract",
        status="fail" if missing else "pass",
        summary=(
            "Antigravity LocalAgentConfig exposes required adapter fields."
            if not missing
            else "Antigravity LocalAgentConfig is missing required adapter fields."
        ),
        details={"fields": sorted(fields), "required_fields": sorted(expected), "missing": missing},
        requested_version=version,
        observed_version=observed,
        provenance=_provenance(version, observed),
    )


def _fields(cls: Any) -> set[str]:
    if hasattr(cls, "model_fields"):
        return set(cls.model_fields)
    if hasattr(cls, "__dataclass_fields__"):
        return set(cls.__dataclass_fields__)
    try:
        return set(inspect.signature(cls).parameters)
    except (TypeError, ValueError):
        return set()


def _contract_details(result: BehaviorProbeResult) -> dict[str, Any]:
    if result.probe != "adapter-contract":
        return result.details
    details = result.details
    if "missing" not in details:
        return details
    contract: dict[str, Any] = {"missing": sorted(details.get("missing") or [])}
    if "required_fields" in details:
        contract["required_fields"] = sorted(details.get("required_fields") or [])
    if "required_run_params" in details:
        contract["required_run_params"] = sorted(details.get("required_run_params") or [])
    if "required_start_params" in details:
        contract["required_start_params"] = sorted(details.get("required_start_params") or [])
    return contract


def _failed(
    package: str,
    version: str | None,
    scope: str,
    probe: str,
    exc: Exception,
    observed_version: str | None = None,
) -> BehaviorProbeResult:
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe=probe,
        status="fail",
        summary=str(exc),
        details={"error": str(exc)},
        requested_version=version,
        observed_version=observed_version,
        provenance=_provenance(version, observed_version),
    )


def _skipped_candidate_probe(package: str, version: str) -> BehaviorProbeResult:
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope="candidate",
        probe="adapter-contract",
        status="skip",
        summary=(
            "Candidate behavior probe skipped: probing a candidate installs and "
            "imports freshly downloaded upstream code, which is opt-in. Rerun "
            "with --inspect-candidates to collect this evidence."
        ),
        details={"reason": "candidate installs are opt-in (--inspect-candidates)"},
        requested_version=version,
        observed_version=None,
        provenance="not-observed",
    )


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _observed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _provenance(requested: str | None, observed: str | None) -> str:
    if requested and observed and requested != observed:
        return "mismatched"
    if observed is None:
        return "not-observed"
    return "observed"


def _tail(text: str, *, limit: int = 500) -> str:
    return text[-limit:]


def _install_failed_probe(
    package: str,
    version: str,
    scope: str,
    probe: str,
    summary: str,
) -> BehaviorProbeResult:
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe=probe,
        status="fail",
        summary=summary,
        details={"stderr": summary},
        requested_version=version,
        observed_version=None,
        provenance="not-observed",
    )


def _run_probe_subprocess(
    command: tuple[str, ...],
    *,
    env: dict[str, str],
    timeout: int,
    text: bool = True,
    capture_output: bool = True,
) -> Any:
    try:
        completed = subprocess.run(
            command,
            text=text,
            capture_output=capture_output,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return lambda package, version, scope, probe: _install_failed_probe(
            package, version, scope, probe, f"timed out after {timeout}s"
        )
    if completed.returncode == 0:
        return None
    return lambda package, version, scope, probe: _install_failed_probe(
        package,
        version,
        scope,
        probe,
        _tail(completed.stderr or completed.stdout),
    )


_PROBE_SCRIPT = textwrap.dedent(
    """
    import importlib
    import importlib.metadata
    import inspect
    import json
    import sys

    package, version, scope = sys.argv[1:4]
    contracts = json.loads(sys.argv[4])

    def fields(cls):
        if hasattr(cls, "model_fields"):
            return set(cls.model_fields)
        if hasattr(cls, "__dataclass_fields__"):
            return set(cls.__dataclass_fields__)
        try:
            return set(inspect.signature(cls).parameters)
        except (TypeError, ValueError):
            return set()

    def failed(probe, exc):
        observed = observed_version()
        return {
            "package": package,
            "version": version,
            "scope": scope,
            "probe": probe,
            "status": "fail",
            "summary": str(exc),
            "details": {"error": str(exc)},
            "requested_version": version,
            "observed_version": observed,
            "provenance": provenance(observed),
        }

    def observed_version():
        try:
            return importlib.metadata.version(package)
        except Exception:
            return None

    def provenance(observed):
        if version and observed and version != observed:
            return "mismatched"
        if observed is None:
            return "not-observed"
        return "observed"

    def result(probe, status, summary, details):
        observed = observed_version()
        return {
            "package": package,
            "version": version,
            "scope": scope,
            "probe": probe,
            "status": status,
            "summary": summary,
            "details": details,
            "requested_version": version,
            "observed_version": observed,
            "provenance": provenance(observed),
        }

    try:
        if package == "claude-agent-sdk":
            module = importlib.import_module("claude_agent_sdk")
            option_fields = fields(getattr(module, "ClaudeAgentOptions"))
            expected = set(contracts[package]["options_fields"])
            missing = sorted(expected - option_fields)
            payload = [result(
                "adapter-contract",
                "fail" if missing else "pass",
                "ClaudeAgentOptions exposes required adapter fields." if not missing
                else "ClaudeAgentOptions is missing required adapter fields.",
                {
                    "fields": sorted(option_fields),
                    "required_fields": sorted(expected),
                    "missing": missing,
                },
            )]
        elif package == "openai-codex":
            module = importlib.import_module("openai_codex")
            run_params = set(inspect.signature(module.AsyncThread.run).parameters)
            start_params = set(inspect.signature(module.AsyncCodex.thread_start).parameters)
            expected_run = set(contracts[package]["run_params"])
            expected_start = set(contracts[package]["thread_start_params"])
            missing_run = sorted(expected_run - run_params)
            missing_start = sorted(expected_start - start_params)
            missing = missing_run + [f"thread_start.{item}" for item in missing_start]
            payload = [result(
                "adapter-contract",
                "fail" if missing else "pass",
                "Codex thread APIs expose required adapter parameters." if not missing
                else "Codex thread APIs are missing required adapter parameters.",
                {
                    "run_params": sorted(run_params),
                    "start_params": sorted(start_params),
                    "required_run_params": sorted(expected_run),
                    "required_start_params": sorted(expected_start),
                    "missing": missing,
                },
            )]
        elif package == "openai-codex-cli-bin":
            installed = importlib.metadata.version(package)
            payload = [result(
                "binary-distribution",
                "pass",
                "Codex CLI binary distribution metadata is available.",
                {"installed_version": installed},
            )]
        elif package == "google-antigravity":
            for module_name in contracts[package]["required_imports"]:
                importlib.import_module(module_name)
            config_module = importlib.import_module(
                "google.antigravity.connections.local.local_connection_config"
            )
            config_fields = fields(getattr(config_module, "LocalAgentConfig"))
            expected = set(contracts[package]["config_fields"])
            missing = sorted(expected - config_fields)
            payload = [result(
                "adapter-contract",
                "fail" if missing else "pass",
                "Antigravity LocalAgentConfig exposes required adapter fields." if not missing
                else "Antigravity LocalAgentConfig is missing required adapter fields.",
                {
                    "fields": sorted(config_fields),
                    "required_fields": sorted(expected),
                    "missing": missing,
                },
            )]
        else:
            payload = [result("package-import", "skip", "No behavior probe is defined.", {})]
    except Exception as exc:
        payload = [failed("adapter-contract", exc)]

    print(json.dumps(payload, sort_keys=True))
    """
).strip()
