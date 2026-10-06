"""Shared numbers used by both the dashboard and the reports page."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..utils import is_open, severity as norm_severity, status_sql


def scoped_dc_ids(db: Session, scope: str) -> list[int]:
    query = db.query(models.DataCenter.data_center_id)
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    return [r[0] for r in query.all()]


def drive_counts(db: Session, scope: str) -> dict:
    st = status_sql()
    query = (
        db.query(st, func.count(models.HardDrive.drive_id))
        .join(models.DataCenter, models.DataCenter.data_center_id == models.HardDrive.data_center_id)
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    counts = {"healthy": 0, "warning": 0, "critical": 0}
    for status, n in query.group_by(st).all():
        counts[status] = n
    return counts


def open_alert_severity(db: Session, scope: str) -> dict:
    query = (
        db.query(models.Alert.severity, models.Alert.outcome)
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Alert.data_center_id)
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    sev = {"critical": 0, "warning": 0, "info": 0}
    for raw, outcome in query.all():
        if is_open(outcome):
            sev[norm_severity(raw)] += 1
    return sev


def replacement_count(db: Session, scope: str, start, end) -> int:
    query = (
        db.query(func.count(models.Replacement.replacement_id))
        .join(models.DataCenter, models.DataCenter.data_center_id == models.Replacement.data_center_id)
        .filter(models.Replacement.replacement_date >= start, models.Replacement.replacement_date < end)
    )
    if scope != "all":
        query = query.filter(models.DataCenter.name == scope)
    return query.scalar() or 0
