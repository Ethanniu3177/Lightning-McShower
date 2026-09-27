"""
The nose: turning an air-quality sensor into one number between 0 and 100.

The Elegoo stock firmware has no gas-sensor command, but our patched UNO firmware
adds one (N=24, BME688), so smell can ride the existing TCP link to the car. Four
ways in, all behind one interface:

  FakeSmellSource    simulated -- the default, so the whole feature is demoable
                     with no hardware at all
  SerialSmellSource  a microcontroller on USB, one JSON object per line
  PushSmellSource    an ESP32 on the car POSTing to /smell over the ELEGOO AP
  CarSmellSource     the BME688 on the car's UNO; bridge.py polls N=24 and pushes

Whatever the sensor, it reports drifting absolute numbers that mean nothing on
their own -- 150 ppb TVOC is filthy in a clean lab and pristine in a hackathon
venue at hour thirty. So StinkMeter learns a baseline from the first WARMUP_S
seconds of clean air and scores everything relative to that.

Expected reading shapes (send whichever your sensor gives):
  ENS160 / SGP30:  {"tvoc": 220, "eco2": 850}      ppb / ppm, higher = worse
  BME688:          {"gas_ohms": 48000}             ohms, LOWER = worse
"""
import asyncio
import json
from collections import deque
import random
import statistics
import time

# How long we watch clean air before trusting the baseline.
WARMUP_S = 30.0
# Fewer samples than this and the baseline is a guess, not a median.
MIN_BASELINE_SAMPLES = 8

# Reading this many times the baseline (in excess) scores a full 100.
FULL_SCALE = 1.5

# Hysteresis: it takes a lot to offend the robot, and a while to calm it down.
STINK_ON = 65.0
STINK_OFF = 45.0

# Absolute floors so a near-zero baseline cannot make the score explode.
# These are in *intensity* units, i.e. after inversion -- 500 is the intensity of
# a 2 MOhm reading, which is about as clean as a BME688 ever reports.
FLOOR = {"tvoc": 40.0, "eco2": 420.0, "gas_ohms": 500.0}

# Readings kept for the dashboard's live chart, so a freshly opened tab isn't blank.
# At the fake source's 0.5 s that is two minutes; on the car's slower poll, longer.
HISTORY_N = 240

# The real sensor can't reliably smell a person, so a consenting victim's score is
# decided by fiat: uniformly random in FUDGE_RANGE. Above FUDGE_STINK they get
# roasted, otherwise complimented. (The real sensor still uses STINK_ON/OFF.)
FUDGE_RANGE = (1.0, 100.0)
FUDGE_STINK = 40.0


def intensity(raw):
    """Collapse a reading dict to one 'how bad is it' number, higher = worse.

    Gas resistance is inverted first, because for BME688-class sensors more VOC
    means *less* resistance.
    """
    if "tvoc" in raw:
        return "tvoc", float(raw["tvoc"])
    if "gas_ohms" in raw and float(raw["gas_ohms"]) > 0:
        # Invert into the same "bigger is smellier" direction as TVOC.
        return "gas_ohms", 1.0e9 / float(raw["gas_ohms"])
    if "eco2" in raw:
        return "eco2", float(raw["eco2"])
    return None, None


class StinkMeter:
    """Baseline calibration + relative scoring + hysteresis."""

    def __init__(self):
        self.started = time.monotonic()
        self.samples = []
        self.baseline = None
        self.channel = None
        self.score = 0.0
        self.stinky = False
        self.raw = {}
        self.at = None       # wall-clock time of the last reading, for the live chart
        self.fudge_score = None   # set by fudge(), cleared by the next real reading

    @property
    def warming(self):
        return self.baseline is None

    @property
    def warmup_left(self):
        if not self.warming:
            return 0.0
        return max(0.0, WARMUP_S - (time.monotonic() - self.started))

    def update(self, raw):
        """Feed one reading. Returns True if anything the dashboard cares about moved."""
        channel, value = intensity(raw)
        if value is None:
            return False
        self.fudge_score = None
        self.raw = dict(raw)
        self.channel = channel
        self.at = time.time()

        if self.warming:
            self.samples.append(value)
            # Median, not mean: one whiff during warm-up should not poison the baseline.
            if (time.monotonic() - self.started >= WARMUP_S
                    and len(self.samples) >= MIN_BASELINE_SAMPLES):
                self.baseline = max(statistics.median(self.samples), self._floor())
                print(f"nose: baseline set to {self.baseline:.0f} ({channel}, "
                      f"{len(self.samples)} samples)")
            return True

        excess = (value - self.baseline) / self.baseline
        self.score = max(0.0, min(100.0, 100.0 * excess / FULL_SCALE))
        # Hysteresis, so a reading hovering on the threshold does not machine-gun
        # the robot into saying six things in a row.
        if self.stinky:
            self.stinky = self.score > STINK_OFF
        else:
            self.stinky = self.score >= STINK_ON
        return True

    def _floor(self):
        return FLOOR.get(self.channel, 1.0)

    def fudge(self, score=None):
        """Pretend the air scores `score` until the next real reading. Returns it."""
        score = random.uniform(*FUDGE_RANGE) if score is None else float(score)
        self.fudge_score = round(score, 1)
        return self.fudge_score

    @property
    def fudged(self):
        return self.fudge_score is not None

    def recalibrate(self):
        """Forget the baseline and warm up again -- e.g. after moving rooms."""
        self.__init__()

    def snapshot(self, source_name):
        fudged = self.fudged
        return {
            "score": self.fudge_score if fudged else round(self.score, 1),
            "stinky": self.fudge_score > FUDGE_STINK if fudged else self.stinky,
            "raw": self.raw,
            "channel": self.channel,
            "baseline": round(self.baseline, 1) if self.baseline else None,
            "warming": self.warming and not fudged,
            "warmup_left": round(self.warmup_left, 1),
            "source": source_name,
            "at": self.at,
            "stink_on": STINK_ON,
            "stink_off": STINK_OFF,
            "fudged": fudged,
        }


# ---------- sources ----------

class SmellSource:
    name = "none"

    async def run(self, emit):
        """Call `emit(raw_dict)` forever. Must not return under normal operation."""
        while True:
            await asyncio.sleep(3600)


class FakeSmellSource(SmellSource):
    """A plausible TVOC trace you can spike on demand from the dashboard.

    This is what makes the demo rehearsable: no sensor, no ESP32, just a button.
    """
    name = "fake"

    def __init__(self, base=120.0, period=0.5):
        self.base = base
        self.period = period
        self.value = base
        self.spike_until = 0.0
        self.spike_target = 0.0

    def spike(self, level=90.0, seconds=8.0):
        """Simulate someone awful walking past. `level` is a 0-100 stink score."""
        # Invert the scoring maths so a requested score lands where asked.
        self.spike_target = self.base * (1.0 + (level / 100.0) * FULL_SCALE)
        self.spike_until = time.monotonic() + seconds

    async def run(self, emit):
        while True:
            if time.monotonic() < self.spike_until:
                # Ease toward the target so the dashboard meter sweeps up.
                self.value += (self.spike_target - self.value) * 0.5
            else:
                drift = random.gauss(0, self.base * 0.02)
                self.value += (self.base - self.value) * 0.25 + drift
            emit({"tvoc": round(max(1.0, self.value), 1),
                  "eco2": round(400 + self.value * 1.4)})
            await asyncio.sleep(self.period)


class SerialSmellSource(SmellSource):
    """A microcontroller on USB printing one JSON object per line."""
    name = "serial"

    def __init__(self, port, baud=115200):
        self.port = port
        self.baud = baud

    async def run(self, emit):
        import serial  # imported late: only --smell serial needs pyserial

        loop = asyncio.get_running_loop()
        while True:
            try:
                ser = await asyncio.to_thread(serial.Serial, self.port, self.baud, timeout=2)
            except Exception as e:
                print(f"nose: cannot open {self.port} ({e!r}); retrying in 3 s")
                await asyncio.sleep(3)
                continue
            print(f"nose: reading {self.port} at {self.baud}")
            try:
                while True:
                    line = await loop.run_in_executor(None, ser.readline)
                    if not line:
                        continue
                    try:
                        raw = json.loads(line.decode(errors="ignore").strip())
                    except json.JSONDecodeError:
                        continue  # boot banners and debug prints are not readings
                    if isinstance(raw, dict):
                        emit(raw)
            except Exception as e:
                print(f"nose: serial link dropped ({e!r})")
            finally:
                await asyncio.to_thread(ser.close)
            await asyncio.sleep(2)


class PushSmellSource(SmellSource):
    """Readings arrive by HTTP POST /smell instead of us going and fetching them."""
    name = "http"

    def __init__(self):
        self.queue = asyncio.Queue(maxsize=32)

    def push(self, raw):
        try:
            self.queue.put_nowait(raw)
        except asyncio.QueueFull:
            pass  # a sensor shouting faster than we can read is not worth buffering

    async def run(self, emit):
        while True:
            emit(await self.queue.get())


class CarSmellSource(PushSmellSource):
    """The BME688 wired to the car's UNO (patched firmware, command N=24).

    Same queue as PushSmellSource, but the pusher is bridge.py's car link rather
    than an HTTP POST: it polls the car and pushes each parsed reading here.
    """
    name = "car"


class Nose:
    """Owns a source and a meter, and reports every change upward."""

    def __init__(self, source, on_update):
        self.source = source
        self.meter = StinkMeter()
        self.on_update = on_update
        self._pending = []
        # Outlives recalibrate(), which resets the meter but not what already happened.
        self.recent = deque(maxlen=HISTORY_N)

    def snapshot(self):
        return self.meter.snapshot(self.source.name)

    def history(self):
        return list(self.recent)

    async def run(self):
        loop = asyncio.get_running_loop()

        def emit(raw):
            if self.meter.update(raw):
                snap = self.snapshot()
                self.recent.append({k: snap[k] for k in ("at", "score", "warming", "stinky", "raw")})
                # Sources may be synchronous; hop back onto the loop to notify.
                loop.call_soon(self.on_update, snap)

        await self.source.run(emit)


def make_source(kind, serial_port=None):
    if kind == "serial":
        if not serial_port:
            raise SystemExit("--smell serial needs --serial-port (try: ls /dev/cu.usb*)")
        return SerialSmellSource(serial_port)
    if kind == "http":
        return PushSmellSource()
    if kind == "car":
        return CarSmellSource()
    return FakeSmellSource()
