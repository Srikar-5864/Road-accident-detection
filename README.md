# AI-Powered Road Accident Detection & Emergency Alert System (MVP)

FastAPI + SQLite backend, React frontend (no build step), optional PyTorch image classifier, OpenStreetMap/Leaflet maps.

## Run
```bash
cd backend
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```
Open http://localhost:8000 (internet needed for the React/Leaflet CDN scripts and map tiles).
Interactive API docs: http://localhost:8000/docs

**Admin login (change it!):** `admin@example.com` / `admin123`, or set `ADMIN_EMAIL` / `ADMIN_PASSWORD` env vars before first run.

## Features (per project plan)
Home page with live stats · public incident map with hotspot areas · safety guide & emergency numbers · admin dashboard with charts, search and photo thumbnails.

Report form · browser geolocation + click-on-map fallback · image upload (validated type/size) · AI classification (optional) · incident IDs (INC-1001…) · tracking with status history · admin dashboard (filter, status changes incl. Duplicate/Rejected) · mock notifications with recorded delivery status and retry · privacy (public tracking shows rounded location, no reporter info).

## AI model
Without a trained model the app still works: image reports are saved and flagged "AI unavailable - manual review".
To enable it:
```bash
pip install torch torchvision
# arrange data/train/{accident,no_accident}/ and data/test/{accident,no_accident}/ (e.g. a Kaggle accident dataset)
python train.py --data data --epochs 5     # prints precision, recall, false-positive rate on the test set
```
This saves `backend/models/accident_classifier.pt`, loaded automatically on the next server start.
AI output is preliminary only; a prediction never counts as a verified emergency.

## Notifications
Mock only: alerts are printed to the server console and stored in the `notifications` table. Set
`NOTIFY_RECIPIENTS="a@test.com,fail@test.com"` - any address containing `fail` simulates a failed delivery so you can demo the failure/retry flow. The UI never claims emergency services were contacted.

## Tests
```bash
pip install pytest && python -m pytest tests
```
Covers: valid report, invalid input, valid/invalid image, AI unavailable, access control, status updates, notification failure.

## Layout
`backend/main.py` API + DB · `backend/ai.py` classifier wrapper · `backend/train.py` training/evaluation · `frontend/index.html` UI · `tests/` pytest.

## Known MVP limits
Login tokens are in-memory (reset on restart); SQLite; no real email/SMS (add SMTP in `notify()`); no hotspot analytics yet (coordinates are stored for it).
Use the project's safety note: in a real emergency contact local emergency services directly.
