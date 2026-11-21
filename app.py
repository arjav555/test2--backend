import os
import csv
import io
import traceback
from functools import wraps
from datetime import datetime, date

import numpy as np
from flask import Flask, request, jsonify, session, Response
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

from database import q, one, execute
import face_recognition as fr
import attendance as att


app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-in-production")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
    SESSION_COOKIE_SAMESITE="None",
    PERMANENT_SESSION_LIFETIME=86400
)

CORS(
    app,
    origins=os.getenv("FRONTEND_URL", "http://localhost:5173").split(","),
    supports_credentials=True
)


def success(data=None, message="Success", status=200):
    return jsonify({
        "success": True,
        "message": message,
        "data": data
    }), status


def error(message="Something went wrong", status=400):
    return jsonify({
        "success": False,
        "message": message
    }), status


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return error("Authentication required", 401)
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = session.get("user")
            if not user:
                return error("Authentication required", 401)

            if user.get("role") not in roles:
                return error("Permission denied", 403)

            return f(*args, **kwargs)
        return decorated
    return decorator


@app.route("/", methods=["GET"])
def home():
    return success({
        "application": "FacePulse",
        "status": "running",
        "version": "1.0"
    })


@app.route("/health", methods=["GET"])
def health():
    try:
        one("SELECT 1 AS status")
        return success({"database": "connected"})
    except Exception:
        return error("Database connection failed", 503)


# AUTHENTICATION

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return error("Email and password are required")

    user = one(
        "SELECT * FROM faculty WHERE LOWER(email) = %s",
        (email,)
    )

    if not user:
        return error("Invalid email or password", 401)

    stored_password = user.get("password_hash", user.get("password", ""))

    try:
        valid = check_password_hash(stored_password, password)
    except Exception:
        valid = False

    if not valid:
        return error("Invalid email or password", 401)

    session.permanent = True

    session["user"] = {
        "id": user["faculty_id"],
        "name": user["name"],
        "email": user["email"],
        "role": "faculty",
        "department": user.get("department")
    }

    return success(session["user"], "Login successful")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return success(message="Logged out successfully")


@app.route("/me", methods=["GET"])
@login_required
def me():
    return success(session["user"])


# STUDENTS

@app.route("/students", methods=["GET"])
@login_required
def get_students():
    students = q("SELECT * FROM students ORDER BY student_id DESC")
    return success(students)


@app.route("/students", methods=["POST"])
@role_required("faculty", "admin")
def create_student():
    data = request.get_json(silent=True) or {}

    required = ["name", "usn"]

    for field in required:
        if not data.get(field):
            return error(f"{field} is required")

    execute(
        """
        INSERT INTO students (name, usn, email)
        VALUES (%s, %s, %s)
        """,
        (
            data["name"],
            data["usn"],
            data.get("email")
        )
    )

    return success(message="Student created", status=201)


@app.route("/students/<int:student_id>", methods=["GET"])
@login_required
def get_student(student_id):
    student = one(
        "SELECT * FROM students WHERE student_id = %s",
        (student_id,)
    )

    if not student:
        return error("Student not found", 404)

    return success(student)


@app.route("/students/<int:student_id>", methods=["DELETE"])
@role_required("faculty", "admin")
def delete_student(student_id):
    execute(
        "DELETE FROM students WHERE student_id = %s",
        (student_id,)
    )

    return success(message="Student deleted")


# FACULTY

@app.route("/faculty", methods=["GET"])
@role_required("faculty", "admin")
def get_faculty():
    faculty = q(
        "SELECT faculty_id, name, email, department FROM faculty"
    )
    return success(faculty)


# SUBJECTS

@app.route("/subjects", methods=["GET"])
@login_required
def get_subjects():
    subjects = q("SELECT * FROM subjects")
    return success(subjects)


@app.route("/subjects", methods=["POST"])
@role_required("faculty", "admin")
def create_subject():
    data = request.get_json(silent=True) or {}

    if not data.get("name"):
        return error("Subject name is required")

    execute(
        "INSERT INTO subjects (name) VALUES (%s)",
        (data["name"],)
    )

    return success(message="Subject created", status=201)


# ENROLLMENTS

@app.route("/enrollments", methods=["GET"])
@login_required
def get_enrollments():
    return success(q("SELECT * FROM enrollments"))


@app.route("/enrollments", methods=["POST"])
@role_required("faculty", "admin")
def create_enrollment():
    data = request.get_json(silent=True) or {}

    student_id = data.get("student_id")
    subject_id = data.get("subject_id")

    if not student_id or not subject_id:
        return error("Student and subject are required")

    execute(
        """
        INSERT INTO enrollments (student_id, subject_id)
        VALUES (%s, %s)
        """,
        (student_id, subject_id)
    )

    return success(message="Enrollment successful", status=201)


# ATTENDANCE

@app.route("/attendance", methods=["GET"])
@login_required
def get_attendance():
    records = q("SELECT * FROM attendance ORDER BY 1 DESC")
    return success(records)


@app.route("/attendance", methods=["POST"])
@role_required("faculty", "admin")
def mark_attendance():
    data = request.get_json(silent=True) or {}

    student_id = data.get("student_id")
    subject_id = data.get("subject_id")

    if not student_id or not subject_id:
        return error("Student and subject are required")

    execute(
        """
        INSERT INTO attendance
        (student_id, subject_id, date, status)
        VALUES (%s, %s, %s, %s)
        """,
        (
            student_id,
            subject_id,
            date.today(),
            data.get("status", "Present")
        )
    )

    return success(message="Attendance marked")


# ANALYTICS

@app.route("/analytics", methods=["GET"])
@login_required
def analytics():
    students = one("SELECT COUNT(*) AS total FROM students")
    attendance = one("SELECT COUNT(*) AS total FROM attendance")

    return success({
        "total_students": students["total"],
        "total_attendance": attendance["total"]
    })


# CSV EXPORT

@app.route("/attendance/export", methods=["GET"])
@role_required("faculty", "admin")
def export_attendance():
    records = q("SELECT * FROM attendance")

    output = io.StringIO()
    writer = csv.writer(output)

    if records:
        writer.writerow(records[0].keys())

        for record in records:
            writer.writerow(record.values())

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            "attachment; filename=facepulse_attendance.csv"
        }
    )


# ERROR HANDLING

@app.errorhandler(404)
def not_found(e):
    return error("Endpoint not found", 404)


@app.errorhandler(500)
def internal_error(e):
    traceback.print_exc()
    return error("Internal server error", 500)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False
    )
