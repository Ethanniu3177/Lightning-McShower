# Lightning McShower

**Tagline:** A robot car that sees you, smells you, and, only when both are true, tells you to go take a shower.

---

## Inspiration

You walk into a CS class. Everyone knows, and nobody says anything.

We built the friend who will. Lightning McShower does the awkward part for you. It's a small robot car that rolls up and delivers the message in a voice it doesn't feel bad about.

## What it does

Lightning McShower is an ELEGOO robot car with a camera, a gas sensor and no filter.

- **It sees you.** YOLO pose detection runs on the laptop and draws a box around every person in the car's camera feed.
- **It smells you.** An air-quality sensor spends 30 seconds learning what the room normally smells like. After that it scores the air from 0 to 100 by how much worse it is than that baseline.
- **It roasts you, but only when both are true.** The whole project comes down to one rule:

  > Speak only when the air is foul **AND** a person is in frame.

  Bad air in an empty hallway is just a sensor reading, and a person with no smell is just a person. The joke only works when both happen at once.
- **The roast escalates.** During one encounter the robot goes from *mild* ("Beep boop. Shower status: overdue.") to *rude* ("My gas sensor just filed a complaint with management.") to *savage* ("BIOHAZARD DETECTED. EVACUATE."). It never says the same line twice in a row. The voices are ElevenLabs clips or recordings of our team.
- **The Hall of Stench.** This is an opt-in leaderboard that ranks people by the worst air they were ever caught in. To join, you raise a hand above your head for about a second. If you don't, you never show up on it.
- **You can drive it from a browser.** The dashboard shows live annotated video, a drive pad (WASD or the arrow keys), camera pan, the smell meter, a reaction log, a mute switch and a manual "Tell them to shower" button.

## How we built it

```
 ELEGOO car + our firmware                   MacBook: bridge.py (aiohttp)
 ┌──────────────────────────┐   TCP :100    ┌──────────────────────────────────┐
 │ Arduino UNO ⇄ ESP32-CAM  │ ────────────▶ │ Car link: heartbeat, drive,      │
 │ motors, ultrasonic, servo│ ◀──────────── │   0.5 s stop watchdog            │
 │                          │  MJPEG :81    │ Vision: YOLO11n-pose on Apple GPU│──▶ /stream (annotated)
 └──────────────────────────┘ ────────────▶ │ Nose: baseline + score + hysteresis
 BME688 ────── serial / HTTP POST ────────▶ │ Reactor: person AND stink gate   │──▶ Voice (ElevenLabs / say)
                                            │ Faces: YuNet + SFace, consent    │──▶ Hall of Stench
                                            └──────────────────────────────────┘
                                                    ▲ WebSocket
                                              dashboard.html (browser)
```

- **We flashed our own code onto the car to add a BME688 gas sensor.** For everything else we use ELEGOO's JSON-over-TCP protocol, which we learned by reading their firmware source. `bridge.py` drives with the 8-direction "rocker" command and answers the car's heartbeat every second. It also stops the car if drive commands pause for 0.5 s or the dashboard tab closes.
- **The laptop takes over the camera.** The ESP32-CAM only serves one viewer at a time. So the bridge becomes that viewer: it splits frames on JPEG markers and runs YOLO on some of them. Then it re-serves an annotated stream, which means the whole team can watch at once.
- **One model does two jobs.** `yolo11n-pose` runs on the Mac's GPU (MPS). It tells us whether a person is in frame, and it finds the wrist and nose keypoints used for the raise-your-hand opt-in. A person counts as present once they appear in 2 of the last 4 inferences. That way one dropped frame can't cut off a roast halfway through.
- **The nose scores air relative to the room.** The BME688 reports gas resistance, which *drops* as the air gets worse, so the nose inverts it. It also accepts ENS160/SGP30 readings (TVOC, eCO₂). Readings come in over USB serial or HTTP POST. The baseline is the median of the first 30 s. Hysteresis (on at 65, off at 45) keeps a borderline reading from setting off six roasts in a row. There's also a simulated source with a "Trigger stink" button, so the demo works without any hardware.
- **The reactor is a small, tested state machine.** It has a 15 s cooldown between lines, and the tiers reset after 45 s of quiet so a new person starts at "mild". The clock is passed in, so the tests can play out a whole encounter in microseconds.
- **Voice works offline.** The laptop has no internet while it's on the car's WiFi. So we pre-render every line with ElevenLabs ahead of time and play the clips with `afplay`. If a clip is missing, it falls back to macOS `say`, and after that to the browser's speech.
- **Faces stay on the laptop.** When someone opts in, OpenCV YuNet finds their face and SFace turns it into a 128-number fingerprint. A cosine-similarity check recognizes the same person on their next visit instead of making a duplicate entry. For someone seen in the last 3 seconds we loosen the match threshold, so turning their head doesn't create a second entry.
- **The dashboard and leaderboard are single HTML files that make no external requests.** They have to work on a WiFi network with no internet.
- **Tests:** 35 unit tests cover the reactor gate and the leaderboard and consent logic.

**Built with:** Python, aiohttp, WebSockets, Ultralytics YOLO11 (pose), PyTorch (MPS), OpenCV (YuNet, SFace), NumPy, pyserial, ElevenLabs, macOS `afplay`/`say`, HTML/CSS/JavaScript, ELEGOO Smart Robot Car V4.0, ESP32-CAM, Arduino UNO, Bosch BME688

## Challenges we ran into

- **The car's WiFi has no internet.** The laptop has to join the car's own access point to control it, and that access point goes nowhere. So everything that matters runs locally: YOLO, face matching and voice playback. We pre-render the ElevenLabs clips while we still have internet. We also turned off Ultralytics' telemetry so a request that can't get out doesn't hang the demo.
- **The camera only serves one viewer.** Our first dashboard used the stream directly, which left Python with no frames for YOLO. We fixed it by proxying the stream through the bridge.
- **The Apple GPU backend isn't thread-safe.** Two YOLO predictions running at once crashed the process with a Metal assertion. Now only one inference runs at a time and extra frames are dropped, never queued. That also protects the car link: the event loop must never block, because the car disconnects after three missed heartbeats.
- **Gas sensors drift, and every room smells different.** An absolute threshold that works in a clean lab is useless at hour 30 of a hackathon. Scoring against a baseline plus hysteresis fixed that.
- **The camera sits about 10 cm off the floor.** Many frames show only a chin or a torso. We only record someone when an actual face is found and is big enough to fingerprint reliably. To fix this, we found some PVC pipe laying around the event and stuck the camera and sniffer on top.
- **Opting in has to work from a low, cheap camera.** A thumbs-up is only a few pixels at that range, so we use a raised arm held for about a second. A wave as someone walks past doesn't count.

## Accomplishments that we're proud of

- **It's funny for the right reason.** Requiring both a person *and* bad air, plus escalating tiers and no repeated lines, makes it feel like a bit that builds rather than a sensor wired to a speaker.
- **Consent comes first, and data stays private.** Camera frames never leave the laptop. A face that hasn't opted in is kept only in memory, never on disk, and is forgotten 30 s after the person leaves. Anyone on the board can remove themselves with one click. The lines are written to be goofy, never cruel. If we wouldn't say it to someone's face with a grin, it isn't in the robot.
- **It works with zero hardware.** `python bridge.py --camera 0 --smell fake` runs the full pipeline on a laptop webcam, so anyone on the team can build and rehearse without the car.
- **It's built to hold up on stage.** It has a drive watchdog, automatic reconnects, a visible "No camera" card instead of a frozen frame, and fallbacks for voice, vision and smell.

## What we learned

- Reading the firmware source beats guessing the protocol. It also warned us that one firmware update would have broken camera panning.
- The venue's network is a design constraint, not an afterthought.
- Comedy needs state. The first version, which said a random line on every trigger, got old in a minute. Escalation and a cooldown are what made it funny.
- Asking for consent on a camera-equipped robot takes real design work. We had to pick a gesture a low, cheap camera can actually see.

## What's next for Lightning McShower

- **Autonomous driving:** wander, spot a person, approach, roast, back away. The state machine is designed, and the bridge's watchdog already protects it.
- **Evidence-based scoring:** use YOLO to spot "hackathon evidence" (energy drinks, snack stashes, open laptops) and show the robot's very scientific reasoning on screen.
- **Sponsor integrations:** a public Stank Board hosted on Zo, Moss semantic search to match roast lines to the evidence, and Firecrawl to seed the roast library.
- **A conversational layer** that reacts live ("Oh, you're backing away? Suspicious.").

## Try it

```bash
pip install -r requirements.txt
bash tools/setup.sh                      # YOLO + face models, ElevenLabs clips
python bridge.py --camera 0 --smell fake # no car or sensor needed
# open http://localhost:8080 and http://localhost:8080/leaderboard
```

Repo: https://github.com/Ethanniu3177/Lightning-McShower

## Team

| Name | Background |
|---|---|
| Ethan Niu | CS Master's, USC |
| Ricky Duka | 2nd year, Folsom College |
| Scott Figueroa-Weston | Software Engineer, Google |
| Jake Recharte | 3rd year CS, San Diego State |
| Andrew Link | Software Architecture, Tria Federal |
