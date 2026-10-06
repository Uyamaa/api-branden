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

class DataCenterIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    location: str = Field(min_length=1, max_length=100)


class DriveIn(BaseModel):
    serial: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=50)
    capacityTb: int = Field(gt=0)
    dc: str = Field(min_length=1)
    status: str = "Healthy"


class ReadingIn(BaseModel):
    temperature: float
    powerOnHours: int = Field(ge=0)
    reallocatedSectors: int = Field(0, ge=0)
    spinRetryCount: int = Field(0, ge=0)
    endToEndError: int = Field(0, ge=0)
    reportedUncorrectable: int = Field(0, ge=0)
    commandTimeout: int = Field(0, ge=0)
    currentPendingSector: int = Field(0, ge=0)
    offlineUncorrectable: int = Field(0, ge=0)
