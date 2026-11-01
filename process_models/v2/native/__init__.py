"""Native plugin implementations for the v2 process-model contract.

The native package owns implementations that run inside an installed native
engine (``viennaps``). Importing it never imports the native library or the
worker; both are resolved lazily at execution time.
"""

from __future__ import annotations

from .isotropic import (
    IsotropicNativeVariant,
    IsotropicParams,
    IsotropicProcessSpec,
    IsotropicSpec,
    register_isotropic,
)

__all__ = [
    "IsotropicNativeVariant",
    "IsotropicParams",
    "IsotropicProcessSpec",
    "IsotropicSpec",
    "register_isotropic",
]
