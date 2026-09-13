"""Validated API payloads shared by ingestion and platform settings."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class SettingsUpdate(BaseModel):
    """Accept one atomic update of known, finite platform settings."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    myop_enabled: StrictBool | None = None
    tank_capacity_l: float | None = Field(default=None, gt=0, le=500)
    fuel_density_gl: float | None = Field(default=None, ge=500, le=1200)

    @field_validator("myop_enabled", "tank_capacity_l", "fuel_density_gl", mode="before")
    @classmethod
    def reject_null(cls, value):
        """An explicit null is invalid; omitted settings remain unchanged."""
        if value is None:
            raise ValueError("Il valore non può essere nullo")
        return value

    @field_validator("tank_capacity_l", "fuel_density_gl", mode="before")
    @classmethod
    def reject_boolean_number(cls, value):
        """Prevent booleans from becoming numeric capacities or densities."""
        if isinstance(value, bool):
            raise ValueError("È richiesto un numero")
        return value


class RefuelCreate(BaseModel):
    """Validate a complete refuel before writing any ledger row."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    liters: float = Field(gt=0, le=200)
    odometerKm: float | None = Field(default=None, ge=0, le=2_000_000)
    pricePerL: float | None = Field(default=None, ge=0, le=100)
    ts: str | None = None
    fuelType: str | None = Field(default=None, max_length=40)
    fullTank: StrictBool = True
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("liters", "odometerKm", "pricePerL", mode="before")
    @classmethod
    def reject_boolean_number(cls, value):
        """Reject boolean amounts while accepting normal numeric form values."""
        if isinstance(value, bool):
            raise ValueError("È richiesto un numero")
        return value

    @field_validator("ts")
    @classmethod
    def validate_timestamp(cls, value):
        """Keep supplied local time semantics, accepting only ISO timestamps."""
        if value is None:
            return None
        if not value or "T" not in value:
            raise ValueError("Data e ora ISO richieste")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
