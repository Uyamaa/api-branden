"""Suggested next steps: made once per high-risk prediction, approved in order, and only then written."""
from datetime import date

from app import models
from app.database import SessionLocal

SERIAL = "ZL2C4M9R"


def _make_high(serial=SERIAL, prob=0.95):
    db = SessionLocal()
    drive = db.query(models.HardDrive).filter(models.HardDrive.serial_number == serial).first()
    db.add(models.Prediction(drive_id=drive.drive_id, predicted_failure_date=date.today(), risk_level="High", confidence_level=prob))
    db.commit()
    db.close()


def _new_drive(serial="NEW-0001"):
    db = SessionLocal()
    old = db.query(models.HardDrive).filter(models.HardDrive.serial_number == SERIAL).first()
    db.add(models.HardDrive(serial_number=serial, model="Exos", capacity=18, status="Healthy", data_center_id=old.data_center_id))
    db.commit()
    db.close()


def test_high_risk_gets_two_suggestions_once(client):
    _make_high()
    first = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    again = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    assert [i["kind"] for i in first] == ["log_maintenance", "record_replacement"]
    assert [i["id"] for i in first] == [i["id"] for i in again]
    assert first[0]["locked"] is False and first[1]["locked"] is True
    assert first[0]["payload"]["type"] == "Data backup" and "95%" in first[1]["payload"]["reason"]


def test_low_risk_gets_nothing(client):
    assert client.get(f"/api/drives/{SERIAL}/proposals").json()["items"] == []


def test_nothing_is_written_until_approved_and_order_is_enforced(client):
    _make_high()
    items = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    before = len(client.get("/api/maintenance").json()["items"])
    assert before == len(client.get("/api/maintenance").json()["items"])
    # step 2 cannot be approved first
    assert client.post(f"/api/proposals/{items[1]['id']}/approve", json={"newSerial": "NEW-0001"}).status_code == 409
    r = client.post(f"/api/proposals/{items[0]['id']}/approve")
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "approved" and r.json()["proposal"]["decidedBy"]
    now = client.get("/api/maintenance").json()["items"]
    assert len(now) == before + 1 and now[0]["type"] == "Data backup"
    assert client.post(f"/api/proposals/{items[0]['id']}/approve").status_code == 409  # not twice


def test_replacement_needs_an_existing_new_drive_and_writes_a_record(client):
    _make_high()
    items = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    client.post(f"/api/proposals/{items[0]['id']}/approve")
    rid = items[1]["id"]
    assert client.post(f"/api/proposals/{rid}/approve", json={}).status_code == 422
    assert client.post(f"/api/proposals/{rid}/approve", json={"newSerial": "NOPE"}).status_code == 404
    assert client.post(f"/api/proposals/{rid}/approve", json={"newSerial": SERIAL}).status_code == 422
    _new_drive()
    r = client.post(f"/api/proposals/{rid}/approve", json={"newSerial": "NEW-0001"})
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "approved"
    reps = client.get("/api/replacements?month=" + date.today().strftime("%Y-%m")).json()["items"]
    assert any(x["newSerial"] == "NEW-0001" for x in reps)


def test_skipping_step_one_unlocks_step_two_and_edits_are_used(client):
    _make_high()
    items = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    assert client.post(f"/api/proposals/{items[0]['id']}/skip").json()["proposal"]["status"] == "skipped"
    after = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    assert after[1]["locked"] is False


def test_edited_approval_uses_the_edits(client):
    _make_high()
    items = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    r = client.post(f"/api/proposals/{items[0]['id']}/approve", json={"type": "SMART diagnostic"})
    assert r.status_code == 200
    assert client.get("/api/maintenance").json()["items"][0]["type"] == "SMART diagnostic"


def test_agent_can_push_its_own_suggestions_with_the_key(client, monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", "k")
    _make_high()
    client.get(f"/api/drives/{SERIAL}/proposals")
    body = {"runId": "run-1", "actions": [{"kind": "log_maintenance", "payload": {"type": "SMART diagnostic"}, "rationale": "Check first"}]}
    assert client.post(f"/api/drives/{SERIAL}/proposals", json=body).status_code == 401
    r = client.post(f"/api/drives/{SERIAL}/proposals", json=body, headers={"X-Ingest-Key": "k"})
    assert r.status_code == 201
    items = client.get(f"/api/drives/{SERIAL}/proposals").json()["items"]
    assert len(items) == 1 and items[0]["source"] == "agent" and items[0]["payload"]["type"] == "SMART diagnostic"
    bad = {"actions": [{"kind": "delete_everything"}]}
    assert client.post(f"/api/drives/{SERIAL}/proposals", json=bad, headers={"X-Ingest-Key": "k"}).status_code == 422
