from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import require_permission
from ..schemas import DataCenterIn, DriveIn, DriveUpdate, ReadingIn
from ..utils import drive_status

router = APIRouter(prefix="/api", tags=["data-entry"])


@router.get("/data-centres")
def list_data_centres(db: Session = Depends(get_db)):
    """Real data centres, so forms do not depend on a hard-coded list."""
    rows = db.query(models.DataCenter).order_by(models.DataCenter.name).all()
    return {"items": [{"id": d.data_center_id, "name": d.name, "location": d.location} for d in rows]}


@router.post("/data-centres", status_code=201, dependencies=[Depends(require_permission("manage"))])
def create_data_centre(body: DataCenterIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.query(models.DataCenter).filter(models.DataCenter.name == name).first():
        raise HTTPException(status_code=409, detail=f"Data centre {name} already exists")
    dc = models.DataCenter(name=name, location=body.location.strip())
    db.add(dc)
    db.commit()
    db.refresh(dc)
    return {"id": dc.data_center_id, "name": dc.name, "location": dc.location}


@router.post("/drives", status_code=201, dependencies=[Depends(require_permission("write"))])
def create_drive(body: DriveIn, db: Session = Depends(get_db)):
    """Register a new drive."""
    serial = body.serial.strip()
    if db.query(models.HardDrive).filter(models.HardDrive.serial_number == serial).first():
        raise HTTPException(status_code=409, detail=f"Drive {serial} already exists")
    dc = db.query(models.DataCenter).filter(models.DataCenter.name == body.dc).first()
    if dc is None:
        raise HTTPException(status_code=404, detail=f"Data centre {body.dc} not found")
    drive = models.HardDrive(
        data_center_id=dc.data_center_id, serial_number=serial, model=body.model.strip(),
        capacity=body.capacityTb, status=body.status.strip() or "Healthy",
    )
    db.add(drive)
    db.commit()
    db.refresh(drive)
    return {"serial": drive.serial_number, "model": drive.model, "capacityTb": drive.capacity,
            "status": drive_status(drive.status), "dc": dc.name}


@router.post("/drives/{serial}/readings", status_code=201, dependencies=[Depends(require_permission("write"))])
def create_reading(serial: str, body: ReadingIn, db: Session = Depends(get_db)):
    """Record a SMART reading for a drive. The drive page always shows the newest one."""
    drive = db.query(models.HardDrive).filter(models.HardDrive.serial_number == serial).first()
    if drive is None:
        raise HTTPException(status_code=404, detail=f"Drive {serial} not found")
    reading = models.SmartReading(
        drive_id=drive.drive_id, temperature=body.temperature, power_on_hours=body.powerOnHours,
        reallocated_sectors=body.reallocatedSectors, spin_retry_count=body.spinRetryCount,
        end_to_end_error=body.endToEndError, reported_uncorrectable=body.reportedUncorrectable,
        command_timeout=body.commandTimeout, current_pending_sector=body.currentPendingSector,
        offline_uncorrectable=body.offlineUncorrectable,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return {"id": reading.reading_id, "serial": serial}


def _find_drive(db: Session, serial: str) -> models.HardDrive:
    drive = db.query(models.HardDrive).filter(models.HardDrive.serial_number == serial).first()
    if drive is None:
        raise HTTPException(status_code=404, detail=f"Drive {serial} not found")
    return drive


@router.put("/drives/{serial}", dependencies=[Depends(require_permission("write"))])
def update_drive(serial: str, body: DriveUpdate, db: Session = Depends(get_db)):
    """Edit a drive's serial, model, capacity, data centre and status."""
    drive = _find_drive(db, serial)
    new_serial = body.serial.strip()
    if new_serial != drive.serial_number and db.query(models.HardDrive).filter(
            models.HardDrive.serial_number == new_serial).first():
        raise HTTPException(status_code=409, detail=f"Drive {new_serial} already exists")
    dc = db.query(models.DataCenter).filter(models.DataCenter.name == body.dc).first()
    if dc is None:
        raise HTTPException(status_code=404, detail=f"Data centre {body.dc} not found")
    drive.serial_number = new_serial
    drive.model = body.model.strip()
    drive.capacity = body.capacityTb
    drive.data_center_id = dc.data_center_id
    drive.status = body.status.strip()
    db.commit()
    db.refresh(drive)
    return {"serial": drive.serial_number, "model": drive.model, "capacityTb": drive.capacity,
            "status": drive_status(drive.status), "dc": dc.name}


@router.delete("/drives/{serial}", dependencies=[Depends(require_permission("manage"))])
def delete_drive(serial: str, db: Session = Depends(get_db)):
    """Delete a drive together with its own readings, predictions, alerts, maintenance and replacements."""
    drive = _find_drive(db, serial)
    did = drive.drive_id
    pred_ids = [p for (p,) in db.query(models.Prediction.prediction_id).filter(models.Prediction.drive_id == did)]

    db.query(models.AlertMessage).filter(models.AlertMessage.drive_id == did).delete(synchronize_session=False)
    db.query(models.Alert).filter(models.Alert.drive_id == did).delete(synchronize_session=False)
    db.query(models.SmartReading).filter(models.SmartReading.drive_id == did).delete(synchronize_session=False)
    db.query(models.Maintenance).filter(models.Maintenance.drive_id == did).delete(synchronize_session=False)
    db.query(models.Replacement).filter(
        (models.Replacement.drive_id == did) | (models.Replacement.new_drive_id == did)
    ).delete(synchronize_session=False)
    # A prediction is kept if another drive's alert still points at it.
    for pid in pred_ids:
        if not db.query(models.Alert).filter(models.Alert.prediction_id == pid).first():
            db.query(models.Prediction).filter(models.Prediction.prediction_id == pid).delete(synchronize_session=False)
    db.delete(drive)
    db.commit()
    return {"deleted": serial}
