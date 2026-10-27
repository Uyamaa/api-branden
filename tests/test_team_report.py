from tests.test_rbac import people 


def test_team_report_counts_work_per_person_and_skips_voided(client):
    a = client.post("/api/maintenance", headers={"x-user-id": "2"}, json={"serial": "ZL2C4M9R", "type": "Data backup", "date": "2026-10-06"}).json()["id"]
    client.post("/api/maintenance", headers={"x-user-id": "2"}, json={"serial": "ZL2C4M8Q", "type": "Firmware update", "date": "2026-10-06"})
    client.post(f"/api/maintenance/{a}/void", headers={"x-user-id": "2"}, json={"reason": "Entered by mistake"})
    body = client.get("/api/reports/team?days=0").json()
    by = {p["name"]: p for p in body["people"]}
    assert by["Lukas Weber"]["jobs"] == 1 and by["Lukas Weber"]["drives"] >= 1
    assert body["totals"]["jobs"] >= 1
    assert body["people"] == sorted(body["people"], key=lambda r: (-(r["jobs"] + r["replacements"]), r["name"]))


def test_team_report_filters_by_role_and_window(client):
    only_tech = client.get("/api/reports/team?days=0&role=technician").json()
    assert all(p["role"] == "technician" for p in only_tech["people"])
    assert client.get("/api/reports/team?days=7").status_code == 200


def test_team_report_is_admin_only(people):
    c, t = people
    assert c.get("/api/reports/team", headers=t["admin"]).status_code == 200
    assert c.get("/api/reports/team", headers=t["tech"]).status_code == 403
    assert c.get("/api/reports/team", headers=t["viewer"]).status_code == 403
