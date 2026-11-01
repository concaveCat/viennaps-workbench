"""Canonical parameters for the native isotropic etch/deposit plugin."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["IsotropicParams", "Operation", "RateMode"]

Operation = Literal["etch", "deposit"]
RateMode = Literal["uniform", "material"]


class IsotropicParams(BaseModel):
    """Parameters of one isotropic etch/deposit step.

    ``rateMagnitude`` is the uniform (default) rate. In ``rateMode="material"``
    the rate table is authoritative and unlisted materials do not move, which
    mirrors the worker's material-mode convention of a zero default rate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Operation
    rateMagnitude: float = Field(gt=0)
    duration: float = Field(gt=0)
    depositMaterial: str = "SiO2"
    rateMode: RateMode = "uniform"
    materialRates: dict[str, float] = Field(default_factory=dict)
    maskMaterials: list[str] = Field(default_factory=list)
    isotropicTimeStepRatio: float | None = Field(default=None, ge=0.05, le=0.5)

    @field_validator("rateMagnitude", "duration")
    @classmethod
    def _finite_positive(cls, value: float, info: Any) -> float:
        if not math.isfinite(value):
            raise ValueError(f"{info.field_name} must be finite")
        return value

    @field_validator("materialRates")
    @classmethod
    def _finite_material_rates(cls, value: dict[str, float]) -> dict[str, float]:
        for name, rate in value.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("materialRates keys must be non-empty material names")
            if not math.isfinite(rate):
                raise ValueError(f"materialRates[{name!r}] must be finite")
            if rate < 0.0:
                raise ValueError(f"materialRates[{name!r}] must be non-negative")
        return value

    @field_validator("maskMaterials")
    @classmethod
    def _non_empty_mask_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("maskMaterials entries must be non-empty material names")
        return value
