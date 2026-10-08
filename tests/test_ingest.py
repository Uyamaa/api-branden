from datetime import datetime, timedelta, timezone

import pytest

from app import auth, migrate, models
from app.database import SessionLocal, engine

KEY = "test-collector-key"


@pytest.fixture()
def feed(client, monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", KEY)
    return client


def hdr(key=KEY):
    return {"X-Ingest-Key": key}


def reading(serial="NEW-0001", **over):
    base = {"serial": serial, "model": "Exos X18", "capacityTb": 18, "dc": "London DC-01",
            "temperature": 36, "powerOnHours": 1200}
    base.update(over)
    return base


def post(c, items, key=KEY):
    return c.post("/api/ingest/readings", json={"readings": items}, headers=hdr(key))


def iso_ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_intake_is_off_without_a_key_on_the_server(client, monkeypatch):
    monkeypatch.delenv("INGEST_API_KEY", raising=False)
    assert post(client, [reading()]).status_code == 503


def test_wrong_or_missing_key_is_refused(feed):
    assert post(feed, [reading()], key="nope").status_code == 401
    assert feed.post("/api/ingest/readings", json={"readings": [reading()]}).status_code == 401


def test_a_person_login_is_not_a_key(feed, monkeypatch):
    # a signed-in admin token must not work as an intake key
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    db = SessionLocal()
    db.add(models.UserCredential(user_id=1, password_hash=auth.hash_password("correct horse")))
    db.commit(); db.close()
    token = feed.post("/api/auth/login", json={"email": "amina@x.test", "password": "correct horse"}).json()["token"]
    r = feed.post("/api/ingest/readings", json={"readings": [reading()]}, headers={"X-Ingest-Key": token})
    assert r.status_code == 401


def test_new_serial_registers_the_drive_and_stores_the_time(feed):
    when = iso_ago(minutes=3)
    r = post(feed, [reading(collectedAt=when)])
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1 and body["registered"] == ["NEW-0001"] and body["rejected"] == []
    d = feed.get("/api/drives/NEW-0001").json()
    assert d["info"]["model"] == "Exos X18" and d["info"]["dc"] == "London DC-01" and d["status"] == "healthy"
    assert d["smart"]["capturedAt"] == when


def test_known_serial_needs_only_the_numbers(feed):
    r = post(feed, [{"serial": "ZL2C4M8Q", "temperature": 50, "powerOnHours": 52500, "collectedAt": iso_ago(minutes=1)}])
    body = r.json()
    assert body["accepted"] == 1 and body["registered"] == []
    d = feed.get("/api/drives/ZL2C4M8Q").json()
    assert {t["label"]: t["value"] for t in d["smart"]["readings"]}["Temperature"] == "50°C"  # newest reading wins


def test_new_drive_without_details_is_rejected_and_says_what_is_missing(feed):
    body = post(feed, [{"serial": "GHOST-1", "temperature": 30, "powerOnHours": 5}]).json()
    assert body["accepted"] == 0
    assert "model" in body["rejected"][0]["error"] and "dc" in body["rejected"][0]["error"]
    assert feed.get("/api/drives/GHOST-1").status_code == 404


def test_unknown_data_centre_is_rejected_not_invented(feed):
    body = post(feed, [reading(dc="Atlantis DC-9")]).json()
    assert body["accepted"] == 0 and "does not exist" in body["rejected"][0]["error"]
    assert "London DC-01" in body["rejected"][0]["error"]
    assert feed.get("/api/data-centres").json()["items"].__len__() == 2


def test_one_bad_reading_does_not_block_the_rest(feed):
    items = [reading("OK-1"), reading("BAD-1", temperature=900), reading("OK-2"), {"temperature": 30, "powerOnHours": 1}]
    body = post(feed, items).json()
    assert body["received"] == 4 and body["accepted"] == 2
    assert sorted(body["registered"]) == ["OK-1", "OK-2"]
    assert [x["index"] for x in body["rejected"]] == [1, 3]
    assert "temperature" in body["rejected"][0]["error"]


def test_sending_the_same_reading_twice_is_harmless(feed):
    item = reading(collectedAt=iso_ago(minutes=2))
    first = post(feed, [item]).json()
    again = post(feed, [item]).json()
    assert first["accepted"] == 1 and again["accepted"] == 0 and again["duplicates"] == 1
    db = SessionLocal()
    stored = db.query(models.SmartReading).join(models.HardDrive).filter(models.HardDrive.serial_number == "NEW-0001").count()
    db.close()
    assert stored == 1


def test_same_new_serial_twice_in_one_batch_registers_once(feed):
    body = post(feed, [reading(collectedAt=iso_ago(minutes=2)), reading(collectedAt=iso_ago(minutes=1))]).json()
    assert body["accepted"] == 2 and body["registered"] == ["NEW-0001"]


def test_times_in_the_future_or_far_past_are_rejected(feed):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = post(feed, [reading("F-1", collectedAt=future), reading("O-1", collectedAt=iso_ago(days=90))]).json()
    assert body["accepted"] == 0
    assert "future" in body["rejected"][0]["error"] and "older" in body["rejected"][1]["error"]


def test_time_zones_are_converted_to_utc(feed):
    body = post(feed, [reading(collectedAt=(datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(
        timezone(timedelta(hours=2))).isoformat())]).json()
    assert body["accepted"] == 1
    got = feed.get("/api/drives/NEW-0001").json()["smart"]["capturedAt"]
    delta = datetime.now(timezone.utc) - datetime.strptime(got, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(minutes=4) < delta < timedelta(minutes=6)


def test_batch_limits(feed):
    assert feed.post("/api/ingest/readings", json={"readings": []}, headers=hdr()).status_code == 422
    assert post(feed, [reading(f"B-{i}") for i in range(501)]).status_code == 422


def test_manual_entry_now_records_a_time(feed):
    assert feed.post("/api/drives/ZL2C4M9R/readings", json={"temperature": 40, "powerOnHours": 10}).status_code == 201
    assert feed.get("/api/drives/ZL2C4M9R").json()["smart"]["capturedAt"] is not None


def test_old_readings_without_a_time_show_none_not_a_made_up_one(feed):
    assert feed.get("/api/drives/ZL2C4M8Q").json()["smart"]["capturedAt"] is None


def test_admin_overview_reports_the_feed_and_quiet_drives(feed):
    feed.post("/api/ingest/readings", json={"readings": [
        reading("FRESH-1", collectedAt=iso_ago(minutes=2)),
        reading("QUIET-1", collectedAt=iso_ago(hours=30)),
    ]}, headers=hdr())
    body = feed.get("/api/admin/overview").json()["feed"]
    assert body["enabled"] is True and body["reportingDrives"] == 2 and body["staleDrives"] == 1
    assert body["lastReadingAt"] is not None
    attention = [a["text"] for a in feed.get("/api/admin/overview").json()["attention"]]
    assert any("not reported in 24 h" in t for t in attention)


def test_ingest_clears_the_page_cache(feed):
    from app import cache
    feed.get("/api/dashboard")
    assert cache.stats()["entries"] >= 1
    post(feed, [reading()])
    assert cache.stats()["entries"] == 0


def test_migration_adds_the_column_once_and_is_safe_to_repeat():
    from sqlalchemy import inspect, text
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS smart_reading"))
        conn.execute(text("CREATE TABLE smart_reading (reading_id INTEGER PRIMARY KEY, drive_id INTEGER, temperature FLOAT)"))
    assert migrate.ensure_collected_at(engine) == "added"
    assert "collected_at" in {c["name"] for c in inspect(engine).get_columns("smart_reading")}
    assert migrate.ensure_collected_at(engine) == "present"
