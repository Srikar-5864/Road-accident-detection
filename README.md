# RoadAlert - AI-Powered Road Accident Reporting & Alert System

FastAPI + SQLite backend, React frontend (no build step), CLIP/ResNet image check, OpenStreetMap maps. English, Hindi and Kannada for citizens.

## 1. Run on your laptop
```bash
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows PowerShell  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
pip install torch torchvision transformers   # only needed for the AI image check
python -m uvicorn main:app --reload
python seed_demo.py                   # optional, in a second terminal: adds demo incidents
```
Open http://localhost:8000 (internet needed for map tiles and the React/Leaflet scripts). API docs: /docs.
Admin login: `admin@example.com` / `admin123` (change with `ADMIN_EMAIL` / `ADMIN_PASSWORD` before first run).

## 2. User guide
**Citizens** - Home, Report (description, severity, location by GPS or map click, optional photo), Track (by incident ID), Map (incidents + hotspots), Safety (112/108/100 and first-aid steps). Register on the **Account** tab to keep reports under *My reports*. Use the language box (top right) for English / हिन्दी / ಕನ್ನಡ.
**Admin** - Account tab: charts, search, CSV export, create police/hospital accounts, all statuses (incl. Duplicate/Reject), notification log and retry.
**Police / hospital** - log in on the Account tab: a console of open incidents, **priority cases first**, photo, directions link, one-tap *Confirm / Responding / Resolved* with a note. They cannot set Duplicate/Reject.
**Priority** = open incident that is severity *Critical* or that the AI labels accident-related.
**AI feedback** - on each card staff press *AI was right / wrong*; the console then shows precision, recall and false-positive rate from real reviews (use these numbers in your report).

## 3. AI image check
Without torch the app still works ("AI unavailable - manual review"). With torch + transformers it uses zero-shot CLIP: "accident-related" only at >=80% accident score, 50-80% = "uncertain - needs human review" (tune in `backend/ai.py`). To use your own trained model see `backend/train.py`; put the result in `backend/models/accident_classifier.pt`. AI output is preliminary and never replaces human verification.

## 4. Put it online (live link)
1. Push this folder to a GitHub repository.
2. On render.com: New > Blueprint (or Web Service > Docker) and select the repo. `render.yaml` and `Dockerfile` are included; `ADMIN_PASSWORD` is generated (see the service's Environment tab).
3. Open the URL Render gives you; log in as `admin@example.com` with that password.
Notes: the free tier has no persistent disk (data resets on restart) and too little memory for torch, so the live demo runs with **AI unavailable**; use the laptop version to demo the AI. Run `python seed_demo.py` locally to show a populated map. Check the host's current free-tier limits.

## 5. Tests
`pip install pytest` then `python -m pytest tests` (12 tests: reports, validation, images, access control, statuses, notifications, privacy, priority, AI feedback, My reports, CSV).

## Safety notes & limits
Alerts are mock only (console + `notifications` table; an address containing "fail" simulates failure). The app never claims emergency services were contacted. Public tracking/map show rounded locations only. Login tokens are in memory (reset on restart); passwords are hashed (PBKDF2). Hindi/Kannada text should be reviewed by a native speaker before real use. For real deployment add HTTPS, rate limiting and a persistent database.
