import csv, io, os
import numpy as np
from functools import wraps
from flask import Flask, request, jsonify, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
from database import q, one, execute
import face_recognition as fr
import attendance as att

app = Flask(__name__, static_folder="../frontend", static_url_path="")
app.secret_key = os.getenv("SECRET_KEY", "change-me")
app.json.default = str  # dates/times -> strings

# ---------- helpers ----------
def api(f):
    @wraps(f)
    def w(*a, **k):
        try: return f(*a, **k)
        except KeyError as e: return jsonify(error=f"Missing field: {e}"), 400
        except ValueError as e: return jsonify(error=str(e)), 400
        except Exception as e: return jsonify(error=f"{type(e).__name__}: {e}"), 500
    return w

def need(*roles):
    def d(f):
        @wraps(f)
        def w(*a, **k):
            if "role" not in session: return jsonify(error="Login required"), 401
            if session["role"] not in roles: return jsonify(error="Forbidden"), 403
            return f(*a, **k)
        return w
    return d

def seed_users():
    if one("SELECT 1 x FROM users LIMIT 1"): return
    execute("INSERT INTO users (username,password_hash,role,ref_id) VALUES ('admin',%s,'ADMIN',NULL)", (generate_password_hash("admin123"),))
    execute("INSERT INTO users (username,password_hash,role,ref_id) VALUES ('faculty1',%s,'FACULTY',1)", (generate_password_hash("faculty123"),))
    for s in q("SELECT student_id, usn FROM students"):
        execute("INSERT INTO users (username,password_hash,role,ref_id) VALUES (%s,%s,'STUDENT',%s)",
                (s["usn"].lower(), generate_password_hash("student123"), s["student_id"]))

# ---------- auth ----------
@app.post("/api/login")
@api
def login():
    d = request.json; u = one("SELECT * FROM users WHERE username=%s", (d["username"].strip().lower(),))
    if not u or not check_password_hash(u["password_hash"], d["password"]): return jsonify(error="Invalid credentials"), 401
    session.update(role=u["role"], ref_id=u["ref_id"], username=u["username"])
    return jsonify(role=u["role"], ref_id=u["ref_id"], username=u["username"])

@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)

@app.get("/api/me")
def me(): return jsonify(**{k: session.get(k) for k in ("role", "ref_id", "username")})

# ---------- students (CRUD) ----------
@app.get("/api/students")
@need("ADMIN", "FACULTY")
@api
def students():
    s = f"%{request.args.get('search', '')}%"
    return jsonify(q("""SELECT s.*, COUNT(f.face_id)>0 AS face_enrolled FROM students s
        LEFT JOIN face_data f ON f.student_id=s.student_id
        WHERE s.name LIKE %s OR s.usn LIKE %s GROUP BY s.student_id ORDER BY s.usn""", (s, s)))

@app.post("/api/students")
@need("ADMIN")
@api
def add_student():
    d = request.json
    sid, _ = execute("INSERT INTO students (usn,name,email,department,semester) VALUES (%s,%s,%s,%s,%s)",
                     (d["usn"].upper(), d["name"], d["email"], d["department"], d.get("semester", 1)))
    execute("INSERT INTO users (username,password_hash,role,ref_id) VALUES (%s,%s,'STUDENT',%s)",
            (d["usn"].lower(), generate_password_hash(d.get("password", "student123")), sid))
    return jsonify(student_id=sid), 201

@app.put("/api/students/<int:sid>")
@need("ADMIN")
@api
def edit_student(sid):
    d = request.json
    execute("UPDATE students SET name=%s,email=%s,department=%s,semester=%s WHERE student_id=%s",
            (d["name"], d["email"], d["department"], d["semester"], sid)); return jsonify(ok=True)

@app.delete("/api/students/<int:sid>")
@need("ADMIN")
@api
def del_student(sid):
    execute("DELETE FROM users WHERE role='STUDENT' AND ref_id=%s", (sid,))
    execute("DELETE FROM students WHERE student_id=%s", (sid,)); fr.reload_index(); return jsonify(ok=True)

# ---------- faculty & subjects ----------
@app.get("/api/faculty")
@need("ADMIN")
def faculty_list(): return jsonify(q("SELECT * FROM faculty"))

@app.post("/api/faculty")
@need("ADMIN")
@api
def add_faculty():
    d = request.json
    fid, _ = execute("INSERT INTO faculty (name,email,department) VALUES (%s,%s,%s)", (d["name"], d["email"], d["department"]))
    execute("INSERT INTO users (username,password_hash,role,ref_id) VALUES (%s,%s,'FACULTY',%s)",
            (d["email"].split("@")[0].lower(), generate_password_hash(d.get("password", "faculty123")), fid))
    return jsonify(faculty_id=fid), 201

@app.delete("/api/faculty/<int:fid>")
@need("ADMIN")
@api
def del_faculty(fid):
    execute("DELETE FROM users WHERE role='FACULTY' AND ref_id=%s", (fid,))
    execute("DELETE FROM faculty WHERE faculty_id=%s", (fid,)); return jsonify(ok=True)

@app.get("/api/subjects")
@need("ADMIN", "FACULTY")
def subjects():
    sql = """SELECT sb.*, f.name AS faculty_name, COUNT(e.student_id) AS enrolled FROM subjects sb
             JOIN faculty f ON f.faculty_id=sb.faculty_id LEFT JOIN enrollments e ON e.subject_id=sb.subject_id"""
    if session["role"] == "FACULTY": return jsonify(q(sql + " WHERE sb.faculty_id=%s GROUP BY sb.subject_id", (session["ref_id"],)))
    return jsonify(q(sql + " GROUP BY sb.subject_id"))

@app.post("/api/subjects")
@need("ADMIN")
@api
def add_subject():
    d = request.json
    sid, _ = execute("INSERT INTO subjects (subject_code,subject_name,faculty_id) VALUES (%s,%s,%s)", (d["subject_code"], d["subject_name"], d["faculty_id"]))
    return jsonify(subject_id=sid), 201

@app.post("/api/subjects/<int:sid>/enroll")
@need("ADMIN")
@api
def enroll_students(sid):
    for st in request.json["student_ids"]: execute("INSERT IGNORE INTO enrollments (student_id,subject_id) VALUES (%s,%s)", (st, sid))
    return jsonify(ok=True)

# ---------- face enrollment ----------
@app.post("/api/enroll/<int:sid>")
@need("ADMIN", "STUDENT")
@api
def enroll(sid):
    """Body: {consent:true, frames:[base64 jpeg,...]} (>=5 frames: straight/left/right). Raw images are never saved."""
    if session["role"] == "STUDENT" and session["ref_id"] != sid: return jsonify(error="Forbidden"), 403
    d = request.json
    if not d.get("consent"): raise ValueError("Biometric consent is required")
    embs, report = [], []
    for fb in d["frames"]:
        img = fr.decode(fb); faces = fr.detect(img)
        if len(faces) != 1: report.append({"ok": False, "reason": f"{len(faces)} faces"}); continue
        qlt = fr.quality(faces[0], img); report.append(qlt)
        if qlt["ok"]: embs.append(faces[0].normed_embedding)
    if len(embs) < 5: return jsonify(error="Need at least 5 good-quality samples", report=report), 422
    fr.save_embeddings(sid, embs)
    return jsonify(enrolled=True, samples=len(embs), model=fr.MODEL_VERSION, report=report)

@app.post("/api/enroll/check")
@need("ADMIN", "STUDENT")
@api
def check_frame():
    """Live guidance: face present? centered? quality? pose (pitch/yaw)."""
    img = fr.decode(request.json["frame"]); faces = fr.detect(img)
    if len(faces) != 1: return jsonify(detected=False, faces=len(faces))
    f = faces[0]; h, w = img.shape[:2]; cx = (f.bbox[0] + f.bbox[2]) / 2 / w
    p = fr.pose(f)
    return jsonify(detected=True, bbox=[float(v) for v in f.bbox], centered=0.3 < cx < 0.7,
                   pitch=p[0] if p else None, yaw=p[1] if p else None, quality=fr.quality(f, img))

# ---------- live attendance ----------
@app.post("/api/sessions")
@need("FACULTY")
@api
def start_session():
    sub = one("SELECT * FROM subjects WHERE subject_id=%s AND faculty_id=%s", (request.json["subject_id"], session["ref_id"]))
    if not sub: return jsonify(error="Not your subject"), 403
    sid, _ = execute("INSERT INTO attendance_sessions (subject_id,faculty_id,session_date,start_time) VALUES (%s,%s,CURDATE(),CURTIME())",
                     (sub["subject_id"], session["ref_id"]))
    return jsonify(session_id=sid), 201

@app.post("/api/sessions/<int:sid>/recognize")
@need("FACULTY")
@api
def recognize(sid):
    """Body: {frame: base64}. Returns one result per face: recognized | uncertain | needs_liveness | unknown."""
    ses = one("SELECT * FROM attendance_sessions WHERE session_id=%s AND session_status='ACTIVE'", (sid,))
    if not ses: return jsonify(error="Session not active"), 404
    img = fr.decode(request.json["frame"]); out = []; unk = 0
    for f in fr.detect(img):
        box = [float(v) for v in f.bbox]; stid, sim = fr.match(f.normed_embedding)
        stu = one("""SELECT s.student_id, s.usn, s.name FROM students s JOIN enrollments e ON e.student_id=s.student_id
                     WHERE s.student_id=%s AND e.subject_id=%s""", (stid, ses["subject_id"])) if stid else None
        if not stu or sim < fr.REVIEW_T:
            unk += 1; out.append(dict(bbox=box, status="unknown", label="UNKNOWN PERSON", similarity=round(sim, 3))); continue
        base = dict(bbox=box, student_id=stu["student_id"], name=stu["name"], usn=stu["usn"], similarity=round(sim, 3))
        if sim < fr.MATCH_T:
            att.mark(stu["student_id"], sid, "PENDING_REVIEW", sim); out.append(dict(base, status="uncertain", label="REVIEW NEEDED")); continue
        if not fr.liveness(sid, stu["student_id"], f):
            out.append(dict(base, status="needs_liveness", label="MOVE YOUR HEAD")); continue
        new = att.mark(stu["student_id"], sid, "PRESENT", sim)
        out.append(dict(base, status="recognized", label="PRESENT", new=new))
    return jsonify(faces=out, unknown=unk)

@app.get("/api/sessions/<int:sid>/status")
@need("FACULTY", "ADMIN")
@api
def session_status(sid):
    rows = q("""SELECT s.student_id, s.usn, s.name, a.attendance_status, a.recognition_confidence, a.recognition_timestamp
                FROM attendance_sessions x JOIN enrollments e ON e.subject_id=x.subject_id JOIN students s ON s.student_id=e.student_id
                LEFT JOIN attendance a ON a.student_id=s.student_id AND a.session_id=x.session_id
                WHERE x.session_id=%s ORDER BY s.usn""", (sid,))
    pres = [r for r in rows if r["attendance_status"] == "PRESENT"]
    confs = [r["recognition_confidence"] for r in pres if r["recognition_confidence"]]
    return jsonify(students=rows, total=len(rows), present=len(pres),
                   pending=sum(r["attendance_status"] == "PENDING_REVIEW" for r in rows),
                   avg_similarity=round(sum(confs) / len(confs), 3) if confs else None)

@app.post("/api/sessions/<int:sid>/stop")
@need("FACULTY")
@api
def stop_session(sid): att.finalize(sid); fr.clear_liveness(sid); return jsonify(ok=True)

# ---------- attendance management ----------
@app.put("/api/attendance")
@need("ADMIN", "FACULTY")
@api
def set_attendance():
    """Manual correction / review of uncertain matches."""
    d = request.json
    if d["status"] not in ("PRESENT", "ABSENT"): raise ValueError("Invalid status")
    execute("""INSERT INTO attendance (student_id,session_id,attendance_status) VALUES (%s,%s,%s)
               ON DUPLICATE KEY UPDATE attendance_status=VALUES(attendance_status)""", (d["student_id"], d["session_id"], d["status"]))
    return jsonify(ok=True)

@app.delete("/api/attendance/<int:aid>")
@need("ADMIN")
@api
def del_attendance(aid): execute("DELETE FROM attendance WHERE attendance_id=%s", (aid,)); return jsonify(ok=True)

@app.get("/api/attendance")
@need("ADMIN", "FACULTY")
@api
def attendance_list():
    w, a = ["1=1"], []
    for k, col in (("date", "x.session_date"), ("subject_id", "x.subject_id"), ("student_id", "a.student_id")):
        if request.args.get(k): w.append(f"{col}=%s"); a.append(request.args[k])
    if session["role"] == "FACULTY": w.append("x.faculty_id=%s"); a.append(session["ref_id"])
    return jsonify(q(f"""SELECT a.attendance_id, s.usn, s.name, sb.subject_code, x.session_date, a.attendance_status, a.recognition_confidence
        FROM attendance a JOIN students s ON s.student_id=a.student_id JOIN attendance_sessions x ON x.session_id=a.session_id
        JOIN subjects sb ON sb.subject_id=x.subject_id WHERE {' AND '.join(w)} ORDER BY x.session_date DESC, s.usn""", a))

# ---------- student view ----------
@app.get("/api/student/me")
@need("STUDENT")
@api
def student_me():
    sid = session["ref_id"]
    return jsonify(profile=one("SELECT * FROM students WHERE student_id=%s", (sid,)), **att.student_summary(sid),
        history=q("""SELECT x.session_date, sb.subject_name, a.attendance_status FROM attendance a
            JOIN attendance_sessions x ON x.session_id=a.session_id JOIN subjects sb ON sb.subject_id=x.subject_id
            WHERE a.student_id=%s ORDER BY x.session_date DESC LIMIT 50""", (sid,)))

# ---------- analytics ----------
@app.get("/api/analytics")
@need("ADMIN", "FACULTY")
@api
def analytics():
    P = "SUM(a.attendance_status='PRESENT')"
    return jsonify(
        totals=one("""SELECT (SELECT COUNT(*) FROM students) students, (SELECT COUNT(*) FROM faculty) faculty,
            (SELECT COUNT(*) FROM subjects) subjects, (SELECT COUNT(*) FROM attendance_sessions) sessions"""),
        today_pct=one(f"""SELECT ROUND(100*{P}/COUNT(*),1) pct FROM attendance a JOIN attendance_sessions x
            ON x.session_id=a.session_id WHERE x.session_date=CURDATE()""")["pct"],
        daily=q(f"""SELECT x.session_date d, ROUND(100*{P}/COUNT(*),1) pct, {P} present, COUNT(*)-{P} absent
            FROM attendance a JOIN attendance_sessions x ON x.session_id=a.session_id GROUP BY x.session_date ORDER BY d"""),
        by_subject=q(f"""SELECT sb.subject_code, ROUND(100*{P}/COUNT(*),1) pct FROM attendance a
            JOIN attendance_sessions x ON x.session_id=a.session_id JOIN subjects sb ON sb.subject_id=x.subject_id GROUP BY sb.subject_id"""),
        monthly=q(f"""SELECT DATE_FORMAT(x.session_date,'%Y-%m') m, ROUND(100*{P}/COUNT(*),1) pct FROM attendance a
            JOIN attendance_sessions x ON x.session_id=a.session_id GROUP BY m ORDER BY m"""),
        below_75=q(f"""SELECT s.usn, s.name, ROUND(100*{P}/COUNT(*),1) pct FROM students s JOIN attendance a ON a.student_id=s.student_id
            GROUP BY s.student_id HAVING pct<75 ORDER BY pct"""),
        recent=q("""SELECT s.usn, s.name, a.recognition_timestamp, a.recognition_confidence FROM attendance a
            JOIN students s ON s.student_id=a.student_id WHERE a.recognition_confidence IS NOT NULL ORDER BY a.attendance_id DESC LIMIT 8"""))

@app.get("/api/reports/export")
@need("ADMIN", "FACULTY")
@api
def export_csv():
    rows = attendance_list().json; buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["usn", "name", "subject_code", "session_date", "attendance_status", "recognition_confidence"], extrasaction="ignore")
    w.writeheader(); w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=attendance.csv"})

@app.get("/")
def index(): return app.send_static_file("index.html")

if __name__ == "__main__":
    seed_users(); fr.reload_index()
    app.run(host="127.0.0.1", port=5000, debug=False)  # webcam needs localhost or HTTPS
