from datetime import date

from pydantic import BaseModel, Field


class MaintenanceIn(BaseModel):
    serial: str = Field(min_length=1)
    dc: str | None = None
    type: str = Field(min_length=1)
    date: date


class ReplacementIn(BaseModel):
    oldSerial: str = Field(min_length=1)
    newSerial: str = Field(min_length=1)
    date: date
    reason: str = Field(min_length=1)
