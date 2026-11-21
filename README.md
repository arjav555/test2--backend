# FACEPULSE — AI Facial Recognition Attendance (Stage 1: Database + Backend + AI)

## 1. MySQL
```bash
mysql -u root -p < database/database.sql      # creates DB `facepulse`, tables, sample data
```
## 2. Backend
```bash
cd backend
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
export FACE_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
export DB_USER=root DB_PASSWORD=yourpassword SECRET_KEY=anything     # Windows: use `set`
python app.py        # first run downloads InsightFace buffalo_l (~280 MB) and seeds demo logins
```
Keep `FACE_KEY` the same between runs — it decrypts stored embeddings.

## Demo logins
admin / admin123 · faculty1 / faculty123 · 1ms24cs001 / student123 (any student USN, lowercase)

## How recognition works
- InsightFace ArcFace → 512-d embedding, stored **Fernet-encrypted** in `face_data` (no photos saved).
- Cosine similarity ≥ 0.45 → PRESENT (after liveness); 0.35–0.45 → PENDING_REVIEW for faculty; below → UNKNOWN.
- Liveness: head-pose (yaw/pitch) must vary across recent frames, so a static photo is not marked.
  (Basic check only; it will not stop video replay. State this in your viva.)
- `UNIQUE(student_id, session_id)` blocks duplicates.
- Thresholds tunable via `MATCH_THRESHOLD` / `REVIEW_THRESHOLD`.

## API (all JSON, cookie session)
POST /api/login · GET/POST/PUT/DELETE /api/students · /api/faculty · /api/subjects · POST /api/enroll/check · POST /api/enroll/<id>
POST /api/sessions · POST /api/sessions/<id>/recognize · GET .../status · POST .../stop
PUT /api/attendance · GET /api/attendance · GET /api/student/me · GET /api/analytics · GET /api/reports/export
