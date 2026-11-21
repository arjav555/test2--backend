import os
import io
import csv
import traceback
from datetime import date, datetime
from functools import wraps
from flask import Flask, request, jsonify, session, Response
from flask_cors import CORS
from werkzeug.security import check_password_hash
from database import q, one, execute
import face_recognition as fr
import attendance as att
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "facepulse-change-this-secret")
FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "http://localhost:5173"
)
CORS(
    app,
    origins=FRONTEND_URL.split(","),
    supports_credentials=True
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    SESSION_COOKIE_SAMESITE=os.getenv("COOKIE_SAMESITE", "Lax"),
    PERMANENT_SESSION_LIFETIME=86400
)
def ok(data=None, message="Success", status=200):
    return jsonify({
        "success": True,
        "message": message,
        "data": data
    }), status
def fail(message, status=400):
    return jsonify({
        "success": False,
        "message": message
    }), status
def body():
    return request.get_json(silent=True) or {}
def current_user():
    return session.get("user")
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return fail("Please login", 401)
        return fn(*args, **kwargs)
    return wrapper
def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user()["role"] not in roles:
                return fail("Access denied", 403)
            return fn(*args, **kwargs)
        return decorator
    return decorator
def faculty_id():
    user = current_user()
    return user.get("ref_id") if user else None
def admin_or_faculty():
    return current_user()["role"] in ("ADMIN", "FACULTY")
# --------------------------------------------------
# HEALTH
# --------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    return ok({
        "application": "FacePulse",
        "status": "online"
    })
@app.route("/health", methods=["GET"])
def health():
    try:
        one("SELECT 1 AS connected")
        return ok({"database": "connected"})
    except Exception:
        return fail("Database connection failed", 503)
# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------
@app.route("/login", methods=["POST"])
def login():
    data = body()
    username = str(data.get("username") or data.get("email") or "").strip()
    password = data.get("password", "")
    if not username or not password:
        return fail("Username and password are required")
    user = one(
        """
        SELECT user_id, username, password_hash, role, ref_id
        FROM users
        WHERE username = %s
        """,
        (username,)
    )
    if not user:
        return fail("Invalid credentials", 401)
    try:
        valid = check_password_hash(
            user["password_hash"],
            password
        )
    except Exception:
        valid = False
    if not valid:
        return fail("Invalid credentials", 401)
    role = user["role"]
    profile = None
    if role == "FACULTY":
        profile = one(
            """
            SELECT faculty_id, name, email, department
            FROM faculty
            WHERE faculty_id = %s
            """,
            (user["ref_id"],)
        )
    elif role == "STUDENT":
        profile = one(
            """
            SELECT student_id, usn, name, email,
                   department, semester
            FROM students
            WHERE student_id = %s
            """,
            (user["ref_id"],)
        )
    session.clear()
    session.permanent = True
    session["user"] = {
        "user_id": user["user_id"],
        "username": user["username"],
        "role": role,
        "ref_id": user["ref_id"],
        "profile": profile
    }
    return ok(session["user"], "Login successful")
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return ok(message="Logged out")
@app.route("/me", methods=["GET"])
@login_required
def me():
    return ok(current_user())
# --------------------------------------------------
# STUDENTS
# --------------------------------------------------
@app.route("/students", methods=["GET"])
@roles_required("ADMIN", "FACULTY")
def students():
    result = q(
        """
        SELECT student_id, usn, name, email,
               department, semester, created_at
        FROM students
        ORDER BY student_id DESC
        """
    )
    return ok(result)
@app.route("/students", methods=["POST"])
@roles_required("ADMIN", "FACULTY")
def add_student():
    data = body()
    required = [
        "usn", "name", "email",
        "department", "semester"
    ]
    for field in required:
        if data.get(field) is None:
            return fail(f"{field} is required")
    execute(
        """
        INSERT INTO students
        (usn, name, email, department, semester)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            data["usn"],
            data["name"],
            data["email"],
            data["department"],
            data["semester"]
        )
    )
    return ok(message="Student created", status=201)
@app.route("/students/<int:sid>", methods=["GET"])
@login_required
def student_details(sid):
    if current_user()["role"] == "STUDENT":
        if current_user()["ref_id"] != sid:
            return fail("Access denied", 403)
    student = one(
        "SELECT * FROM students WHERE student_id = %s",
        (sid,)
    )
    if not student:
        return fail("Student not found", 404)
    return ok(student)
@app.route("/students/<int:sid>", methods=["DELETE"])
@roles_required("ADMIN")
def delete_student(sid):
    execute(
        "DELETE FROM students WHERE student_id = %s",
        (sid,)
    )
    return ok(message="Student deleted")
# --------------------------------------------------
# FACULTY
# --------------------------------------------------
@app.route("/faculty", methods=["GET"])
@roles_required("ADMIN", "FACULTY")
def get_faculty():
    return ok(q(
        """
        SELECT faculty_id, name, email, department
        FROM faculty
        ORDER BY name
        """
    ))
@app.route("/faculty", methods=["POST"])
@roles_required("ADMIN")
def add_faculty():
    data = body()
    if not all(data.get(k) for k in ("name", "email", "department")):
        return fail("Name, email and department are required")
    execute(
        """
        INSERT INTO faculty (name, email, department)
        VALUES (%s, %s, %s)
        """,
        (data["name"], data["email"], data["department"])
    )
    return ok(message="Faculty created", status=201)
# --------------------------------------------------
# SUBJECTS
# --------------------------------------------------
@app.route("/subjects", methods=["GET"])
@login_required
def get_subjects():
    return ok(q(
        """
        SELECT sb.subject_id, sb.subject_code,
               sb.subject_name, sb.faculty_id,
               f.name AS faculty_name
        FROM subjects sb
        JOIN faculty f ON f.faculty_id = sb.faculty_id
        ORDER BY sb.subject_code
        """
    ))
@app.route("/subjects", methods=["POST"])
@roles_required("ADMIN", "FACULTY")
def add_subject():
    data = body()
    code = data.get("subject_code")
    name = data.get("subject_name")
    fid = faculty_id() if current_user()["role"] == "FACULTY" else data.get("faculty_id")
    if not code or not name or not fid:
        return fail("Subject code, name and faculty are required")
    execute(
        """
        INSERT INTO subjects
        (subject_code, subject_name, faculty_id)
        VALUES (%s, %s, %s)
        """,
        (code, name, fid)
    )
    return ok(message="Subject created", status=201)
# --------------------------------------------------
# ENROLLMENTS
# --------------------------------------------------
@app.route("/enrollments", methods=["GET"])
@roles_required("ADMIN", "FACULTY")
def get_enrollments():
    return ok(q(
        """
        SELECT e.enrollment_id, e.student_id,
               e.subject_id, s.usn, s.name,
               sb.subject_code, sb.subject_name
        FROM enrollments e
        JOIN students s ON s.student_id = e.student_id
        JOIN subjects sb ON sb.subject_id = e.subject_id
        ORDER BY e.enrollment_id DESC
        """
    ))
@app.route("/enrollments", methods=["POST"])
@roles_required("ADMIN", "FACULTY")
def add_enrollment():
    data = body()
    if not data.get("student_id") or not data.get("subject_id"):
        return fail("Student ID and subject ID are required")
    execute(
        """
        INSERT INTO enrollments (student_id, subject_id)
        VALUES (%s, %s)
        """,
        (data["student_id"], data["subject_id"])
    )
    return ok(message="Enrollment created", status=201)
# --------------------------------------------------
# ATTENDANCE SESSIONS
# --------------------------------------------------
@app.route("/sessions", methods=["GET"])
@login_required
def get_sessions():
    if current_user()["role"] == "FACULTY":
        result = q(
            """
            SELECT x.*, sb.subject_name, sb.subject_code
            FROM attendance_sessions x
            JOIN subjects sb ON sb.subject_id = x.subject_id
            WHERE x.faculty_id = %s
            ORDER BY x.session_id DESC
            """,
            (faculty_id(),)
        )
    else:
        result = q(
            """
            SELECT x.*, sb.subject_name, sb.subject_code
            FROM attendance_sessions x
            JOIN subjects sb ON sb.subject_id = x.subject_id
            ORDER BY x.session_id DESC
            """
        )
    return ok(result)
@app.route("/sessions", methods=["POST"])
@roles_required("ADMIN", "FACULTY")
def create_session():
    data = body()
    subject_id = data.get("subject_id")
    start_time = data.get("start_time")
    if not subject_id or not start_time:
        return fail("Subject and start time are required")
    fid = faculty_id() if current_user()["role"] == "FACULTY" else data.get("faculty_id")
    subject = one(
        "SELECT faculty_id FROM subjects WHERE subject_id = %s",
        (subject_id,)
    )
    if not subject:
        return fail("Subject not found", 404)
    if current_user()["role"] == "FACULTY" and subject["faculty_id"] != fid:
        return fail("You cannot create a session for this subject", 403)
    if not fid:
        fid = subject["faculty_id"]
    execute(
        """
        INSERT INTO attendance_sessions
        (subject_id, faculty_id, session_date, start_time, session_status)
        VALUES (%s, %s, CURDATE(), %s, 'ACTIVE')
        """,
        (subject_id, fid, start_time)
    )
    return ok(message="Attendance session started", status=201)
@app.route("/sessions/<int:sid>", methods=["GET"])
@login_required
def session_details(sid):
    result = one(
        """
        SELECT x.*, sb.subject_name, sb.subject_code
        FROM attendance_sessions x
        JOIN subjects sb ON sb.subject_id = x.subject_id
        WHERE x.session_id = %s
        """,
        (sid,)
    )
    if not result:
        return fail("Session not found", 404)
    return ok(result)
@app.route("/sessions/<int:sid>/stop", methods=["POST"])
@roles_required("ADMIN", "FACULTY")
def stop_session(sid):
    result = one(
        "SELECT * FROM attendance_sessions WHERE session_id = %s",
        (sid,)
    )
    if not result:
        return fail("Session not found", 404)
    if current_user()["role"] == "FACULTY" and result["faculty_id"] != faculty_id():
        return fail("Access denied", 403)
    execute(
        """
        UPDATE attendance_sessions
        SET session_status = 'COMPLETED',
            end_time = CURTIME()
        WHERE session_id = %s
        """,
        (sid,)
    )
    return ok(message="Session completed")
# --------------------------------------------------
# ATTENDANCE RECORDS
# --------------------------------------------------
@app.route("/attendance", methods=["GET"])
@login_required
def get_attendance():
    if current_user()["role"] == "STUDENT":
        result = q(
            """
            SELECT a.*, s.usn, s.name,
                   x.session_date, sb.subject_name
            FROM attendance a
            JOIN students s ON s.student_id = a.student_id
            JOIN attendance_sessions x ON x.session_id = a.session_id
            JOIN subjects sb ON sb.subject_id = x.subject_id
            WHERE a.student_id = %s
            ORDER BY a.recognition_timestamp DESC
            """,
            (current_user()["ref_id"],)
        )
    else:
        result = q(
            """
            SELECT a.*, s.usn, s.name,
                   x.session_date, sb.subject_name
            FROM attendance a
            JOIN students s ON s.student_id = a.student_id
            JOIN attendance_sessions x ON x.session_id = a.session_id
            JOIN subjects sb ON sb.subject_id = x.subject_id
            ORDER BY a.recognition_timestamp DESC
            """
        )
    return ok(result)
@app.route("/attendance/mark", methods=["POST"])
@roles_required("ADMIN", "FACULTY")
def manual_attendance():
    data = body()
    student_id = data.get("student_id")
    session_id = data.get("session_id")
    status = str(data.get("attendance_status", "PRESENT")).upper()
    if not student_id or not session_id:
        return fail("Student and session are required")
    if status not in ("PRESENT", "ABSENT", "PENDING_REVIEW"):
        return fail("Invalid attendance status")
    execute(
        """
        INSERT INTO attendance
        (student_id, session_id, attendance_status)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
        attendance_status = VALUES(attendance_status)
        """,
        (student_id, session_id, status)
    )
    return ok(message="Attendance updated")
# --------------------------------------------------
# STUDENT ATTENDANCE SUMMARY
# --------------------------------------------------
@app.route("/students/<int:sid>/attendance", methods=["GET"])
@login_required
def student_attendance_summary(sid):
    if current_user()["role"] == "STUDENT" and current_user()["ref_id"] != sid:
        return fail("Access denied", 403)
    result = q(
        """
        SELECT sb.subject_id, sb.subject_code,
               sb.subject_name,
               COUNT(a.attendance_id) AS total_classes,
               SUM(a.attendance_status = 'PRESENT') AS present_classes,
               ROUND(
                   100 * SUM(a.attendance_status = 'PRESENT')
                   / NULLIF(COUNT(a.attendance_id), 0), 2
               ) AS percentage
        FROM subjects sb
        JOIN enrollments e ON e.subject_id = sb.subject_id
        LEFT JOIN attendance_sessions x ON x.subject_id = sb.subject_id
        LEFT JOIN attendance a
            ON a.session_id = x.session_id
            AND a.student_id = e.student_id
        WHERE e.student_id = %s
        GROUP BY sb.subject_id, sb.subject_code, sb.subject_name
        """,
        (sid,)
    )
    return ok(result)
# --------------------------------------------------
# ANALYTICS
# --------------------------------------------------
@app.route("/analytics", methods=["GET"])
@roles_required("ADMIN", "FACULTY")
def analytics():
    students = one("SELECT COUNT(*) AS total FROM students")
    faculty = one("SELECT COUNT(*) AS total FROM faculty")
    subjects = one("SELECT COUNT(*) AS total FROM subjects")
    sessions = one("SELECT COUNT(*) AS total FROM attendance_sessions")
    attendance = one(
        """
        SELECT
            COUNT(*) AS total,
            SUM(attendance_status = 'PRESENT') AS present,
            SUM(attendance_status = 'ABSENT') AS absent
        FROM attendance
        """
    )
    return ok({
        "total_students": students["total"],
        "total_faculty": faculty["total"],
        "total_subjects": subjects["total"],
        "total_sessions": sessions["total"],
        "total_attendance_records": attendance["total"],
        "present": attendance["present"] or 0,
        "absent": attendance["absent"] or 0
    })
# --------------------------------------------------
# CSV EXPORT
# --------------------------------------------------
@app.route("/attendance/export", methods=["GET"])
@roles_required("ADMIN", "FACULTY")
def export_attendance():
    records = q(
        """
        SELECT s.usn, s.name, sb.subject_name,
               x.session_date, a.attendance_status,
               a.recognition_timestamp,
               a.recognition_confidence
        FROM attendance a
        JOIN students s ON s.student_id = a.student_id
        JOIN attendance_sessions x ON x.session_id = a.session_id
        JOIN subjects sb ON sb.subject_id = x.subject_id
        ORDER BY x.session_date DESC
        """
    )
    output = io.StringIO()
    writer = csv.writer(output)
    if records:
        writer.writerow(records[0].keys())
        for row in records:
            writer.writerow(row.values())
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            "attachment; filename=facepulse_attendance.csv"
        }
    )
# --------------------------------------------------
# ERROR HANDLING
# --------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return fail("Route not found", 404)
@app.errorhandler(500)
def internal_error(e):
    traceback.print_exc()
    return fail("Internal server error", 500)
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False
    )
