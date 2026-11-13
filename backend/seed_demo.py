"""Optional: load demo incidents so the map, hotspots and charts are not empty.  Run:  python seed_demo.py"""
from datetime import datetime, timedelta
from main import db, init_db

init_db()
DEMO = [  # (description, severity, lat, lng, status, hours_ago)
    ("[DEMO] Two-wheeler skid at signal", "Minor", 12.9352, 77.6245, "Resolved", 70),
    ("[DEMO] Car and auto collision", "Serious", 12.9358, 77.6251, "Responding", 5),
    ("[DEMO] Rear-end collision, traffic blocked", "Serious", 12.9349, 77.6240, "Verified", 3),
    ("[DEMO] Bus and car collision", "Critical", 12.9755, 77.6068, "Reported", 1),
    ("[DEMO] Pedestrian hit near crossing", "Critical", 12.9760, 77.6075, "Responding", 2),
    ("[DEMO] Minor bumper collision", "Minor", 12.9166, 77.6101, "Resolved", 30),
    ("[DEMO] Truck breakdown and collision", "Serious", 13.0358, 77.5970, "Resolved", 50),
    ("[DEMO] Bike fall on flyover", "Minor", 12.9250, 77.5938, "Resolved", 20),
]
c = db()
n = c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
for i, (d, sev, la, lo, st, h) in enumerate(DEMO):
    iid, ts = f"INC-{1001 + n + i}", (datetime.utcnow() - timedelta(hours=h)).isoformat(timespec="seconds")
    c.execute("INSERT INTO incidents(incident_id,description,severity,latitude,longitude,status,created_at,reported_at) VALUES(?,?,?,?,?,?,?,?)",
              (iid, d, sev, la, lo, st, ts, ts))
    c.execute("INSERT INTO status_history(incident_id,status,changed_at,changed_by) VALUES(?,?,?,?)", (iid, "Reported", ts, None))
    if st != "Reported":
        c.execute("INSERT INTO status_history(incident_id,status,changed_at,changed_by) VALUES(?,?,?,?)", (iid, st, ts, "police"))
c.commit(); c.close()
print(len(DEMO), "demo incidents added")
