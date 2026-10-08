"""Numbers for the Admin page. Admin only."""
import os
import time

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from datetime import datetime, timedelta, timezone

from .. import cache, models
from ..cache import cached
from ..database import get_db
from ..deps import require_permission, role_of
from ..assistant import DEFAULT_SERVICE_URL, SERVICE_URL
from ..utils import iso, status_sql
from .summary import open_alert_severity

STALE_HOURS = 24

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_permission("manage"))])

SLOW_MS = 1500


def _services(db: Session) -> list[dict]:
    out = [{"name": "API", "state": "ok", "detail": "online"}]

    started = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        ms = round((time.perf_counter() - started) * 1000)
        out.append({"name": "Database", "state": "ok", "detail": f"online · {ms} ms"})
    except Exception:
        out.append({"name": "Database", "state": "down", "detail": "not answering"})

    url = os.getenv(SERVICE_URL, DEFAULT_SERVICE_URL).strip()
    if not url:
        out.append({"name": "Alert service", "state": "off", "detail": "not configured"})
    else:
        started = time.perf_counter()
        try:
            r = httpx.get(f"{url.rstrip('/')}/health", timeout=3)
            r.raise_for_status()
            ms = round((time.perf_counter() - started) * 1000)
            llm = r.json().get("llm_configured")
            state = "slow" if ms > SLOW_MS else "ok"
            out.append({"name": "Alert service", "state": state,
                        "detail": f"{'AI on' if llm else 'AI off, templates only'} · {ms} ms"})
        except Exception:
            out.append({"name": "Alert service", "state": "down", "detail": "not answering"})
    return out


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    """The slow part is cached for 15 s; session and cache numbers are always live."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active = (db.query(func.count(models.UserSession.token_hash))
              .filter(models.UserSession.revoked_at.is_(None), models.UserSession.expires_at > now).scalar() or 0)
    return {**_overview(db=db), "sessions": {"active": active}, "cache": cache.stats()}


@cached("admin-overview", ttl=15)
def _overview(db: Session):
    users = db.query(models.User).all()
    roles = {"admin": 0, "technician": 0, "viewer": 0}
    for u in users:
        roles[role_of(u)] += 1
    with_login = {r[0] for r in db.query(models.UserCredential.user_id).all()}
    cannot_sign_in = [u.full_name for u in users if u.user_id not in with_login]

    st = status_sql()
    per_dc = {}
    for dc_id, status, n in (db.query(models.HardDrive.data_center_id, st, func.count(models.HardDrive.drive_id))
                             .group_by(models.HardDrive.data_center_id, st).all()):
        row = per_dc.setdefault(dc_id, {"drives": 0, "atRisk": 0})
        row["drives"] += n
        if status != "healthy":
            row["atRisk"] += n
    centres = [{"id": d.data_center_id, "name": d.name, "location": d.location,
                **per_dc.get(d.data_center_id, {"drives": 0, "atRisk": 0})}
               for d in db.query(models.DataCenter).order_by(models.DataCenter.data_center_id).all()]
    total_drives = sum(c["drives"] for c in centres)
    at_risk = sum(c["atRisk"] for c in centres)
    sev = open_alert_severity(db, "all")
    open_alerts = sum(sev.values())

    no_readings = (db.query(func.count(models.HardDrive.drive_id))
                   .outerjoin(models.SmartReading, models.SmartReading.drive_id == models.HardDrive.drive_id)
                   .filter(models.SmartReading.drive_id.is_(None)).scalar() or 0)

    # Data feed: when the newest reading arrived, and which drives have gone quiet (only drives with timestamps count).
    stale_before = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=STALE_HOURS)
    newest = {drive_id: when for drive_id, when in
              db.query(models.SmartReading.drive_id, func.max(models.SmartReading.collected_at))
              .group_by(models.SmartReading.drive_id).all() if when is not None}
    stale = sum(1 for when in newest.values() if when < stale_before)
    last_reading = max(newest.values()) if newest else None

    names = {u.user_id: u.full_name for u in users}
    serial = {d.drive_id: d.serial_number for d in db.query(models.HardDrive).all()}
    events = []
    for m in db.query(models.Maintenance).order_by(models.Maintenance.maintenance_date.desc()).limit(8).all():
        events.append({"date": iso(m.maintenance_date), "kind": "Maintenance",
                       "text": f"{names.get(m.performed_by, 'Someone')} logged {m.maintenance_type.lower()} on {serial.get(m.drive_id, 'a drive')}"})
    for r in db.query(models.Replacement).order_by(models.Replacement.replacement_date.desc()).limit(8).all():
        events.append({"date": iso(r.replacement_date), "kind": "Replacement",
                       "text": f"{names.get(r.replaced_by, 'Someone')} replaced {serial.get(r.drive_id, 'a drive')}"})
    events.sort(key=lambda e: e["date"] or "", reverse=True)

    services = _services(db)
    attention = []
    if sev["critical"]:
        attention.append({"level": "bad", "text": f"{sev['critical']} critical alert{'s' if sev['critical'] != 1 else ''} still open", "to": "/alerts"})
    for s in services:
        if s["state"] in ("down", "slow"):
            attention.append({"level": "warn", "text": f"{s['name']} is {'not answering' if s['state'] == 'down' else 'slow'}", "to": None})
    if no_readings:
        attention.append({"level": "warn", "text": f"{no_readings} drive{'s' if no_readings != 1 else ''} with no SMART reading yet", "to": "/drives"})
    if stale:
        attention.append({"level": "warn", "text": f"{stale} drive{'s' if stale != 1 else ''} not reported in {STALE_HOURS} h", "to": "/drives"})
    if cannot_sign_in:
        attention.append({"level": "warn", "text": f"{len(cannot_sign_in)} user{'s' if len(cannot_sign_in) != 1 else ''} cannot sign in yet (no password set)", "to": None})

    return {
        "users": {"total": len(users), **roles},
        "drives": {"total": total_drives, "atRisk": at_risk},
        "openAlerts": open_alerts, "criticalAlerts": sev["critical"],
        "dataCentres": centres,
        "attention": attention,
        "activity": events[:10],
        "services": services,
        "feed": {"enabled": bool(os.getenv("INGEST_API_KEY")), "lastReadingAt": iso(last_reading),
                 "reportingDrives": len(newest), "staleDrives": stale, "staleHours": STALE_HOURS},
    }
