"""Shared, credential-scrubbed candidate environments for one evolution run."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CandidateEnvironment:
    """A prepared isolated interpreter containing one exact package version."""

    python: Path
    env: Mapping[str, str]


@dataclass
class _CandidateEnvironmentCache:
    root: Path
    environments: dict[tuple[str, str, str], CandidateEnvironment] = field(default_factory=dict)


class CandidatePreparationError(RuntimeError):
    """A candidate environment could not be created or populated."""

    def __init__(self, step: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.step = step
        self.cause = cause


_ACTIVE_CACHE: contextvars.ContextVar[_CandidateEnvironmentCache | None] = contextvars.ContextVar(
    "sdk_evolution_candidate_environment_cache", default=None
)


@contextlib.contextmanager
def candidate_environment_cache() -> Iterator[None]:
    """Reuse each package/version install across snapshots and behavior probes."""

    with tempfile.TemporaryDirectory(prefix="ark-sdk-inspection-") as directory:
        token = _ACTIVE_CACHE.set(_CandidateEnvironmentCache(Path(directory)))
        try:
            yield
        finally:
            _ACTIVE_CACHE.reset(token)


def prepare_cached_candidate_environment(
    package: str,
    version: str,
    *,
    python: str,
    timeout: int,
    runner: Callable[..., Any],
    env_factory: Callable[[Path], Mapping[str, str]],
) -> CandidateEnvironment | None:
    """Prepare or return a cached environment; return ``None`` outside a run cache."""

    cache = _ACTIVE_CACHE.get()
    if cache is None:
        return None
    key = (package, version, python)
    existing = cache.environments.get(key)
    if existing is not None:
        _progress(f"reusing isolated {package}=={version} environment")
        return existing

    digest = hashlib.sha256("\0".join(key).encode()).hexdigest()[:16]
    workspace = cache.root / digest
    workspace.mkdir(parents=True, exist_ok=False)
    venv = workspace / ".venv"
    env = dict(env_factory(workspace))
    _progress(f"creating isolated environment for {package}=={version}")
    try:
        runner(
            (python, "-m", "venv", str(venv)),
            check=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise CandidatePreparationError("virtual-environment-creation", exc) from exc

    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    venv_python = venv / bin_dir / "python"
    install_command: Sequence[str] = (
        str(venv_python),
        "-m",
        "pip",
        "install",
        f"{package}=={version}",
    )
    for attempt in range(3):
        try:
            _progress(f"installing {package}=={version} (attempt {attempt + 1}/3)")
            runner(
                install_command,
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            break
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if attempt == 2 or isinstance(exc, OSError):
                raise CandidatePreparationError("package-installation", exc) from exc
            time.sleep(0.25 * (attempt + 1))

    prepared = CandidateEnvironment(python=venv_python, env=env)
    cache.environments[key] = prepared
    _progress(f"isolated environment ready for {package}=={version}")
    return prepared


def _progress(message: str) -> None:
    print(f"[sdk-evolution] {message}", flush=True)
