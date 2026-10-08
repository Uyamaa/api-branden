"""Machine-to-machine intake of SMART readings (a collector, later a Kafka consumer).

* Signs in with an API key (header X-Ingest-Key), not a person's login.
* A reading for a serial number we have never seen REGISTERS the drive automatically.
* Every reading is stored with the time it was taken (collected_at).
* One bad reading never blocks the others: the answer lists what was accepted and what was not.
* Sending the same reading twice (same drive, same collected_at) is harmless: it is skipped.
"""
import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

MAX_BATCH = 500
FUTURE_SLACK = timedelta(minutes=5)   # collectors' clocks are never perfect
MAX_AGE = timedelta(days=30)          # a reading older than this is almost certainly a bug


class Reading(BaseModel):
    serial: str = Field(min_length=1, max_length=50)
    # Needed only the first time a serial is seen (that is when the drive is registered).
    model: str | None = Field(None, min_length=1, max_length=50)
    capacityTb: int | None = Field(None, gt=0, le=1000)
    dc: str | None = Field(None, min_length=1, max_length=50)
    collectedAt: datetime | None = None   # defaults to "now"; a time without a zone is taken as UTC
    temperature: float = Field(ge=-40, le=150)
    powerOnHours: int = Field(ge=0)
    reallocatedSectors: int = Field(0, ge=0)
    spinRetryCount: int = Field(0, ge=0)
    endToEndError: int = Field(0, ge=0)
    reportedUncorrectable: int = Field(0, ge=0)
    commandTimeout: int = Field(0, ge=0)
    currentPendingSector: int = Field(0, ge=0)
    offlineUncorrectable: int = Field(0, ge=0)

    @field_validator("serial", "model", "dc")
    @classmethod
    def _trim(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class Batch(BaseModel):
    readings: list[dict] = Field(min_length=1, max_length=MAX_BATCH)


def _key_ok(given: str | None) -> bool:
    expected = os.getenv("INGEST_API_KEY", "")
    return bool(expected) and bool(given) and hmac.compare_digest(given.encode(), expected.encode())


def require_key(x_ingest_key: str | None = Header(None)):
    if not os.getenv("INGEST_API_KEY"):
        raise HTTPException(status_code=503, detail="Intake is switched off. Set INGEST_API_KEY on the server to enable it.")
    if not _key_ok(x_ingest_key):
        raise HTTPException(status_code=401, detail="Missing or wrong X-Ingest-Key.")


def _utc_naive(value: datetime | None, now: datetime) -> datetime:
    if value is None:
        return now
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"]) or "reading"
    return f"{where}: {first['msg']}"


@router.post("/readings", dependencies=[Depends(require_key)])
def ingest(body: Batch, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    centres = {d.name: d for d in db.query(models.DataCenter).all()}
    drives: dict[str, models.HardDrive] = {}
    registered: list[str] = []
    rejected: list[dict] = []
    duplicates = 0
    accepted = 0

    for index, raw in enumerate(body.readings):
        serial_hint = str(raw.get("serial", "")) if isinstance(raw, dict) else ""

        def reject(why: str):
            rejected.append({"index": index, "serial": serial_hint or None, "error": why})

        try:
            r = Reading.model_validate(raw)
        except ValidationError as exc:
            reject(_message(exc))
            continue

        when = _utc_naive(r.collectedAt, now)
        if when > now + FUTURE_SLACK:
            reject("collectedAt: is in the future")
            continue
        if when < now - MAX_AGE:
            reject(f"collectedAt: is older than {MAX_AGE.days} days")
            continue

        drive = drives.get(r.serial) or db.query(models.HardDrive).filter(models.HardDrive.serial_number == r.serial).first()
        if drive is None:
            missing = [name for name, value in (("model", r.model), ("capacityTb", r.capacityTb), ("dc", r.dc)) if value is None]
            if missing:
                reject(f"new drive: {', '.join(missing)} required to register it")
                continue
            centre = centres.get(r.dc)
            if centre is None:
                reject(f"dc: data centre '{r.dc}' does not exist (known: {', '.join(sorted(centres)) or 'none'})")
                continue
            drive = models.HardDrive(data_center_id=centre.data_center_id, serial_number=r.serial, model=r.model,
                                     capacity=r.capacityTb, status="Healthy")
            db.add(drive)
            db.flush()
            registered.append(r.serial)
        drives[r.serial] = drive

        if db.query(models.SmartReading.reading_id).filter(
                models.SmartReading.drive_id == drive.drive_id, models.SmartReading.collected_at == when).first():
            duplicates += 1
            continue

        db.add(models.SmartReading(
            drive_id=drive.drive_id, collected_at=when, temperature=r.temperature, power_on_hours=r.powerOnHours,
            reallocated_sectors=r.reallocatedSectors, spin_retry_count=r.spinRetryCount, end_to_end_error=r.endToEndError,
            reported_uncorrectable=r.reportedUncorrectable, command_timeout=r.commandTimeout,
            current_pending_sector=r.currentPendingSector, offline_uncorrectable=r.offlineUncorrectable))
        db.flush()
        accepted += 1

    db.commit()
    return {"received": len(body.readings), "accepted": accepted, "duplicates": duplicates,
            "registered": registered, "rejected": rejected}
