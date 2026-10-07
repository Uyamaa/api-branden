from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..cache import cached
from ..database import get_db
from ..utils import MONTHS, month_bounds, now_utc
from .summary import drive_counts, open_alert_severity, replacement_count, scoped_dc_ids

router = APIRouter(prefix="/api", tags=["reports"])


@router.get("/reports/summary")
@cached("report", ttl=60)
def report_summary(scope: str = "all", month: str = "2026-10", db: Session = Depends(get_db)):
    """Fleet aggregates for one month. Insert point 10."""
    try:
        start, end = month_bounds(month)
    except ValueError:
        raise HTTPException(status_code=422, detail="month must look like 2026-10")

    now = now_utc()
    if (start.year, start.month) == (now.year, now.month):
        label = f"Month to date · 01–{now.day:02d} {MONTHS[now.month - 1]} · UTC"
    else:
        last = (end.replace(day=1) - start.resolution).day
        label = f"Full month · 01–{last:02d} {MONTHS[start.month - 1]} · UTC"

    counts = drive_counts(db, scope)
    sev = open_alert_severity(db, scope)
    dcs = db.query(models.DataCenter).order_by(models.DataCenter.data_center_id)
    if scope != "all":
        dcs = dcs.filter(models.DataCenter.name == scope)

    return {
        "rangeLabel": label,
        "dataCentres": len(scoped_dc_ids(db, scope)),
        "metrics": {
            "totalDrives": sum(counts.values()),
            "healthy": counts["healthy"],
            "openAlerts": sum(sev.values()),
            "severity": sev,
            "replacements": replacement_count(db, scope, start, end),
        },
        "byStatus": counts,
        "replacementsByDc": [{"dc": dc.name, "count": replacement_count(db, dc.name, start, end)} for dc in dcs.all()],
    }
