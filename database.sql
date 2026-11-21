-- FACEPULSE schema (3NF). MySQL 8+
CREATE DATABASE IF NOT EXISTS facepulse CHARACTER SET utf8mb4;
USE facepulse;

CREATE TABLE faculty (
  faculty_id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(120) NOT NULL UNIQUE,
  department VARCHAR(50) NOT NULL
);
CREATE TABLE students (
  student_id INT AUTO_INCREMENT PRIMARY KEY,
  usn VARCHAR(20) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(120) NOT NULL UNIQUE,
  department VARCHAR(50) NOT NULL,
  semester TINYINT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE subjects (
  subject_id INT AUTO_INCREMENT PRIMARY KEY,
  subject_code VARCHAR(15) NOT NULL UNIQUE,
  subject_name VARCHAR(100) NOT NULL,
  faculty_id INT NOT NULL,                       -- one faculty : many subjects (1:N)
  FOREIGN KEY (faculty_id) REFERENCES faculty(faculty_id) ON DELETE RESTRICT
);
CREATE TABLE enrollments (                        -- students <-> subjects (M:N)
  enrollment_id INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT NOT NULL,
  subject_id INT NOT NULL,
  UNIQUE (student_id, subject_id),
  FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
  FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE
);
CREATE TABLE face_data (                          -- one student : many samples (1:N)
  face_id INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT NOT NULL,
  face_embedding BLOB NOT NULL,                   -- Fernet-encrypted float32[512]; no raw photos stored
  registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  model_version VARCHAR(40) NOT NULL,
  FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
);
CREATE TABLE attendance_sessions (
  session_id INT AUTO_INCREMENT PRIMARY KEY,
  subject_id INT NOT NULL,
  faculty_id INT NOT NULL,
  session_date DATE NOT NULL,
  start_time TIME NOT NULL,
  end_time TIME NULL,
  session_status ENUM('ACTIVE','COMPLETED') NOT NULL DEFAULT 'ACTIVE',
  FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
  FOREIGN KEY (faculty_id) REFERENCES faculty(faculty_id)
);
CREATE TABLE attendance (
  attendance_id INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT NOT NULL,
  session_id INT NOT NULL,
  attendance_status ENUM('PRESENT','ABSENT','PENDING_REVIEW') NOT NULL,
  recognition_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  recognition_confidence DECIMAL(5,4) NULL,       -- cosine similarity (NULL if manual)
  UNIQUE KEY uq_student_session (student_id, session_id),   -- blocks duplicate attendance
  FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
  FOREIGN KEY (session_id) REFERENCES attendance_sessions(session_id) ON DELETE CASCADE
);
CREATE TABLE users (                              -- login accounts for the 3 roles
  user_id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(40) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('ADMIN','FACULTY','STUDENT') NOT NULL,
  ref_id INT NULL                                 -- faculty_id or student_id
);

-- ---------- Sample data ----------
INSERT INTO faculty (name,email,department) VALUES
('Dr. Meera Rao','meera@college.edu','CSE'),('Prof. Kiran Shetty','kiran@college.edu','CSE');
INSERT INTO subjects (subject_code,subject_name,faculty_id) VALUES
('CS301','Database Management Systems',1),('CS302','Operating Systems',2),('CS303','Computer Networks',1);
INSERT INTO students (usn,name,email,department,semester) VALUES
('1MS24CS001','Arjun Nair','arjun@college.edu','CSE',3),
('1MS24CS002','Rahul Verma','rahul@college.edu','CSE',3),
('1MS24CS003','Priya Menon','priya@college.edu','CSE',3),
('1MS24CS004','Sneha Iyer','sneha@college.edu','CSE',3),
('1MS24CS005','Vikram Rao','vikram@college.edu','CSE',3),
('1MS24CS006','Ananya Das','ananya@college.edu','CSE',3);
INSERT INTO enrollments (student_id,subject_id) SELECT s.student_id, b.subject_id FROM students s CROSS JOIN subjects b;
INSERT INTO attendance_sessions (subject_id,faculty_id,session_date,start_time,end_time,session_status) VALUES
(1,1,CURDATE()-INTERVAL 4 DAY,'10:00','10:55','COMPLETED'),
(1,1,CURDATE()-INTERVAL 3 DAY,'10:00','10:55','COMPLETED'),
(2,2,CURDATE()-INTERVAL 2 DAY,'11:00','11:55','COMPLETED'),
(1,1,CURDATE()-INTERVAL 1 DAY,'10:00','10:55','COMPLETED'),
(3,1,CURDATE()-INTERVAL 1 DAY,'12:00','12:55','COMPLETED');
INSERT INTO attendance (student_id,session_id,attendance_status,recognition_confidence) VALUES
(1,1,'PRESENT',.71),(2,1,'PRESENT',.66),(3,1,'PRESENT',.69),(4,1,'ABSENT',NULL),(5,1,'PRESENT',.62),(6,1,'ABSENT',NULL),
(1,2,'PRESENT',.73),(2,2,'ABSENT',NULL),(3,2,'PRESENT',.7),(4,2,'ABSENT',NULL),(5,2,'PRESENT',.64),(6,2,'PRESENT',.6),
(1,3,'PRESENT',.7),(2,3,'PRESENT',.68),(3,3,'ABSENT',NULL),(4,3,'PRESENT',.61),(5,3,'ABSENT',NULL),(6,3,'ABSENT',NULL),
(1,4,'PRESENT',.72),(2,4,'PRESENT',.67),(3,4,'PRESENT',.7),(4,4,'ABSENT',NULL),(5,4,'PRESENT',.65),(6,4,'ABSENT',NULL),
(1,5,'PRESENT',.7),(2,5,'ABSENT',NULL),(3,5,'PRESENT',.68),(4,5,'PRESENT',.6),(5,5,'PRESENT',.66),(6,5,'ABSENT',NULL);
-- Demo logins are seeded by backend/app.py on first run (hashed passwords).

-- ---------- Demo queries (INNER/LEFT JOIN, GROUP BY, aggregates) ----------
-- Subject-wise % per student:
--   SELECT s.usn, sb.subject_name, ROUND(100*SUM(a.attendance_status='PRESENT')/COUNT(*),1) pct
--   FROM attendance a JOIN students s USING(student_id) JOIN attendance_sessions x USING(session_id)
--   JOIN subjects sb ON sb.subject_id=x.subject_id GROUP BY s.usn, sb.subject_name;
-- Students without face data (LEFT JOIN):
--   SELECT s.usn FROM students s LEFT JOIN face_data f USING(student_id) WHERE f.face_id IS NULL;
-- Students below 75% (HAVING):
--   SELECT s.usn, ROUND(100*AVG(a.attendance_status='PRESENT'),1) pct FROM students s
--   JOIN attendance a USING(student_id) GROUP BY s.usn HAVING pct<75;
