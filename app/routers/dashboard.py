from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import assistant, models
from ..database import get_db
from ..utils import iso, month_bounds, now_utc, severity as norm_severity, status_sql
from .summary import drive_counts, open_alert_severity, replacement_count, scoped_dc_ids

router = APIRouter(prefix="/api", tags=["dashboard"])

TREND_DAYS = 7
KEYS = ("total_drives", "at_risk", "open_alerts", "replacements")


def trends(db: Session, scope: str, today, values: dict) -> dict:
    """Save today's numbers, then return the last 7 days of each as a list (oldest first).

    The database keeps no history, so the lines build up from the first day this runs. Days before the
    first saved day repeat that first value; days with no visit repeat the last known value.
    """
    row = db.get(models.FleetSnapshot, (today, scope))
    if row is None:
        row = models.FleetSnapshot(snapshot_date=today, scope=scope)
        db.add(row)
    for key in KEYS:
        setattr(row, key, values[key])
    db.commit()

    start = today - timedelta(days=TREND_DAYS - 1)
    saved = {r.snapshot_date: r for r in db.query(models.FleetSnapshot).filter(
        models.FleetSnapshot.scope == scope, models.FleetSnapshot.snapshot_date >= start).all()}
    before = (db.query(models.FleetSnapshot)
              .filter(models.FleetSnapshot.scope == scope, models.FleetSnapshot.snapshot_date < start)
              .order_by(models.FleetSnapshot.snapshot_date.desc()).first())
    first = before or saved[min(saved)]
    out = {k: [] for k in KEYS}
    last = {k: getattr(first, k) for k in KEYS}
    for i in range(TREND_DAYS):
        day = start + timedelta(days=i)
        if day in saved:
            last = {k: getattr(saved[day], k) for k in KEYS}
        for k in KEYS:
            out[k].append(last[k])
    return out


def attention(db: Session, scope: str) -> list[dict]:
    """The drives that need a technician, worst first, each with its written alert."""
    st = status_sql()
    query = (
        db.query(models.HardDrive, models.DataCenter.name, st)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.HardDrive.data_center_id)
        .filter(st.in_(("critical", "warning")))
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    rows = query.all()
    rows.sort(key=lambda r: (0 if r[2] == "critical" else 1, r[0].serial_number))
    out = []
    for drive, dc, status in rows[:5]:
        a = assistant.current(db, drive)
        if a is None:
            continue
        out.append({"serial": drive.serial_number, "dc": dc, "severity": status, "message": a["message"],
                    "steps": a["steps"], "source": a["source"], "probability": a["probability"]})
    return out


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

    metrics = {
        "totalDrives": sum(counts.values()),
        "atRisk": counts["warning"] + counts["critical"],
        "warning": counts["warning"],
        "critical": counts["critical"],
        "openAlerts": sum(sev.values()),
        "severity": sev,
        "replacements": replacement_count(db, scope, start, end),
    }
    series = trends(db, scope, now.date(), {
        "total_drives": metrics["totalDrives"], "at_risk": metrics["atRisk"],
        "open_alerts": metrics["openAlerts"], "replacements": metrics["replacements"]})

    return {
        "snapshot": f"{now.day:02d} {now.strftime('%b %Y, %H:%M')} UTC",
        "dataCentres": len(scoped_dc_ids(db, scope)),
        "metrics": metrics,
        "trends": {"totalDrives": series["total_drives"], "atRisk": series["at_risk"],
                   "openAlerts": series["open_alerts"], "replacements": series["replacements"]},
        "attention": attention(db, scope),
        "recent": [
            {"id": a.alert_id, "ts": iso(a.alert_date), "serial": serial, "type": a.alert_type,
             "severity": norm_severity(a.severity), "outcome": a.outcome}
            for a, serial in recent
        ],
    }
