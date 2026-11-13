import io, os, sys, tempfile
d = tempfile.mkdtemp()
os.environ["DB_PATH"] = d + "/t.db"; os.environ["UPLOAD_DIR"] = d
os.environ["NOTIFY_RECIPIENTS"] = "ok@example.com,fail@example.com"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from fastapi.testclient import TestClient
from PIL import Image
from main import app

c = TestClient(app)
form = {"description": "Possible collision near junction", "latitude": "12.97", "longitude": "77.59"}


def admin_h():
    t = c.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def png():
    b = io.BytesIO(); Image.new("RGB", (20, 20), "red").save(b, "PNG"); return b.getvalue()


def test_valid_report():
    r = c.post("/api/incidents", data=form); assert r.status_code == 200 and r.json()["incident_id"].startswith("INC-")


def test_invalid_input():
    assert c.post("/api/incidents", data={**form, "latitude": "999"}).status_code == 422
    assert c.post("/api/incidents", data={**form, "description": "x"}).status_code == 422


def test_image_valid_invalid_and_ai_unavailable():
    r = c.post("/api/incidents", data=form, files={"image": ("a.png", png(), "image/png")})
    assert r.status_code == 200 and ("AI unavailable" in r.json()["ai_note"] or "Preliminary AI" in r.json()["ai_note"])
    assert c.post("/api/incidents", data=form, files={"image": ("a.txt", b"hello", "text/plain")}).status_code == 422


def test_admin_access_control():
    assert c.get("/api/incidents").status_code == 401
    c.post("/api/auth/register", json={"email": "u@x.com", "password": "secret123"})
    t = c.post("/api/auth/login", json={"email": "u@x.com", "password": "secret123"}).json()["token"]
    assert c.get("/api/incidents", headers={"Authorization": f"Bearer {t}"}).status_code == 403
    assert c.get("/api/incidents", headers=admin_h()).status_code == 200


def test_status_update_and_tracking_privacy():
    iid = c.post("/api/incidents", data=form).json()["incident_id"]
    assert c.patch(f"/api/incidents/{iid}/status", json={"status": "Verified"}, headers=admin_h()).status_code == 200
    assert c.patch(f"/api/incidents/{iid}/status", json={"status": "Bogus"}, headers=admin_h()).status_code == 422
    g = c.get(f"/api/incidents/{iid}").json()
    assert g["status"] == "Verified" and len(g["history"]) == 2 and "user_id" not in g and g["latitude"] == 12.97


def test_notification_failure_recorded():
    iid = c.post("/api/incidents", data=form).json()["incident_id"]
    n = c.get(f"/api/incidents/{iid}/notifications", headers=admin_h()).json()
    assert {x["delivery_status"] for x in n} == {"Sent", "Failed"}


def test_public_stats_and_map():
    for _ in range(2): c.post("/api/incidents", data=form)
    s = c.get("/api/public/stats").json()
    assert s["total"] >= 2 and s["last24h"] >= 2
    m = c.get("/api/public/map").json()
    assert m["points"] and "description" not in m["points"][0]
    assert any(h["count"] >= 2 for h in m["hotspots"])


def test_admin_stats_by_day():
    assert len(c.get("/api/stats", headers=admin_h()).json()["by_day"]) == 7


def test_responder_flow():
    body = {"name": "Station 1", "email": "p@x.com", "password": "police123", "role": "police"}
    assert c.post("/api/admin/responders", json=body, headers=admin_h()).status_code == 200
    t = c.post("/api/auth/login", json={"email": "p@x.com", "password": "police123"}).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    iid = c.post("/api/incidents", data=form).json()["incident_id"]
    assert c.get("/api/incidents", headers=h).status_code == 200
    assert c.patch(f"/api/incidents/{iid}/status", json={"status": "Responding", "note": "Unit 4 dispatched"}, headers=h).status_code == 200
    assert c.patch(f"/api/incidents/{iid}/status", json={"status": "Rejected"}, headers=h).status_code == 403
    pub = c.get(f"/api/incidents/{iid}").json()["history"][-1]
    assert pub["by"] == "police" and "note" not in pub
    assert c.get(f"/api/incidents/{iid}", headers=h).json()["history"][-1]["note"] == "Unit 4 dispatched"
    assert c.post("/api/admin/responders", json=body, headers=h).status_code == 403


def test_priority_sorting():
    c.post("/api/incidents", data={**form, "severity": "Critical"})
    rows = c.get("/api/incidents", headers=admin_h()).json()
    assert rows[0]["priority"] == "High"


def test_ai_feedback_and_metrics():
    import main
    iid = c.post("/api/incidents", data=form).json()["incident_id"]
    cx = main.db(); cx.execute("UPDATE incidents SET ai_prediction='accident-related', ai_confidence=0.9 WHERE incident_id=?", (iid,)); cx.commit(); cx.close()
    assert c.post(f"/api/incidents/{iid}/ai-feedback", json={"correct": False}, headers=admin_h()).status_code == 200
    m = c.get("/api/ai-metrics", headers=admin_h()).json()
    assert m["fp"] >= 1 and m["precision"] is not None


def test_my_reports_and_csv():
    c.post("/api/auth/register", json={"email": "cit@x.com", "password": "secret123", "name": "Cit"})
    t = c.post("/api/auth/login", json={"email": "cit@x.com", "password": "secret123"}).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    c.post("/api/incidents", data=form, headers=h)
    assert len(c.get("/api/my/incidents", headers=h).json()) == 1
    assert c.get("/api/export.csv", headers=h).status_code == 403
    r = c.get("/api/export.csv", headers=admin_h())
    assert r.status_code == 200 and r.text.startswith("incident_id")


def test_register_cannot_elevate_role():
    r = c.post("/api/auth/register", json={"email": "sneak@x.com", "password": "password123", "name": "Sneak", "role": "admin"})
    assert r.status_code == 200
    log = c.post("/api/auth/login", json={"email": "sneak@x.com", "password": "password123"}).json()
    assert log["role"] == "citizen"
    t = log["token"]
    assert c.get("/api/incidents", headers={"Authorization": f"Bearer {t}"}).status_code == 403


def test_admin_responders_security():
    staff_body = {"name": "Police 2", "email": "p2@x.com", "password": "password123", "role": "police"}
    assert c.post("/api/admin/responders", json=staff_body).status_code == 401
    c.post("/api/auth/register", json={"email": "cit2@x.com", "password": "password123"})
    cit_tok = c.post("/api/auth/login", json={"email": "cit2@x.com", "password": "password123"}).json()["token"]
    assert c.post("/api/admin/responders", json=staff_body, headers={"Authorization": f"Bearer {cit_tok}"}).status_code == 403
    assert c.post("/api/admin/responders", json=staff_body, headers=admin_h()).status_code == 200
    pol_log = c.post("/api/auth/login", json={"email": "p2@x.com", "password": "password123"}).json()
    assert pol_log["role"] == "police"


def test_password_min_length_8():
    assert c.post("/api/auth/register", json={"email": "short@x.com", "password": "short"}).status_code == 422
    assert c.post("/api/auth/register", json={"email": "seven12@x.com", "password": "1234567"}).status_code == 422
    assert c.post("/api/admin/responders", json={"name": "H", "email": "h@x.com", "password": "short", "role": "hospital"}, headers=admin_h()).status_code == 422
    assert c.post("/api/admin/responders", json={"name": "H", "email": "h@x.com", "password": "1234567", "role": "hospital"}, headers=admin_h()).status_code == 422


def test_email_case_insensitivity():
    assert c.post("/api/auth/register", json={"email": "caseuser@x.com", "password": "password123"}).status_code == 200
    assert c.post("/api/auth/register", json={"email": "CaseUser@x.com", "password": "password123"}).status_code == 409
    assert c.post("/api/auth/login", json={"email": "CASEUSER@X.COM", "password": "password123"}).status_code == 200


def test_brute_force_lockout():
    test_email = "brute_target@example.com"
    c.post("/api/auth/register", json={"email": test_email, "password": "password123"})
    for _ in range(5):
        r = c.post("/api/auth/login", json={"email": test_email, "password": "wrongpassword"})
        assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    r6 = c.post("/api/auth/login", json={"email": test_email, "password": "wrongpassword"})
    assert r6.status_code == 429 and "Too many attempts" in r6.json()["detail"]


def test_auth_me():
    assert c.get("/api/auth/me").status_code == 401
    assert c.get("/api/auth/me", headers={"Authorization": "Bearer invalidtoken"}).status_code == 401
    c.post("/api/auth/register", json={"email": "me_test@x.com", "password": "password123", "name": "MeTest"})
    t = c.post("/api/auth/login", json={"email": "me_test@x.com", "password": "password123"}).json()["token"]
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 200
    assert r.json() == {"role": "citizen", "name": "MeTest"}
    r_admin = c.get("/api/auth/me", headers=admin_h())
    assert r_admin.status_code == 200 and r_admin.json()["role"] == "admin"


def test_public_map_excludes_resolved():
    loc_form = {"description": "Accident in spot A", "latitude": "13.010", "longitude": "77.610"}
    id1 = c.post("/api/incidents", data=loc_form).json()["incident_id"]
    id2 = c.post("/api/incidents", data=loc_form).json()["incident_id"]
    assert c.patch(f"/api/incidents/{id1}/status", json={"status": "Resolved"}, headers=admin_h()).status_code == 200
    m = c.get("/api/public/map").json()
    point_ids = [p["id"] for p in m["points"]]
    assert id1 not in point_ids
    assert id2 in point_ids
    matched_hotspots = [h for h in m["hotspots"] if h["lat"] == 13.01 and h["lng"] == 77.61]
    assert len(matched_hotspots) >= 1 and matched_hotspots[0]["count"] >= 2
