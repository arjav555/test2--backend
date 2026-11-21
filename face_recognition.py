"""Real face recognition with InsightFace (buffalo_l, ArcFace 512-d embeddings)."""
import base64, os
from collections import defaultdict, deque
import cv2, numpy as np
from cryptography.fernet import Fernet
from insightface.app import FaceAnalysis
from database import q, execute

MODEL_VERSION = "insightface-buffalo_l"
MATCH_T = float(os.getenv("MATCH_THRESHOLD", 0.45))    # >= auto-mark PRESENT
REVIEW_T = float(os.getenv("REVIEW_THRESHOLD", 0.35))  # between -> faculty review
fernet = Fernet(os.environ["FACE_KEY"].encode())       # generate: Fernet.generate_key()

_app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition", "landmark_3d_68"])
_app.prepare(ctx_id=-1, det_size=(640, 640))           # ctx_id=0 for GPU

def decode(b64):
    img = cv2.imdecode(np.frombuffer(base64.b64decode(b64.split(",")[-1]), np.uint8), cv2.IMREAD_COLOR)
    if img is None: raise ValueError("Invalid image")
    return img

def detect(img): return _app.get(img)

def quality(face, img):
    x1, y1, x2, y2 = [max(0, int(v)) for v in face.bbox]
    crop = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(crop, cv2.CV_64F).var()) if crop.size else 0.0
    size = x2 - x1
    ok = face.det_score > 0.7 and size > 100 and blur > 40
    return {"det_score": round(float(face.det_score), 3), "face_px": size, "sharpness": round(blur, 1), "ok": bool(ok)}

def pose(face):
    p = getattr(face, "pose", None)                    # (pitch, yaw, roll) degrees
    return None if p is None else tuple(float(v) for v in p)

# ---- encrypted embedding store + in-memory match index ----
_ids, _mat = [], np.zeros((0, 512), np.float32)

def reload_index():
    global _ids, _mat
    rows = q("SELECT student_id, face_embedding FROM face_data")
    _ids = [r["student_id"] for r in rows]
    _mat = (np.stack([np.frombuffer(fernet.decrypt(bytes(r["face_embedding"])), np.float32) for r in rows])
            if rows else np.zeros((0, 512), np.float32))

def save_embeddings(student_id, embs):
    execute("DELETE FROM face_data WHERE student_id=%s", (student_id,))
    for e in embs:
        e = (e / np.linalg.norm(e)).astype(np.float32)
        execute("INSERT INTO face_data (student_id, face_embedding, model_version) VALUES (%s,%s,%s)",
                (student_id, fernet.encrypt(e.tobytes()), MODEL_VERSION))
    reload_index()

def match(emb):
    """-> (student_id, cosine_similarity) of best match, or (None, best_sim)."""
    if not len(_ids): return None, 0.0
    sims = _mat @ (emb / np.linalg.norm(emb))
    i = int(np.argmax(sims)); return _ids[i], float(sims[i])

# ---- liveness: head movement over recent frames (blocks static photos) ----
_hist = defaultdict(lambda: deque(maxlen=12))
def liveness(session_id, student_id, face):
    p = pose(face)
    if p is None: return False
    h = _hist[(session_id, student_id)]; h.append(p)
    a = np.array(h)
    return len(h) >= 4 and (np.ptp(a[:, 1]) > 6 or np.ptp(a[:, 0]) > 6)  # yaw/pitch range in degrees
def clear_liveness(session_id):
    for k in [k for k in _hist if k[0] == session_id]: del _hist[k]
