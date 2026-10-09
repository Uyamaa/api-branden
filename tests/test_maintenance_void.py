def _new(client, headers=None):
    r = client.post("/api/maintenance", headers=headers or {}, json={"serial": "ZL2C4M9R", "type": "Cooling inspection", "date": "2026-10-06"})
    assert r.status_code == 201
    return r.json()["id"]


def test_void_hides_the_record_and_keeps_it(client):
    mid = _new(client)
    r = client.post(f"/api/maintenance/{mid}/void", json={"reason": "Entered by mistake"})
    assert r.status_code == 200
    body = r.json()
    assert body["voided"] is True and body["voidReason"] == "Entered by mistake" and body["voidedBy"] == "Amina Khan"

    listing = client.get("/api/maintenance").json()
    assert mid not in [i["id"] for i in listing["items"]] and listing["voidedCount"] == 1
    with_voided = client.get("/api/maintenance?voided=true").json()
    assert mid in [i["id"] for i in with_voided["items"]]
    # not shown on the drive page or in a person's activity either
    assert mid not in [m["id"] for m in client.get("/api/drives/ZL2C4M9R").json()["maintenance"]]


def test_void_needs_a_listed_reason_and_a_note_for_other(client):
    mid = _new(client)
    assert client.post(f"/api/maintenance/{mid}/void", json={"reason": "Because"}).status_code == 422
    assert client.post(f"/api/maintenance/{mid}/void", json={"reason": "Other"}).status_code == 422
    r = client.post(f"/api/maintenance/{mid}/void", json={"reason": "Other", "note": "Test drive"})
    assert r.status_code == 200 and r.json()["voidReason"] == "Other: Test drive"


def test_void_twice_and_unknown_record(client):
    mid = _new(client)
    assert client.post(f"/api/maintenance/{mid}/void", json={"reason": "Wrong drive"}).status_code == 200
    assert client.post(f"/api/maintenance/{mid}/void", json={"reason": "Wrong drive"}).status_code == 409
    assert client.post("/api/maintenance/9999/void", json={"reason": "Wrong drive"}).status_code == 404


def test_only_the_owner_or_an_admin_may_void(client):
    mid = _new(client, {"x-user-id": "2"})  # Lukas (technician) logged it
    other = client.post("/api/maintenance", headers={"x-user-id": "2"}, json={"serial": "ZL2C4M8Q", "type": "Firmware update", "date": "2026-10-06"}).json()["id"]
    # Amina is the first user here but x-user-id picks who is acting
    assert client.post(f"/api/maintenance/{mid}/void", headers={"x-user-id": "2"}, json={"reason": "Duplicate of another record"}).status_code == 200
    # a technician cannot void somebody else's record (record 1 belongs to Amina)
    assert client.post("/api/maintenance/1/void", headers={"x-user-id": "2"}, json={"reason": "Wrong drive"}).status_code == 403
    # an administrator can void anyone's
    assert client.post(f"/api/maintenance/{other}/void", headers={"x-user-id": "1"}, json={"reason": "Wrong drive"}).status_code == 200


def test_restore(client):
    mid = _new(client, {"x-user-id": "2"})
    client.post(f"/api/maintenance/{mid}/void", headers={"x-user-id": "2"}, json={"reason": "Wrong drive"})
    assert client.post(f"/api/maintenance/{mid}/restore", headers={"x-user-id": "2"}).status_code == 200  # own undo
    assert client.post(f"/api/maintenance/{mid}/restore", headers={"x-user-id": "1"}).status_code == 409  # not voided any more
    client.post(f"/api/maintenance/{mid}/void", headers={"x-user-id": "1"}, json={"reason": "Wrong drive"})
    assert client.post(f"/api/maintenance/{mid}/restore", headers={"x-user-id": "2"}).status_code == 403  # admin voided it
    r = client.post(f"/api/maintenance/{mid}/restore", headers={"x-user-id": "1"})
    assert r.status_code == 200 and r.json()["voided"] is False
    assert mid in [i["id"] for i in client.get("/api/maintenance").json()["items"]]


def test_edit_fixes_type_and_date_but_not_voided_records(client):
    mid = _new(client)
    r = client.patch(f"/api/maintenance/{mid}", json={"type": "Health inspection", "date": "2026-10-07"})
    assert r.status_code == 200 and r.json()["type"] == "Health inspection" and r.json()["date"] == "2026-10-07"
    assert client.patch(f"/api/maintenance/{mid}", json={"type": ""}).status_code == 422
    client.post(f"/api/maintenance/{mid}/void", json={"reason": "Wrong drive"})
    assert client.patch(f"/api/maintenance/{mid}", json={"type": "Firmware update"}).status_code == 409
    assert client.patch("/api/maintenance/9999", json={"type": "x"}).status_code == 404


def test_migration_adds_the_columns_to_an_old_table(client):
    from sqlalchemy import create_engine, inspect, text
    from app import migrate
    old = create_engine("sqlite://")
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE maintenance (maintenance_id INTEGER PRIMARY KEY, drive_id INTEGER)"))
    assert migrate.ensure_maintenance_void(old) == "added"
    assert {"voided_at", "voided_by", "void_reason"} <= {c["name"] for c in inspect(old).get_columns("maintenance")}
    assert migrate.ensure_maintenance_void(old) == "present"
