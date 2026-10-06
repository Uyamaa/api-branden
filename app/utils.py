"""Small helpers that translate database values into what the frontend expects."""
from datetime import date, datetime, timezone

from sqlalchemy import case, func

from . import models

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def drive_status(raw: str | None) -> str:
    """hardDrive.status is free text. Map it to healthy | warning | critical."""
    v = (raw or "").lower()
    if "crit" in v or "fail" in v:
        return "critical"
    if "warn" in v or "risk" in v or "degrad" in v:
        return "warning"
    return "healthy"


def status_sql():
    """The same mapping as drive_status(), as a SQL expression (so we can filter and group)."""
    s = func.lower(models.HardDrive.status)
    return case(
        (s.like("%crit%") | s.like("%fail%"), "critical"),
        (s.like("%warn%") | s.like("%risk%") | s.like("%degrad%"), "warning"),
        else_="healthy",
    )


def severity(raw: str | None) -> str:
    """alert.severity -> critical | warning | info."""
    v = (raw or "").lower()
    if "crit" in v or "high" in v:
        return "critical"
    if "warn" in v or "med" in v:
        return "warning"
    return "info"


def is_open(outcome: str | None) -> bool:
    """An alert counts as open until its outcome says it is finished."""
    return (outcome or "").strip().lower() not in {"resolved", "closed", "dismissed"}


def iso(d: date | datetime | None) -> str | None:
    """The database stores alert dates without a time, so we report midnight UTC."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d.tzinfo else d.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{d.isoformat()}T00:00:00Z"


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-10' -> (2026-10-01, 2026-11-01). Raises ValueError for bad input."""
    year, mon = (int(x) for x in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    return start, end


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
