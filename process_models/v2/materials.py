"""Engine-neutral material-name registry.

Native material IDs are engine-specific. Plugins and the platform speak material
*names*; the engine's :class:`MaterialRegistry` maps those names to the IDs the
native handle uses. Keeping this boundary explicit prevents every plugin from
hard-coding ``getattr(ps.Material, name)`` or assuming a native ID is portable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, runtime_checkable

__all__ = [
    "MaterialRegistry",
    "MaterialRegistryError",
    "StaticMaterialRegistry",
]


class MaterialRegistryError(RuntimeError):
    """Raised when a material name or ID is absent or inconsistent."""


@runtime_checkable
class MaterialRegistry(Protocol):
    """Name <-> native material-id mapping for one engine."""

    def id_for(self, name: str) -> int: ...

    def name_for(self, material_id: int) -> str | None: ...

    def require(self, names: Iterable[str]) -> tuple[int, ...]: ...


@dataclass(frozen=True)
class StaticMaterialRegistry:
    """A small immutable registry for tests and custom engines."""

    mapping: Mapping[str, int]

    def __post_init__(self) -> None:
        mapping = {str(name): int(material_id) for name, material_id in dict(self.mapping).items()}
        if any(not name.strip() for name in mapping):
            raise ValueError("material names must be non-empty strings")
        if any(material_id < 0 for material_id in mapping.values()):
            raise ValueError(f"material ids must be >= 0, got {sorted(mapping.values())!r}")
        identifiers = list(mapping.values())
        duplicates = sorted({material_id for material_id in identifiers if identifiers.count(material_id) > 1})
        if duplicates:
            raise ValueError(f"material ids must be unique, got duplicates {duplicates!r}")
        object.__setattr__(self, "mapping", mapping)

    def id_for(self, name: str) -> int:
        try:
            return self.mapping[str(name)]
        except KeyError as error:
            raise MaterialRegistryError(f"material {name!r} is not registered") from error

    def name_for(self, material_id: int) -> str | None:
        for name, candidate in self.mapping.items():
            if candidate == int(material_id):
                return name
        return None

    def require(self, names: Iterable[str]) -> tuple[int, ...]:
        return tuple(self.id_for(name) for name in names)
