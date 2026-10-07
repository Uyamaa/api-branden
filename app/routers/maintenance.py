from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import acting_user, require_permission
from ..schemas import MaintenanceIn

router = APIRouter(prefix="/api", tags=["maintenance"])


def _record(m: models.Maintenance, serial: str, by: str | None):
    return {"id": m.maintenance_id, "date": m.maintenance_date.isoformat(), "serial": serial,
            "type": m.maintenance_type, "by": by or "Unknown"}


@router.get("/maintenance")
def list_maintenance(scope: str = "all", type: str = "all", db: Session = Depends(get_db)):
    """Maintenance history, newest first. Insert point 5."""
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
    return {"items": [_record(m, serial, by) for m, serial, by in rows]}


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
