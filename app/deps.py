import os

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import auth, models
from .database import get_db


def is_admin(user: models.User) -> bool:
    return (user.role or "").strip().lower() in ("admin", "administrator")


# Three roles. Anything that is not an admin or a technician is treated as a viewer (read-only).
PERMISSIONS = {
    "admin": ["read", "write", "manage"],
    "technician": ["read", "write"],
    "viewer": ["read"],
}


def role_of(user: models.User) -> str:
    role = (user.role or "").strip().lower()
    if role in ("admin", "administrator"):
        return "admin"
    if role == "technician":
        return "technician"
    return "viewer"


def permissions_of(user: models.User) -> list[str]:
    return PERMISSIONS[role_of(user)]


def auth_required() -> bool:
    """Sign-in is required unless AUTH_REQUIRED is set to false (the tests do this)."""
    return os.getenv("AUTH_REQUIRED", "true").strip().lower() not in ("0", "false", "no")


def current_user(db: Session, request: Request) -> models.User | None:
    """The person named by the Authorization: Bearer token, or None."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    user_id = auth.read_session(db, header[7:].strip())
    return db.get(models.User, user_id) if user_id is not None else None


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def require_auth(request: Request, db: Session = Depends(get_db)):
    """Guard for every data route. Skipped only when AUTH_REQUIRED=false."""
    if not auth_required():
        return None
    user = current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user


def acting_user(db: Session, request: Request) -> models.User:
    """Who is performing a write?

    1. The signed-in person. An Administrator may send X-Acting-As to record work for someone else.
    2. With no token (only possible when AUTH_REQUIRED=false): X-User-Id, DEFAULT_USER_ID, the first user.
    """
    user = current_user(db, request)
    if user is not None:
        target = request.headers.get("x-acting-as")
        if target and target.isdigit() and is_admin(user):
            other = db.get(models.User, int(target))
            if other is not None:
                return other
        return user

    raw = request.headers.get("x-user-id") or os.getenv("DEFAULT_USER_ID")
    user = None
    if raw and raw.isdigit():
        user = db.get(models.User, int(raw))
    if user is None:
        user = db.query(models.User).order_by(models.User.user_id).first()
    if user is None:
        raise HTTPException(status_code=400, detail="No users exist yet, so the action cannot be attributed.")
    return user


def require_permission(name: str):
    """Guard for a route: read / write (technician and up) / manage (admin only).

    The check is made on the person who is signed in, never on "X-Acting-As".
    Skipped only when AUTH_REQUIRED=false (the tests do this).
    """

    def guard(request: Request, db: Session = Depends(get_db)):
        if not auth_required():
            return None
        user = current_user(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in required")
        if name not in permissions_of(user):
            raise HTTPException(status_code=403, detail=f"Your role ({role_of(user)}) cannot do this. Ask an administrator.")
        return user

    return guard
