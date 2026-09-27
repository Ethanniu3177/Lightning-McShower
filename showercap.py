"""
Shower caps for everyone on camera.

The pose model already hands vision.py 17 COCO keypoints per person, so there is
no second detector here: eyes and ears give us where the head is, how wide it is
and how it is tilted, and we alpha-blend resources/shower-cap.png onto the
annotated stream. faces.py still sees the raw frame, so leaderboard photos and
re-identification never see a cap.

head_pose() is pure arithmetic on keypoints and is unit-tested; CapOverlay needs
OpenCV and only runs inside vision.py's draw step.
"""
import math
from pathlib import Path

HERE = Path(__file__).parent
CAP_PNG = HERE / "resources" / "shower-cap.png"

KP_CONF = 0.5
NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SHOULDER, R_SHOULDER = 0, 1, 2, 3, 4, 5, 6

# Head width from whatever we can see. Ear-to-ear is the head; eyes and
# shoulders are scaled to roughly match it.
EYE_TO_HEAD = 2.4
SHOULDER_TO_HEAD = 0.55

# The brow (where the elastic band sits) is this many head-widths above the eyes.
BROW_ABOVE_EYES = 0.3
MAX_TILT_DEG = 45

# Fitting the PNG: the cap is this many head-widths across, and this point of the
# image (as fractions of width, height) is the middle of the elastic band.
CAP_WIDTH = 1.35
CAP_ANCHOR = (0.5, 0.70)
# The PNG is ~2000px wide; shrink once at load so per-frame warps stay cheap.
WORK_WIDTH = 480


def _pt(xy, conf, i):
    return (float(xy[i][0]), float(xy[i][1])) if conf[i] >= KP_CONF else None


def _pair(xy, conf, a, b):
    pa, pb = _pt(xy, conf, a), _pt(xy, conf, b)
    return (pa, pb) if pa and pb else None


def head_pose(xy, conf):
    """COCO keypoints -> (brow_x, brow_y, head_width, tilt_deg), or None.

    tilt_deg is clockwise on screen (image y grows downward), clamped to
    +/-MAX_TILT_DEG so a bad keypoint can't turn the cap upside down.
    """
    ears = _pair(xy, conf, R_EAR, L_EAR)
    eyes = _pair(xy, conf, R_EYE, L_EYE)
    shoulders = _pair(xy, conf, R_SHOULDER, L_SHOULDER)
    nose = _pt(xy, conf, NOSE)

    if ears:
        line, width = ears, math.dist(*ears)
    elif eyes:
        line, width = eyes, math.dist(*eyes) * EYE_TO_HEAD
    elif shoulders:
        line, width = shoulders, math.dist(*shoulders) * SHOULDER_TO_HEAD
    else:
        return None
    if width < 2:
        return None

    (rx, ry), (lx, ly) = line
    dx, dy = lx - rx, ly - ry
    if dx < 0:          # facing away: the ears swap sides, the head doesn't flip
        dx, dy = -dx, -dy
    tilt = math.degrees(math.atan2(dy, dx))
    tilt = max(-MAX_TILT_DEG, min(MAX_TILT_DEG, tilt))

    # Eye line, best source first.
    if eyes:
        ex, ey = (eyes[0][0] + eyes[1][0]) / 2, (eyes[0][1] + eyes[1][1]) / 2
    elif ears:
        ex, ey = (rx + lx) / 2, (ry + ly) / 2
    elif nose:
        ex, ey = nose[0], nose[1] - 0.1 * width
    else:
        # Shoulders only: the head is roughly a head and a half above them.
        ex, ey = (rx + lx) / 2, (ry + ly) / 2 - 1.5 * width

    # Step up from the eyes to the brow, perpendicular to the tilt.
    t = math.radians(tilt)
    up = BROW_ABOVE_EYES * width
    return ex + up * math.sin(t), ey - up * math.cos(t), width, tilt


class CapOverlay:
    """Loads the PNG once and blends it onto frames. Needs cv2 and numpy."""

    def __init__(self, cv2, path=CAP_PNG):
        import numpy as np
        self.cv2, self.np = cv2, np
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim != 3 or img.shape[2] != 4:
            raise ValueError(f"{path} must be an RGBA PNG")
        h, w = img.shape[:2]
        if w > WORK_WIDTH:
            img = cv2.resize(img, (WORK_WIDTH, round(h * WORK_WIDTH / w)),
                             interpolation=cv2.INTER_AREA)
        self.img = img
        h, w = img.shape[:2]
        self.anchor = (CAP_ANCHOR[0] * w, CAP_ANCHOR[1] * h)
        self.corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], dtype=np.float64)

    def draw(self, frame, keypoints):
        """In place. keypoints is vision's list: (xy, conf) per person, or None."""
        for kp in keypoints:
            if kp is None:
                continue
            pose = head_pose(*kp)
            if pose is not None:
                self._blend(frame, *pose)

    def _blend(self, frame, bx, by, width, tilt):
        cv2, np = self.cv2, self.np
        fh, fw = frame.shape[:2]
        scale = CAP_WIDTH * width / self.img.shape[1]
        # cv2 angles are counter-clockwise on screen; ours are clockwise.
        m = cv2.getRotationMatrix2D(self.anchor, -tilt, scale)
        m[0, 2] += bx - self.anchor[0]
        m[1, 2] += by - self.anchor[1]

        # Warp only into the cap's on-screen bounding box, clipped to the frame.
        pts = self.corners @ m.T
        x0, y0 = max(int(pts[:, 0].min()), 0), max(int(pts[:, 1].min()), 0)
        x1, y1 = min(int(math.ceil(pts[:, 0].max())), fw), min(int(math.ceil(pts[:, 1].max())), fh)
        if x1 <= x0 or y1 <= y0:
            return
        m[0, 2] -= x0
        m[1, 2] -= y0
        cap = cv2.warpAffine(self.img, m, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

        alpha = cap[:, :, 3:4].astype(np.float32) / 255.0
        roi = frame[y0:y1, x0:x1]
        roi[:] = (cap[:, :, :3] * alpha + roi * (1.0 - alpha)).astype(np.uint8)
