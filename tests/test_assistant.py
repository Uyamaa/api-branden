from app import assistant, auth, models
from app.database import SessionLocal


def test_detail_has_template_alert_before_one_is_saved(client):
    a = client.get("/api/drives/ZL2C4M8Q").json()["assistant"]
    assert a["source"] == "template" and a["stored"] is False
    assert a["risk"] == "High" and a["probability"] == 91
    assert "Drive 1 has a 91% failure probability" in a["message"]
    assert a["steps"] == ["Back up this drive's data", "Schedule replacement as soon as possible"]
    labels = [e["label"] for e in a["evidence"]]
    assert "Reallocated sectors" in labels and "Pending sectors" in labels


def test_healthy_drive_has_no_alert(client):
    assert client.get("/api/drives/7JG2K9HG").json()["assistant"] is None


def test_write_alert_uses_the_service_and_saves(client, monkeypatch):
    seen = {}

    def fake(drive_id, probability, risk, issues):
        seen.update(drive_id=drive_id, probability=probability, risk=risk, issues=issues)
        return {"message": "Written by the AI.", "steps": ["Do the thing"], "source": "llm"}

    monkeypatch.setattr(assistant, "call_service", fake)
    r = client.post("/api/drives/ZL2C4M8Q/assistant", json={"riskLevel": "high", "failureProbability": 0.95})
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "Written by the AI." and body["source"] == "llm" and body["stored"] is True
    assert body["probability"] == 95
    assert seen["risk"] == "High" and seen["probability"] == 0.95 and "high reallocated sectors" in seen["issues"]
    # It is now what the drive page and the dashboard show.
    assert client.get("/api/drives/ZL2C4M8Q").json()["assistant"]["message"] == "Written by the AI."
    top = client.get("/api/dashboard").json()["attention"][0]
    assert top["serial"] == "ZL2C4M8Q" and top["message"] == "Written by the AI." and top["source"] == "llm"


def test_write_alert_falls_back_to_template_when_service_is_off(client):
    body = client.post("/api/drives/ZL2C4M9R/assistant").json()
    assert body["source"] == "template" and body["risk"] == "Medium"
    assert body["steps"] == ["Schedule an inspection", "Keep monitoring this drive"]


def test_write_alert_for_low_risk_is_refused(client):
    r = client.post("/api/drives/7JG2K9HG/assistant", json={"riskLevel": "Low", "failureProbability": 0.04})
    assert r.status_code == 422
    assert client.post("/api/drives/NOPE/assistant").status_code == 404


def test_dashboard_attention_and_trends(client):
    body = client.get("/api/dashboard").json()
    assert [a["serial"] for a in body["attention"]] == ["ZL2C4M8Q", "ZL2C4M9R"]
    assert body["attention"][0]["severity"] == "critical"
    t = body["trends"]
    assert set(t) == {"totalDrives", "atRisk", "openAlerts", "replacements"}
    assert all(len(v) == 7 for v in t.values())
    assert t["totalDrives"][-1] == 4 and t["atRisk"][-1] == 2


def test_trends_repeat_the_first_saved_day(client):
    client.get("/api/dashboard")
    again = client.get("/api/dashboard").json()["trends"]
    assert again["totalDrives"] == [4] * 7  # one day of history so far: a flat line


def test_deleting_a_drive_removes_its_saved_alert(client):
    client.post("/api/drives/ZL2C4M8Q/assistant")
    assert client.delete("/api/drives/ZL2C4M8Q").status_code == 200
    db = SessionLocal()
    assert db.get(models.AlertMessage, 1) is None
    db.close()


def test_role_admin_counts_as_administrator(secured_admin):
    c, token = secured_admin
    r = c.post("/api/maintenance", headers={"Authorization": f"Bearer {token}", "X-Acting-As": "2"},
               json={"serial": "ZL2C4M8Q", "type": "SMART diagnostic", "date": "2026-10-06"})
    assert r.status_code == 201
    db = SessionLocal()
    assert db.query(models.Maintenance).order_by(models.Maintenance.maintenance_id.desc()).first().performed_by == 2
    db.close()


import pytest  # noqa: E402


@pytest.fixture()
def secured_admin(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    db = SessionLocal()
    user = db.get(models.User, 1)
    user.role = "admin"  # how the real database spells it
    db.add(models.UserCredential(user_id=1, password_hash=auth.hash_password("correct horse")))
    db.commit()
    db.close()
    token = client.post("/api/auth/login", json={"email": "amina@x.test", "password": "correct horse"}).json()["token"]
    yield client, token
    auth.reset_failures()
