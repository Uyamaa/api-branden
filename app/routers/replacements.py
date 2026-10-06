from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, aliased

from .. import models
from ..database import get_db
from ..deps import acting_user
from ..schemas import ReplacementIn
from ..utils import month_bounds

router = APIRouter(prefix="/api", tags=["replacements"])

NewDrive = aliased(models.HardDrive)


def _month(month: str):
    try:
        return month_bounds(month)
    except ValueError:
        raise HTTPException(status_code=422, detail="month must look like 2026-10")


@router.get("/replacements")
def list_replacements(scope: str = "all", month: str = Query("2026-10"), db: Session = Depends(get_db)):
    """The replacement register for one month. Insert point 7."""
    start, end = _month(month)
    query = (
        db.query(models.Replacement, models.HardDrive.serial_number, NewDrive.serial_number, models.User.full_Name,
                 models.DataCenter.name)
        .join(models.HardDrive, models.HardDrive.drive_id == models.Replacement.drive_id)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Replacement.data_center_id)
        .outerjoin(NewDrive, NewDrive.drive_id == models.Replacement.new_drive_id)
        .outerjoin(models.User, models.User.user_id == models.Replacement.replaced_by)
        .filter(models.Replacement.replacement_date >= start, models.Replacement.replacement_date < end)
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    rows = query.order_by(models.Replacement.replacement_date.desc(), models.Replacement.replacement_id.desc()).all()
    items = [
        {"id": r.replacement_id, "date": r.replacement_date.isoformat(), "oldSerial": old,
         "newSerial": new or f"DRV-{r.new_drive_id}", "reason": r.reason, "by": by or "Unknown", "dc": dc}
        for r, old, new, by, dc in rows
    ]
    return {"monthTotal": len(items), "items": items}


@router.post("/replacements", status_code=201)
def create_replacement(body: ReplacementIn, request: Request, db: Session = Depends(get_db)):
    """Record a drive swap. Insert point 8."""
    if body.oldSerial == body.newSerial:
        raise HTTPException(status_code=422, detail="The new drive must be different from the old drive")
    old = db.query(models.HardDrive).filter(models.HardDrive.serial_number == body.oldSerial).first()
    if old is None:
        raise HTTPException(status_code=404, detail=f"Drive {body.oldSerial} not found")
    new = db.query(models.HardDrive).filter(models.HardDrive.serial_number == body.newSerial).first()
    if new is None:
        raise HTTPException(status_code=404, detail=f"Drive {body.newSerial} not found")

    user = acting_user(db, request)
    record = models.Replacement(
        drive_id=old.drive_id, data_center_id=old.data_center_id, replacement_date=body.date,
        replaced_by=user.user_id, reason=body.reason.strip(), new_drive_id=new.drive_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    dc = db.get(models.DataCenter, old.data_center_id)
    return {"id": record.replacement_id, "date": record.replacement_date.isoformat(), "oldSerial": old.serial_number,
            "newSerial": new.serial_number, "reason": record.reason, "by": user.full_Name, "dc": dc.name if dc else ""}
