from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from .. import assistant, models
from ..schemas import AssistantIn
from ..database import get_db
from ..deps import require_permission
from ..utils import drive_status, iso, now_utc, status_sql

router = APIRouter(prefix="/api", tags=["drives"])


def _level(n):
    return "warning" if n and n > 0 else "ok"


def _counter(label, n):
    n = n or 0
    return {"label": label, "value": str(n), "level": _level(n), "note": "Warning · Non-zero" if n > 0 else "Within range"}


def smart_readings(r: models.SmartReading | None):
    """Turn the latest smart_reading row into the nine tiles the drive page shows."""
    if r is None:
        return []
    temp = r.temperature or 0
    return [
        {"label": "Temperature", "value": f"{temp:g}°C", "level": "warning" if temp >= 45 else "ok",
         "note": "High temperature" if temp >= 45 else "Within range"},
        {"label": "Power-on hours", "value": f"{(r.power_on_hours or 0):,} h", "level": "ok", "note": "Lifetime counter"},
        _counter("Reallocated sectors", r.reallocated_sectors),
        _counter("Spin retry count", r.spin_retry_count),
        _counter("End-to-end error", r.end_to_end_error),
        _counter("Reported uncorrectable", r.reported_uncorrectable),
        _counter("Command timeout", r.command_timeout),
        _counter("Current pending sector", r.current_pending_sector),
        _counter("Offline uncorrectable", r.offline_uncorrectable),
    ]


@router.get("/drives")
def list_drives(
    q: str = "",
    status: str = "all",
    scope: str = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Drive inventory with search, filters and paging. Insert point 2 (and 2b)."""
    st = status_sql()
    query = (
        db.query(models.HardDrive, models.DataCenter.name)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.HardDrive.data_center_id)
    )
    if q.strip():
        query = query.filter(models.HardDrive.serial_number.ilike(f"%{q.strip()}%"))
    if status in ("healthy", "warning", "critical"):
        query = query.filter(st == status)
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)

    total = query.count()
    rank = case((st == "critical", 0), (st == "warning", 1), else_=2)
    rows = (
        query.order_by(rank, models.HardDrive.serial_number)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        {"id": d.drive_id, "serial": d.serial_number, "model": d.model, "capacityTb": d.capacity,
         "status": drive_status(d.status), "dc": dc}
        for d, dc in rows
    ]
    return {"total": total, "items": items}


@router.get("/drives/{serial}")
def drive_detail(serial: str, db: Session = Depends(get_db)):
    """Everything the drive page shows. Insert point 3. 404 when the serial is unknown."""
    row = (
        db.query(models.HardDrive, models.DataCenter.name)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.HardDrive.data_center_id)
        .filter(models.HardDrive.serial_number == serial)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    drive, dc = row

    pred = (
        db.query(models.Prediction)
        .filter(models.Prediction.drive_id == drive.drive_id)
        .order_by(models.Prediction.prediction_id.desc())
        .first()
    )
    reading = (
        db.query(models.SmartReading)
        .filter(models.SmartReading.drive_id == drive.drive_id)
        .order_by(models.SmartReading.reading_id.desc())
        .first()
    )
    alerts = (
        db.query(models.Alert, models.Prediction.confidence_level)
        .outerjoin(models.Prediction, models.Prediction.prediction_id == models.Alert.prediction_id)
        .filter(models.Alert.drive_id == drive.drive_id)
        .order_by(models.Alert.alert_date.desc(), models.Alert.alert_id.desc())
        .all()
    )
    maint = (
        db.query(models.Maintenance, models.User.full_name)
        .outerjoin(models.User, models.User.user_id == models.Maintenance.performed_by)
        .filter(models.Maintenance.drive_id == drive.drive_id)
        .order_by(models.Maintenance.maintenance_date.desc(), models.Maintenance.maintenance_id.desc())
        .all()
    )

    if pred is None:
        prediction = {"date": None, "risk": "No prediction yet", "confidence": None, "model": "KNN", "horizon": None}
    else:
        risk = pred.risk_level
        prediction = {
            "date": pred.predicted_failure_date.isoformat(),
            "risk": risk if risk.lower().endswith("risk") else f"{risk} risk",
            "confidence": round((pred.confidence_level or 0) * 100),
            "model": "KNN",
            "horizon": None,
        }

    return {
        "serial": drive.serial_number,
        "status": drive_status(drive.status),
        # The database has no rack column, so rack is not available.
        "info": {"model": drive.model, "capacityTb": drive.capacity, "dc": dc,
                 "rack": "n/a", "asset": f"DRV-{drive.drive_id}"},
        "prediction": prediction,
        # The written alert (None for healthy drives). Not saved here; POST /assistant writes and saves it.
        "assistant": assistant.current(db, drive),
        # smart_reading has no timestamp column; the time shown is when it was read from the database.
        "smart": {"capturedAt": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), "readings": smart_readings(reading)},
        "alerts": [
            {"id": a.alert_id, "ts": iso(a.alert_date), "type": a.alert_type, "severity": a.severity.lower(),
             "confidence": None if conf is None else round(conf * 100), "outcome": a.outcome}
            for a, conf in alerts
        ],
        "maintenance": [
            {"id": m.maintenance_id, "date": m.maintenance_date.isoformat(), "type": m.maintenance_type, "by": by or "Unknown"}
            for m, by in maint
        ],
    }


@router.post("/drives/{serial}/assistant", dependencies=[Depends(require_permission("write"))])
def write_alert(serial: str, body: AssistantIn | None = None, db: Session = Depends(get_db)):
    """Write (or rewrite) the plain-language alert for a drive and save it.

    Body is optional. Send riskLevel and failureProbability (0 to 1) to use a live prediction;
    leave it out to use the latest prediction stored in the database.
    """
    drive = db.query(models.HardDrive).filter(models.HardDrive.serial_number == serial).first()
    if drive is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    body = body or AssistantIn()
    result = assistant.generate(db, drive, body.riskLevel, body.failureProbability)
    if result is None:
        raise HTTPException(status_code=422, detail="This drive is not at Medium or High risk, so no alert is needed.")
    return result
