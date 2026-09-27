"""
Camera ownership, person detection, and re-serving the video.

The ESP32-CAM on the Elegoo shield serves ONE viewer at a time. Until now the
browser was that viewer, pointing an <img> straight at 192.168.4.1:81/stream,
which left Python with no pixels to run YOLO on.

So this module takes the camera. It is the single client of the car's MJPEG
stream; it decodes frames, runs person detection on a throttled subset, draws the
boxes, and re-serves an annotated MJPEG on the bridge's own /stream route. Side
benefit worth more than it sounds: the whole team can now open the dashboard at
once, which the one-viewer camera never allowed.

Two escape hatches keep this developable without the car:
  --camera 0    use the laptop webcam
  --no-vision   passthrough proxy, no YOLO (also the automatic fallback if
                ultralytics is missing, so bridge.py never hard-crashes)
"""
import asyncio
import os
import time
from collections import deque
from pathlib import Path

import aiohttp

HERE = Path(__file__).parent
# The pose model is person-only and returns keypoints with every box, so one
# model serves both presence (here) and raise-a-hand consent (faces.py).
DEFAULT_MODEL = HERE / "models" / "yolo11n-pose.pt"

CAMERA_URL = "http://192.168.4.1:81/stream"

# Inference budget. The MJPEG stream runs faster than this; frames in between
# reuse the last boxes, which at these rates is invisible.
MIN_INFER_INTERVAL = 0.12
INFER_SIZE = 320
CONF = 0.5
PERSON_CLASS = 0          # COCO class 0 is "person"

# Presence debounce: a person must land in 2 of the last 4 inferences, and counts
# as present for HOLD_S after the last sighting. Stops a single dropped frame from
# cutting the robot off mid-insult.
WINDOW = 4
NEEDED = 2
HOLD_S = 1.5

# "Near enough to ask for consent": a person's box is at least this share of the
# frame height. Held for NEAR_HOLD_S after the last hit, like HOLD_S above.
NEAR_FRAC = 0.45
NEAR_HOLD_S = 1.5

JPEG_SOI = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"
MAX_BUFFER = 2 * 1024 * 1024   # a frame bigger than this means we lost sync

BOUNDARY = "showerbotframe"


class PersonDetector:
    """Ultralytics YOLO pose, people only. Loaded lazily so import never blocks.

    Only ever one predict() in flight: PyTorch's MPS backend is not thread-safe,
    and two concurrent passes abort the process with a Metal assertion.
    """

    def __init__(self, model_path=DEFAULT_MODEL):
        self.model_path = Path(model_path)
        self.model = None
        self.device = "cpu"

    def load(self):
        # Ultralytics phones home for analytics on import/first-run. There is no
        # internet on the car's AP, and a hanging telemetry call is the last thing
        # we want mid-demo.
        os.environ.setdefault("YOLO_VERBOSE", "false")
        from ultralytics import settings, YOLO

        try:
            settings.update({"sync": False})
        except Exception:
            pass

        try:
            import torch
            if torch.backends.mps.is_available():
                self.device = "mps"
        except Exception:
            pass

        # Prefer the vendored weights. Bare "yolo11n-pose.pt" makes ultralytics download,
        # which works at home and fails on stage -- run tools/setup.sh first.
        target = self.model_path if self.model_path.exists() else self.model_path.name
        if not self.model_path.exists():
            print(f"vision: {self.model_path} missing, asking ultralytics to fetch "
                  f"{self.model_path.name} (needs internet)")
        self.model = YOLO(str(target))
        print(f"vision: YOLO ready on {self.device}")

    def detect(self, frame):
        """Blocking. Runs in a worker thread.

        Returns (boxes, keypoints): boxes are [(x1, y1, x2, y2, conf), ...] and
        keypoints[i] is box i's (xy (17, 2), conf (17,)) COCO arrays, or None if
        the model has no pose head.
        """
        if self.model is None:
            self.load()
        results = self.model.predict(
            frame, imgsz=INFER_SIZE, conf=CONF, classes=[PERSON_CLASS],
            device=self.device, verbose=False,
        )
        boxes, keypoints = [], []
        for r in results:
            kp = r.keypoints
            xys = kp.xy.cpu().numpy() if kp is not None else None
            kconfs = kp.conf.cpu().numpy() if kp is not None and kp.conf is not None else None
            for i, b in enumerate(r.boxes):
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                boxes.append((x1, y1, x2, y2, float(b.conf[0])))
                keypoints.append((xys[i], kconfs[i]) if kconfs is not None else None)
        return boxes, keypoints


class Vision:
    """Owns the camera, publishes annotated frames, reports who is in view."""

    def __init__(self, camera_url=CAMERA_URL, camera_index=None,
                 enabled=True, model_path=DEFAULT_MODEL, on_update=None, on_frame=None,
                 cap=True):
        self.camera_url = camera_url
        self.camera_index = camera_index
        self.enabled = enabled
        self.on_update = on_update or (lambda *_: None)
        # Raw (unannotated) frame, boxes and keypoints after every YOLO pass;
        # faces.py takes photos and reads raised hands from it.
        self.on_frame = on_frame or (lambda *_: None)
        self.detector = PersonDetector(model_path) if enabled else None
        # Shower caps on the stream (showercap.py). Loaded on first draw; None
        # until then, False if it can't load.
        self.cap = None if cap else False

        self.latest = b""
        self.seq = 0
        self._waiters = set()

        self.boxes = []
        self.keypoints = []
        self.people = 0
        self.person = False
        self.near = False
        self.fps = 0.0
        self.online = False

        self._recent = deque(maxlen=WINDOW)
        self._last_seen = -1e9
        self._last_near = -1e9
        self._last_infer = 0.0
        self._infer_task = None
        self._frame_times = deque(maxlen=30)
        self._cv2 = None

    # ---------- publishing ----------
    def _publish(self, jpeg):
        self.latest = jpeg
        self.seq += 1
        self._frame_times.append(time.monotonic())
        if len(self._frame_times) > 1:
            span = self._frame_times[-1] - self._frame_times[0]
            self.fps = round((len(self._frame_times) - 1) / span, 1) if span > 0 else 0.0
        for fut in self._waiters:
            if not fut.done():
                fut.set_result(None)
        self._waiters.clear()

    async def frames(self):
        """Async generator of JPEG bytes, one per new frame. Used by GET /stream."""
        seen = -1
        while True:
            if self.latest and self.seq != seen:
                seen = self.seq
                yield self.latest
                continue
            fut = asyncio.get_running_loop().create_future()
            self._waiters.add(fut)
            try:
                await asyncio.wait_for(fut, timeout=5)
            except asyncio.TimeoutError:
                self._waiters.discard(fut)
                if self.latest:
                    yield self.latest   # keep the connection warm while the camera sulks

    def snapshot(self):
        return {
            "people": self.people,
            "person": self.person,
            "near": self.near,
            "fps": self.fps,
            "online": self.online,
            "enabled": self.enabled,
        }

    # ---------- presence ----------
    def _note_detection(self, boxes, frame_h=None):
        now = time.monotonic()
        self.boxes = boxes
        self._recent.append(bool(boxes))
        if boxes:
            self._last_seen = now
        if frame_h and any((y2 - y1) / frame_h >= NEAR_FRAC for (_, y1, _, y2, _) in boxes):
            self._last_near = now

        people = len(boxes)
        present = (sum(self._recent) >= NEEDED) or (now - self._last_seen < HOLD_S)
        near = present and now - self._last_near < NEAR_HOLD_S
        if present != self.person or people != self.people or near != self.near:
            self.person, self.people, self.near = present, people, near
            self.on_update(self.snapshot())

    def _set_online(self, online):
        if online != self.online:
            self.online = online
            if not online:
                self.boxes, self.people, self.person, self.near = [], 0, False, False
                self.keypoints = []
                self._recent.clear()
            self.on_update(self.snapshot())

    # ---------- the loop ----------
    async def run(self):
        while True:
            try:
                if self.camera_index is not None:
                    await self._run_webcam()
                else:
                    await self._run_mjpeg()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"vision: camera loop died ({e!r})")
            self._set_online(False)
            await self._publish_placeholder("No camera")
            await asyncio.sleep(2)

    async def _run_mjpeg(self):
        timeout = aiohttp.ClientTimeout(total=None, sock_read=10, sock_connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            print(f"vision: opening {self.camera_url}")
            async with session.get(self.camera_url) as resp:
                resp.raise_for_status()
                self._set_online(True)
                print("vision: camera stream open")
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(4096):
                    buf.extend(chunk)
                    # Frame boundaries by JPEG markers rather than the multipart
                    # boundary string: ESP32 firmwares disagree about the latter,
                    # and 0xFFD8/0xFFD9 only ever appear as real markers.
                    while True:
                        start = buf.find(JPEG_SOI)
                        if start < 0:
                            break
                        end = buf.find(JPEG_EOI, start + 3)
                        if end < 0:
                            break
                        jpeg = bytes(buf[start:end + 2])
                        del buf[:end + 2]
                        await self._handle(jpeg)
                    if len(buf) > MAX_BUFFER:
                        buf.clear()   # lost sync; drop it and resync on the next SOI

    async def _run_webcam(self):
        cv2 = self._load_cv2()
        cap = await asyncio.to_thread(cv2.VideoCapture, self.camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open webcam {self.camera_index}")
        self._set_online(True)
        print(f"vision: using local webcam {self.camera_index}")
        try:
            while True:
                ok, frame = await asyncio.to_thread(cap.read)
                if not ok:
                    raise RuntimeError("webcam read failed")
                await self._handle(None, frame=frame)
                await asyncio.sleep(0.02)
        finally:
            await asyncio.to_thread(cap.release)

    async def _handle(self, jpeg, frame=None):
        """One frame in: detect (sometimes), annotate, publish."""
        if not self.enabled:
            if jpeg is not None:
                self._publish(jpeg)     # pure passthrough, no decode at all
                return
            frame_bgr = frame
        else:
            frame_bgr = frame

        cv2 = self._load_cv2()
        if cv2 is None:
            if jpeg is not None:
                self._publish(jpeg)
            return

        if frame_bgr is None:
            import numpy as np
            frame_bgr = cv2.imdecode(np.frombuffer(jpeg, dtype="uint8"), cv2.IMREAD_COLOR)
            if frame_bgr is None:
                return   # truncated frame, skip it

        if self.enabled:
            self._maybe_infer(frame_bgr)
            self._draw(cv2, frame_bgr)

        ok, encoded = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self._publish(encoded.tobytes())

    def _maybe_infer(self, frame):
        now = time.monotonic()
        # Never queue a second inference: if the last one is still running we keep
        # streaming with the boxes we have. Dropping detections is fine; blocking
        # the event loop is not -- the car drops us after 3 missed heartbeats.
        if now - self._last_infer < MIN_INFER_INTERVAL:
            return
        if self._infer_task is not None and not self._infer_task.done():
            return
        self._last_infer = now
        self._infer_task = asyncio.create_task(self._infer(frame.copy()))

    async def _infer(self, frame):
        try:
            boxes, keypoints = await asyncio.to_thread(self.detector.detect, frame)
        except Exception as e:
            print(f"vision: detection failed, continuing without it ({e!r})")
            self.enabled = False
            self.on_update(self.snapshot())
            return
        self.keypoints = keypoints
        self._note_detection(boxes, frame.shape[0])
        self.on_frame(frame, boxes, keypoints)

    def _draw(self, cv2, frame):
        for (x1, y1, x2, y2, conf) in self.boxes:
            p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
            cv2.rectangle(frame, p1, p2, (163, 192, 92), 2)
            label = f"person {conf:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (p1[0], p1[1] - th - 6), (p1[0] + tw + 6, p1[1]),
                          (163, 192, 92), -1)
            cv2.putText(frame, label, (p1[0] + 3, p1[1] - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (29, 60, 73), 1, cv2.LINE_AA)
        self._draw_caps(cv2, frame)

    def _draw_caps(self, cv2, frame):
        if self.cap is False or not self.keypoints:
            return
        if self.cap is None:
            try:
                import showercap
                self.cap = showercap.CapOverlay(cv2)
                print("vision: shower caps on")
            except Exception as e:
                print(f"vision: shower caps off ({e!r})")
                self.cap = False
                return
        try:
            self.cap.draw(frame, self.keypoints)
        except Exception as e:
            print(f"vision: shower cap draw failed, turning caps off ({e!r})")
            self.cap = False

    async def _publish_placeholder(self, text):
        """A visible 'no signal' card, so a dead camera looks dead instead of frozen."""
        cv2 = self._load_cv2()
        if cv2 is None:
            return
        import numpy as np
        img = np.full((240, 320, 3), 24, dtype=np.uint8)
        cv2.putText(img, text, (28, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (214, 232, 235), 2, cv2.LINE_AA)
        ok, encoded = cv2.imencode(".jpg", img)
        if ok:
            self._publish(encoded.tobytes())

    def _load_cv2(self):
        if self._cv2 is None:
            try:
                import cv2
                self._cv2 = cv2
            except ImportError:
                print("vision: opencv-python not installed, falling back to passthrough")
                self.enabled = False
                self._cv2 = False
        return self._cv2 or None


def probe(enabled):
    """Is YOLO actually usable here? Called once at startup so we fail loudly, early."""
    if not enabled:
        return False, "disabled with --no-vision"
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False, "opencv-python not installed (pip install -r requirements.txt)"
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        return False, "ultralytics not installed (pip install -r requirements.txt)"
    return True, "ready"
