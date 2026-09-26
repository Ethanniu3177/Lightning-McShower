# Lightning McShower
ShowerHacks

A robot car that drives around, sees you with YOLO, smells you with a gas sensor,
and — only when both are true — insults you in an ElevenLabs voice.

## The one rule

    speak only when the air is foul AND a person is in frame

Smell alone is a sensor reading in an empty hallway. A person alone is just a
person. The joke is entirely in the overlap, so that gate lives in one place:
`reactor.py`.

## Run it

Needs the internet, so do it **days before the demo** (ultralytics pulls PyTorch,
>1 GB, and none of it downloads over the car's WiFi):

    pip install -r requirements.txt
    bash tools/setup.sh                    # weights + ElevenLabs clips
    export ELEVENLABS_API_KEY=sk_...       # optional; falls back to macOS `say`

No car and no sensor yet? The whole pipeline still runs:

    python bridge.py --camera 0 --smell fake

With the car:

    1. Shield switch on "cam", stock Elegoo firmware.
    2. Join the laptop to the car's ELEGOO-XXXX WiFi.
    3. python bridge.py
    4. Open http://localhost:8080

## How it fits together

    bridge.py       aiohttp app; TCP to the car (:100), owns every task
    vision.py       pulls the camera's MJPEG, runs YOLO, re-serves it annotated
    nose.py         smell sources (fake / serial / http) + baseline + scoring
    reactor.py      the gate: person AND stink, cooldown, escalating tiers
    voice.py        plays cached clips with afplay, falls back to `say`
    lines.py        what it says, in three tiers of rudeness
    dashboard.html  the UI, still one file with zero external requests

The bridge takes the camera because the ESP32-CAM only serves **one viewer at a
time** — that is why `dashboard.html` now points at `/stream` instead of straight
at `192.168.4.1:81`. Nice side effect: the whole team can watch at once.

ElevenLabs is pre-rendered to `audio/` by `tools/gen_voices.py` rather than called
live, because the laptop has no internet while it is on the car's access point.
Hand-recorded clips can go in `audio/manifest.json` too.

## Smell sensor wiring

The stock Elegoo firmware has no gas-sensor command, so the sensor does not ride
the car's TCP link. Two real options, both supported:

    python bridge.py --smell serial --serial-port /dev/cu.usbmodem1101
    python bridge.py --smell http     # an ESP32 POSTs to http://<laptop>:8080/smell

Send whatever your sensor gives: `{"tvoc": 220, "eco2": 850}` for ENS160/SGP30, or
`{"gas_ohms": 48000}` for a BME688 (lower resistance = worse air; it gets
inverted). The first 30 s establish a baseline, so let it settle before judging
anyone.

## Tests

    python -m pytest tests/

## To build
- driving model - ricky
- record our voices - all → drop clips in `audio/`, add them to `manifest.json`
- add a sniff module - scott → `nose.py`, needs the real I2C board
- output audio to speakers - mac api - jake → `voice.py` (done, afplay + `say`)
- update dashboard to show the streaming data - ethan → nose panel + reaction log
- person detection - only speak when a person is in frame - use yolo model → done
- figure out how to integrate sponsors - ?
- mock out presentation - andrew
- potentially have conversational ai reacting to data and speaking things out - andrew
