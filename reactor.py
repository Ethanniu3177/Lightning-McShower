"""
The decision: when does the robot actually open its mouth?

One rule, and it is the whole feature:

    speak only when the air is foul AND a person is in frame

Smell alone is not a joke -- it is a sensor reading in an empty hallway. A person
alone is not a joke either. The comedy is entirely in the overlap, which is why
this gate exists rather than just piping the gas sensor at the speaker.

Everything here is pure logic with an injected clock, so tests/test_reactor.py can
drive a whole encounter in microseconds and the state machine is inspectable on
stage when something goes sideways.
"""
import random
import time
from dataclasses import dataclass, field

import lines

# Minimum gap between two spoken lines.
COOLDOWN_S = 15.0
# Quiet for this long and the next trigger is treated as a fresh victim,
# so the robot starts over at tier 0 instead of opening with the meltdown.
ENCOUNTER_RESET_S = 45.0


@dataclass
class Reaction:
    text: str
    tier: str
    at: float
    reason: str = "auto"

    def as_dict(self):
        return {"text": self.text, "tier": self.tier, "at": self.at, "reason": self.reason}


@dataclass
class Reactor:
    cooldown: float = COOLDOWN_S
    encounter_reset: float = ENCOUNTER_RESET_S
    rng: random.Random = field(default_factory=random.Random)

    muted: bool = False
    last_fire: float = -1e9
    tier: int = 0
    last_text: str = ""
    history: list = field(default_factory=list)

    # ---------- the gate ----------
    def consider(self, person, stinky, now=None):
        """Called on every state change. Returns a Reaction to speak, or None."""
        now = time.monotonic() if now is None else now

        if not (person and stinky):
            # Conditions broken: the encounter is over, so the next one restarts mild.
            self.tier = 0
            return None
        if self.muted:
            return None
        if now - self.last_fire < self.cooldown:
            return None

        # Long enough since the last line that this is somebody new.
        if now - self.last_fire > self.encounter_reset:
            self.tier = 0

        return self._fire(self.tier, now, "auto")

    # ---------- consent ----------
    def ask_consent(self, now=None):
        """Ask to rate someone. The caller decides who needs asking."""
        now = time.monotonic() if now is None else now
        if self.muted:
            return None
        r = Reaction(text=self._pick(lines.CONSENT), tier="consent", at=now, reason="consent")
        self._record(r, now, roast=False)
        return r

    def thank(self, now=None):
        """They raised a hand."""
        now = time.monotonic() if now is None else now
        if self.muted:
            return None
        r = Reaction(text=self._pick(lines.CONSENT_THANKS), tier="thanks", at=now, reason="consent")
        self._record(r, now, roast=False)
        return r

    def welcome_back(self, score, rank, total, now=None):
        """Someone already on the board: tell them their score and rank."""
        now = time.monotonic() if now is None else now
        if self.muted:
            return None
        pool = lines.WELCOME_BACK_TOP if rank == 1 else lines.WELCOME_BACK
        text = self._pick(pool).format(score=round(score), rank=rank, total=total)
        r = Reaction(text=text, tier="rated", at=now, reason="rated")
        self._record(r, now, roast=False)
        return r

    def roast(self, now=None):
        """A freshly rated victim: roast them now, whatever the cooldown says."""
        now = time.monotonic() if now is None else now
        if self.muted:
            return None
        if now - self.last_fire > self.encounter_reset:
            self.tier = 0
        return self._fire(self.tier, now, "rated")

    def manual(self, now=None, text=None):
        """The soap button. Bypasses the person/smell gate but not the mute switch."""
        now = time.monotonic() if now is None else now
        if self.muted:
            return None
        if text:
            r = Reaction(text=text, tier="custom", at=now, reason="manual")
            self._record(r, now)
            return r
        r = Reaction(text=lines.MANUAL, tier="manual", at=now, reason="manual")
        self._record(r, now)
        return r

    # ---------- internals ----------
    def _fire(self, tier, now, reason):
        tier = max(0, min(tier, len(lines.TIERS) - 1))
        r = Reaction(text=self._pick(lines.TIERS[tier]), tier=lines.TIER_NAMES[tier],
                     at=now, reason=reason)
        self._record(r, now)
        # The bit builds: each line in one encounter is ruder than the last.
        self.tier = min(tier + 1, len(lines.TIERS) - 1)
        return r

    def _pick(self, pool):
        # Saying the exact same thing twice kills the joke faster than anything.
        fresh = [t for t in pool if t != self.last_text] or pool
        return self.rng.choice(fresh)

    def _record(self, reaction, now, roast=True):
        # Consent lines don't count against the roast cooldown.
        if roast:
            self.last_fire = now
        self.last_text = reaction.text
        self.history.append(reaction)
        del self.history[:-20]
