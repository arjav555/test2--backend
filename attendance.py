from database import q, one, execute

def mark(student_id, session_id, status, conf):
    """Insert once per (student, session). A PENDING_REVIEW may be upgraded to PRESENT; nothing else changes."""
    _, n = execute("""INSERT INTO attendance (student_id, session_id, attendance_status, recognition_confidence)
        VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
        attendance_status = IF(attendance_status='PENDING_REVIEW' AND VALUES(attendance_status)='PRESENT','PRESENT',attendance_status),
        recognition_confidence = IF(attendance_status='PRESENT', VALUES(recognition_confidence), recognition_confidence)""",
        (student_id, session_id, status, conf))
    return n > 0  # True when a new row/upgrade happened

def finalize(session_id):
    """Close the session; every enrolled student without a record becomes ABSENT."""
    execute("""INSERT IGNORE INTO attendance (student_id, session_id, attendance_status)
        SELECT e.student_id, s.session_id, 'ABSENT' FROM attendance_sessions s
        JOIN enrollments e ON e.subject_id = s.subject_id WHERE s.session_id=%s""", (session_id,))
    execute("UPDATE attendance_sessions SET session_status='COMPLETED', end_time=CURTIME() WHERE session_id=%s", (session_id,))

PCT_SQL = """SELECT sb.subject_id, sb.subject_code, sb.subject_name,
    SUM(a.attendance_status='PRESENT') AS attended, COUNT(*) AS total,
    ROUND(100*SUM(a.attendance_status='PRESENT')/COUNT(*),1) AS percentage
  FROM attendance a JOIN attendance_sessions x ON x.session_id=a.session_id
  JOIN subjects sb ON sb.subject_id=x.subject_id
  WHERE a.student_id=%s AND a.attendance_status<>'PENDING_REVIEW' GROUP BY sb.subject_id"""

def student_summary(student_id):
    subj = q(PCT_SQL, (student_id,))
    att, tot = sum(int(s["attended"]) for s in subj), sum(int(s["total"]) for s in subj)
    return {"subjects": subj, "attended": att, "missed": tot - att,
            "overall": round(100 * att / tot, 1) if tot else 0, "alert": bool(tot and 100 * att / tot < 75)}
