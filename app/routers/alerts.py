
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..utils import is_open, iso, now_utc, severity as norm_severity

router = APIRouter(prefix="/api", tags=["alerts"])

MAX_ITEMS = 100


@router.get("/alerts")
def list_alerts(severity: str = "all", range: str = "all", scope: str = "all", db: Session = Depends(get_db)):
    """Fleet-wide alerts with the prediction behind each one. Insert point 4."""
    query = (
        db.query(models.Alert, models.HardDrive.serial_number, models.Prediction.risk_level,
                 models.Prediction.confidence_level)
        .join(models.HardDrive, models.HardDrive.drive_id == models.Alert.drive_id)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Alert.data_center_id)
        .outerjoin(models.Prediction, models.Prediction.prediction_id == models.Alert.prediction_id)
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)

    rows = query.order_by(models.Alert.alert_date.desc(), models.Alert.alert_id.desc()).all()
    open_total = sum(1 for a, *_ in rows if is_open(a.outcome))

    today = now_utc().date()
    items = []
    for a, serial, risk, conf in rows:
        sev = norm_severity(a.severity)
        if severity != "all" and sev != severity:
            continue
        if range == "today" and a.alert_date != today:
            continue
        items.append({
            "id": a.alert_id, "ts": iso(a.alert_date), "serial": serial, "type": a.alert_type,
            "severity": sev, "risk": risk or "n/a",
            "confidence": None if conf is None else round(conf * 100), "outcome": a.outcome,
        })
    return {"openTotal": open_total, "items": items[:MAX_ITEMS]}
