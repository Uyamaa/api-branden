"""Passwords and sign-in tokens.

Passwords are stored only as bcrypt hashes. A signed-in person holds a signed token (JWT)
that the frontend sends with every request.
"""
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

TOKEN_HOURS = 12
MAX_FAILURES = 8          # wrong passwords allowed ...
WINDOW_SECONDS = 600      # ... per email within this many seconds

_failures: dict[str, list[float]] = {}


def _secret() -> str:
    """JWT_SECRET if set; otherwise a stable value derived from the database URL (which is secret)."""
    explicit = os.getenv("JWT_SECRET")
    if explicit:
        return explicit
    return hashlib.sha256(("uyamaa-fleet-api|" + os.getenv("DATABASE_URL", "")).encode()).hexdigest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


# Used to spend the same time on unknown emails as on real ones.
_DUMMY_HASH = hash_password("not-a-real-password")


def spend_time():
    verify_password("x", _DUMMY_HASH)


def create_token(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, _secret(), algorithm="HS256")


def read_token(token: str) -> int | None:
    try:
        data = jwt.decode(token, _secret(), algorithms=["HS256"])
        return int(data["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def too_many_failures(key: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(key: str):
    _failures.setdefault(key, []).append(time.time())


def clear_failures(key: str):
    _failures.pop(key, None)


def reset_failures():
    _failures.clear()
