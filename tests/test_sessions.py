from datetime import timedelta

import pytest

from app import auth, cache, models
from app.database import SessionLocal


@pytest.fixture()
def secured(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    auth.reset_failures()
    cache.clear()
    cache.reset_stats()
    db = SessionLocal()
    db.add(models.UserCredential(user_id=1, password_hash=auth.hash_password("correct horse")))
    db.add(models.UserCredential(user_id=2, password_hash=auth.hash_password("tech password")))
    db.commit()
    db.close()
    yield client
    auth.reset_failures()
    cache.clear()


def sign_in(c, email="amina@x.test", pw="correct horse"):
    return c.post("/api/auth/login", json={"email": email, "password": pw}).json()["token"]


def h(token):
    return {"Authorization": f"Bearer {token}"}


def test_token_is_opaque_and_only_its_hash_is_stored(secured):
    token = sign_in(secured)
    assert "." not in token  # not a JWT
    db = SessionLocal()
    rows = db.query(models.UserSession).all()
    db.close()
    assert len(rows) == 1 and rows[0].token_hash != token and len(rows[0].token_hash) == 64


def test_logout_ends_the_session_at_once(secured):
    token = sign_in(secured)
    assert secured.get("/api/auth/me", headers=h(token)).status_code == 200
    assert secured.post("/api/auth/logout", headers=h(token)).status_code == 200
    assert secured.get("/api/auth/me", headers=h(token)).status_code == 401
    assert secured.get("/api/drives", headers=h(token)).status_code == 401


def test_forged_or_old_style_tokens_are_refused(secured):
    assert secured.get("/api/auth/me", headers=h("not-a-session")).status_code == 401


def test_session_expires_after_idle_time(secured):
    token = sign_in(secured)
    db = SessionLocal()
    row = db.query(models.UserSession).one()
    row.last_seen = row.last_seen - timedelta(minutes=auth.IDLE_MINUTES + 1)
    db.commit()
    db.close()
    assert secured.get("/api/auth/me", headers=h(token)).status_code == 401


def test_session_expires_after_absolute_limit(secured):
    token = sign_in(secured)
    db = SessionLocal()
    row = db.query(models.UserSession).one()
    row.expires_at = row.created_at - timedelta(seconds=1)
    db.commit()
    db.close()
    assert secured.get("/api/auth/me", headers=h(token)).status_code == 401


def test_sign_out_everywhere_keeps_only_this_one(secured):
    a, b = sign_in(secured), sign_in(secured)
    assert secured.post("/api/auth/logout-all", headers=h(a)).json() == {"ended": 1}
    assert secured.get("/api/auth/me", headers=h(a)).status_code == 200
    assert secured.get("/api/auth/me", headers=h(b)).status_code == 401


def test_admin_can_sign_someone_out_but_a_technician_cannot(secured):
    admin, tech = sign_in(secured), sign_in(secured, "lukas@x.test", "tech password")
    assert secured.delete("/api/users/1/sessions", headers=h(tech)).status_code == 403
    assert secured.delete("/api/users/2/sessions", headers=h(admin)).json() == {"ended": 1}
    assert secured.get("/api/auth/me", headers=h(tech)).status_code == 401
    assert secured.delete("/api/users/99/sessions", headers=h(admin)).status_code == 404


def test_cache_serves_repeat_reads_and_forgets_after_a_write(secured):
    admin = sign_in(secured)
    secured.get("/api/dashboard", headers=h(admin))
    before = cache.stats()["hits"]
    secured.get("/api/dashboard", headers=h(admin))
    assert cache.stats()["hits"] == before + 1
    assert cache.stats()["entries"] >= 1
    # a successful write empties the cache
    r = secured.put("/api/users/2/role", json={"role": "viewer"}, headers=h(admin))
    assert r.status_code == 200 and cache.stats()["entries"] == 0
    # signing in is not a data change, so it leaves the cache alone
    secured.get("/api/dashboard", headers=h(admin))
    sign_in(secured, "lukas@x.test", "tech password")
    assert cache.stats()["entries"] >= 1


def test_overview_reports_sessions_and_cache(secured):
    admin = sign_in(secured)
    body = secured.get("/api/admin/overview", headers=h(admin)).json()
    assert body["sessions"]["active"] == 1
    assert {"entries", "hits", "misses", "hitRate"} <= set(body["cache"])


def test_cache_can_be_switched_off(secured, monkeypatch):
    monkeypatch.setenv("CACHE_ENABLED", "false")
    admin = sign_in(secured)
    secured.get("/api/alerts", headers=h(admin))
    secured.get("/api/alerts", headers=h(admin))
    assert cache.stats()["hits"] == 0
