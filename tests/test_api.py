def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_users(client):
    items = client.get("/api/users").json()["items"]
    assert items[0] == {"id": 1, "name": "Amina Khan", "email": "amina@x.test", "role": "Administrator", "access": "admin"}


def test_drives_list_sorted_by_risk_and_counted(client):
    body = client.get("/api/drives").json()
    assert body["total"] == 4
    assert [d["serial"] for d in body["items"]][:2] == ["ZL2C4M8Q", "ZL2C4M9R"]
    assert body["items"][0] == {"id": 1, "serial": "ZL2C4M8Q", "model": "Exos X18", "capacityTb": 18, "status": "critical", "dc": "London DC-01"}


def test_drives_filters_and_paging(client):
    assert client.get("/api/drives?q=7jg").json()["total"] == 2
    assert client.get("/api/drives?status=warning").json()["total"] == 1
    assert client.get("/api/drives?scope=Frankfurt DC-02").json()["total"] == 2
    page2 = client.get("/api/drives?page=2&page_size=3").json()
    assert page2["total"] == 4 and len(page2["items"]) == 1


def test_drive_detail(client):
    d = client.get("/api/drives/ZL2C4M8Q").json()
    assert d["status"] == "critical"
    assert d["info"]["dc"] == "London DC-01" and d["info"]["asset"] == "DRV-1"
    assert d["prediction"]["date"] == "2026-10-09" and d["prediction"]["risk"] == "High risk" and d["prediction"]["confidence"] == 91
    assert len(d["smart"]["readings"]) == 9
    assert d["smart"]["readings"][0]["level"] == "warning"  # 48 C
    assert d["alerts"][0]["confidence"] == 91
    assert d["maintenance"][0] == {"id": 1, "date": "2026-10-05", "type": "SMART diagnostic", "by": "Amina Khan"}


def test_drive_without_data_and_unknown_drive(client):
    d = client.get("/api/drives/7JG1R3VA").json()
    assert d["prediction"]["date"] is None and d["smart"]["readings"] == [] and d["alerts"] == []
    assert client.get("/api/drives/NOPE").status_code == 404


def test_alerts(client):
    body = client.get("/api/alerts").json()
    assert body["openTotal"] == 2  # the resolved one is not open
    assert len(body["items"]) == 3 and body["items"][0]["serial"] == "ZL2C4M8Q"
    assert body["items"][0]["risk"] == "High" and body["items"][0]["confidence"] == 91
    assert [a["severity"] for a in client.get("/api/alerts?severity=critical").json()["items"]] == ["critical"]
    assert len(client.get("/api/alerts?range=today").json()["items"]) == 1
    assert client.get("/api/alerts?scope=Frankfurt DC-02").json()["openTotal"] == 0


def test_dashboard(client):
    d = client.get("/api/dashboard").json()
    m = d["metrics"]
    assert (m["totalDrives"], m["atRisk"], m["warning"], m["critical"]) == (4, 2, 1, 1)
    assert m["openAlerts"] == 2 and m["severity"] == {"critical": 1, "warning": 1, "info": 0}
    assert d["dataCentres"] == 2 and len(d["recent"]) == 3
    assert client.get("/api/dashboard?scope=London DC-01").json()["dataCentres"] == 1


def test_maintenance_list_and_create(client):
    assert len(client.get("/api/maintenance").json()["items"]) == 1
    r = client.post("/api/maintenance", json={"serial": "ZL2C4M9R", "dc": "London DC-01", "type": "Firmware update", "date": "2026-10-06"})
    assert r.status_code == 201
    assert r.json()["serial"] == "ZL2C4M9R" and r.json()["by"] == "Amina Khan"
    assert len(client.get("/api/maintenance?type=Firmware update").json()["items"]) == 1
    # the X-User-Id header attributes the work to someone else
    r = client.post("/api/maintenance", headers={"x-user-id": "2"}, json={"serial": "ZL2C4M8Q", "type": "Health inspection", "date": "2026-10-06"})
    assert r.json()["by"] == "Lukas Weber"


def test_maintenance_validation(client):
    assert client.post("/api/maintenance", json={"serial": "NOPE", "type": "x", "date": "2026-10-06"}).status_code == 404
    assert client.post("/api/maintenance", json={"serial": "ZL2C4M8Q", "type": "", "date": "2026-10-06"}).status_code == 422
    assert client.post("/api/maintenance", json={"serial": "ZL2C4M8Q", "dc": "Nowhere", "type": "x", "date": "2026-10-06"}).status_code == 404


def test_replacements(client):
    body = client.get("/api/replacements?month=2026-10").json()
    assert body["monthTotal"] == 1
    assert body["items"][0] == {"id": 1, "date": "2026-10-04", "oldSerial": "7JG2K9HG", "newSerial": "7JG1R3VA",
                                "reason": "Reallocated sectors", "by": "Lukas Weber", "dc": "Frankfurt DC-02"}
    assert client.get("/api/replacements?month=2026-09").json()["monthTotal"] == 0
    assert client.get("/api/replacements?month=bad").status_code == 422
    r = client.post("/api/replacements", json={"oldSerial": "ZL2C4M8Q", "newSerial": "ZL2C4M9R", "date": "2026-10-06", "reason": "Predicted failure"})
    assert r.status_code == 201 and r.json()["dc"] == "London DC-01"
    assert client.get("/api/replacements?month=2026-10").json()["monthTotal"] == 2


def test_replacement_validation(client):
    same = {"oldSerial": "ZL2C4M8Q", "newSerial": "ZL2C4M8Q", "date": "2026-10-06", "reason": "x"}
    assert client.post("/api/replacements", json=same).status_code == 422
    unknown = {"oldSerial": "ZL2C4M8Q", "newSerial": "NOPE", "date": "2026-10-06", "reason": "x"}
    assert client.post("/api/replacements", json=unknown).status_code == 404


def test_reports(client):
    r = client.get("/api/reports/summary?month=2026-09").json()
    assert r["rangeLabel"] == "Full month · 01–30 Sep · UTC"
    assert r["byStatus"] == {"healthy": 2, "warning": 1, "critical": 1}
    assert r["metrics"]["replacements"] == 0 and r["metrics"]["healthy"] == 2
    assert [x["dc"] for x in r["replacementsByDc"]] == ["London DC-01", "Frankfurt DC-02"]
    oct_ = client.get("/api/reports/summary?month=2026-10&scope=Frankfurt DC-02").json()
    assert oct_["replacementsByDc"] == [{"dc": "Frankfurt DC-02", "count": 1}]
    assert client.get("/api/reports/summary?month=2026-13").status_code == 422


def test_data_entry(client):
    dcs = client.get("/api/data-centres").json()["items"]
    assert dcs
    name = dcs[0]["name"]
    r = client.post("/api/drives", json={"serial": "NEW-001", "model": "Test", "capacityTb": 4, "dc": name, "status": "Healthy"})
    assert r.status_code == 201
    assert client.post("/api/drives", json={"serial": "NEW-001", "model": "Test", "capacityTb": 4, "dc": name}).status_code == 409
    assert client.post("/api/drives", json={"serial": "NEW-002", "model": "T", "capacityTb": 4, "dc": "nope"}).status_code == 404
    r = client.post("/api/drives/NEW-001/readings", json={"temperature": 51, "powerOnHours": 1000, "reallocatedSectors": 3})
    assert r.status_code == 201
    detail = client.get("/api/drives/NEW-001").json()
    assert detail["smart"]["readings"][0]["value"] == "51°C"
    assert client.post("/api/drives/NOPE/readings", json={"temperature": 1, "powerOnHours": 1}).status_code == 404
    assert client.post("/api/data-centres", json={"name": name, "location": "x"}).status_code == 409
    assert client.post("/api/data-centres", json={"name": "Cape Town DC-09", "location": "Cape Town"}).status_code == 201


def test_edit_and_delete_drive(client):
    r = client.put("/api/drives/7JG2K9HG", json={"serial": "7JG2K9HG-X", "model": "New", "capacityTb": 20, "dc": "London DC-01", "status": "Warning"})
    assert r.status_code == 200 and r.json()["dc"] == "London DC-01" and r.json()["status"] == "warning"
    assert client.get("/api/drives/7JG2K9HG").status_code == 404
    assert client.put("/api/drives/7JG2K9HG-X", json={"serial": "ZL2C4M8Q", "model": "a", "capacityTb": 1, "dc": "London DC-01", "status": "Healthy"}).status_code == 409
    assert client.put("/api/drives/NOPE", json={"serial": "n", "model": "a", "capacityTb": 1, "dc": "London DC-01", "status": "Healthy"}).status_code == 404
    # drive 1 has a reading, prediction, alert and maintenance; drive 2 is its neighbour
    assert client.delete("/api/drives/ZL2C4M8Q").status_code == 200
    assert client.get("/api/drives/ZL2C4M8Q").status_code == 404
    assert client.delete("/api/drives/ZL2C4M8Q").status_code == 404
    # drive 4 is the new_drive of a replacement: deleting it removes that record
    assert client.delete("/api/drives/7JG1R3VA").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/replacements?month=2026-10").json()["monthTotal"] == 0


def test_user_activity(client):
    r = client.get("/api/users/1/activity")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["name"] == "Amina Khan"
    assert body["totals"]["maintenance"] == 1 and body["totals"]["drives"] == 1
    assert body["work"][0]["kind"] == "Maintenance" and body["work"][0]["serial"] == "ZL2C4M8Q"
    assert body["alerts"][0]["serial"] == "ZL2C4M8Q" and body["totals"]["openAlerts"] == 1
    body2 = client.get("/api/users/2/activity").json()
    assert body2["totals"]["replacements"] == 1 and body2["work"][0]["kind"] == "Replacement"
    assert "7JG1R3VA" in body2["work"][0]["detail"]
    assert client.get("/api/users/999/activity").status_code == 404
