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
from typing import Any, cast

from examples.sdk_evolution_agent.collectors import ResolverTransition, parse_refresh_transitions
from examples.sdk_evolution_agent.models import BehaviorDiff, BehaviorProbeResult
from examples.sdk_evolution_agent.snapshots import DEFAULT_MODULES, isolated_env

_RESULT_STATUSES = frozenset({"pass", "fail", "skip"})
_DIFF_SEVERITIES = frozenset({"none", "changed", "breaking"})
_RESULT_SCOPES = frozenset(
    {"current-baseline", "current-environment", "candidate", "isolated-venv"}
)
_DETAIL_STRING_SEQUENCE_FIELDS = frozenset(
    {
        "fields",
        "missing",
        "required_fields",
        "required_run_params",
        "required_start_params",
        "run_params",
        "start_params",
    }
)


def collect_behavior_evidence(
    packages: Sequence[Mapping[str, object]],
    update_versions: Mapping[str, str],
    *,
    inspect_candidates: bool = False,
    expected_transitions: Sequence[ResolverTransition] | None = None,
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
        if inspect_candidates and locked_version and locked_version != installed_version:
            results.extend(probe_candidate_in_venv(name, locked_version, scope="current-baseline"))
        else:
            results.extend(probe_current_package(name, version=installed_version))
        candidate = update_versions.get(name)
        if candidate:
            if inspect_candidates:
                results.extend(probe_candidate_in_venv(name, candidate, scope="candidate"))
            else:
                results.append(_skipped_candidate_probe(name, candidate))
    diffs = diff_behavior_results(results)
    transitions = tuple(
        expected_transitions
        if expected_transitions is not None
        else _expected_transitions(packages, update_versions)
    )
    expectations = build_behavior_expectations(packages, transitions)
    payload: dict[str, Any] = {
        "results": [result for result in results],
        "diffs": [diff for diff in diffs],
        **expectations,
    }
    payload["summary"] = assess_behavior_payload(payload, expectations=expectations)
    return payload


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

    step = "virtual-environment-creation"
    try:
        with tempfile.TemporaryDirectory(prefix="ark-sdk-behavior-") as directory:
            venv = Path(directory) / ".venv"
            # Scrub the environment for every subprocess that touches freshly
            # downloaded upstream code (same scrub as the API snapshots): a
            # throwaway HOME and only PATH, so a malicious or buggy candidate
            # package cannot read the caller's credentials/config.
            env = isolated_env(Path(directory))
            subprocess.run(
                (python, "-m", "venv", str(venv)),
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            bin_dir = "Scripts" if sys.platform == "win32" else "bin"
            venv_python = venv / bin_dir / "python"
            step = "package-installation"
            subprocess.run(
                (str(venv_python), "-m", "pip", "install", f"{package}=={version}"),
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            step = "probe-execution"
            completed = subprocess.run(
                (str(venv_python), "-c", _PROBE_SCRIPT, package, version, scope),
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
    except subprocess.TimeoutExpired as exc:
        return (
            _probe_execution_failure(
                package,
                version,
                scope,
                step,
                f"timed out after {exc.timeout}s",
            ),
        )
    except subprocess.CalledProcessError as exc:
        detail = _bounded_text(exc.stderr or exc.stdout or str(exc))
        return (_probe_execution_failure(package, version, scope, step, detail),)
    except OSError as exc:
        return (_probe_execution_failure(package, version, scope, step, exc),)

    try:
        return _parse_probe_output(
            completed.stdout,
            package=package,
            version=version,
            scope=scope,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        output = _bounded_text(completed.stdout)
        detail = f"malformed probe output: {exc}"
        if output:
            detail += f"; stdout={output}"
        return (
            _probe_execution_failure(
                package,
                version,
                scope,
                "probe-output-validation",
                detail,
            ),
        )


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
        if (
            before.status == "skip"
            or after.status == "skip"
            or _is_probe_execution_error(before)
            or _is_probe_execution_error(after)
        ):
            # Missing or failed execution is incomplete evidence, not an observed
            # behavior change. The raw-result assessment preserves that state.
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


def build_behavior_expectations(
    packages: Sequence[Mapping[str, object]],
    transitions: Sequence[ResolverTransition],
) -> dict[str, Any]:
    """Build canonical behavior expectations from deterministic package evidence."""

    expected_packages: list[str] = []
    expected_baselines: dict[str, str | None] = {}
    for package in packages:
        name = str(package.get("name") or "")
        if not name or name in expected_baselines:
            continue
        locked_version = _string_or_none(package.get("locked_version"))
        installed_version = _string_or_none(package.get("installed_version"))
        expected_packages.append(name)
        expected_baselines[name] = (
            locked_version if locked_version is not None else installed_version
        )
    return {
        "expected_packages": expected_packages,
        "expected_transitions": [
            {
                "package": transition.package,
                "from_version": transition.from_version,
                "to_version": transition.to_version,
            }
            for transition in sorted(set(transitions))
        ],
        "expected_baselines": expected_baselines,
    }


def behavior_expectations_from_evidence(evidence: Mapping[str, object]) -> dict[str, Any]:
    """Derive authoritative behavior expectations from the deterministic evidence bundle."""

    raw_packages = evidence.get("packages")
    packages: list[Mapping[str, object]] = []
    issues: list[str] = []
    if not _is_sequence_payload(raw_packages):
        issues.append("deterministic evidence packages must be an array")
    else:
        for index, package in enumerate(cast(Sequence[object], raw_packages)):
            if not isinstance(package, Mapping):
                issues.append(f"deterministic evidence package {index} must be an object")
                continue
            name = package.get("name")
            if not isinstance(name, str) or not name:
                issues.append(f"deterministic evidence package {index} must have a non-empty name")
                continue
            packages.append(package)
    expectations = build_behavior_expectations(packages, parse_refresh_transitions(evidence))
    expectations["expectation_issues"] = issues
    return expectations


def assess_behavior_payload(
    behavior: Mapping[str, object],
    *,
    expectations: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Recompute the canonical assessment from raw evidence and trusted expectations."""

    context = expectations if expectations is not None else behavior
    summary = summarize_behavior(
        behavior.get("results"),
        behavior.get("diffs"),
        expected_packages=context.get("expected_packages"),
        expected_transitions=context.get("expected_transitions"),
        expected_baselines=context.get("expected_baselines"),
    )
    if expectations is None:
        return summary

    raw_expectation_issues = context.get("expectation_issues", [])
    issues = (
        list(raw_expectation_issues)
        if _is_sequence_payload(raw_expectation_issues)
        and all(isinstance(issue, str) for issue in cast(Sequence[object], raw_expectation_issues))
        else ["deterministic behavior expectation issues are malformed"]
    )
    issues.extend(
        [
            f"behavior payload {key} contradicts deterministic evidence"
            for key in ("expected_packages", "expected_transitions", "expected_baselines")
            if behavior.get(key) != expectations.get(key)
        ]
    )
    if not issues:
        return summary
    summary["malformed_count"] = int(summary["malformed_count"]) + len(issues)
    summary["reasons"] = list(dict.fromkeys([*summary["reasons"], *issues]))
    if summary["status"] != "fail":
        summary["status"] = "incomplete"
    return summary


def summarize_behavior(
    results: object,
    diffs: object,
    *,
    expected_packages: object,
    expected_transitions: object,
    expected_baselines: object,
) -> dict[str, Any]:
    """Assess raw probes and diffs without collapsing missing evidence into pass."""

    reasons: list[str] = []
    malformed_count = 0

    packages: list[str] = []
    if not _is_sequence_payload(expected_packages):
        malformed_count += 1
        reasons.append("expected_packages must be an array")
    else:
        for index, package in enumerate(cast(Sequence[object], expected_packages)):
            if not isinstance(package, str) or not package:
                malformed_count += 1
                reasons.append(f"expected package {index} must be a non-empty string")
            elif package in packages:
                malformed_count += 1
                reasons.append(f"expected package {package} is duplicated")
            else:
                packages.append(package)

    baselines: dict[str, str | None] = {}
    if not isinstance(expected_baselines, Mapping):
        malformed_count += 1
        reasons.append("expected_baselines must be an object")
    else:
        for package, version in expected_baselines.items():
            if not isinstance(package, str) or not package:
                malformed_count += 1
                reasons.append("expected baseline package names must be non-empty strings")
                continue
            if version is not None and (not isinstance(version, str) or not version):
                malformed_count += 1
                reasons.append(
                    f"expected baseline for {package} must be a non-empty string or null"
                )
                continue
            baselines[package] = version

    package_set = set(packages)
    for package in sorted(package_set - baselines.keys()):
        malformed_count += 1
        reasons.append(f"expected_baselines is missing {package}")
    for package in sorted(baselines.keys() - package_set):
        malformed_count += 1
        reasons.append(f"expected_baselines contains unexpected package {package}")

    normalized_results: list[BehaviorProbeResult] = []
    if not _is_sequence_payload(results):
        malformed_count += 1
        reasons.append("behavior results must be an array")
    else:
        for index, item in enumerate(cast(Sequence[object], results)):
            try:
                normalized_results.append(_coerce_probe_result(item))
            except (KeyError, TypeError, ValueError) as exc:
                malformed_count += 1
                reasons.append(f"behavior result {index} is malformed: {_bounded_text(exc)}")

    normalized_diffs: list[BehaviorDiff] = []
    if not _is_sequence_payload(diffs):
        malformed_count += 1
        reasons.append("behavior diffs must be an array")
    else:
        for index, item in enumerate(cast(Sequence[object], diffs)):
            try:
                normalized_diffs.append(_coerce_behavior_diff(item))
            except (KeyError, TypeError, ValueError) as exc:
                malformed_count += 1
                reasons.append(f"behavior diff {index} is malformed: {_bounded_text(exc)}")

    expected_diffs = diff_behavior_results(normalized_results)
    if sorted(normalized_diffs, key=_behavior_diff_key) != sorted(
        expected_diffs, key=_behavior_diff_key
    ):
        malformed_count += 1
        reasons.append("behavior diffs contradict raw probe results")

    contract_failures: list[BehaviorProbeResult] = []
    probe_errors: list[BehaviorProbeResult] = []
    skipped: list[BehaviorProbeResult] = []
    for result in normalized_results:
        if result.status == "skip":
            skipped.append(result)
            reasons.append(_probe_reason(result, "skipped"))
        elif result.status == "fail" and _is_probe_execution_error(result):
            probe_errors.append(result)
            reasons.append(_probe_reason(result, "could not execute"))
        elif result.status == "fail":
            contract_failures.append(result)
            reasons.append(_probe_reason(result, "failed the required contract"))

    breaking = [diff for diff in expected_diffs if diff.severity == "breaking"]
    changed = [diff for diff in expected_diffs if diff.severity == "changed"]
    unchanged = [diff for diff in expected_diffs if diff.severity == "none"]
    for diff in breaking:
        reasons.append(
            f"{diff.package}:{diff.probe} {diff.from_version} -> {diff.to_version} "
            f"is breaking: {_bounded_text(diff.summary)}"
        )

    transitions: list[ResolverTransition] = []
    if not _is_sequence_payload(expected_transitions):
        malformed_count += 1
        reasons.append("expected_transitions must be an array")
    else:
        for index, item in enumerate(cast(Sequence[object], expected_transitions)):
            try:
                transition = _coerce_transition(item)
            except (KeyError, TypeError, ValueError) as exc:
                malformed_count += 1
                reasons.append(f"expected transition {index} is malformed: {_bounded_text(exc)}")
                continue
            transitions.append(transition)
            if transition.package not in package_set:
                malformed_count += 1
                reasons.append(
                    f"expected transition contains unexpected package {transition.package}"
                )
            elif (
                transition.package in baselines
                and baselines[transition.package] != transition.from_version
            ):
                malformed_count += 1
                reasons.append(
                    f"{transition.package} transition baseline {transition.from_version} "
                    f"contradicts expected baseline {baselines[transition.package]}"
                )

    missing_comparison_count = 0
    transition_packages = {transition.package for transition in transitions}
    for package in sorted(package_set - transition_packages):
        observations = [
            result
            for result in normalized_results
            if result.package == package
            and result.scope in {"current-baseline", "current-environment"}
        ]
        if not observations:
            missing_comparison_count += 1
            reasons.append(f"{package} has no observed current baseline")
            continue
        if package not in baselines:
            continue
        expected_version = baselines[package]
        if not any(
            result.package == package
            and result.version == expected_version
            and result.scope in {"current-baseline", "current-environment"}
            for result in observations
        ):
            missing_comparison_count += 1
            observed_versions = sorted(
                {
                    result.version if result.version is not None else "<not-installed>"
                    for result in observations
                }
            )
            expected_label = expected_version if expected_version is not None else "<not-installed>"
            reasons.append(
                f"{package} current observation does not match expected baseline "
                f"{expected_label} (observed: {', '.join(observed_versions)})"
            )

    for transition in sorted(set(transitions)):
        before = [
            result
            for result in normalized_results
            if result.package == transition.package
            and result.version == transition.from_version
            and result.scope in {"current-baseline", "current-environment"}
        ]
        after = [
            result
            for result in normalized_results
            if result.package == transition.package
            and result.version == transition.to_version
            and result.scope in {"candidate", "isolated-venv"}
        ]
        before_probes = {result.probe for result in before}
        after_probes = {result.probe for result in after}
        compared_probes = {
            diff.probe
            for diff in expected_diffs
            if diff.package == transition.package
            and diff.from_version == transition.from_version
            and diff.to_version == transition.to_version
        }
        if not before_probes or before_probes != after_probes or compared_probes != before_probes:
            missing_comparison_count += 1
            reasons.append(
                f"{transition.package} lacks a paired behavior comparison for "
                f"{transition.from_version} -> {transition.to_version}"
            )

    if contract_failures or breaking:
        status = "fail"
    elif probe_errors or skipped or missing_comparison_count or malformed_count:
        status = "incomplete"
    elif changed:
        status = "changed"
    else:
        status = "pass"

    return {
        "status": status,
        "breaking_count": len(breaking),
        "changed_count": len(changed),
        "unchanged_count": len(unchanged),
        "contract_failure_count": len(contract_failures),
        "probe_error_count": len(probe_errors),
        "skipped_count": len(skipped),
        "missing_comparison_count": missing_comparison_count,
        "malformed_count": malformed_count,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _is_sequence_payload(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _coerce_probe_result(item: object) -> BehaviorProbeResult:
    if isinstance(item, BehaviorProbeResult):
        result = item
    elif isinstance(item, Mapping):
        _require_string_fields(item, "package", "scope", "probe", "status", "summary")
        version = item.get("version")
        if version is not None and not isinstance(version, str):
            raise TypeError("version must be a string or null")
        details = item.get("details", {})
        if not isinstance(details, Mapping):
            raise TypeError("details must be an object")
        result = BehaviorProbeResult(
            package=item["package"],
            version=version,
            scope=item["scope"],
            probe=item["probe"],
            status=item["status"],
            summary=item["summary"],
            details=dict(details),
        )
    else:
        raise TypeError("result must be an object")
    if not result.package or not result.scope or not result.probe:
        raise ValueError("package, scope, and probe must be non-empty")
    if result.version is not None and not isinstance(result.version, str):
        raise TypeError("version must be a string or null")
    if result.scope not in _RESULT_SCOPES:
        raise ValueError(f"unknown probe scope {result.scope!r}")
    if result.status not in _RESULT_STATUSES:
        raise ValueError(f"unknown probe status {result.status!r}")
    if not isinstance(result.summary, str):
        raise TypeError("summary must be a string")
    if not isinstance(result.details, Mapping):
        raise TypeError("details must be an object")
    details = _normalize_probe_details(result.details)
    return BehaviorProbeResult(
        package=result.package,
        version=result.version,
        scope=result.scope,
        probe=result.probe,
        status=result.status,
        summary=result.summary,
        details=details,
    )


def _coerce_behavior_diff(item: object) -> BehaviorDiff:
    if isinstance(item, BehaviorDiff):
        diff = item
    elif isinstance(item, Mapping):
        _require_string_fields(
            item,
            "package",
            "probe",
            "severity",
            "summary",
            "before_status",
            "after_status",
        )
        from_version = item.get("from_version")
        to_version = item.get("to_version")
        if from_version is not None and not isinstance(from_version, str):
            raise TypeError("from_version must be a string or null")
        if to_version is not None and not isinstance(to_version, str):
            raise TypeError("to_version must be a string or null")
        diff = BehaviorDiff(
            package=item["package"],
            from_version=from_version,
            to_version=to_version,
            probe=item["probe"],
            severity=item["severity"],
            summary=item["summary"],
            before_status=item["before_status"],
            after_status=item["after_status"],
        )
    else:
        raise TypeError("diff must be an object")
    if not diff.package or not diff.probe:
        raise ValueError("diff package and probe must be non-empty")
    if diff.from_version is not None and not isinstance(diff.from_version, str):
        raise TypeError("diff from_version must be a string or null")
    if diff.to_version is not None and not isinstance(diff.to_version, str):
        raise TypeError("diff to_version must be a string or null")
    if not isinstance(diff.summary, str):
        raise TypeError("diff summary must be a string")
    if diff.severity not in _DIFF_SEVERITIES:
        raise ValueError(f"unknown diff severity {diff.severity!r}")
    if diff.before_status not in _RESULT_STATUSES or diff.after_status not in _RESULT_STATUSES:
        raise ValueError("diff contains an unknown probe status")
    return diff


def _coerce_transition(item: ResolverTransition | Mapping[str, object]) -> ResolverTransition:
    if isinstance(item, ResolverTransition):
        transition = item
    elif isinstance(item, Mapping):
        _require_string_fields(item, "package", "from_version", "to_version")
        transition = ResolverTransition(
            package=cast(str, item["package"]),
            from_version=cast(str, item["from_version"]),
            to_version=cast(str, item["to_version"]),
        )
    else:
        raise TypeError("transition must be an object")
    if not all(
        isinstance(value, str)
        for value in (transition.package, transition.from_version, transition.to_version)
    ):
        raise TypeError("transition fields must be strings")
    if not transition.package or not transition.from_version or not transition.to_version:
        raise ValueError("transition fields must be non-empty")
    return transition


def _require_string_fields(item: Mapping[str, object], *fields: str) -> None:
    for field in fields:
        if field not in item:
            raise KeyError(field)
        if not isinstance(item[field], str):
            raise TypeError(f"{field} must be a string")


def _normalize_probe_details(details: Mapping[str, object]) -> dict[str, Any]:
    normalized = dict(details)
    for field in _DETAIL_STRING_SEQUENCE_FIELDS & details.keys():
        value = details[field]
        if not _is_sequence_payload(value) or not all(
            isinstance(item, str) for item in cast(Sequence[object], value)
        ):
            raise TypeError(f"details.{field} must be an array of strings")
        normalized[field] = list(cast(Sequence[str], value))
    if "error" in details and not isinstance(details["error"], str):
        raise TypeError("details.error must be a string")
    if "failure_step" in details and not isinstance(details["failure_step"], str):
        raise TypeError("details.failure_step must be a string")
    return normalized


def _behavior_diff_key(diff: BehaviorDiff) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        diff.package,
        diff.from_version or "",
        diff.to_version or "",
        diff.probe,
        diff.severity,
        diff.summary,
        diff.before_status,
        diff.after_status,
    )


def _expected_transitions(
    packages: Sequence[Mapping[str, object]],
    update_versions: Mapping[str, str],
) -> tuple[ResolverTransition, ...]:
    transitions: list[ResolverTransition] = []
    for package in packages:
        name = str(package.get("name") or "")
        candidate = update_versions.get(name)
        if not name or not candidate:
            continue
        baseline = _string_or_none(package.get("locked_version")) or _string_or_none(
            package.get("installed_version")
        )
        transitions.append(
            ResolverTransition(
                package=name,
                from_version=baseline or "<missing-baseline>",
                to_version=str(candidate),
            )
        )
    return tuple(transitions)


def _probe_reason(result: BehaviorProbeResult, classification: str) -> str:
    return (
        f"{result.package}:{result.probe} {result.scope} {classification}: "
        f"{_bounded_text(result.summary, limit=240)}"
    )


def _is_probe_execution_error(result: BehaviorProbeResult) -> bool:
    return (
        result.status == "fail"
        and "error" in result.details
        and not _has_contract_failure_evidence(result)
    )


def _has_contract_failure_evidence(result: BehaviorProbeResult) -> bool:
    missing = result.details.get("missing")
    return bool(missing) and _is_sequence_payload(missing)


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
    try:
        module = importlib.import_module("claude_agent_sdk")
        options_cls = module.ClaudeAgentOptions
    except Exception as exc:
        return _failed(package, version, scope, "adapter-contract", exc)
    fields = _fields(options_cls)
    expected = {
        "model",
        "allowed_tools",
        "disallowed_tools",
        "permission_mode",
        "system_prompt",
        "cwd",
        "mcp_servers",
        "resume",
        "env",
        "max_budget_usd",
        "output_format",
    }
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
    )


def _probe_codex(*, version: str | None, scope: str) -> BehaviorProbeResult:
    package = "openai-codex"
    try:
        module = importlib.import_module("openai_codex")
        run_params = set(inspect.signature(module.AsyncThread.run).parameters)
        start_params = set(inspect.signature(module.AsyncCodex.thread_start).parameters)
    except Exception as exc:
        return _failed(package, version, scope, "adapter-contract", exc)
    expected_run = {"cwd", "model", "approval_mode", "sandbox", "output_schema", "effort"}
    expected_start = {"developer_instructions", "cwd", "model", "approval_mode", "sandbox"}
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
    )


def _probe_antigravity(*, version: str | None, scope: str) -> BehaviorProbeResult:
    package = "google-antigravity"
    try:
        importlib.import_module(DEFAULT_MODULES[package])
        importlib.import_module("google.antigravity.types")
        importlib.import_module("google.antigravity.agent")
        importlib.import_module("google.antigravity.hooks.policy")
        config_module = importlib.import_module(
            "google.antigravity.connections.local.local_connection_config"
        )
        config_cls = config_module.LocalAgentConfig
    except Exception as exc:
        return _failed(package, version, scope, "adapter-contract", exc)
    fields = _fields(config_cls)
    expected = {
        "model",
        "api_key",
        "vertex",
        "project",
        "location",
        "system_instructions",
        "capabilities",
        "policies",
        "workspaces",
        "conversation_id",
        "save_dir",
        "app_data_dir",
        "response_schema",
        "mcp_servers",
    }
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
    contract: dict[str, Any] = {"missing": _normalized_contract_field(details.get("missing"))}
    if "required_fields" in details:
        contract["required_fields"] = _normalized_contract_field(details.get("required_fields"))
    if "required_run_params" in details:
        contract["required_run_params"] = _normalized_contract_field(
            details.get("required_run_params")
        )
    if "required_start_params" in details:
        contract["required_start_params"] = _normalized_contract_field(
            details.get("required_start_params")
        )
    return contract


def _normalized_contract_field(value: object) -> object:
    if _is_sequence_payload(value) and all(
        isinstance(item, str) for item in cast(Sequence[object], value)
    ):
        return sorted(cast(Sequence[str], value))
    return value


def _failed(
    package: str,
    version: str | None,
    scope: str,
    probe: str,
    exc: Exception,
) -> BehaviorProbeResult:
    error = _bounded_text(exc)
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe=probe,
        status="fail",
        summary=error,
        details={"error": error, "failure_step": "adapter-probe"},
    )


def _probe_execution_failure(
    package: str,
    version: str,
    scope: str,
    step: str,
    error: object,
) -> BehaviorProbeResult:
    detail = _bounded_text(error)
    summary = _bounded_text(f"{step} failed: {detail}", limit=560)
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe=_probe_identity(package),
        status="fail",
        summary=summary,
        details={"error": summary, "failure_step": step},
    )


def _parse_probe_output(
    output: str,
    *,
    package: str,
    version: str,
    scope: str,
) -> tuple[BehaviorProbeResult, ...]:
    raw = json.loads(output)
    if not isinstance(raw, list) or not raw:
        raise ValueError("probe output must be a non-empty array")
    results: list[BehaviorProbeResult] = []
    for item in raw:
        result = _coerce_probe_result(item)
        if result.package != package:
            raise ValueError(
                f"probe package {result.package!r} does not match requested package {package!r}"
            )
        if result.version != version:
            raise ValueError(
                f"probe version {result.version!r} does not match requested version {version!r}"
            )
        if result.scope != scope:
            raise ValueError(
                f"probe scope {result.scope!r} does not match requested scope {scope!r}"
            )
        results.append(result)
    return tuple(results)


def _probe_identity(package: str) -> str:
    if package == "openai-codex-cli-bin":
        return "binary-distribution"
    if package in {"claude-agent-sdk", "openai-codex", "google-antigravity"}:
        return "adapter-contract"
    return "package-import"


def _skipped_candidate_probe(package: str, version: str) -> BehaviorProbeResult:
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope="candidate",
        probe=_probe_identity(package),
        status="skip",
        summary=(
            "Candidate behavior probe skipped: probing a candidate installs and "
            "imports freshly downloaded upstream code, which is opt-in. Rerun "
            "with --inspect-candidates to collect this evidence."
        ),
        details={"reason": "candidate installs are opt-in (--inspect-candidates)"},
    )


def _bounded_text(value: object, *, limit: int = 480) -> str:
    if isinstance(value, bytes):
        text = value.decode(errors="replace")
    else:
        text = str(value)
    return " ".join(text.split())[:limit]


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


_PROBE_SCRIPT = textwrap.dedent(
    """
    import importlib
    import importlib.metadata
    import inspect
    import json
    import sys

    package, version, scope = sys.argv[1:4]

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
        return {
            "package": package,
            "version": version,
            "scope": scope,
            "probe": probe,
            "status": "fail",
            "summary": str(exc),
            "details": {"error": str(exc)},
        }

    def result(probe, status, summary, details):
        return {
            "package": package,
            "version": version,
            "scope": scope,
            "probe": probe,
            "status": status,
            "summary": summary,
            "details": details,
        }

    try:
        if package == "claude-agent-sdk":
            module = importlib.import_module("claude_agent_sdk")
            option_fields = fields(getattr(module, "ClaudeAgentOptions"))
            expected = {
                "model", "allowed_tools", "disallowed_tools", "permission_mode",
                "system_prompt", "cwd", "mcp_servers", "resume", "env",
                "max_budget_usd", "output_format",
            }
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
            expected_run = {"cwd", "model", "approval_mode", "sandbox", "output_schema", "effort"}
            expected_start = {"developer_instructions", "cwd", "model", "approval_mode", "sandbox"}
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
            importlib.import_module("google.antigravity")
            importlib.import_module("google.antigravity.types")
            importlib.import_module("google.antigravity.agent")
            importlib.import_module("google.antigravity.hooks.policy")
            config_module = importlib.import_module(
                "google.antigravity.connections.local.local_connection_config"
            )
            config_fields = fields(getattr(config_module, "LocalAgentConfig"))
            expected = {
                "model", "api_key", "vertex", "project", "location",
                "system_instructions", "capabilities", "policies", "workspaces",
                "conversation_id", "save_dir", "app_data_dir", "response_schema",
                "mcp_servers",
            }
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
