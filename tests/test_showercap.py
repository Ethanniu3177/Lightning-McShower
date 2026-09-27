"""Tests for where the shower cap goes: keypoints in, brow point / width / tilt out."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import showercap  # noqa: E402

SEEN, UNSEEN = 0.9, 0.1


def pose(**points):
    """17 COCO keypoints, all unseen except the ones named, e.g. l_eye=(x, y)."""
    index = {"nose": 0, "l_eye": 1, "r_eye": 2, "l_ear": 3, "r_ear": 4,
             "l_shoulder": 5, "r_shoulder": 6}
    xy, conf = [(0.0, 0.0)] * 17, [UNSEEN] * 17
    for name, p in points.items():
        xy[index[name]], conf[index[name]] = p, SEEN
    return xy, conf


def test_upright_face_puts_the_brow_above_the_eyes():
    # Facing the camera: the person's left side is on the image's right.
    bx, by, width, tilt = showercap.head_pose(*pose(
        r_ear=(80, 100), l_ear=(120, 100), r_eye=(92, 98), l_eye=(108, 98), nose=(100, 110)))
    assert width == pytest.approx(40)
    assert tilt == pytest.approx(0)
    assert bx == pytest.approx(100)
    assert by == pytest.approx(98 - showercap.BROW_ABOVE_EYES * 40)


def test_facing_away_does_not_flip_the_cap():
    upright = showercap.head_pose(*pose(r_ear=(80, 100), l_ear=(120, 100)))
    away = showercap.head_pose(*pose(r_ear=(120, 100), l_ear=(80, 100)))
    assert away == pytest.approx(upright)


def test_head_tilted_clockwise_on_screen():
    # Right ear higher than left on screen: head leans clockwise, brow moves right.
    bx, by, _, tilt = showercap.head_pose(*pose(r_ear=(80, 90), l_ear=(120, 110)))
    assert tilt == pytest.approx(math.degrees(math.atan2(20, 40)))
    assert bx > 100 and by < 100


def test_tilt_is_clamped():
    _, _, _, tilt = showercap.head_pose(*pose(r_ear=(100, 50), l_ear=(101, 150)))
    assert tilt == showercap.MAX_TILT_DEG


def test_eyes_only_estimates_head_width():
    _, _, width, _ = showercap.head_pose(*pose(r_eye=(90, 100), l_eye=(110, 100)))
    assert width == pytest.approx(20 * showercap.EYE_TO_HEAD)


def test_shoulders_only_still_places_a_cap_above_them():
    bx, by, width, _ = showercap.head_pose(*pose(r_shoulder=(50, 200), l_shoulder=(150, 200)))
    assert width == pytest.approx(100 * showercap.SHOULDER_TO_HEAD)
    assert bx == pytest.approx(100)
    assert by < 200


def test_no_head_keypoints_means_no_cap():
    assert showercap.head_pose(*pose(nose=(100, 100))) is None
    assert showercap.head_pose(*pose()) is None
