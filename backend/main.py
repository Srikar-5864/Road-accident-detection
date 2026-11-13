"""AI-Powered Road Accident Detection & Emergency Alert System - FastAPI MVP backend."""
import csv, hashlib, io, os, secrets, sqlite3, uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from PIL import Image

import ai

BASE = Path(__file__).parent
DB_PATH = os.getenv("DB_PATH", str(BASE / "app.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE / "uploads")); UPLOAD_DIR.mkdir(exist_ok=True)
FRONTEND = BASE.parent / "frontend"
STATUSES = ["Reported", "Verified", "Responding", "Resolved", "Duplicate", "Rejected"]
RECIPIENTS = [r for r in os.getenv("NOTIFY_RECIPIENTS", "responder-test@example.com").split(",") if r]
MAX_IMG = 5 * 1024 * 1024
RESPONDERS = ("admin", "police", "hospital")
if os.getenv("PRODUCTION") and not os.getenv("ADMIN_PASSWORD"):
    raise RuntimeError("Set ADMIN_PASSWORD when PRODUCTION is set")
RESP_STATUSES = ("Verified", "Responding", "Resolved")  # police/hospital may set only these
TOKENS: dict = {}  # token -> user (in-memory; fine for local MVP)
FAILED_LOGINS: dict = {}  # email -> list of failed attempt timestamps

app = FastAPI(title="Road Accident Reporting MVP")


def db():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
    return c


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()


def check_pw(pw, stored):
    return secrets.compare_digest(hash_pw(pw, stored.split("$")[0]), stored)


def now(): return datetime.utcnow().isoformat(timespec="seconds")


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE,
        password_hash TEXT, role TEXT DEFAULT 'citizen');
    CREATE TABLE IF NOT EXISTS incidents(incident_id TEXT PRIMARY KEY, user_id INTEGER, description TEXT,
        severity TEXT, latitude REAL, longitude REAL, image_path TEXT, ai_prediction TEXT, ai_confidence REAL,
        status TEXT DEFAULT 'Reported', created_at TEXT, reported_at TEXT);
    CREATE TABLE IF NOT EXISTS status_history(id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT, status TEXT, changed_at TEXT);
    CREATE TABLE IF NOT EXISTS notifications(notification_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT,
        recipient TEXT, channel TEXT, delivery_status TEXT, created_at TEXT);
    """)
    for tbl, col in (("status_history", "changed_by TEXT"), ("status_history", "note TEXT"), ("incidents", "ai_feedback TEXT")):
        try: c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col}")  # migrate older databases
        except sqlite3.OperationalError: pass
    if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
        c.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')",
                  ("Admin", admin_email, hash_pw(os.getenv("ADMIN_PASSWORD", "admin123"))))
    c.commit(); c.close()


init_db()


def current_user(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        return TOKENS.get(authorization[7:])
    return None


def require_admin(user=Depends(current_user)):
    if not user: raise HTTPException(401, "Login required")
    if user["role"] != "admin": raise HTTPException(403, "Admin only")
    return user


def require_responder(user=Depends(current_user)):
    if not user: raise HTTPException(401, "Login required")
    if user["role"] not in RESPONDERS: raise HTTPException(403, "Staff only")
    return user


# ---------- auth ----------
class Cred(BaseModel):
    email: str
    password: str
    name: str = ""
    model_config = {"extra": "ignore"}


@app.post("/api/auth/register")
def register(b: Cred):
    email = b.email.strip().lower()
    if len(b.password) < 8 or "@" not in email: raise HTTPException(422, "Invalid email or password too short")
    c = db()
    try:
        c.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'citizen')", (b.name.strip(), email, hash_pw(b.password)))
        c.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email already registered")
    finally:
        c.close()
    return {"message": "Registered"}


@app.post("/api/auth/login")
def login(b: Cred):
    email = b.email.strip().lower()
    now_dt = datetime.utcnow()
    cutoff = now_dt - timedelta(minutes=15)
    recent = [t for t in FAILED_LOGINS.get(email, []) if t > cutoff]
    FAILED_LOGINS[email] = recent
    if len(recent) >= 5:
        raise HTTPException(429, "Too many attempts, try again later")
    c = db(); u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone(); c.close()
    if not u or not check_pw(b.password, u["password_hash"]):
        FAILED_LOGINS.setdefault(email, []).append(now_dt)
        raise HTTPException(401, "Invalid credentials")
    FAILED_LOGINS.pop(email, None)
    t = secrets.token_urlsafe(24)
    TOKENS[t] = {"user_id": u["user_id"], "role": u["role"], "name": u["name"]}
    return {"token": t, "role": u["role"], "name": u["name"]}


@app.get("/api/auth/me")
def auth_me(user=Depends(current_user)):
    if not user: raise HTTPException(401, "Login required")
    return {"role": user["role"], "name": user["name"]}


# ---------- helpers ----------
def notify(c, iid, desc, lat, lng):
    """Mock alerts. A recipient containing 'fail' simulates a failed delivery (recorded honestly)."""
    link = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=17/{lat}/{lng}"
    print(f"[TEST ALERT] {iid}: {desc[:80]} | {link}")
    for r in RECIPIENTS:
        c.execute("INSERT INTO notifications(incident_id,recipient,channel,delivery_status,created_at) VALUES(?,?,?,?,?)",
                  (iid, r, "test", "Failed" if "fail" in r else "Sent", now()))


def view(row, admin):
    d = dict(row)
    if not admin:  # privacy: coarse location, no reporter id / file path for the public
        d["latitude"], d["longitude"] = round(d["latitude"], 2), round(d["longitude"], 2)
        d.pop("user_id", None); d.pop("image_path", None); d.pop("ai_feedback", None)
    return d


def run_ai(path):
    return ai.classify(path) or (None, None)


# ---------- incidents ----------
@app.post("/api/incidents")
async def create_incident(description: str = Form(...), latitude: float = Form(...), longitude: float = Form(...),
                          severity: str = Form("Unknown"), reported_at: str = Form(""),
                          image: Optional[UploadFile] = File(None), user=Depends(current_user)):
    if not (5 <= len(description.strip()) <= 2000): raise HTTPException(422, "Description must be 5-2000 characters")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180): raise HTTPException(422, "Invalid coordinates")
    path = None
    if image and image.filename:
        data = await image.read()
        if len(data) > MAX_IMG: raise HTTPException(413, "Image larger than 5 MB")
        try:
            im = Image.open(io.BytesIO(data)); fmt = im.format; im.verify()
        except Exception:
            raise HTTPException(422, "File is not a valid image")
        if fmt not in ("JPEG", "PNG", "WEBP"): raise HTTPException(422, "Only JPEG, PNG or WEBP allowed")
        path = str(UPLOAD_DIR / f"{uuid.uuid4().hex}.{fmt.lower()}")
        Path(path).write_bytes(data)
    label, conf = run_ai(path) if path else (None, None)
    c = db()
    iid = f"INC-{1001 + c.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]}"
    c.execute("INSERT INTO incidents(incident_id,user_id,description,severity,latitude,longitude,image_path,ai_prediction,ai_confidence,status,created_at,reported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (iid, user["user_id"] if user else None, description.strip(), severity, latitude, longitude, path,
               label, conf, "Reported", now(), reported_at or now()))
    c.execute("INSERT INTO status_history(incident_id,status,changed_at) VALUES(?,?,?)", (iid, "Reported", now()))
    notify(c, iid, description, latitude, longitude)
    c.commit(); c.close()
    return {"incident_id": iid, "status": "Reported", "ai_prediction": label, "ai_confidence": conf,
            "ai_note": ("Preliminary AI result - needs human verification." if label else
                        ("AI unavailable - report saved for manual review." if path else "No image supplied.")),
            "message": "Incident report created. In a real emergency, call your local emergency number immediately."}


@app.get("/api/incidents")
def list_incidents(status: Optional[str] = None, user=Depends(require_responder)):
    c = db()
    q, a = "SELECT * FROM incidents", ()
    if status: q += " WHERE status=?"; a = (status,)
    rows = [dict(r) for r in c.execute(q + " ORDER BY created_at DESC, incident_id DESC", a)]; c.close()
    for r in rows:  # priority: open incident that is Critical or that the AI flags as accident-related
        open_ = r["status"] in ("Reported", "Verified", "Responding")
        r["priority"] = "High" if open_ and (r["severity"] == "Critical" or r["ai_prediction"] == ai.CLASSES[1]) else "Normal"
    rows.sort(key=lambda r: r["priority"] != "High")  # stable sort: newest first inside each group
    return rows


@app.get("/api/incidents/{iid}")
def get_incident(iid: str, user=Depends(current_user)):
    c = db(); r = c.execute("SELECT * FROM incidents WHERE incident_id=?", (iid,)).fetchone()
    if not r: raise HTTPException(404, "Incident not found")
    staff = bool(user and user["role"] in RESPONDERS)
    hist = [{"status": h["status"], "changed_at": h["changed_at"], "by": h["changed_by"], **({"note": h["note"]} if staff else {})}
            for h in c.execute("SELECT status,changed_at,changed_by,note FROM status_history WHERE incident_id=? ORDER BY id", (iid,))]
    c.close()
    d = view(r, staff); d["history"] = hist
    return d


class StatusBody(BaseModel):
    status: str
    note: str = ""


@app.patch("/api/incidents/{iid}/status")
def set_status(iid: str, b: StatusBody, user=Depends(require_responder)):
    if b.status not in STATUSES: raise HTTPException(422, f"Status must be one of {STATUSES}")
    if user["role"] != "admin" and b.status not in RESP_STATUSES:
        raise HTTPException(403, "Police/hospital accounts can set Verified, Responding or Resolved only")
    c = db()
    if not c.execute("SELECT 1 FROM incidents WHERE incident_id=?", (iid,)).fetchone(): raise HTTPException(404, "Incident not found")
    c.execute("UPDATE incidents SET status=? WHERE incident_id=?", (b.status, iid))
    c.execute("INSERT INTO status_history(incident_id,status,changed_at,changed_by,note) VALUES(?,?,?,?,?)",
              (iid, b.status, now(), user["role"], b.note.strip()[:300]))
    c.commit(); c.close()
    return {"incident_id": iid, "status": b.status}


@app.post("/api/incidents/{iid}/image-analysis")
def reanalyze(iid: str, admin=Depends(require_admin)):
    c = db(); r = c.execute("SELECT image_path FROM incidents WHERE incident_id=?", (iid,)).fetchone()
    if not r: raise HTTPException(404, "Incident not found")
    if not r["image_path"]: raise HTTPException(422, "Incident has no image")
    label, conf = run_ai(r["image_path"])
    if label is None: raise HTTPException(503, "AI model unavailable; report remains valid for manual review")
    c.execute("UPDATE incidents SET ai_prediction=?, ai_confidence=? WHERE incident_id=?", (label, conf, iid))
    c.commit(); c.close()
    return {"ai_prediction": label, "ai_confidence": conf, "note": "Preliminary result - requires human verification"}


@app.get("/api/incidents/{iid}/notifications")
def get_notifications(iid: str, admin=Depends(require_admin)):
    c = db(); rows = [dict(r) for r in c.execute("SELECT * FROM notifications WHERE incident_id=?", (iid,))]; c.close()
    return rows


@app.post("/api/incidents/{iid}/notifications/retry")
def retry_notifications(iid: str, admin=Depends(require_admin)):
    c = db()
    if not c.execute("SELECT 1 FROM incidents WHERE incident_id=?", (iid,)).fetchone(): raise HTTPException(404, "Incident not found")
    failed = [r["recipient"] for r in c.execute("SELECT recipient FROM notifications WHERE incident_id=? AND delivery_status='Failed'", (iid,))]
    for rec in failed:  # mock: 'fail' recipients keep failing so the failure stays visible
        c.execute("INSERT INTO notifications(incident_id,recipient,channel,delivery_status,created_at) VALUES(?,?,?,?,?)",
                  (iid, rec, "test", "Failed" if "fail" in rec else "Sent", now()))
    c.commit(); c.close()
    return {"message": f"Retried {len(failed)} failed notification(s)"}


@app.get("/api/stats")
def stats(user=Depends(require_responder)):
    c = db()
    out = {s: c.execute("SELECT COUNT(*) FROM incidents WHERE status=?", (s,)).fetchone()[0] for s in STATUSES}
    out["total"] = c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    days = [(datetime.utcnow() - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
    out["by_day"] = [{"day": d, "count": c.execute("SELECT COUNT(*) FROM incidents WHERE substr(created_at,1,10)=?", (d,)).fetchone()[0]} for d in days]
    c.close()
    return out


@app.get("/api/incidents/{iid}/image")
def get_image(iid: str, user=Depends(require_responder)):
    c = db(); r = c.execute("SELECT image_path FROM incidents WHERE incident_id=?", (iid,)).fetchone(); c.close()
    if not r or not r["image_path"]: raise HTTPException(404, "No image")
    return FileResponse(r["image_path"])


class Staff(BaseModel):
    name: str
    email: str
    password: str
    role: str


@app.post("/api/admin/responders")
def add_responder(b: Staff, admin=Depends(require_admin)):
    if b.role not in ("police", "hospital"): raise HTTPException(422, "Role must be police or hospital")
    email = b.email.strip().lower()
    if len(b.password) < 8 or "@" not in email: raise HTTPException(422, "Invalid email or password too short")
    c = db()
    try:
        c.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)", (b.name.strip(), email, hash_pw(b.password), b.role))
        c.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email already registered")
    finally:
        c.close()
    return {"message": f"{b.role} account created for {email}"}


class FB(BaseModel):
    correct: bool


@app.post("/api/incidents/{iid}/ai-feedback")
def ai_feedback(iid: str, b: FB, user=Depends(require_responder)):
    c = db(); r = c.execute("SELECT ai_prediction FROM incidents WHERE incident_id=?", (iid,)).fetchone()
    if not r: raise HTTPException(404, "Incident not found")
    if not r["ai_prediction"]: raise HTTPException(422, "No AI prediction to review")
    c.execute("UPDATE incidents SET ai_feedback=? WHERE incident_id=?", ("correct" if b.correct else "wrong", iid))
    c.commit(); c.close()
    return {"incident_id": iid, "ai_feedback": "correct" if b.correct else "wrong"}


@app.get("/api/ai-metrics")
def ai_metrics(user=Depends(require_responder)):
    """Precision/recall from staff reviews. 'uncertain' predictions are skipped."""
    c = db()
    rows = c.execute("SELECT ai_prediction, ai_feedback FROM incidents WHERE ai_feedback IS NOT NULL AND ai_prediction IN (?,?)",
                     (ai.CLASSES[0], ai.CLASSES[1])).fetchall(); c.close()
    tp = fp = fn = tn = 0
    for r in rows:
        pred = r["ai_prediction"] == ai.CLASSES[1]
        truth = pred if r["ai_feedback"] == "correct" else not pred
        if pred and truth: tp += 1
        elif pred: fp += 1
        elif truth: fn += 1
        else: tn += 1
    d = lambda a, b: round(a / b, 3) if b else None
    return {"reviewed": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": d(tp, tp + fp), "recall": d(tp, tp + fn), "false_positive_rate": d(fp, fp + tn)}


@app.get("/api/my/incidents")
def my_incidents(user=Depends(current_user)):
    if not user: raise HTTPException(401, "Login required")
    c = db()
    rows = [dict(r) for r in c.execute("SELECT incident_id, description, severity, status, created_at FROM incidents "
                                       "WHERE user_id=? ORDER BY created_at DESC, incident_id DESC", (user["user_id"],))]
    c.close()
    return rows


@app.get("/api/export.csv")
def export_csv(admin=Depends(require_admin)):
    cols = ["incident_id", "created_at", "status", "severity", "latitude", "longitude", "ai_prediction", "ai_confidence", "ai_feedback", "description"]
    c = db(); rows = c.execute(f"SELECT {','.join(cols)} FROM incidents ORDER BY created_at").fetchall(); c.close()
    safe = lambda v: ("'" + v) if v[:1] in ("=", "+", "-", "@") else v  # block spreadsheet formula injection
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
    for r in rows:
        w.writerow([safe(str(r[k])) if k == "description" else ("" if r[k] is None else r[k]) for k in cols])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=incidents.csv"})


@app.get("/api/public/stats")
def public_stats():
    c = db(); rows = c.execute("SELECT status, created_at FROM incidents").fetchall(); c.close()
    cut = (datetime.utcnow() - timedelta(hours=24)).isoformat(timespec="seconds")
    ok = [r for r in rows if r["status"] not in ("Duplicate", "Rejected")]
    return {"total": len(ok), "resolved": sum(r["status"] == "Resolved" for r in ok),
            "active": sum(r["status"] in ("Reported", "Verified", "Responding") for r in ok),
            "last24h": sum(r["created_at"] >= cut for r in ok)}


@app.get("/api/public/map")
def public_map():
    """Public map data: coordinates rounded (~110 m), no descriptions, images or reporter info.
    Hotspots = ~1 km grid cells holding 2+ reports."""
    c = db()
    rows = c.execute("SELECT incident_id, latitude, longitude, status, created_at FROM incidents "
                     "WHERE status NOT IN ('Duplicate','Rejected')").fetchall(); c.close()
    grid = {}
    for r in rows:
        grid.setdefault((round(r["latitude"], 2), round(r["longitude"], 2)), []).append(r)
    hot = [{"lat": k[0], "lng": k[1], "count": len(v)} for k, v in grid.items() if len(v) >= 2]
    pts = [{"id": r["incident_id"], "lat": round(r["latitude"], 3), "lng": round(r["longitude"], 3),
            "status": r["status"], "at": r["created_at"]} for r in rows if r["status"] != "Resolved"]
    return {"points": pts, "hotspots": sorted(hot, key=lambda h: -h["count"])}


@app.get("/")
def index(): return FileResponse(FRONTEND / "index.html")
