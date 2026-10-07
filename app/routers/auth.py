from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import auth, models
from ..database import get_db
from ..deps import bearer_token, current_user, permissions_of, role_of

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=1, max_length=200)


def _public(u: models.User):
    return {"id": u.user_id, "name": u.full_name, "email": u.email, "role": u.role,
            "access": role_of(u), "permissions": permissions_of(u)}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if auth.too_many_failures(email):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a few minutes and try again.")

    user = db.query(models.User).filter(func.lower(models.User.email) == email).first()
    cred = db.get(models.UserCredential, user.user_id) if user else None
    if user is None or cred is None:
        auth.spend_time()
        ok = False
    else:
        ok = auth.verify_password(body.password, cred.password_hash)

    if not ok:
        auth.record_failure(email)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    auth.clear_failures(email)
    return {"token": auth.create_session(db, user.user_id, request.headers.get("user-agent", "")), "user": _public(user)}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return _public(user)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """End this browser's session on the server (the token stops working at once)."""
    auth.end_session(db, bearer_token(request))
    return {"ok": True}


@router.post("/logout-all")
def logout_everywhere(request: Request, db: Session = Depends(get_db)):
    """End all of my other sessions (other browsers/devices). This one stays signed in."""
    user = current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return {"ended": auth.end_user_sessions(db, user.user_id, keep_token=bearer_token(request))}
