import pytest

from app import auth, models
from app.database import SessionLocal

DRIVE = {"serial": "NEW-001", "model": "Test", "capacityTb": 4, "dc": "London DC-01", "status": "Healthy"}
MAINT = {"serial": "ZL2C4M8Q", "type": "SMART diagnostic", "date": "2026-10-06"}


@pytest.fixture()
def people(client, monkeypatch):
    """Sign-in on, with an admin (1), a technician (2) and a viewer (3)."""
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    auth.reset_failures()
    db = SessionLocal()
    db.add(models.User(user_id=3, full_name="Vera Viewer", email="vera@x.test", role="viewer"))
    for uid, pw in ((1, "admin pw"), (2, "tech pw"), (3, "view pw")):
        db.add(models.UserCredential(user_id=uid, password_hash=auth.hash_password(pw)))
    db.commit()
    db.close()
    tokens = {}
    for name, email, pw in (("admin", "amina@x.test", "admin pw"), ("tech", "lukas@x.test", "tech pw"), ("viewer", "vera@x.test", "view pw")):
        r = client.post("/api/auth/login", json={"email": email, "password": pw})
        tokens[name] = {"Authorization": f"Bearer {r.json()['token']}"}
    yield client, tokens
    auth.reset_failures()


def test_login_reports_access_and_permissions(people):
    c, t = people
    me = {k: c.get("/api/auth/me", headers=h).json() for k, h in t.items()}
    assert me["admin"]["access"] == "admin" and me["admin"]["permissions"] == ["read", "write", "manage"]
    assert me["tech"]["access"] == "technician" and me["tech"]["permissions"] == ["read", "write"]
    assert me["viewer"]["access"] == "viewer" and me["viewer"]["permissions"] == ["read"]


def test_viewer_can_read_but_not_change(people):
    c, t = people
    h = t["viewer"]
    assert c.get("/api/drives", headers=h).status_code == 200
    assert c.get("/api/dashboard", headers=h).status_code == 200
    assert c.get("/api/users", headers=h).status_code == 403          # who else has access is private
    assert c.get("/api/users/1/activity", headers=h).status_code == 403
    assert c.post("/api/drives", headers=h, json=DRIVE).status_code == 403
    assert c.put("/api/drives/7JG2K9HG", headers=h, json={**DRIVE, "serial": "7JG2K9HG"}).status_code == 403
    assert c.post("/api/drives/ZL2C4M8Q/readings", headers=h, json={"temperature": 1, "powerOnHours": 1}).status_code == 403
    assert c.post("/api/maintenance", headers=h, json=MAINT).status_code == 403
    assert c.post("/api/drives/ZL2C4M8Q/assistant", headers=h).status_code == 403
    assert c.delete("/api/drives/ZL2C4M8Q", headers=h).status_code == 403
    assert "viewer" in c.post("/api/drives", headers=h, json=DRIVE).json()["detail"]


def test_technician_can_work_but_not_manage(people):
    c, t = people
    h = t["tech"]
    assert c.get("/api/users", headers=h).status_code == 403
    assert c.post("/api/drives", headers=h, json=DRIVE).status_code == 201
    assert c.post("/api/maintenance", headers=h, json=MAINT).status_code == 201
    assert c.post("/api/drives/ZL2C4M8Q/assistant", headers=h).status_code == 200
    assert c.delete("/api/drives/NEW-001", headers=h).status_code == 403
    assert c.post("/api/data-centres", headers=h, json={"name": "Cape Town DC-09", "location": "Cape Town"}).status_code == 403
    assert c.put("/api/users/3/role", headers=h, json={"role": "admin"}).status_code == 403


def test_admin_can_do_everything_and_change_roles(people):
    c, t = people
    h = t["admin"]
    assert c.post("/api/data-centres", headers=h, json={"name": "Cape Town DC-09", "location": "Cape Town"}).status_code == 201
    assert c.post("/api/drives", headers=h, json=DRIVE).status_code == 201
    assert c.delete("/api/drives/NEW-001", headers=h).status_code == 200
    r = c.put("/api/users/3/role", headers=h, json={"role": "technician"})
    assert r.status_code == 200 and r.json()["access"] == "technician"
    assert c.post("/api/maintenance", headers=t["viewer"], json=MAINT).status_code == 201  # same token, new role
    assert c.put("/api/users/1/role", headers=h, json={"role": "viewer"}).status_code == 400  # not yourself
    assert c.put("/api/users/3/role", headers=h, json={"role": "boss"}).status_code == 422
    assert c.put("/api/users/99/role", headers=h, json={"role": "viewer"}).status_code == 404


def test_unknown_role_is_a_viewer(people):
    c, t = people
    db = SessionLocal()
    db.get(models.User, 3).role = "Auditor"
    db.commit()
    db.close()
    assert c.get("/api/auth/me", headers=t["viewer"]).json()["access"] == "viewer"
    assert c.post("/api/drives", headers=t["viewer"], json=DRIVE).status_code == 403


def test_admin_overview_is_admin_only(people):
    c, t = people
    assert c.get("/api/admin/overview", headers=t["tech"]).status_code == 403
    assert c.get("/api/admin/overview", headers=t["viewer"]).status_code == 403
    body = c.get("/api/admin/overview", headers=t["admin"]).json()
    assert body["users"]["total"] == 3 and body["users"]["admin"] == 1
    assert body["drives"]["total"] == 4
    assert len(body["dataCentres"]) == 2
    assert {s["name"] for s in body["services"]} == {"API", "Database", "Alert service"}
    assert isinstance(body["attention"], list) and isinstance(body["activity"], list)


def test_admin_can_add_a_user_who_can_sign_in(people):
    c, t = people
    new = {"name": "Nia New", "email": "Nia@x.test", "role": "viewer", "password": "temp-pass-1"}
    assert c.post("/api/users", headers=t["tech"], json=new).status_code == 403
    r = c.post("/api/users", headers=t["admin"], json=new)
    assert r.status_code == 201 and r.json()["access"] == "viewer"
    assert c.post("/api/users", headers=t["admin"], json=new).status_code == 409
    assert c.post("/api/users", headers=t["admin"], json={**new, "email": "x@y.test", "password": "short"}).status_code == 422
    login = c.post("/api/auth/login", json={"email": "nia@x.test", "password": "temp-pass-1"})
    assert login.status_code == 200 and login.json()["user"]["access"] == "viewer"
