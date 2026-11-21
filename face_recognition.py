"""Face recognition using InsightFace buffalo_s (ArcFace 512-d embeddings)."""

import os

# Limit CPU and memory usage on Render
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import base64
from collections import defaultdict, deque

import cv2
import numpy as np
from cryptography.fernet import Fernet
from insightface.app import FaceAnalysis
from database import q, execute


MODEL_VERSION = "insightface-buffalo_s"

MATCH_T = float(os.getenv("MATCH_THRESHOLD", 0.45))
REVIEW_T = float(os.getenv("REVIEW_THRESHOLD", 0.35))

fernet = Fernet(os.environ["FACE_KEY"].encode())


# Smaller model to reduce Render memory usage
_app = FaceAnalysis(
    name="buffalo_s",
    allowed_modules=["detection", "recognition", "landmark_2d_106"]
)

_app.prepare(
    ctx_id=-1,
    det_size=(320, 320)
)


def decode(b64):
    img = cv2.imdecode(
        np.frombuffer(
            base64.b64decode(b64.split(",")[-1]),
            np.uint8
        ),
        cv2.IMREAD_COLOR
    )

    if img is None:
        raise ValueError("Invalid image")

    return img


def detect(img):
    return _app.get(img)


def quality(face, img):
    x1, y1, x2, y2 = [max(0, int(v)) for v in face.bbox]

    h, w = img.shape[:2]
    x2 = min(x2, w)
    y2 = min(y2, h)

    crop = img[y1:y2, x1:x2]

    if crop.size:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    else:
        blur = 0.0

    size = x2 - x1

    ok = (
        face.det_score > 0.7
        and size > 80
        and blur > 40
    )

    return {
        "det_score": round(float(face.det_score), 3),
        "face_px": size,
        "sharpness": round(blur, 1),
        "ok": bool(ok)
    }


def pose(face):
    # Use 3D pose if the model provides it
    p = getattr(face, "pose", None)

    if p is not None:
        return tuple(float(v) for v in p)

    # Fallback: estimate movement from facial landmarks
    kps = getattr(face, "kps", None)

    if kps is None or len(kps) < 5:
        return None

    left_eye, right_eye, nose, left_mouth, right_mouth = kps[:5]

    eye_center = (left_eye + right_eye) / 2
    mouth_center = (left_mouth + right_mouth) / 2

    eye_distance = np.linalg.norm(right_eye - left_eye)

    if eye_distance < 1:
        return None

    yaw = float((nose[0] - eye_center[0]) / eye_distance)
    pitch = float((nose[1] - eye_center[1]) / eye_distance)

    return pitch, yaw, 0.0


# Encrypted embedding storage and matching index
_ids = []
_mat = np.zeros((0, 512), np.float32)


def reload_index():
    global _ids, _mat

    rows = q(
        "SELECT student_id, face_embedding FROM face_data"
    )

    _ids = [r["student_id"] for r in rows]

    if rows:
        _mat = np.stack([
            np.frombuffer(
                fernet.decrypt(bytes(r["face_embedding"])),
                np.float32
            )
            for r in rows
        ])
    else:
        _mat = np.zeros((0, 512), np.float32)


def save_embeddings(student_id, embs):
    execute(
        "DELETE FROM face_data WHERE student_id=%s",
        (student_id,)
    )

    for e in embs:
        norm = np.linalg.norm(e)

        if norm == 0:
            continue

        e = (e / norm).astype(np.float32)

        execute(
            """
            INSERT INTO face_data
            (student_id, face_embedding, model_version)
            VALUES (%s, %s, %s)
            """,
            (
                student_id,
                fernet.encrypt(e.tobytes()),
                MODEL_VERSION
            )
        )

    reload_index()


def match(emb):
    """Return best matching student ID and cosine similarity."""

    if not len(_ids):
        return None, 0.0

    norm = np.linalg.norm(emb)

    if norm == 0:
        return None, 0.0

    sims = _mat @ (emb / norm)

    i = int(np.argmax(sims))

    return _ids[i], float(sims[i])


# Liveness tracking using head movement
_hist = defaultdict(lambda: deque(maxlen=12))


def liveness(session_id, student_id, face):
    p = pose(face)

    if p is None:
        return False

    h = _hist[(session_id, student_id)]
    h.append(p)

    a = np.array(h)

    return (
        len(h) >= 4
        and (
            np.ptp(a[:, 1]) > 0.08
            or np.ptp(a[:, 0]) > 0.08
        )
    )


def clear_liveness(session_id):
    for k in [k for k in _hist if k[0] == session_id]:
        del _hist[k]
