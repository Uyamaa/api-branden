from datetime import date

from pydantic import BaseModel, Field

_Date = date 


class MaintenanceIn(BaseModel):
    serial: str = Field(min_length=1)
    dc: str | None = None
    type: str = Field(min_length=1)
    date: date


VOID_REASONS = ["Entered by mistake", "Wrong drive", "Wrong date or type", "Duplicate of another record", "Other"]


class MaintenanceVoidIn(BaseModel):
    reason: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=150)


class MaintenanceEditIn(BaseModel):
    type: str | None = Field(default=None, min_length=1)
    date: _Date | None = None


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


class DriveUpdate(BaseModel):
    serial: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=50)
    capacityTb: int = Field(gt=0)
    dc: str = Field(min_length=1)
    status: str = Field(min_length=1)


class AssistantIn(BaseModel):
    riskLevel: str | None = None
    failureProbability: float | None = Field(default=None, ge=0, le=1)
