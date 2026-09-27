"""Tests for the BME688-over-the-car-link path: N=24 reply -> nose reading.

No car needed: replies are fed straight into Car.handle, the way read_loop would.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge            # noqa: E402
import nose as nose_mod  # noqa: E402


def feed(car, frame):
    asyncio.run(car.handle(frame))


def make_car(smell):
    return bridge.Car("127.0.0.1", 0, bridge.Hub(), smell=smell)


# ---------- parsing the firmware's reply ----------

def test_parse_env_scales_units():
    assert bridge.parse_env("2345,4512,101325,52000") == {
        "gas_ohms": 52000, "temp_c": 23.45, "rh": 45.12, "hpa": 1013.2,
    }


def test_parse_env_before_first_sample_is_none():
    assert bridge.parse_env("none") is None


def test_parse_env_rejects_garbage():
    assert bridge.parse_env("1,2,3") is None
    assert bridge.parse_env("") is None


# ---------- the car link pushes into the nose ----------

def test_env_reply_reaches_car_source():
    smell = nose_mod.CarSmellSource()
    feed(make_car(smell), "{env_2345,4512,101325,52000}")
    assert smell.queue.get_nowait()["gas_ohms"] == 52000


def test_env_none_pushes_nothing():
    smell = nose_mod.CarSmellSource()
    feed(make_car(smell), "{env_none}")
    assert smell.queue.empty()


def test_env_ignored_without_car_source():
    car = make_car(None)
    feed(car, "{env_2345,4512,101325,52000}")  # must not raise
    assert car.smell is None


def test_heater_not_ready_is_skipped_by_meter():
    # Gas 0 means the heater was not stable; the meter must not treat it as a sample.
    m = nose_mod.StinkMeter()
    assert m.update(bridge.parse_env("2345,4512,101325,0")) is False


def test_make_source_car():
    src = nose_mod.make_source("car")
    assert isinstance(src, nose_mod.CarSmellSource)
    assert src.name == "car"
