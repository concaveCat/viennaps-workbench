"""Installed ViennaPS/ViennaLS identity and version-qualified policy.

The Workbench keeps compatibility with the 4.6.2 production runtime while
allowing an isolated 4.7.0 evaluation runtime. Everything that depends on a
concrete engine version belongs here instead of spread-out literals.

``EVIDENCE_ENGINE_VERSION`` is deliberately *not* the installed version: the
archived capability/evidence matrices in ``docs/`` were measured against 4.6.2
and stay pinned to that run. Only current-runtime metadata uses
``engine_version()``.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Iterable


SUPPORTED_ENGINE_VERSIONS = ("4.6.2", "4.7.0")
EVIDENCE_ENGINE_VERSION = "4.6.2"
# On-disk catalogs/registries keep the historical header so the unchanged
# 4.6.2 service can still read a file written by the 4.7.0 evaluation runtime.
# Readers accept every SUPPORTED_ENGINE_VERSIONS value.
ENGINE_REGISTRY_VERSION = "4.6.2"

# The patched multi-ion GPU runtime is an ABI-bound 4.6.2 build. It cannot be
# loaded by a 4.7.0/ViennaLS-5.10 environment, so keep the accepted set explicit.
PATCHED_RUNTIME_VERSIONS = ("4.6.2",)


@lru_cache(maxsize=1)
def runtime_versions() -> tuple[str | None, str | None]:
    """Return the installed (viennaps, viennals) versions, or None if absent."""
    found: list[str | None] = []
    for package in ("viennaps", "viennals"):
        try:
            found.append(version(package))
        except PackageNotFoundError:
            found.append(None)
    return found[0], found[1]


def engine_version() -> str:
    """The installed ViennaPS version used for current-runtime metadata."""
    return runtime_versions()[0] or "unknown"


def viennals_version() -> str:
    return runtime_versions()[1] or "unknown"


def _release(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def at_least(version_value: str, minimum: str) -> bool:
    if not version_value or version_value == "unknown":
        return False
    left, right = _release(version_value), _release(minimum)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


def ald_surface_diffusion_supported(engine: str | None = None) -> bool:
    """False on 4.7.0, where the native ALD stencil can hang."""
    return not at_least(engine or engine_version(), "4.7.0")


def ald_surface_diffusion_error(engine: str | None = None) -> str | None:
    """Shared rejection message; None when the runtime is safe.

    API validation and the worker builder both call this so a hand-edited
    worker input cannot reach the native hanging stencil.
    """
    version_value = engine or engine_version()
    if at_least(version_value, "4.7.0"):
        return (f"ViennaPS {version_value} has a confirmed native hang in the ALD "
                f"SurfaceDiffusionParameters stencil; the worker process would never "
                f"return. Use the ViennaPS 4.6.2 runtime, or remove or disable this block.")
    return None


# Setters that are bound but inert (or unbound) on 4.7.0 for the Workbench
# oxidation configuration. ``probe_oxidation_setters.py`` is the evidence:
# cflFactor still changes the surface; mechanicsTolerance and
# couplingTolerance do not; setSimplePressureRelaxation is not bound at all.
OXIDATION_ADVANCED_RESTRICTED = {
    "simplePressureRelaxation": "removed from the 4.7.0 binding",
    "mechanicsTolerance": "inert on 4.7.0 (probe_oxidation_setters.py)",
    "couplingTolerance": "inert on 4.7.0 (probe_oxidation_setters.py)",
}


def oxidation_advanced_supported(key: str, engine: str | None = None) -> bool:
    if key not in OXIDATION_ADVANCED_RESTRICTED:
        return True
    return ald_surface_diffusion_supported(engine)


def is_supported_engine(value: str) -> bool:
    return value in SUPPORTED_ENGINE_VERSIONS


def version_label(values: Iterable[str]) -> str:
    return " / ".join(values)


def installed_version_map() -> dict[str, str]:
    viennaps, viennals = runtime_versions()
    return {"viennaps": viennaps or "unknown", "viennals": viennals or "unknown"}


def version_mismatch_message(required: dict[str, str], installed: dict[str, str]) -> str:
    return (f"requires ViennaPS {required['viennaps']} / ViennaLS {required['viennals']}; "
            f"installed is {installed['viennaps']} / {installed['viennals']}. "
            f"Saved runs and native state are never reinterpreted under a different "
            f"runtime: start the matching ViennaPS {required['viennaps']} runtime.")


class EngineVersionMismatch(RuntimeError):
    """Required engine versions differ from the installed runtime."""

    def __init__(self, required: dict[str, str], installed: dict[str, str] | None = None):
        self.required_versions = {key: str(required[key]) for key in ("viennaps", "viennals")}
        self.installed_versions = installed or installed_version_map()
        self.message = version_mismatch_message(self.required_versions, self.installed_versions)
        super().__init__(self.message)

    def detail(self) -> dict[str, Any]:
        return {"message": self.message,
                "requiredVersions": dict(self.required_versions),
                "installedVersions": dict(self.installed_versions)}


def version_mismatch(required: Any) -> EngineVersionMismatch | None:
    """Compare a stored ``expectedVersions``/``executionRequirements`` mapping.

    Returns None when the mapping is absent or incomplete (legacy data), so
    only records that explicitly pin a runtime are gated.
    """
    if not isinstance(required, dict):
        return None
    wanted = {}
    for key in ("viennaps", "viennals"):
        value = required.get(key)
        if not isinstance(value, str) or not value:
            return None
        wanted[key] = value
    installed = installed_version_map()
    if (wanted["viennaps"] == installed["viennaps"]
            and wanted["viennals"] == installed["viennals"]):
        return None
    return EngineVersionMismatch(wanted, installed)
