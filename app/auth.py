"""Passwords and sign-in tokens.

Passwords are stored only as bcrypt hashes. Signing in creates a SESSION row on the server and hands
the browser a random token. The server keeps only the token's hash, so a session can be ended at
any time (sign out, "sign out everywhere", an admin removing someone), which a signed JWT could not.
"""
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt

SESSION_HOURS = 12        # a session never lasts longer than this ...
IDLE_MINUTES = 120        # ... and ends sooner if nobody uses it for this long
TOUCH_SECONDS = 60        # last_seen is written at most this often
MAX_FAILURES = 8          # wrong passwords allowed ...
WINDOW_SECONDS = 600      # ... per email within this many seconds

_failures: dict[str, list[float]] = {}


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


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, user_id: int, device: str = "") -> str:
    """Start a session and return the token to give the browser (shown once, never stored)."""
    from . import models
    now = _now()
    # tidy up: forget sessions that ended more than a day ago
    db.query(models.UserSession).filter(models.UserSession.expires_at < now - timedelta(days=1)).delete()
    token = secrets.token_urlsafe(32)
    db.add(models.UserSession(token_hash=_hash(token), user_id=user_id, created_at=now, last_seen=now,
                              expires_at=now + timedelta(hours=SESSION_HOURS), device=(device or "")[:120]))
    db.commit()
    return token


def read_session(db, token: str) -> int | None:
    """The user id for a live session, else None. Slides last_seen forward (at most once a minute)."""
    from . import models
    row = db.get(models.UserSession, _hash(token)) if token else None
    if row is None or row.revoked_at is not None:
        return None
    now = _now()
    if row.expires_at <= now or row.last_seen + timedelta(minutes=IDLE_MINUTES) <= now:
        return None
    if (now - row.last_seen).total_seconds() >= TOUCH_SECONDS:
        row.last_seen = now
        db.commit()
    return row.user_id


def end_session(db, token: str) -> bool:
    from . import models
    row = db.get(models.UserSession, _hash(token)) if token else None
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    db.commit()
    return True


def end_user_sessions(db, user_id: int, keep_token: str | None = None) -> int:
    """End every live session of a user (optionally keeping the one that is asking). Returns how many."""
    from . import models
    keep = _hash(keep_token) if keep_token else None
    rows = db.query(models.UserSession).filter(models.UserSession.user_id == user_id,
                                               models.UserSession.revoked_at.is_(None)).all()
    n = 0
    for r in rows:
        if r.token_hash != keep:
            r.revoked_at = _now()
            n += 1
    db.commit()
    return n


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
