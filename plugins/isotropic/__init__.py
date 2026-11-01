"""Native isotropic etch/deposit plugin.

This package is an installable plugin: ``plugin.toml`` sits next to the code,
and the manifest entrypoint is ``plugins.isotropic:register``. The platform
loader adds the plugin directory's parent to ``sys.path``, so an external
operator package can use the same ``module:attribute`` shape.
"""

from __future__ import annotations

from .params import IsotropicParams
from .register import register, register_isotropic
from .spec import IsotropicProcessSpec, IsotropicSpec
from .variants import IsotropicNativeVariant

__all__ = [
    "IsotropicNativeVariant",
    "IsotropicParams",
    "IsotropicProcessSpec",
    "IsotropicSpec",
    "register",
    "register_isotropic",
]
