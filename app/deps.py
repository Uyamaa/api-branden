import os

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from . import models


def acting_user(db: Session, request: Request) -> models.User:
    """Who is performing a write?

    There is no login yet, so a write is attributed to (in order):
      1. the X-User-Id request header (the frontend can send it once login exists),
      2. the DEFAULT_USER_ID environment variable,
      3. the first user in the table.
    """
    raw = request.headers.get("x-user-id") or os.getenv("DEFAULT_USER_ID")
    user = None
    if raw and raw.isdigit():
        user = db.get(models.User, int(raw))
    if user is None:
        user = db.query(models.User).order_by(models.User.user_id).first()
    if user is None:
        raise HTTPException(status_code=400, detail="No users exist yet, so the action cannot be attributed.")
    return user
