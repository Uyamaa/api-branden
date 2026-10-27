from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import acting_user, is_admin, require_permission
from ..schemas import VOID_REASONS, MaintenanceEditIn, MaintenanceIn, MaintenanceVoidIn

router = APIRouter(prefix="/api", tags=["maintenance"])


def _record(m: models.Maintenance, serial: str, by: str | None, voided_by: str | None = None):
    out = {"id": m.maintenance_id, "date": m.maintenance_date.isoformat(), "serial": serial,
           "type": m.maintenance_type, "by": by or "Unknown", "byId": m.performed_by, "voided": m.voided_at is not None}
    if m.voided_at is not None:
        out["voidedAt"] = m.voided_at.isoformat()
        out["voidedBy"] = voided_by or "Unknown"
        out["voidReason"] = m.void_reason or ""
    return out


def _load(db: Session, maintenance_id: int):
    row = (
        db.query(models.Maintenance, models.HardDrive.serial_number)
        .join(models.HardDrive, models.HardDrive.drive_id == models.Maintenance.drive_id)
        .filter(models.Maintenance.maintenance_id == maintenance_id).first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Maintenance record not found")
    return row


def _name(db: Session, user_id: int | None) -> str | None:
    user = db.get(models.User, user_id) if user_id is not None else None
    return user.full_name if user else None


def _may_change(user: models.User, record: models.Maintenance):
    """The person who logged a record, or an administrator, may edit or void it."""
    if not (is_admin(user) or record.performed_by == user.user_id):
        raise HTTPException(status_code=403, detail="Only the person who logged this record, or an administrator, can change it.")


@router.get("/maintenance")
def list_maintenance(scope: str = "all", type: str = "all", voided: bool = False, db: Session = Depends(get_db)):
    """Maintenance history, newest first. Voided records are left out unless voided=true. Insert point 5."""
    query = (
        db.query(models.Maintenance, models.HardDrive.serial_number, models.User.full_name)
        .join(models.HardDrive, models.HardDrive.drive_id == models.Maintenance.drive_id)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Maintenance.data_center_id)
        .outerjoin(models.User, models.User.user_id == models.Maintenance.performed_by)
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    if type != "all":
        query = query.filter(models.Maintenance.maintenance_type == type)
    rows = query.order_by(models.Maintenance.maintenance_date.desc(), models.Maintenance.maintenance_id.desc()).all()
    voided_count = sum(1 for m, _s, _b in rows if m.voided_at is not None)
    if not voided:
        rows = [r for r in rows if r[0].voided_at is None]
    names = {u.user_id: u.full_name for u in db.query(models.User).all()}
    return {"items": [_record(m, serial, by, names.get(m.voided_by)) for m, serial, by in rows], "voidedCount": voided_count}


@router.post("/maintenance", status_code=201, dependencies=[Depends(require_permission("write"))])
def create_maintenance(body: MaintenanceIn, request: Request, db: Session = Depends(get_db)):
    """Log maintenance work on a drive. Insert point 6."""
    drive = db.query(models.HardDrive).filter(models.HardDrive.serial_number == body.serial).first()
    if drive is None:
        raise HTTPException(status_code=404, detail=f"Drive {body.serial} not found")

    dc_id = drive.data_center_id
    if body.dc:
        dc = db.query(models.DataCenter).filter(models.DataCenter.name == body.dc).first()
        if dc is None:
            raise HTTPException(status_code=404, detail=f"Data centre {body.dc} not found")
        dc_id = dc.data_center_id

    user = acting_user(db, request)
    record = models.Maintenance(
        drive_id=drive.drive_id, data_center_id=dc_id, maintenance_date=body.date,
        maintenance_type=body.type.strip(), performed_by=user.user_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _record(record, drive.serial_number, user.full_name)


@router.patch("/maintenance/{maintenance_id}", dependencies=[Depends(require_permission("write"))])
def edit_maintenance(maintenance_id: int, body: MaintenanceEditIn, request: Request, db: Session = Depends(get_db)):
    """Fix the type or date of a record. A voided record cannot be edited (restore it first)."""
    record, serial = _load(db, maintenance_id)
    _may_change(acting_user(db, request), record)
    if record.voided_at is not None:
        raise HTTPException(status_code=409, detail="This record is voided. Restore it before editing.")
    if body.type is not None:
        record.maintenance_type = body.type.strip()
    if body.date is not None:
        record.maintenance_date = body.date
    db.commit()
    db.refresh(record)
    return _record(record, serial, _name(db, record.performed_by))


@router.post("/maintenance/{maintenance_id}/void", dependencies=[Depends(require_permission("write"))])
def void_maintenance(maintenance_id: int, body: MaintenanceVoidIn, request: Request, db: Session = Depends(get_db)):
    """Mark a record as added by mistake. It stays in the history with who voided it and why."""
    if body.reason not in VOID_REASONS:
        raise HTTPException(status_code=422, detail="Choose one of the listed reasons.")
    note = (body.note or "").strip()
    if body.reason == "Other" and not note:
        raise HTTPException(status_code=422, detail="Add a short note when the reason is Other.")
    record, serial = _load(db, maintenance_id)
    user = acting_user(db, request)
    _may_change(user, record)
    if record.voided_at is not None:
        raise HTTPException(status_code=409, detail="This record is already voided.")
    record.voided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    record.voided_by = user.user_id
    record.void_reason = (f"{body.reason}: {note}" if note else body.reason)[:200]
    db.commit()
    db.refresh(record)
    return _record(record, serial, _name(db, record.performed_by), user.full_name)


@router.post("/maintenance/{maintenance_id}/restore", dependencies=[Depends(require_permission("write"))])
def restore_maintenance(maintenance_id: int, request: Request, db: Session = Depends(get_db)):
    """Undo a void. The person who voided it can undo it straight away; later, only an administrator can."""
    record, serial = _load(db, maintenance_id)
    user = acting_user(db, request)
    if record.voided_at is None:
        raise HTTPException(status_code=409, detail="This record is not voided.")
    own_undo = record.voided_by == user.user_id
    if not (is_admin(user) or own_undo):
        raise HTTPException(status_code=403, detail="Only an administrator can restore a voided record.")
    record.voided_at = None
    record.voided_by = None
    record.void_reason = None
    db.commit()
    db.refresh(record)
    return _record(record, serial, _name(db, record.performed_by))
