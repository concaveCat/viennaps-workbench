"""Explicit registry for v2 process specs and engine variants.

Resolution is intentionally dumb and deterministic:

* process identity is keyed by ``(process_id, process_version)``;
* implementations are keyed by ``(process_id, variant_id, variant_version)``;
* variants are *not* tied to a process version, so adding a variant never
  forces a process-version bump.

Without a version pin, the newest ``MAJOR.MINOR.PATCH`` version wins. If any
candidate version is not semver, registration order decides instead. Pins are
strict: a missing pinned version raises ``KeyError`` and never falls back.

``implementation_hash`` covers the variant's entire source *file* plus the
qualified class name. That closes the v1 gap where a change to a module-level
helper function left the hash untouched.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .contract import ModelDescriptor, ProcessSpec, VariantDescriptor, VariantRegistration

__all__ = [
    "ProcessRegistry",
    "ResolvedVariant",
    "compute_implementation_hash",
]

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True)
class ResolvedVariant:
    """A concrete process version plus a concrete variant version.

    ``implementation_hash`` is the registration's explicit hash, or the
    module-file hash computed by :func:`compute_implementation_hash`.
    """

    process: ProcessSpec
    variant: VariantRegistration
    process_version: str
    variant_version: str
    implementation_hash: str


class ProcessRegistry:
    """In-memory registry with explicit, ID-based registration.

    Registration order is preserved for listing and for non-semver version
    selection. The registry owns no native objects and performs no imports
    named by project JSON.
    """

    def __init__(self) -> None:
        self._processes: dict[str, dict[str, ProcessSpec]] = {}
        self._variants: dict[str, dict[str, list[VariantRegistration]]] = {}

    def register_process(self, spec: ProcessSpec) -> None:
        """Register one process version.

        Raises ``ValueError`` when ``(spec.id, spec.version)`` is already
        registered. Multiple versions of the same id may coexist.
        """
        if not isinstance(spec, ProcessSpec):
            raise TypeError("register_process expects a ProcessSpec")
        versions = self._processes.setdefault(spec.id, {})
        if spec.version in versions:
            raise ValueError(f"process {spec.id!r} version {spec.version!r} is already registered")
        versions[spec.version] = spec

    def register_variant(self, registration: VariantRegistration) -> None:
        """Register one variant version under an existing process id.

        Raises ``KeyError`` when the process id is unknown and ``ValueError``
        for a duplicate ``(process_id, variant_id, variant_version)``.
        """
        if not isinstance(registration, VariantRegistration):
            raise TypeError("register_variant expects a VariantRegistration")
        if registration.process_id not in self._processes:
            raise KeyError(f"unknown process {registration.process_id!r}")

        declared_process_id = getattr(registration.variant, "process_id", None)
        if declared_process_id is not None and declared_process_id != registration.process_id:
            raise ValueError(
                f"variant {registration.variant_id!r} declares process {declared_process_id!r}, "
                f"expected {registration.process_id!r}"
            )

        by_variant = self._variants.setdefault(registration.process_id, {})
        versions = by_variant.setdefault(registration.variant_id, [])
        if any(version.version == registration.version for version in versions):
            raise ValueError(
                f"variant {registration.process_id!r}/{registration.variant_id!r} "
                f"version {registration.version!r} is already registered"
            )
        versions.append(registration)

    def get_process(self, process_id: str, expected_version: str | None = None) -> ProcessSpec:
        """Return a process version, newest by default.

        Raises ``KeyError`` for an unknown id or a pinned version that is not
        registered.
        """
        versions = self._processes.get(process_id)
        if not versions:
            raise KeyError(f"unknown process {process_id!r}")
        if expected_version is not None:
            if expected_version not in versions:
                raise KeyError(f"unknown version {expected_version!r} for process {process_id!r}")
            return versions[expected_version]
        return versions[_latest_version(list(versions))]

    def get_variant(
        self,
        process_id: str,
        variant_id: str,
        expected_process_version: str | None = None,
        expected_variant_version: str | None = None,
    ) -> ResolvedVariant:
        """Resolve stable IDs to a process version and a variant registration.

        ``expected_process_version`` pins the process metadata only; variants
        remain independent. ``expected_variant_version`` is a strict pin. All
        misses raise ``KeyError``.
        """
        process = self.get_process(process_id, expected_process_version)

        by_variant = self._variants.get(process_id)
        if not by_variant:
            raise KeyError(f"process {process_id!r} has no registered variants")
        registrations = by_variant.get(variant_id)
        if not registrations:
            raise KeyError(f"unknown variant {variant_id!r} for process {process_id!r}")

        if expected_variant_version is not None:
            matches = [item for item in registrations if item.version == expected_variant_version]
            if not matches:
                raise KeyError(
                    f"unknown variant {variant_id!r} version {expected_variant_version!r} for process {process_id!r}"
                )
            registration = matches[0]
        else:
            selected = _latest_version([item.version for item in registrations])
            registration = next(item for item in registrations if item.version == selected)

        implementation_hash = registration.implementation_hash or compute_implementation_hash(registration.variant)
        return ResolvedVariant(
            process=process,
            variant=registration,
            process_version=process.version,
            variant_version=registration.version,
            implementation_hash=implementation_hash,
        )

    def list_processes(self) -> list[ModelDescriptor]:
        """Return the newest descriptor per process id, in registration order."""
        descriptors: list[ModelDescriptor] = []
        for process_id, versions in self._processes.items():
            spec = versions[_latest_version(list(versions))]
            descriptors.append(replace(spec.describe(), variants=tuple(self._variant_descriptors(process_id))))
        return descriptors

    def list_variants(self, process_id: str) -> list[VariantDescriptor]:
        """Return every registered variant for ``process_id`` in registration order."""
        if process_id not in self._processes:
            raise KeyError(f"unknown process {process_id!r}")
        return self._variant_descriptors(process_id)

    def _variant_descriptors(self, process_id: str) -> list[VariantDescriptor]:
        descriptors: list[VariantDescriptor] = []
        for registrations in self._variants.get(process_id, {}).values():
            for registration in registrations:
                capabilities = registration.capabilities
                descriptors.append(
                    VariantDescriptor(
                        id=registration.variant_id,
                        version=registration.version,
                        kind=registration.engine.kind,
                        engine_id=registration.engine.engine_id,
                        reads=tuple(sorted(capabilities.reads)),
                        writes=tuple(sorted(capabilities.writes)),
                        experimental=capabilities.experimental,
                        implementation_hash=registration.implementation_hash
                        or compute_implementation_hash(registration.variant),
                    )
                )
        return descriptors


def _semver_key(version: str) -> tuple[int, int, int] | None:
    match = _SEMVER.match(version)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _latest_version(versions: list[str]) -> str:
    """Pick the highest semver, or the last registered version otherwise."""
    keys = [_semver_key(version) for version in versions]
    if all(key is not None for key in keys):
        return max(zip(keys, versions), key=lambda item: item[0])[1]  # type: ignore[arg-type]
    return versions[-1]


def _source_file(cls: type) -> str | None:
    """Return the class source file, isolated for tests and fallbacks."""
    return inspect.getsourcefile(cls)


def compute_implementation_hash(variant: object) -> str:
    """Hash the variant's source file bytes plus its qualified class name.

    Hashing the whole file (not just the class source) means module-level
    helpers used by the implementation are part of the identity. Fallbacks:

    1. class source, when the file cannot be read;
    2. the qualified class name, when source is unavailable entirely.

    The result is a 64-character lowercase SHA-256 hex digest.
    """
    cls = type(variant)
    qualname = f"{cls.__module__}.{cls.__qualname__}"
    suffix = qualname.encode("utf-8")

    source_file = _source_file(cls)
    if source_file:
        try:
            file_bytes = Path(source_file).read_bytes()
        except OSError:
            file_bytes = None
        if file_bytes is not None:
            return hashlib.sha256(file_bytes + b"\x00" + suffix).hexdigest()

    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        return hashlib.sha256(suffix).hexdigest()
    return hashlib.sha256(source.encode("utf-8") + b"\x00" + suffix).hexdigest()
