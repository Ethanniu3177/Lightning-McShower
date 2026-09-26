# ⚡🚿 Lightning McShower

> **Ka-chow… you might want a shower.**
> **ShowerBot** is a little robot car that rolls up to you at a hackathon, gives you a once-over, and lets you know (politely-ish) whether it's time to hit the showers. Its verdicts go on a public **Stank Board**.

Built at **ShowerHacks** (Sep 26, 2026). It's a comedy hack: the car roasts vibes, never people. Everyone it approaches has opted in (see [Privacy & consent](#privacy--consent)).

<!-- TODO: replace with demo GIF (record ~10s: approach → roast → board update) -->
![Demo GIF placeholder](docs/media/demo-placeholder.gif)

📐 [Technical design](docs/TECHNICAL.md) · 🗓️ [Sprint plan](docs/SPRINTS.md)

---

## To build

| Task | Owner | Status |
|---|---|---|
| Driving model | Ricky | ⏳ |
| Record our voices | All | ⏳ Playback + upload is built. Now we need the recordings. |
| Add a sniff module | Scott | 🧪 stretch |
| Output audio to speakers (Mac API) | Jake | ⏳ Clips play through the default output; speaker picker next |
| Update dashboard to show the streaming data | Ethan | ⏳ |
| Figure out how to integrate sponsors | **?** | ⏳ unassigned |
| *Potentially:* conversational AI reacting to data and speaking things out | Andrew | 🧪 stretch |
| Vision: local YOLO → stank score | **?** | ⏳ unassigned |

See [SPRINTS.md](docs/SPRINTS.md) for the checklists, timeline and fallback demo plan.

## What works right now

| Piece | Status | File |
|---|---|---|
| Laptop ↔ car link (TCP, heartbeat, auto-reconnect) | ✅ built | [`bridge.py`](bridge.py) |
| Browser dashboard: live camera, drive pad + WASD/arrows, speed, camera pan, `Space` = stop | ✅ built | [`dashboard.html`](dashboard.html) |
| Live ultrasonic distance + line sensors on the dashboard | ✅ built | both |
| Safety: car stops if drive updates stop for 0.5 s or the tab closes | ✅ built | `bridge.py` |
| "Tell them to shower" button + auto-remind when someone is within N cm | ✅ built | `dashboard.html` |
| Recorded voice clips: drop `.m4a/.mp3/.wav` into `clips/` or drag onto the dashboard; plays a random clip with no immediate repeats; computer-voice fallback | ✅ built | both |
| Vision: local YOLO → stank score from the camera | ⏳ planned | |
| Speaker picker (laptop vs Bluetooth) | ⏳ planned | |
| Moss roast matching · Zo Stank Board · Firecrawl library | ⏳ planned | |
| Autonomous driving | ⏳ planned | |
| Gas-sensor "sniff" (BME688) | 🧪 stretch | |

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
   pip install aiohttp
   python bridge.py            # or: python bridge.py --car-host 192.168.4.1 --port 8080
   ```
5. Open **http://localhost:8080** and drive.

| Control | Action |
|---|---|
| Hold a pad arrow, or `W/A/S/D` / arrow keys | Drive (diagonals work) |
| `Space` or **Stop** | Stop |
| Speed slider | 60–255 |
| Sensor direction slider | Pan the camera + ultrasonic |
| **Tell them to shower** | Play a random recording (or speak a typed/random line in computer-voice mode) |
| Reminder voice | *My recordings* or *Computer voice* |
| Add files / drag-and-drop | Upload voice clips (saved to `clips/`). Export Voice Memos as `.m4a`. |
| Auto-remind checkbox | Speak automatically when someone is within N cm (12 s cooldown) |

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
