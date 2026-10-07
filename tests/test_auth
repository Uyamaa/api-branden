import pytest

from app import auth, models
from app.database import SessionLocal


@pytest.fixture()
def secured(client, monkeypatch):
    """Turn sign-in on and give Amina (admin) and Lukas (technician) passwords."""
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    auth.reset_failures()
    db = SessionLocal()
    db.add(models.UserCredential(user_id=1, password_hash=auth.hash_password("correct horse")))
    db.add(models.UserCredential(user_id=2, password_hash=auth.hash_password("tech password")))
    db.commit()
    db.close()
    yield client
    auth.reset_failures()


def login(c, email, password):
    return c.post("/api/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_success_and_me(secured):
    r = login(secured, "AMINA@x.test", "correct horse")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "amina@x.test" and body["user"]["role"] == "Administrator"
    me = secured.get("/api/auth/me", headers=bearer(body["token"]))
    assert me.json()["id"] == 1


def test_login_failures_are_generic(secured):
    wrong = login(secured, "amina@x.test", "nope")
    unknown = login(secured, "nobody@x.test", "whatever")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_rate_limit(secured):
    for _ in range(auth.MAX_FAILURES):
        assert login(secured, "lukas@x.test", "bad").status_code == 401
    assert login(secured, "lukas@x.test", "tech password").status_code == 429


def test_guard(secured):
    assert secured.get("/api/health").status_code == 200
    assert secured.get("/api/drives").status_code == 401
    assert secured.get("/api/drives", headers=bearer("garbage")).status_code == 401
    token = login(secured, "amina@x.test", "correct horse").json()["token"]
    assert secured.get("/api/drives", headers=bearer(token)).status_code == 200


def _who(c, headers):
    r = c.post("/api/maintenance", headers=headers, json={"serial": "ZL2C4M8Q", "type": "SMART diagnostic", "date": "2026-10-06"})
    assert r.status_code in (200, 201), r.text
    db = SessionLocal()
    last = db.query(models.Maintenance).order_by(models.Maintenance.maintenance_id.desc()).first()
    who = last.performed_by
    db.close()
    return who


def test_acting_as_only_for_admin(secured):
    admin = login(secured, "amina@x.test", "correct horse").json()["token"]
    tech = login(secured, "lukas@x.test", "tech password").json()["token"]
    assert _who(secured, bearer(admin)) == 1
    assert _who(secured, {**bearer(admin), "X-Acting-As": "2"}) == 2
    assert _who(secured, {**bearer(tech), "X-Acting-As": "1"}) == 2
