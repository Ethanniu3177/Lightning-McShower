"""Tests for the two pieces of pure logic: the speak/don't-speak gate and scoring.

The reactor takes an injected clock, so a whole encounter runs in microseconds.
"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lines            # noqa: E402
import nose as nose_mod  # noqa: E402
import reactor as reactor_mod  # noqa: E402

T0 = 1000.0


def make():
    return reactor_mod.Reactor(rng=random.Random(0))


# ---------- the gate: smell AND a person, never one alone ----------

def test_stink_with_nobody_in_frame_is_silent():
    r = make()
    assert r.consider(person=False, stinky=True, now=T0) is None


def test_person_in_clean_air_is_silent():
    r = make()
    assert r.consider(person=True, stinky=False, now=T0) is None


def test_both_conditions_fire():
    r = make()
    out = r.consider(person=True, stinky=True, now=T0)
    assert out is not None
    assert out.text in lines.MILD
    assert out.tier == "mild"


# ---------- cooldown ----------

def test_cooldown_blocks_a_second_line():
    r = make()
    assert r.consider(person=True, stinky=True, now=T0)
    assert r.consider(person=True, stinky=True, now=T0 + 1) is None
    assert r.consider(person=True, stinky=True, now=T0 + reactor_mod.COOLDOWN_S + 0.1)


# ---------- escalation ----------

def test_lines_escalate_through_the_tiers():
    r = make()
    tiers = []
    t = T0
    for _ in range(4):
        tiers.append(r.consider(person=True, stinky=True, now=t).tier)
        t += reactor_mod.COOLDOWN_S + 1
    assert tiers == ["mild", "rude", "savage", "savage"]


def test_leaving_the_frame_resets_to_mild():
    r = make()
    t = T0
    assert r.consider(person=True, stinky=True, now=t).tier == "mild"
    t += reactor_mod.COOLDOWN_S + 1
    assert r.consider(person=True, stinky=True, now=t).tier == "rude"
    # They walk off; the encounter is over.
    r.consider(person=False, stinky=True, now=t + 1)
    t += reactor_mod.COOLDOWN_S + 2
    assert r.consider(person=True, stinky=True, now=t).tier == "mild"


def test_clean_air_also_resets_the_encounter():
    r = make()
    assert r.consider(person=True, stinky=True, now=T0).tier == "mild"
    r.consider(person=True, stinky=False, now=T0 + 1)
    assert r.consider(person=True, stinky=True,
                      now=T0 + reactor_mod.COOLDOWN_S + 1).tier == "mild"


def test_a_long_silence_starts_the_bit_over():
    r = make()
    r.consider(person=True, stinky=True, now=T0)
    # Conditions stay true but nothing is evaluated for a while: new victim.
    later = T0 + reactor_mod.ENCOUNTER_RESET_S + 1
    assert r.consider(person=True, stinky=True, now=later).tier == "mild"


# ---------- politeness switches ----------

def test_mute_silences_everything():
    r = make()
    r.muted = True
    assert r.consider(person=True, stinky=True, now=T0) is None
    assert r.manual(now=T0) is None


def test_manual_ignores_the_gate_but_not_mute():
    r = make()
    out = r.manual(now=T0)
    assert out.text == lines.MANUAL
    assert out.reason == "manual"
    assert r.manual(now=T0, text="custom line").text == "custom line"


def test_never_says_the_same_line_twice_in_a_row():
    r = reactor_mod.Reactor(rng=random.Random(7))
    seen = []
    t = T0
    for _ in range(12):
        out = r.consider(person=True, stinky=True, now=t)
        seen.append(out.text)
        t += reactor_mod.COOLDOWN_S + 1
    assert all(a != b for a, b in zip(seen, seen[1:]))


# ---------- scoring ----------

def warm(meter, value, n=10):
    meter.started -= nose_mod.WARMUP_S + 1
    for _ in range(n):
        meter.update({"tvoc": value})
    return meter


def test_baseline_is_learned_then_scored_relatively():
    m = warm(nose_mod.StinkMeter(), 120)
    assert m.baseline == pytest.approx(120)
    m.update({"tvoc": 120})
    assert m.score == pytest.approx(0)
    m.update({"tvoc": 120 * (1 + nose_mod.FULL_SCALE)})
    assert m.score == pytest.approx(100)


def test_hysteresis_stops_threshold_chatter():
    m = warm(nose_mod.StinkMeter(), 100)
    m.update({"tvoc": 100 * (1 + 0.55 * nose_mod.FULL_SCALE)})   # score 55
    assert not m.stinky                       # below STINK_ON, stays quiet
    m.update({"tvoc": 100 * (1 + 0.70 * nose_mod.FULL_SCALE)})   # score 70
    assert m.stinky                           # crossed STINK_ON
    m.update({"tvoc": 100 * (1 + 0.55 * nose_mod.FULL_SCALE)})   # back to 55
    assert m.stinky                           # above STINK_OFF, still offended
    m.update({"tvoc": 100 * (1 + 0.30 * nose_mod.FULL_SCALE)})   # score 30
    assert not m.stinky


def test_gas_resistance_is_inverted():
    """BME688-class sensors read LOWER when the air is worse."""
    m = nose_mod.StinkMeter()
    m.started -= nose_mod.WARMUP_S + 1
    for _ in range(10):
        m.update({"gas_ohms": 50000})
    assert not m.stinky
    m.update({"gas_ohms": 10000})   # resistance collapses => very smelly
    assert m.score == 100
    assert m.stinky


def test_warming_up_never_scores():
    m = nose_mod.StinkMeter()
    m.update({"tvoc": 9999})
    assert m.warming
    assert m.score == 0
    assert not m.stinky


# ---------- consent: ask once when someone steps close ----------

def test_asks_consent_when_someone_steps_close():
    r = make()
    out = r.ask_consent(near=True, now=T0)
    assert out is not None
    assert out.text in lines.CONSENT
    assert out.tier == "consent"


def test_does_not_re_ask_while_still_near():
    r = make()
    r.ask_consent(near=True, now=T0)
    assert r.ask_consent(near=True, now=T0 + 60) is None


def test_consent_cooldown_across_approaches():
    r = make()
    r.ask_consent(near=True, now=T0)
    r.ask_consent(near=False, now=T0 + 5)
    assert r.ask_consent(near=True, now=T0 + 10) is None
    r.ask_consent(near=False, now=T0 + 20)
    assert r.ask_consent(near=True, now=T0 + reactor_mod.CONSENT_COOLDOWN_S + 1) is not None


def test_muted_means_no_ask_and_no_thanks():
    r = make()
    r.muted = True
    assert r.ask_consent(near=True, now=T0) is None
    assert r.thank(now=T0) is None


def test_thanks_line():
    r = make()
    out = r.thank(now=T0)
    assert out is not None and out.text in lines.CONSENT_THANKS


def test_consent_ask_does_not_block_a_roast():
    r = make()
    r.ask_consent(near=True, now=T0)
    assert r.consider(person=True, stinky=True, now=T0 + 1) is not None
