from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..utils import iso, month_bounds, now_utc, severity as norm_severity
from .summary import drive_counts, open_alert_severity, replacement_count, scoped_dc_ids

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(scope: str = "all", db: Session = Depends(get_db)):
    """Everything on the dashboard in one call. Insert point 1."""
    now = now_utc()
    counts = drive_counts(db, scope)
    sev = open_alert_severity(db, scope)
    start, end = month_bounds(now.strftime("%Y-%m"))

    query = (
        db.query(models.Alert, models.HardDrive.serial_number)
        .join(models.HardDrive, models.HardDrive.drive_id == models.Alert.drive_id)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Alert.data_center_id)
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    recent = query.order_by(models.Alert.alert_date.desc(), models.Alert.alert_id.desc()).limit(5).all()

    return {
        "snapshot": f"{now.day:02d} {now.strftime('%b %Y, %H:%M')} UTC",
        "dataCentres": len(scoped_dc_ids(db, scope)),
        "metrics": {
            "totalDrives": sum(counts.values()),
            "atRisk": counts["warning"] + counts["critical"],
            "warning": counts["warning"],
            "critical": counts["critical"],
            "openAlerts": sum(sev.values()),
            "severity": sev,
            "replacements": replacement_count(db, scope, start, end),
        },
        "recent": [
            {"id": a.alert_id, "ts": iso(a.alert_date), "serial": serial, "type": a.alert_type,
             "severity": norm_severity(a.severity), "outcome": a.outcome}
            for a, serial in recent
        ],
    }
