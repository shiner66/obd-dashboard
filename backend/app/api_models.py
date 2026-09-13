"""Validated API payloads shared by ingestion and platform settings."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

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
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?", value):
            raise ValueError("Data e ora locali richieste, senza fuso orario")
        return datetime.fromisoformat(value).isoformat(timespec="seconds")


class MaintenanceCreate(BaseModel):
    """Validate one dated manual intervention before atomically changing its row."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    ts: str
    type: Literal["oil_change", "service", "battery", "tyres", "other"]
    odometerKm: float | None = Field(default=None, ge=0, le=2_000_000)
    note: str = Field(default="", max_length=2000)
    archived: StrictBool = False

    @field_validator("ts")
    @classmethod
    def validate_timestamp(cls, value):
        """Use the same explicit local timestamps as the refuel ledger."""
        return RefuelCreate.validate_timestamp(value)

    @field_validator("odometerKm", mode="before")
    @classmethod
    def reject_boolean_number(cls, value):
        """Keep a checkbox from silently becoming a mileage reading."""
        return RefuelCreate.reject_boolean_number(value)
