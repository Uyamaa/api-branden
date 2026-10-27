import json
import os
import uuid
from datetime import date, datetime, timezone

_Date = date 

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import assistant, models
from ..database import get_db
from ..deps import acting_user, require_permission
from .ingest import require_key

router = APIRouter(prefix="/api", tags=["proposals"])
machine_router = APIRouter(prefix="/api", tags=["proposals"])

BACKUP_TYPE = "Data backup"
KINDS = ("log_maintenance", "record_replacement")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _drive(db: Session, serial: str) -> models.HardDrive:
    drive = db.query(models.HardDrive).filter(models.HardDrive.serial_number == serial).first()
    if drive is None:
        raise HTTPException(status_code=404, detail=f"Drive {serial} not found")
    return drive


def _rule_actions(probability: float) -> list[dict]:
    return [
        {"kind": "log_maintenance", "payload": {"type": BACKUP_TYPE},
         "rationale": "Back up the drive's data before any other work on it."},
        {"kind": "record_replacement", "payload": {"reason": f"Predicted failure ({round(probability * 100)}%)"},
         "rationale": "The drive is expected to fail. Replace it once the backup is safe."},
    ]


def _add(db: Session, drive: models.HardDrive, prediction_id, actions: list[dict], source: str, run_id: str | None):
    # Anything still waiting for a decision is replaced by the new suggestions.
    for old in db.query(models.ProposedAction).filter(
            models.ProposedAction.drive_id == drive.drive_id, models.ProposedAction.status == "pending").all():
        old.status = "superseded"
    batch, made = str(uuid.uuid4()), []
    for i, a in enumerate(actions, 1):
        row = models.ProposedAction(
            drive_id=drive.drive_id, prediction_id=prediction_id, batch_id=batch, seq=i, kind=a["kind"], payload=json.dumps(a.get("payload") or {}),
            rationale=a.get("rationale"), status="pending", source=source, run_id=run_id, created_at=_now())
        db.add(row)
        made.append(row)
    db.commit()
    return made


def _ensure_rules(db: Session, drive: models.HardDrive):
    """Make the rule-based suggestions for the drive's latest high-risk prediction, once."""
    pred, _ = assistant.latest(db, drive)
    if pred is None or assistant.normalise_risk(pred.risk_level) != "High":
        return
    exists = db.query(models.ProposedAction).filter(
        models.ProposedAction.drive_id == drive.drive_id, models.ProposedAction.prediction_id == pred.prediction_id,
        models.ProposedAction.status != "superseded").first()
    if exists is None:
        _add(db, drive, pred.prediction_id, _rule_actions(pred.confidence_level or 0), "rules", None)


def _view(p: models.ProposedAction, siblings: list[models.ProposedAction], names: dict) -> dict:
    earlier = [s for s in siblings if s.seq < p.seq]
    locked = p.status == "pending" and any(s.status == "pending" for s in earlier)
    return {
        "id": p.proposal_id, "seq": p.seq, "kind": p.kind, "payload": json.loads(p.payload or "{}"), "rationale": p.rationale,
        "status": p.status, "source": p.source, "locked": locked,
        "decidedBy": names.get(p.decided_by), "decidedAt": p.decided_at.isoformat() if p.decided_at else None,
    }


def _listing(db: Session, drive: models.HardDrive) -> list[dict]:
    rows = (db.query(models.ProposedAction)
            .filter(models.ProposedAction.drive_id == drive.drive_id, models.ProposedAction.status != "superseded")
            .order_by(models.ProposedAction.proposal_id.desc()).all())
    if not rows:
        return []
    batch = sorted([r for r in rows if r.batch_id == rows[0].batch_id], key=lambda r: r.seq)
    ids = {r.decided_by for r in batch if r.decided_by}
    names = {u.user_id: u.full_name for u in db.query(models.User).filter(models.User.user_id.in_(ids)).all()} if ids else {}
    return [_view(p, batch, names) for p in batch]


@router.get("/drives/{serial}/proposals")
def list_proposals(serial: str, db: Session = Depends(get_db)):
    drive = _drive(db, serial)
    _ensure_rules(db, drive)
    return {"items": _listing(db, drive)}


class ApproveIn(BaseModel):
    type: str | None = Field(None, min_length=1, max_length=50)       # log_maintenance
    date: _Date | None = None                                         # both
    newSerial: str | None = Field(None, min_length=1)                 # record_replacement
    reason: str | None = Field(None, min_length=1)                    # record_replacement


def _tell_agent(p: models.ProposedAction, decision: str, edits: dict):
    """Let a waiting agent run continue. Best effort: a missing or slow service never blocks a person's decision."""
    base = os.getenv(assistant.SERVICE_URL, assistant.DEFAULT_SERVICE_URL).strip()
    if not p.run_id or not base:
        return
    try:
        httpx.post(f"{base.rstrip('/')}/proposals/resume", timeout=5,
                   json={"run_id": p.run_id, "proposal_id": p.proposal_id, "kind": p.kind, "decision": decision, "edits": edits})
    except Exception as exc:
        print(f"could not tell the alert service about proposal {p.proposal_id}: {exc}")


def _load(db: Session, proposal_id: int) -> tuple[models.ProposedAction, models.HardDrive, list[models.ProposedAction]]:
    p = db.get(models.ProposedAction, proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    drive = db.get(models.HardDrive, p.drive_id)
    siblings = db.query(models.ProposedAction).filter(
        models.ProposedAction.batch_id == p.batch_id, models.ProposedAction.status != "superseded").all()
    if p.status != "pending":
        raise HTTPException(status_code=409, detail=f"This step is already {p.status}.")
    if any(s.seq < p.seq and s.status == "pending" for s in siblings):
        raise HTTPException(status_code=409, detail="Approve or skip the earlier step first.")
    return p, drive, siblings


@router.post("/proposals/{proposal_id}/approve", dependencies=[Depends(require_permission("write"))])
def approve(proposal_id: int, request: Request, body: ApproveIn | None = None, db: Session = Depends(get_db)):
    body = body or ApproveIn()
    p, drive, _ = _load(db, proposal_id)
    user = acting_user(db, request)
    draft = json.loads(p.payload or "{}")
    when = body.date or date.today()
    if when > date.today():
        raise HTTPException(status_code=422, detail="The date cannot be in the future.")

    if p.kind == "log_maintenance":
        record = models.Maintenance(
            drive_id=drive.drive_id, data_center_id=drive.data_center_id, maintenance_date=when,
            maintenance_type=(body.type or draft.get("type") or BACKUP_TYPE).strip(), performed_by=user.user_id)
        edits = {"type": record.maintenance_type, "date": when.isoformat()}
    else:
        if not body.newSerial:
            raise HTTPException(status_code=422, detail="Enter the new drive's serial number.")
        new = db.query(models.HardDrive).filter(models.HardDrive.serial_number == body.newSerial.strip()).first()
        if new is None:
            raise HTTPException(status_code=404, detail=f"Drive {body.newSerial} not found. Add the new drive first.")
        if new.drive_id == drive.drive_id:
            raise HTTPException(status_code=422, detail="The new drive must be different from the old drive")
        reason = (body.reason or draft.get("reason") or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Give a reason for the replacement.")
        record = models.Replacement(
            drive_id=drive.drive_id, data_center_id=drive.data_center_id, replacement_date=when,
            replaced_by=user.user_id, reason=reason, new_drive_id=new.drive_id)
        edits = {"newSerial": new.serial_number, "reason": reason, "date": when.isoformat()}

    db.add(record)
    db.flush()
    p.status, p.decided_by, p.decided_at = "approved", user.user_id, _now()
    p.result_id = record.maintenance_id if p.kind == "log_maintenance" else record.replacement_id
    db.commit()
    _tell_agent(p, "approved", edits)
    return {"proposal": _listing_one(db, drive, p.proposal_id), "recordId": p.result_id}


@router.post("/proposals/{proposal_id}/skip", dependencies=[Depends(require_permission("write"))])
def skip(proposal_id: int, request: Request, db: Session = Depends(get_db)):
    p, drive, _ = _load(db, proposal_id)
    user = acting_user(db, request)
    p.status, p.decided_by, p.decided_at = "skipped", user.user_id, _now()
    db.commit()
    _tell_agent(p, "skipped", {})
    return {"proposal": _listing_one(db, drive, p.proposal_id)}


def _listing_one(db: Session, drive: models.HardDrive, proposal_id: int) -> dict:
    return next(i for i in _listing(db, drive) if i["id"] == proposal_id)


class AgentAction(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    rationale: str | None = None


class AgentProposalsIn(BaseModel):
    runId: str | None = Field(None, max_length=80)
    actions: list[AgentAction] = Field(min_length=1, max_length=5)


@machine_router.post("/drives/{serial}/proposals", status_code=201, dependencies=[Depends(require_key)])
def agent_proposals(serial: str, body: AgentProposalsIn, db: Session = Depends(get_db)):
    """The alert agent suggests its own steps. They replace any suggestions still waiting for a decision."""
    drive = _drive(db, serial)
    for a in body.actions:
        if a.kind not in KINDS:
            raise HTTPException(status_code=422, detail=f"Unknown kind {a.kind!r}. Use one of: {', '.join(KINDS)}.")
    pred, _ = assistant.latest(db, drive)
    _add(db, drive, pred.prediction_id if pred else None, [a.model_dump() for a in body.actions], "agent", body.runId)
    return {"items": _listing(db, drive)}
