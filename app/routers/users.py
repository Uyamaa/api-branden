from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, aliased

from .. import models
from ..database import get_db
from ..utils import is_open, iso, severity as norm_severity

router = APIRouter(prefix="/api", tags=["users"])

MAX_WORK = 50
MAX_ALERTS = 20

NewDrive = aliased(models.HardDrive)


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """Read-only directory. Insert point 9."""
    rows = db.query(models.User).order_by(models.User.user_id).all()
    return {"items": [{"id": u.user_id, "name": u.full_name, "email": u.email, "role": u.role} for u in rows]}


@router.get("/users/{user_id}/activity")
def user_activity(user_id: int, db: Session = Depends(get_db)):
    """What one person has done: maintenance, replacements, and the alerts on the drives they worked on."""
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    maint = (
        db.query(models.Maintenance, models.HardDrive.serial_number, models.DataCenter.name)
        .join(models.HardDrive, models.HardDrive.drive_id == models.Maintenance.drive_id)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Maintenance.data_center_id)
        .filter(models.Maintenance.performed_by == user_id)
        .all()
    )
    repl = (
        db.query(models.Replacement, models.HardDrive.serial_number, NewDrive.serial_number, models.DataCenter.name)
        .join(models.HardDrive, models.HardDrive.drive_id == models.Replacement.drive_id)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Replacement.data_center_id)
        .outerjoin(NewDrive, NewDrive.drive_id == models.Replacement.new_drive_id)
        .filter(models.Replacement.replaced_by == user_id)
        .all()
    )

    work = [
        {"id": f"m{m.maintenance_id}", "kind": "Maintenance", "date": m.maintenance_date.isoformat(),
         "serial": serial, "detail": m.maintenance_type, "dc": dc, "_k": (m.maintenance_date, m.maintenance_id)}
        for m, serial, dc in maint
    ] + [
        {"id": f"r{r.replacement_id}", "kind": "Replacement", "date": r.replacement_date.isoformat(),
         "serial": old, "detail": f"Replaced by {new or f'DRV-{r.new_drive_id}'} · {r.reason}", "dc": dc,
         "_k": (r.replacement_date, r.replacement_id)}
        for r, old, new, dc in repl
    ]
    work.sort(key=lambda w: w["_k"], reverse=True)
    for w in work:
        del w["_k"]

    drive_ids = {m.drive_id for m, *_ in maint} | {r.drive_id for r, *_ in repl}
    alerts = []
    if drive_ids:
        alerts = (
            db.query(models.Alert, models.HardDrive.serial_number)
            .join(models.HardDrive, models.HardDrive.drive_id == models.Alert.drive_id)
            .filter(models.Alert.drive_id.in_(drive_ids))
            .order_by(models.Alert.alert_date.desc(), models.Alert.alert_id.desc())
            .all()
        )

    return {
        "user": {"id": user.user_id, "name": user.full_name, "email": user.email, "role": user.role},
        "totals": {
            "maintenance": len(maint), "replacements": len(repl), "drives": len(drive_ids),
            "openAlerts": sum(1 for a, _ in alerts if is_open(a.outcome)),
        },
        "work": work[:MAX_WORK],
        "alerts": [
            {"id": a.alert_id, "ts": iso(a.alert_date), "serial": serial, "type": a.alert_type,
             "severity": norm_severity(a.severity), "outcome": a.outcome}
            for a, serial in alerts[:MAX_ALERTS]
        ],
    }
