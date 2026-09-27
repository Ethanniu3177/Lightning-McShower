"""
Who is that? Face capture, re-identification, and the stink leaderboard.

Every time YOLO sees a person, the capturer looks for a face inside their box and
turns it into a 128-number fingerprint (OpenCV's SFace). If the fingerprint is
close to someone we already have, it is the same person: we bump their score
instead of adding a duplicate. Otherwise they get a new row on the board.

No face, no capture. The camera rides low on the car, so plenty of frames show a
chin or a torso; skipping those is what keeps the board free of junk and repeats.

Nobody goes on the board without saying yes. A new face waits in memory -- never on
disk -- until that person raises a hand above their head for about a second (YOLO
pose, the same model vision runs, finds their wrists and nose). No hand within PENDING_TTL of last being seen and
the waiting face and photo are simply dropped. A thumbs-up would be friendlier, but
at car-camera range a thumb is a handful of pixels; a raised arm is not.

Everything runs on the laptop: the car's WiFi has no internet. The board, with each
person's fingerprint, lives in captures/leaderboard.json and survives restarts.
"""
import asyncio
import json
import time
import uuid
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
MODELS_DIR = HERE / "models"
CAPTURES_DIR = HERE / "captures"
YUNET = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
SFACE = MODELS_DIR / "face_recognition_sface_2021dec.onnx"

# Cosine similarity for "same person". 0.363 is OpenCV's published SFace threshold.
MATCH_SIM = 0.363
# Someone who was matched a moment ago gets a looser bar, so a head turn mid-encounter
# does not spawn a second entry for the same person.
RECENT_SIM = 0.28
RECENT_S = 3.0

MIN_FACE_PX = 28          # smaller faces embed too noisily to trust; the ESP32-CAM
                          # frame is small, so a person a few metres off is ~30 px
FACE_SCORE = 0.8          # YuNet confidence
CAPTURE_INTERVAL = 0.5    # seconds between face passes; YOLO already runs far more often

# Consent: a hand above the head in this many face passes in a row (~1 s at 0.5 s).
CONSENT_PASSES = 2
# How long a face that hasn't said yes is remembered (in memory only) after last seen.
PENDING_TTL = 30.0

KP_CONF = 0.5
NOSE, L_SHOULDER, R_SHOULDER, L_WRIST, R_WRIST = 0, 5, 6, 9, 10


def normalise(vec):
    v = np.asarray(vec, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def closest(vec, rows, now):
    """Id of the row whose embedding is the same person as `vec`, or None.

    A straight cosine scan: with a few dozen faces it is instant. Someone seen in the
    last RECENT_S seconds gets the looser bar, to ride out a head turn.
    """
    vec = normalise(vec)
    best, best_sim = None, -1.0
    for id_, row in rows.items():
        sim = float(np.dot(vec, normalise(row["embedding"])))
        bar = RECENT_SIM if now - row["last_seen"] < RECENT_S else MATCH_SIM
        if sim >= bar and sim > best_sim:
            best, best_sim = id_, sim
    return best


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

class FaceEmbedder:
    """YuNet finds the face, SFace fingerprints it. Loaded lazily, used off-thread."""

    def __init__(self, yunet=YUNET, sface=SFACE):
        self.yunet_path, self.sface_path = Path(yunet), Path(sface)
        self._detector = None
        self._recognizer = None

    def available(self):
        return self.yunet_path.exists() and self.sface_path.exists()

    def _load(self):
        import cv2
        self._cv2 = cv2
        self._detector = cv2.FaceDetectorYN.create(
            str(self.yunet_path), "", (320, 320), FACE_SCORE, 0.3, 5000)
        self._recognizer = cv2.FaceRecognizerSF.create(str(self.sface_path), "")

    def embed(self, frame, box):
        """Blocking. frame is BGR, box is (x1, y1, x2, y2, conf) from YOLO.

        Returns (embedding, portrait_jpeg, face_centre_xy) or None when there is no
        usable face.
        """
        if self._detector is None:
            self._load()
        cv2 = self._cv2
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(round(v)) for v in box[:4])
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < MIN_FACE_PX or y2 - y1 < MIN_FACE_PX:
            return None
        crop = frame[y1:y2, x1:x2]

        self._detector.setInputSize((crop.shape[1], crop.shape[0]))
        _, faces = self._detector.detect(crop)
        if faces is None or len(faces) == 0:
            return None
        face = max(faces, key=lambda f: f[2] * f[3])   # the biggest face in the box
        if min(face[2], face[3]) < MIN_FACE_PX:
            return None

        aligned = self._recognizer.alignCrop(crop, face)
        vec = normalise(self._recognizer.feature(aligned))

        fx, fy = x1 + face[0], y1 + face[1]
        jpeg = self._portrait(cv2, frame, fx, fy, face[2], face[3])
        centre = (float(fx + face[2] / 2), float(fy + face[3] / 2))
        return (vec, jpeg, centre) if jpeg else None

    @staticmethod
    def _portrait(cv2, frame, fx, fy, fw, fh):
        """Head-and-shoulders crop around the face, from the raw (unannotated) frame."""
        h, w = frame.shape[:2]
        cx, cy = fx + fw / 2, fy + fh / 2
        side = max(fw, fh) * 2.2
        px1, py1 = int(max(0, cx - side / 2)), int(max(0, cy - side * 0.45))
        px2, py2 = int(min(w, cx + side / 2)), int(min(h, cy + side * 0.55))
        ok, enc = cv2.imencode(".jpg", frame[py1:py2, px1:px2], [cv2.IMWRITE_JPEG_QUALITY, 88])
        return enc.tobytes() if ok else None


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------

class Leaderboard:
    """One row per face, ranked by the worst smell they were ever caught in."""

    def __init__(self, directory=CAPTURES_DIR, clock=time.time):
        self.dir = Path(directory)
        self.path = self.dir / "leaderboard.json"
        self.clock = clock
        self.entries = {}

    # ---------- lifecycle ----------
    def load(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                rows = json.loads(self.path.read_text())
                self.entries = {r["id"]: r for r in rows}
            except (ValueError, KeyError) as e:
                print(f"faces: {self.path} unreadable ({e!r}), starting a fresh board")
                self.entries = {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self.entries.values())))
        tmp.replace(self.path)

    # ---------- matching ----------
    def match(self, vec):
        """Returns the id of the stored person this face belongs to, or None."""
        return closest(vec, self.entries, self.clock())

    def observe(self, vec, jpeg, score):
        """A face was seen while the air scored `score`. Returns (entry, changed)."""
        score = round(float(score), 1)
        now = self.clock()
        id_ = self.match(vec)

        if id_ is None:
            id_ = uuid.uuid4().hex[:10]
            entry = {
                "id": id_, "photo": f"{id_}.jpg", "peak_score": score,
                "first_seen": now, "last_seen": now, "sightings": 1,
                "embedding": [float(x) for x in normalise(vec)],
            }
            self._write_photo(entry, jpeg)
            self.entries[id_] = entry
            self._save()
            return self.public(entry), True

        entry = self.entries[id_]
        entry["last_seen"] = now
        entry["sightings"] += 1
        changed = False
        if score > entry["peak_score"]:
            # The photo on the board is them at their worst.
            entry["peak_score"] = score
            self._write_photo(entry, jpeg)
            changed = True
        self._save()
        return self.public(entry), changed

    def _write_photo(self, entry, jpeg):
        # A fresh file name per photo, so browsers never show a cached older one.
        old = self.dir / entry["photo"]
        entry["photo"] = f"{entry['id']}-{uuid.uuid4().hex[:6]}.jpg"
        (self.dir / entry["photo"]).write_bytes(jpeg)
        if old.exists() and old.name != entry["photo"]:
            old.unlink()

    def delete(self, id_):
        entry = self.entries.pop(id_, None)
        if entry is None:
            return False
        photo = self.dir / entry["photo"]
        if photo.exists():
            photo.unlink()
        self._save()
        return True

    # ---------- reading ----------
    @staticmethod
    def public(entry):
        return {k: v for k, v in entry.items() if k != "embedding"}

    def ranked(self):
        rows = sorted(self.entries.values(),
                      key=lambda e: (-e["peak_score"], e["first_seen"]))
        return [self.public(e) for e in rows]


# ---------------------------------------------------------------------------
# Consent: faces wait here, in memory only, until their owner raises a hand
# ---------------------------------------------------------------------------

class Waitlist:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.rows = {}

    def see(self, vec, jpeg, score, raised):
        """A face not yet on the board. Returns its row once they have said yes."""
        now = self.clock()
        self.rows = {k: r for k, r in self.rows.items() if now - r["last_seen"] < PENDING_TTL}
        score = round(float(score), 1)

        id_ = closest(vec, self.rows, now)
        if id_ is None:
            id_ = uuid.uuid4().hex[:10]
            self.rows[id_] = {"embedding": [float(x) for x in normalise(vec)],
                              "jpeg": jpeg, "peak_score": score, "streak": 0}
        row = self.rows[id_]
        row["last_seen"] = now
        if score > row["peak_score"]:
            row["peak_score"], row["jpeg"] = score, jpeg   # keep the worst moment
        # Consecutive passes: a wave on the way past doesn't count, holding it up does.
        row["streak"] = row["streak"] + 1 if raised else 0
        if row["streak"] >= CONSENT_PASSES:
            return self.rows.pop(id_)
        return None

    def __len__(self):
        return len(self.rows)


def hand_raised(xy, conf):
    """Is either wrist above the nose? xy is (17, 2) COCO keypoints, conf is (17,)."""
    wrists = [xy[i][1] for i in (L_WRIST, R_WRIST) if conf[i] >= KP_CONF]
    if not wrists:
        return False
    if conf[NOSE] >= KP_CONF:
        line = xy[NOSE][1]
    elif conf[L_SHOULDER] >= KP_CONF and conf[R_SHOULDER] >= KP_CONF:
        # Face turned away: call head height half a shoulder-width above the shoulders.
        span = abs(xy[L_SHOULDER][0] - xy[R_SHOULDER][0])
        line = min(xy[L_SHOULDER][1], xy[R_SHOULDER][1]) - span / 2
    else:
        return False
    return bool(min(wrists) < line)   # image y grows downward


def raised_for(centre, poses):
    """Which pose owns the face at `centre`, and is its hand up?"""
    cx, cy = centre
    best, best_d = False, float("inf")
    for (x1, y1, x2, y2), (ax, ay), raised in poses:
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            d = (ax - cx) ** 2 + (ay - cy) ** 2
            if d < best_d:
                best, best_d = raised, d
    return best


def poses_from(boxes, keypoints):
    """vision's boxes + keypoints -> [((x1, y1, x2, y2), anchor_xy, hand_raised), ...]"""
    out = []
    for (x1, y1, x2, y2, _), kp in zip(boxes, keypoints):
        if kp is None:
            continue
        xy, conf = kp
        anchor = xy[NOSE] if conf[NOSE] >= KP_CONF else ((x1 + x2) / 2, y1)
        out.append(((x1, y1, x2, y2), (float(anchor[0]), float(anchor[1])),
                    hand_raised(xy, conf)))
    return out


# ---------------------------------------------------------------------------
# Glue: YOLO boxes in, leaderboard updates out
# ---------------------------------------------------------------------------

class Capturer:
    """Throttled face pass over vision's frames. Never blocks the event loop."""

    def __init__(self, embedder, board, score_fn, on_change=None, waitlist=None,
                 on_consent=None):
        self.embedder = embedder
        self.board = board
        self.waitlist = waitlist or Waitlist()
        self.score_fn = score_fn
        self.on_change = on_change or (lambda *_: None)
        self.on_consent = on_consent or (lambda *_: None)
        self.enabled = True
        self._last = 0.0
        self._task = None

    def on_frame(self, frame, boxes, keypoints):
        """Called from vision after each YOLO pass, with a private copy of the frame."""
        if not (self.enabled and boxes):
            return
        now = time.monotonic()
        if now - self._last < CAPTURE_INTERVAL:
            return
        if self._task is not None and not self._task.done():
            return   # like vision: drop, never queue
        self._last = now
        self._task = asyncio.create_task(self._capture(frame, list(boxes), list(keypoints)))

    async def _capture(self, frame, boxes, keypoints):
        try:
            faces = await asyncio.to_thread(
                lambda: [f for f in (self.embedder.embed(frame, b) for b in boxes) if f])
        except Exception as e:
            print(f"faces: embedding failed, capture off ({e!r})")
            self.enabled = False
            return
        # The gas sensor can't tell people apart: everyone in frame shares the smell.
        score = self.score_fn()
        changed = False
        poses = poses_from(boxes, keypoints)
        for vec, jpeg, centre in faces:
            try:
                if self.board.match(vec) is not None:
                    _, c = self.board.observe(vec, jpeg, score)   # already said yes
                    changed = changed or c
                    continue
                ready = self.waitlist.see(vec, jpeg, score, raised_for(centre, poses))
                if ready:
                    print("faces: hand up -- adding them to the leaderboard")
                    self.on_consent()
                    self.board.observe(ready["embedding"], ready["jpeg"], ready["peak_score"])
                    changed = True
            except Exception as e:
                print(f"faces: could not record a face ({e!r})")
        if changed:
            self.on_change(self.board.ranked())


def probe(enabled, model_path):
    """Can we capture faces here? Called once at startup, like vision.probe."""
    if not enabled:
        return False, "disabled with --no-capture"
    try:
        import cv2
    except ImportError:
        return False, "opencv-python not installed"
    if not (hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")):
        return False, "this OpenCV build has no FaceDetectorYN/FaceRecognizerSF"
    if not FaceEmbedder().available():
        return False, "face models missing from models/ (run tools/setup.sh)"
    if "pose" not in Path(model_path).name:
        # Without keypoints nobody can say yes, so nobody gets captured.
        return False, f"{Path(model_path).name} is not a pose model (raise-a-hand needs one)"
    return True, "ready"
