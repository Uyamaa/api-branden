import json
import os
from datetime import date, datetime, timezone

import httpx
from sqlalchemy.orm import Session

from . import models

# Where the alert service runs. Set ALERT_SERVICE_URL to an empty value to switch it off (the tests do).
SERVICE_URL = "ALERT_SERVICE_URL"
DEFAULT_SERVICE_URL = "http://10.0.0.158:8003"
SERVICE_TIMEOUT = 12

STEPS = {
    "High": ["Back up this drive's data", "Schedule replacement as soon as possible"],
    "Medium": ["Schedule an inspection", "Keep monitoring this drive"],
}


def normalise_risk(raw: str | None) -> str | None:
    """'High', 'high risk', 'HIGH' -> 'High'. Anything else (including Low) -> None, meaning no alert needed."""
    key = (raw or "").strip().lower().removesuffix("risk").strip()
    return {"high": "High", "medium": "Medium"}.get(key)


def _rules(r: models.SmartReading):
    """(label, value, is_abnormal, phrase for the writer). Same thresholds as the SMART tiles on the drive page."""
    temp = r.temperature or 0
    return [
        ("Temperature", f"{temp:g}°C", temp >= 45, "high temperature"),
        ("Reallocated sectors", r.reallocated_sectors, bool(r.reallocated_sectors), "high reallocated sectors"),
        ("Pending sectors", r.current_pending_sector, bool(r.current_pending_sector), "pending sectors detected"),
        ("Uncorrectable", r.reported_uncorrectable, bool(r.reported_uncorrectable), "reported uncorrectable errors"),
        ("Offline uncorrectable", r.offline_uncorrectable, bool(r.offline_uncorrectable), "offline uncorrectable sectors"),
        ("Spin retries", r.spin_retry_count, bool(r.spin_retry_count), "spin retries"),
        ("End-to-end errors", r.end_to_end_error, bool(r.end_to_end_error), "end-to-end errors"),
        ("Command timeouts", r.command_timeout, bool(r.command_timeout), "command timeouts"),
    ]


def evidence(reading: models.SmartReading | None) -> list[dict]:
    """The abnormal readings, shown as chips on the drive page."""
    if reading is None:
        return []
    return [{"label": label, "value": str(value), "level": "danger" if label == "Reallocated sectors" else "warning"}
            for label, value, bad, _ in _rules(reading) if bad]


def anomalies(reading: models.SmartReading | None) -> list[str]:
    if reading is None:
        return []
    return [phrase for _, _, bad, phrase in _rules(reading) if bad]


def _phrase(items: list[str]) -> str:
    if not items:
        return "abnormal SMART readings"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def template_message(drive_id: int, probability: float, issues: list[str]) -> str:
    """Same wording as the alert service's own fallback."""
    return f"Drive {drive_id} has a {probability:.0%} failure probability, driven by {_phrase(issues)}."


def call_service(drive_id: int, probability: float, risk: str, issues: list[str]) -> dict | None:
    """Ask the alert service. None means 'use the template'."""
    url = os.getenv(SERVICE_URL, DEFAULT_SERVICE_URL).strip()
    if not url:
        return None
    try:
        r = httpx.post(
            f"{url.rstrip('/')}/generate-alert",
            json={"drive_id": drive_id, "failure_probability": probability, "risk_level": risk, "anomalies": issues},
            timeout=SERVICE_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("alert_required") and body.get("message"):
            return {"message": body["message"], "steps": body.get("steps") or STEPS[risk], "source": body.get("source", "llm")}
    except Exception as exc:  # the service is optional: never let it break a page
        print(f"alert service unavailable, using template: {exc}")
    return None


def _view(drive: models.HardDrive, row: dict, reading, stored: bool, predicted: date | None) -> dict:
    days = (predicted - date.today()).days if predicted else None
    return {
        "message": row["message"],
        "steps": row["steps"],
        "source": row["source"],
        "stored": stored,
        "createdAt": row.get("createdAt"),
        "risk": row["risk"],
        "probability": round(row["probability"] * 100),
        "evidence": evidence(reading),
        "expectedInDays": days,
    }


def latest(db: Session, drive: models.HardDrive):
    pred = (db.query(models.Prediction).filter(models.Prediction.drive_id == drive.drive_id)
            .order_by(models.Prediction.prediction_id.desc()).first())
    reading = (db.query(models.SmartReading).filter(models.SmartReading.drive_id == drive.drive_id)
               .order_by(models.SmartReading.reading_id.desc()).first())
    return pred, reading


def current(db: Session, drive: models.HardDrive) -> dict | None:
    """What the drive page shows: the stored message, or a template one (not saved) if none exists yet."""
    pred, reading = latest(db, drive)
    stored = db.get(models.AlertMessage, drive.drive_id)
    if stored is not None:
        row = {"message": stored.message, "steps": json.loads(stored.steps), "source": stored.source,
               "risk": stored.risk_level, "probability": stored.probability,
               "createdAt": stored.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")}
        return _view(drive, row, reading, True, pred.predicted_failure_date if pred else None)
    risk = normalise_risk(pred.risk_level) if pred else None
    if risk is None:
        return None
    prob = pred.confidence_level or 0
    row = {"message": template_message(drive.drive_id, prob, anomalies(reading)), "steps": STEPS[risk],
           "source": "template", "risk": risk, "probability": prob, "createdAt": None}
    return _view(drive, row, reading, False, pred.predicted_failure_date)


def generate(db: Session, drive: models.HardDrive, risk: str | None = None, probability: float | None = None) -> dict | None:
    """Write (or rewrite) the alert for a drive and save it. risk/probability may come from a live prediction."""
    pred, reading = latest(db, drive)
    risk = normalise_risk(risk) if risk else (normalise_risk(pred.risk_level) if pred else None)
    if risk is None:
        return None
    if probability is None:
        probability = (pred.confidence_level or 0) if pred else 0.0
    issues = anomalies(reading)
    written = call_service(drive.drive_id, probability, risk, issues) or {
        "message": template_message(drive.drive_id, probability, issues), "steps": STEPS[risk], "source": "template"}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = db.get(models.AlertMessage, drive.drive_id)
    if row is None:
        row = models.AlertMessage(drive_id=drive.drive_id)
        db.add(row)
    row.risk_level, row.probability = risk, probability
    row.message, row.steps, row.source, row.created_at = written["message"], json.dumps(written["steps"]), written["source"], now
    db.commit()
    view_row = {**written, "risk": risk, "probability": probability, "createdAt": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return _view(drive, view_row, reading, True, pred.predicted_failure_date if pred else None)
