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
    faces.py        face capture, re-identification, the leaderboard
    dashboard.html  the UI, still one file with zero external requests
    leaderboard.html  the Hall of Stench, same rules

The bridge takes the camera because the ESP32-CAM only serves **one viewer at a
time** — that is why `dashboard.html` now points at `/stream` instead of straight
at `192.168.4.1:81`. Nice side effect: the whole team can watch at once.

ElevenLabs is pre-rendered to `audio/` by `tools/gen_voices.py` rather than called
live, because the laptop has no internet while it is on the car's access point.
Hand-recorded clips can go in `audio/manifest.json` too.

## The leaderboard

`http://localhost:8080/leaderboard` ranks everyone who opted in by the worst smell
they were ever caught in. When YOLO sees a person, `faces.py` looks for a face in
their box and fingerprints it (OpenCV YuNet + SFace), all on the laptop.

**Nobody joins without saying yes.** A new face waits in memory, never on disk,
until that person raises a hand above their head for about a second (YOLO pose finds
their wrists and nose). If they don't, the face and photo are forgotten 30 s after
they leave. Once someone has joined, a known face bumps their score, and their photo
is swapped only when they hit a new peak. No visible face means no photo, which keeps
chins and torsos off the board.

The board lives in `captures/` (gitignored: it's people's faces) and survives
restarts. Changed their mind? Hover their photo and hit ✕. `--no-capture` turns the
whole thing off. `tools/setup.sh` fetches all three models.

## Smell sensor wiring

With our patched UNO firmware (Scott's v2.1.2 fork: BME688 driver + command
`N=24`), the BME688 wires to the UNO (`5V, GND, SDA→A4, SCL→A5`) and its readings
ride the car's existing TCP link. The stock ESP32 forwards them unchanged:

    python bridge.py --smell car      # polls N=24 every ~3 s; shows gas/temp/RH/pressure

The stock Elegoo firmware has no gas-sensor command. Without the patch, two other
options are supported:

    python bridge.py --smell serial --serial-port /dev/cu.usbmodem1101
    python bridge.py --smell http     # an ESP32 POSTs to http://<laptop>:8080/smell

Send whatever your sensor gives: `{"tvoc": 220, "eco2": 850}` for ENS160/SGP30, or
`{"gas_ohms": 48000}` for a BME688 (lower resistance = worse air; it gets
inverted). The first 30 s establish a baseline, so let it settle before judging
anyone.

## Tests

    python -m pytest tests/

## To build

| Task | Owner | Status |
|---|---|---|
| Driving model | Ricky | ⏳ |
| Mock out presentation | Andrew | ⏳ |
| Record our voices | All | ⏳ Dashboard upload is built (`clips/`). For the robot's own reactions, drop clips in `audio/` and add them to `manifest.json`. |
| Add a sniff module | Scott | ⏳ `nose.py` is built; needs the real I2C board |
| Output audio to speakers (Mac API) | Jake | ✅ `voice.py` (afplay + `say`); speaker picker next |
| Update dashboard to show the streaming data | Ethan | ✅ nose panel + reaction log |
| Figure out how to integrate sponsors | **?** | ⏳ unassigned |
| *Potentially:* conversational AI reacting to data and speaking things out | Andrew | 🧪 stretch |
| Person detection: only speak when a person is in frame (YOLO) | | ✅ `vision.py` |
| Vision: YOLO evidence → stank score | **?** | ⏳ unassigned |

See [SPRINTS.md](docs/SPRINTS.md) for the checklists, timeline and fallback demo plan.

## What works right now

| Piece | Status | File |
|---|---|---|
| Laptop ↔ car link (TCP, heartbeat, auto-reconnect) | ✅ built | [`bridge.py`](bridge.py) |
| Browser dashboard: live camera, drive pad + WASD/arrows, speed, camera pan, `Space` = stop | ✅ built | [`dashboard.html`](dashboard.html) |
| Live ultrasonic distance + line sensors on the dashboard | ✅ built | both |
| Safety: car stops if drive updates stop for 0.5 s or the tab closes | ✅ built | `bridge.py` |
| Auto-roast when the air is foul AND a person is in frame; "Tell them to shower" button; mute | ✅ built | `reactor.py`, `bridge.py` |
| Recorded voice clips: drop `.m4a/.mp3/.wav` into `clips/` or drag onto the dashboard; plays a random clip with no immediate repeats; computer-voice fallback | ✅ built | both |
| Person detection: local YOLO, boxes drawn on the stream | ✅ built | [`vision.py`](vision.py) |
| Smell: fake / serial / HTTP sources, baseline + score, nose panel | ✅ built | [`nose.py`](nose.py) |
| Vision: YOLO evidence → stank score | ⏳ planned | |
| Speaker picker (laptop vs Bluetooth) | ⏳ planned | |
| Moss roast matching · Zo Stank Board · Firecrawl library | ⏳ planned | |
| Autonomous driving | ⏳ planned | |
| Real gas sensor on the car (BME688 over the car link, `--smell car`) | 🧪 software built, needs patched firmware flashed + hardware test | `bridge.py`, `nose.py` |

## How it works (target design)

1. **Drive:** an ELEGOO Smart Robot Car V4.0 roams the room. The **laptop is the brain**. `bridge.py` talks to the car over its WiFi and serves the dashboard.
2. **Spot:** **YOLO runs locally on the Mac**, finding people in the camera feed and steering the car toward them. No frames leave the laptop.
3. **Judge:** YOLO also spots the "evidence" around you (energy drinks, snack stash, open laptop, arms raised…), and ShowerBot turns it into a playful **stank score** (0–100). The dashboard shows the boxes and evidence, so everyone can see its (very scientific) reasoning.
4. **Roast:** **Moss** semantic search picks the line that best matches the evidence from our library, and a **recorded team voice clip** plays on the chosen Mac output (laptop or Bluetooth speaker).
5. **Rank:** the verdict (score, roast line, time; **no images**) posts to the public **Stank Board** hosted on **Zo**.
6. *Stretch:* a BME688 gas sensor adds a "sniff test" when the car is close.

## Hardware

| Part | Notes |
|---|---|
| ELEGOO Smart Robot Car Kit V4.0 (assembled) | Arduino UNO + SmartCar shield (motors, ultrasonic, 3× line sensors, pan servo, MPU6050). **Runs ELEGOO's stock firmware; no flashing needed.** |
| ESP32-WROVER camera module (in kit) | Runs the car's WiFi network, the camera stream, and the command bridge to the UNO |
| MacBook (Apple Silicon) | Runs `bridge.py` and the dashboard |
| Phone + USB cable, **or** USB-C Ethernet adapter | Internet for the online parts while the Mac's WiFi is on the car ([why](docs/TECHNICAL.md#networking-the-no-internet-problem)) |
| Bluetooth speaker (optional) | Or the laptop speakers |
| Spare charged battery pack | Motors slow down as the battery drops |
| *Stretch:* Seengreat BME688 sensor | I2C, 3.3 V/5 V, address 0x77 |

## Quickstart (what runs today)

1. On the car's shield, set the switch to **"cam"**. Power on the car.
2. **Close the ELEGOO phone app.** The camera serves one viewer at a time.
3. Join the Mac to the car's WiFi (`ELEGOO-XXXXXXXX`).
4. Run the bridge:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python bridge.py            # or: python bridge.py --car-host 192.168.4.1 --port 8080
   ```
5. Open **http://localhost:8080** and drive.

| Control | Action |
|---|---|
| Hold a pad arrow, or `W/A/S/D` / arrow keys | Drive (diagonals work) |
| `Space` or **Stop** | Stop |
| Speed slider | 60–255 |
| Camera direction slider, **Center**, `Q`/`E`/`C` | Look left/right (camera + ultrasonic); the camera faces forward whenever the car connects |
| **Tell them to shower** | Play a random recording (or speak a typed/random line in computer-voice mode) |
| Reminder voice | *My recordings* or *Computer voice* |
| Add files / drag-and-drop | Upload voice clips (saved to `clips/`). Export Voice Memos as `.m4a`. |
| Mute checkbox | The robot keeps sensing but stops speaking |
| Trigger stink / Recalibrate | Fake a smell spike (with `--smell fake`) / relearn the air baseline |

**Kill switches:** `Space`/**Stop** on the dashboard, closing the tab (the bridge stops the car), or the car's **power switch**.

### Getting internet at the same time
While the Mac's WiFi is joined to the car, it has no internet. The core loop works offline, but Moss, the Zo board and first-time YOLO downloads need internet. Plug in a phone over USB and turn on **Personal Hotspot** (or use Ethernet). Then go to **System Settings → Network → ⋯ → Set Service Order** and drag it **above Wi-Fi**. To check:
```bash
route get 192.168.4.1 | grep interface   # → Wi-Fi (the car)
route get 1.1.1.1     | grep interface   # → the phone/Ethernet (internet)
```

## Sponsors

| Sponsor | Plan |
|---|---|
| **Zo** (primary) | Hosts the public **Stank Board** leaderboard, ranked by ShowerBot's stank confidence. The laptop posts verdicts (no images). |
| **Moss** | Real-time semantic search that matches YOLO's evidence tags to the best roast line. |
| **Firecrawl** | Scrapes shower puns and hygiene facts to seed the roast library, which we then curate by hand. |
| *OpenSwarm / Chatforce* | Stretch ideas; one could power the conversational layer. |

## Privacy & consent
- The car only approaches attendees who **opted in**, and there's a sign at the demo table.
- **Camera frames never leave the laptop.** YOLO runs on-device, and nothing is stored.
- YOLO only sees objects and poses, so the score is based on props like drinks, snacks and laptops, never on your body, face or identity.
- The Stank Board shows a shirt-color alias (e.g. "Blue Shirt #4"), a score, the evidence and a roast line. Ask us and we'll delete your entry.

## Team

| Name | Background |
|---|---|
| Ethan Niu | CS Master's, USC |
| Ricky Duka | 2nd year, Folsom College |
| Scott Figueroa-Weston | Software Engineer, Google |
| Jake Recharte | 3rd year CS, San Diego State |
| Andrew Link | Software Architecture, Tria Federal |

## License
MIT. See [LICENSE](LICENSE).
