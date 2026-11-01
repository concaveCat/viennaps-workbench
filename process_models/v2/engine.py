"""Engine/runtime boundary for the v2 process-model contract.

An *engine* is the thing that actually hosts an implementation: the installed
ViennaPS module, the Python interpreter, or an external simulator. A variant
declares an :class:`EngineRequirement`; a provider resolves it to a concrete
:class:`EngineLease`. Resolution is strict - there is no silent fallback to a
different engine, version, or backend.

Version matching
----------------
Only ``MAJOR.MINOR.PATCH`` bounds are supported (no ranges, wildcards, or
pre-release ordering). If either the installed version or a requested bound is
not parseable, that bound is satisfied **only by exact string equality**. This
keeps commit-style versions (``"rev-a"``) deterministic without pretending to
understand them.

Phase-1 boundaries
------------------
There is no process launch, no environment management, and no warm pool. A
``StaticEngine`` wraps an already-initialized handle supplied by the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol, runtime_checkable

__all__ = [
    "Backend",
    "Engine",
    "EngineCapabilities",
    "EngineKind",
    "EngineLease",
    "EngineProvider",
    "EngineRequirement",
    "EngineUnavailableError",
    "StaticEngine",
    "StaticEngineProvider",
    "version_satisfies",
]

EngineKind = Literal["native", "surrogate", "external"]
Backend = Literal["cpu", "gpu", "any"]

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class EngineUnavailableError(RuntimeError):
    """Raised when no provider engine satisfies a requirement."""


def _semver_key(version: str) -> tuple[int, int, int] | None:
    match = _SEMVER.match(version)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_satisfies(version: str, min_version: str | None = None, max_version: str | None = None) -> bool:
    """Return whether ``version`` lies within the inclusive ``min``/``max`` bounds.

    Non-semver versions and bounds compare by exact string equality, as
    documented in the module docstring.
    """
    installed = _semver_key(version)
    if min_version is not None:
        floor = _semver_key(min_version)
        if installed is None or floor is None:
            if version != min_version:
                return False
        elif installed < floor:
            return False
    if max_version is not None:
        ceiling = _semver_key(max_version)
        if installed is None or ceiling is None:
            if version != max_version:
                return False
        elif installed > ceiling:
            return False
    return True


@dataclass(frozen=True)
class EngineRequirement:
    """What a variant needs from an engine.

    ``backend="any"`` matches any backend the engine advertises. ``capabilities``
    is a set of free-form feature names that must all be advertised.
    """

    kind: EngineKind
    engine_id: str
    min_version: str | None = None
    max_version: str | None = None
    backend: Backend = "any"
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.engine_id, str) or not self.engine_id.strip():
            raise ValueError("engine_id must be a non-empty string")
        if self.kind not in ("native", "surrogate", "external"):
            raise ValueError(f"unknown engine kind {self.kind!r}")
        if self.backend not in ("cpu", "gpu", "any"):
            raise ValueError(f"unknown engine backend {self.backend!r}")
        object.__setattr__(self, "capabilities", tuple(str(capability) for capability in self.capabilities))


@dataclass(frozen=True)
class EngineCapabilities:
    """What an engine advertises about itself."""

    engine_id: str
    version: str
    kind: EngineKind
    backends: tuple[str, ...]
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.engine_id, str) or not self.engine_id.strip():
            raise ValueError("engine_id must be a non-empty string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("engine version must be a non-empty string")
        object.__setattr__(self, "backends", tuple(str(backend) for backend in self.backends))
        object.__setattr__(self, "capabilities", tuple(str(capability) for capability in self.capabilities))


@runtime_checkable
class EngineLease(Protocol):
    """An acquired engine handle plus the identity needed for replay.

    The lease is deliberately opaque beyond its identity: ``handle`` is
    whatever the implementation needs (the ViennaPS module, a Python module,
    ...). ``release`` is idempotent and safe to call from a context manager.
    """

    engine_id: str
    engine_version: str
    kind: EngineKind
    backend: str
    fingerprint: str
    handle: Any

    def release(self) -> None: ...

    def __enter__(self) -> EngineLease: ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


@runtime_checkable
class Engine(Protocol):
    """A single describable/acquirable engine."""

    def describe(self) -> EngineCapabilities: ...

    def acquire(self, requirement: EngineRequirement) -> EngineLease: ...


@runtime_checkable
class EngineProvider(Protocol):
    """A set of engines that can be acquired by requirement."""

    def engines(self) -> list[EngineCapabilities]: ...

    def acquire(self, requirement: EngineRequirement) -> EngineLease: ...


@dataclass(frozen=True)
class _StaticLease:
    """Lease over an already-initialized handle; releasing is a no-op."""

    engine_id: str
    engine_version: str
    kind: EngineKind
    backend: str
    fingerprint: str
    handle: Any

    def release(self) -> None:
        """Release the lease.

        Static handles are owned by the caller, so this intentionally does
        nothing. It exists so callers can use a uniform ``with`` block.
        """

    def __enter__(self) -> _StaticLease:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


@dataclass(frozen=True)
class StaticEngine:
    """An engine wrapping an already-initialized handle.

    Matching is strict over kind, ``engine_id``, version bounds, backend, and
    advertised capabilities. A non-match raises :class:`EngineUnavailableError`;
    the engine never substitutes another version or backend.
    """

    capabilities: EngineCapabilities
    handle: Any
    fingerprint: str

    def describe(self) -> EngineCapabilities:
        return self.capabilities

    def acquire(self, requirement: EngineRequirement) -> EngineLease:
        caps = self.capabilities
        reasons: list[str] = []
        if requirement.kind != caps.kind:
            reasons.append(f"kind {requirement.kind!r} != {caps.kind!r}")
        if requirement.engine_id != caps.engine_id:
            reasons.append(f"engine_id {requirement.engine_id!r} != {caps.engine_id!r}")
        if not version_satisfies(caps.version, requirement.min_version, requirement.max_version):
            reasons.append(
                f"version {caps.version!r} outside [{requirement.min_version!r}, {requirement.max_version!r}]"
            )
        backend = _resolve_backend(requirement.backend, caps.backends)
        if backend is None:
            reasons.append(f"backend {requirement.backend!r} not in {list(caps.backends)!r}")
        missing = sorted(set(requirement.capabilities) - set(caps.capabilities))
        if missing:
            reasons.append(f"missing capabilities {missing!r}")

        if reasons:
            raise EngineUnavailableError(
                f"engine {caps.engine_id!r} {caps.version!r} does not match: " + "; ".join(reasons)
            )

        return _StaticLease(
            engine_id=caps.engine_id,
            engine_version=caps.version,
            kind=caps.kind,
            backend=backend or requirement.backend,
            fingerprint=self.fingerprint,
            handle=self.handle,
        )


class StaticEngineProvider:
    """A fixed set of :class:`StaticEngine` values.

    ``acquire`` returns the first engine that matches the requirement. If none
    matches it raises :class:`EngineUnavailableError`; requests never fall back
    to a different requirement.
    """

    def __init__(self, engines: Iterable[StaticEngine]) -> None:
        self._engines = tuple(engines)
        for engine in self._engines:
            if not isinstance(engine, StaticEngine):
                raise TypeError("StaticEngineProvider accepts StaticEngine instances")

    def engines(self) -> list[EngineCapabilities]:
        """Return the advertised capabilities in registration order."""
        return [engine.describe() for engine in self._engines]

    def acquire(self, requirement: EngineRequirement) -> EngineLease:
        failures: list[str] = []
        for engine in self._engines:
            try:
                return engine.acquire(requirement)
            except EngineUnavailableError as error:
                failures.append(str(error))
        detail = "; ".join(failures) if failures else "no engines are registered"
        raise EngineUnavailableError(
            f"no engine satisfies {requirement.kind!r} {requirement.engine_id!r} "
            f"[{requirement.min_version!r}, {requirement.max_version!r}]: {detail}"
        )


def _resolve_backend(requested: Backend, available: tuple[str, ...]) -> str | None:
    """Return the backend to expose, or ``None`` when the requirement cannot match."""
    if requested == "any":
        if not available:
            return "any"
        if "cpu" in available:
            return "cpu"
        return available[0]
    if requested in available or "any" in available:
        return requested
    return None
