"""Tests for the leaderboard: one row per face, ranked by their worst smell.

Faces are stood in for by synthetic unit vectors, so no camera or models needed.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import faces  # noqa: E402

DIM = 128
T0 = 1000.0


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def axis(i):
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def near(v, cos):
    """A unit vector at exactly `cos` similarity to unit vector v."""
    other = axis(DIM - 1) if v[DIM - 1] == 0 else axis(0)
    return cos * v + np.sqrt(1 - cos ** 2) * other


def make(tmp_path, clock=None):
    board = faces.Leaderboard(directory=tmp_path, clock=clock or Clock())
    board.load()
    return board


# ---------- one person, one row ----------

def test_same_face_twice_is_one_entry(tmp_path):
    b = make(tmp_path)
    b.observe(axis(0), b"a", 10)
    b.observe(axis(0), b"b", 5)
    assert len(b.entries) == 1
    assert b.ranked()[0]["sightings"] == 2


def test_similar_face_matches_existing(tmp_path):
    clock = Clock()
    b = make(tmp_path, clock)
    b.observe(axis(0), b"a", 10)
    clock.t += 60   # long gone, so the strict threshold applies
    b.observe(near(axis(0), 0.9), b"b", 10)
    assert len(b.entries) == 1


def test_different_face_is_a_new_entry(tmp_path):
    b = make(tmp_path)
    b.observe(axis(0), b"a", 10)
    b.observe(axis(1), b"b", 10)
    assert len(b.entries) == 2


def test_recent_sighting_gets_the_looser_threshold(tmp_path):
    clock = Clock()
    b = make(tmp_path, clock)
    b.observe(axis(0), b"a", 10)
    turned = near(axis(0), 0.32)   # between RECENT_SIM and MATCH_SIM

    clock.t += 1   # same encounter: a head turn, not a new person
    b.observe(turned, b"b", 10)
    assert len(b.entries) == 1

    clock.t += 60  # much later: too far off to call the same person
    b.observe(turned, b"c", 10)
    assert len(b.entries) == 2


# ---------- scoring and photos ----------

def test_peak_score_only_goes_up_and_photo_follows_it(tmp_path):
    b = make(tmp_path)
    entry, _ = b.observe(axis(0), b"first", 20)
    first_photo = entry["photo"]

    entry, changed = b.observe(axis(0), b"milder", 5)
    assert not changed
    assert entry["peak_score"] == 20
    assert entry["photo"] == first_photo

    entry, changed = b.observe(axis(0), b"worst", 80)
    assert changed
    assert entry["peak_score"] == 80
    assert (tmp_path / entry["photo"]).read_bytes() == b"worst"
    assert not (tmp_path / first_photo).exists()


def test_ranked_stinkiest_first(tmp_path):
    b = make(tmp_path)
    b.observe(axis(0), b"a", 30)
    b.observe(axis(1), b"b", 90)
    b.observe(axis(2), b"c", 60)
    assert [r["peak_score"] for r in b.ranked()] == [90, 60, 30]


def test_public_rows_have_no_embeddings(tmp_path):
    b = make(tmp_path)
    b.observe(axis(0), b"a", 30)
    assert "embedding" not in b.ranked()[0]


# ---------- persistence and deletion ----------

def test_board_survives_a_restart(tmp_path):
    b = make(tmp_path)
    b.observe(axis(0), b"a", 30)
    b.observe(axis(1), b"b", 40)

    again = make(tmp_path)
    assert len(again.entries) == 2
    again.observe(axis(0), b"a2", 10)   # recognised, not re-added
    assert len(again.entries) == 2


def test_delete_removes_row_and_photo(tmp_path):
    b = make(tmp_path)
    entry, _ = b.observe(axis(0), b"a", 30)
    assert b.delete(entry["id"])
    assert b.entries == {}
    assert not (tmp_path / entry["photo"]).exists()
    assert json.loads((tmp_path / "leaderboard.json").read_text()) == []
    # Gone for good: the same face is a brand-new person now.
    b.observe(axis(0), b"a", 30)
    assert len(b.entries) == 1
    assert next(iter(b.entries)) != entry["id"]


def test_unreadable_json_starts_fresh(tmp_path):
    (tmp_path / "leaderboard.json").write_text("{not json")
    b = make(tmp_path)
    assert b.entries == {}


# ---------- consent: nobody on the board until they raise a hand ----------

def waitlist(clock=None):
    return faces.Waitlist(clock=clock or Clock())


def test_no_hand_never_joins():
    w = waitlist()
    for _ in range(10):
        assert w.see(axis(0), b"a", 50, raised=False) is None
    assert len(w) == 1


def test_hand_held_up_joins_with_their_worst_moment():
    w = waitlist()
    w.see(axis(0), b"mild", 20, raised=False)
    w.see(axis(0), b"worst", 90, raised=False)
    assert w.see(axis(0), b"x", 30, raised=True) is None     # one pass is not enough
    row = w.see(axis(0), b"y", 30, raised=True)
    assert row is not None
    assert row["peak_score"] == 90 and row["jpeg"] == b"worst"
    assert len(w) == 0


def test_a_passing_wave_does_not_count():
    w = waitlist()
    w.see(axis(0), b"a", 20, raised=True)
    w.see(axis(0), b"a", 20, raised=False)                   # hand came down
    assert w.see(axis(0), b"a", 20, raised=True) is None


def test_each_person_consents_for_themselves():
    w = waitlist()
    w.see(axis(0), b"a", 20, raised=True)
    assert w.see(axis(1), b"b", 20, raised=True) is None     # someone else's hand
    assert len(w) == 2


def test_waiting_faces_are_forgotten():
    clock = Clock()
    w = waitlist(clock)
    w.see(axis(0), b"a", 20, raised=True)
    clock.t += faces.PENDING_TTL + 1
    w.see(axis(1), b"b", 20, raised=False)                   # any call prunes
    assert len(w) == 1
    assert w.see(axis(0), b"a", 20, raised=True) is None     # streak started over


def test_nothing_touches_disk_before_consent(tmp_path):
    b = make(tmp_path)
    w = waitlist()
    w.see(axis(0), b"a", 20, raised=False)
    assert list(tmp_path.iterdir()) == []
    w.see(axis(0), b"a", 20, raised=True)
    row = w.see(axis(0), b"a", 20, raised=True)
    b.observe(row["embedding"], row["jpeg"], row["peak_score"])
    assert len(b.entries) == 1


def kps(nose=None, shoulders=None, wrists=()):
    xy, conf = np.zeros((17, 2)), np.zeros(17)
    if nose:
        xy[faces.NOSE], conf[faces.NOSE] = nose, 0.9
    if shoulders:
        for i, p in zip((faces.L_SHOULDER, faces.R_SHOULDER), shoulders):
            xy[i], conf[i] = p, 0.9
    for i, p in zip((faces.L_WRIST, faces.R_WRIST), wrists):
        if p:
            xy[i], conf[i] = p, 0.9
    return xy, conf


def test_hand_above_head_is_raised():
    assert faces.hand_raised(*kps(nose=(100, 100), wrists=[(60, 40)]))
    assert faces.hand_raised(*kps(nose=(100, 100), wrists=[None, (140, 50)]))


def test_hands_down_or_at_chest_are_not():
    assert not faces.hand_raised(*kps(nose=(100, 100), wrists=[(60, 220), (140, 150)]))
    assert not faces.hand_raised(*kps(nose=(100, 100)))                    # no wrists seen


def test_face_turned_away_falls_back_to_shoulders():
    # Shoulders at y=150, 80 px apart: head line is y=110.
    assert faces.hand_raised(*kps(shoulders=[(60, 150), (140, 150)], wrists=[(60, 90)]))
    assert not faces.hand_raised(*kps(shoulders=[(60, 150), (140, 150)], wrists=[(60, 130)]))
    assert not faces.hand_raised(*kps(wrists=[(60, 10)]))                  # no reference


def test_the_hand_belongs_to_the_face_it_is_attached_to():
    poses = [((0, 0, 200, 400), (100, 60), True),       # left person, hand up
             ((300, 0, 500, 400), (400, 60), False)]    # right person, hands down
    assert faces.raised_for((102, 58), poses)
    assert not faces.raised_for((398, 62), poses)
    assert not faces.raised_for((250, 60), poses)       # nobody's face
