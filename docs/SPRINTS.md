# Lightning McShower: Sprint Plan

**Today:** Sat Sep 26, 2026 · **Hard deadline: 7:00 pm PT** · **Team:** 5
**Updated ~12:40 pm** after pulling `bridge.py`, `dashboard.html` and the audio drop-in. Sprint 1 is mostly done, and clip playback is built.

## Owners (from the team task list)

| Area | Owner | Sprint |
|---|---|---|
| Dashboard: show streaming data | Ethan | 1 / 3 |
| Driving model (autonomy) | Ricky | 4 |
| Audio output to speakers (Mac) | Jake | 3 |
| Record our voices | **Everyone** | 3 |
| Conversational AI reactions | Andrew | Stretch |
| Sniff module (BME688) | Scott | Stretch |
| **Vision: local YOLO + stank score** | ⚠️ **unassigned**. Proposal: Scott, until sniff becomes relevant | 2 |
| **Sponsor integrations** (Zo board, Moss, Firecrawl) | ⚠️ **unassigned**. Proposal: Andrew (Zo) + Jake (Moss/Firecrawl, which feed audio) | 3 |

## Timeline

| Time (PT) | What |
|---|---|
| 12:30–1:00 | Sync: assign vision + sponsors, get keys, set up the internet uplink |
| 1:00–2:30 | Sprint 1 wrap-up (stream proxy) · Sprint 2 vision on the laptop webcam · Sprint 3 library + recording |
| **2:30** | ✅ **Checkpoint 1:** bridge serves the stream; YOLO boxes people + props on the laptop webcam; 20+ lines recorded |
| 2:30–3:30 | Integrate: YOLO score on the car feed → clip plays on the chosen speaker → Zo board updates |
| **3:30** | ✅ **Checkpoint 2 = MVD-2** (see ladder below) |
| 3:30–5:30 | Sprint 4 autonomy · stretch (sniff, conversational AI) only if MVD-2 is solid |
| **5:30** | 🧊 **Feature freeze** |
| 5:30–6:30 | Rehearse ×3, record the demo GIF, polish the docs |
| 6:30–6:45 | Submit (buffer) |
| **7:00** | **Deadline** |

---

## Sprint 1: Remote control (✅ mostly done; wrap-up by 2:30)

**Goal:** the laptop drives the car, reads its sensors, and shows the camera.

- [x] TCP link to `192.168.4.1:100` with reconnect (`bridge.py`)
- [x] Heartbeat echo
- [x] 8-way driving via N=102, speed control, stop via N=100
- [x] 0.5 s drive watchdog + stop when the dashboard disconnects
- [x] Ultrasonic + line-sensor polling pushed to the dashboard
- [x] Pan via N=5
- [x] Dashboard: live camera, hold-to-drive pad, WASD/arrows, `Space` = stop, sliders
- [ ] **Test all of the above on the real car** and note anything off (pan direction, speed feel, stream lag). Owner: Ethan
- [ ] **Stream proxy:** `bridge.py` reads `:81/stream` once and re-serves it at `/stream`, and the dashboard's `STREAM_URL` becomes `"/stream"`. Vision needs this. Owner: Ethan
- [ ] Internet uplink working next to the car WiFi (phone USB tether, service order). Owner: _anyone, first 30 min_
- [ ] `requirements.txt` + `.env.example`. Owner: _anyone_

**Definition of done:** drive a lap around a table from the dashboard, with the video served from `localhost:8080/stream`, while `curl https://api.anthropic.com` works from the same laptop.
**Risks:** don't reflash the UNO to ELEGOO v2.1.2 (it breaks pan). Close the ELEGOO phone app, because the camera serves one viewer.

---

## Sprint 2: Stank detection (1:00–3:30)

**Goal:** camera frame → person? → stank score + evidence tags, **all local with YOLO** (no cloud vision, no per-call cost, frames never leave the laptop).
**Owner:** ⚠️ _TBD_ (proposal: Scott)

- [ ] Setup (**while you have internet**): `pip install ultralytics` on Python 3.12. Pre-download `yolo11n.pt` + `yolo11n-pose.pt`. Confirm `device="mps"` works.
- [ ] `showerbot/vision.py`: `model.track(persist=True)` on the latest frame at `imgsz=320`. Output: person boxes + track IDs, props (COCO classes), center offset of the target. Target ≥ 10 fps.
- [ ] Evidence scorer: props overlapping the expanded person box (bottle/cup, snacks, laptop, backpack, phone), pose cue (arms raised), lingering time, and seeded per-track jitter → `score` 0–100 + tier + tags (see [TECHNICAL → Vision](TECHNICAL.md#vision-pipeline)). Collect over ~2 s.
- [ ] Alias from the shirt color (mean hue of the upper body box) + track ID → "Blue Shirt #4".
- [ ] Draw boxes, IDs and evidence onto the proxied `/stream` so the audience sees what the car sees.
- [ ] Hook into `bridge.py`: a **"Judge"** button on the dashboard scores the centered person and shows a verdict card (score, tier, alias, evidence).
- [ ] Auto-remind upgrade: require a **person detection**, not just distance (right now it fires on table legs).
- [ ] Test on teammates with and without props (hold up a Red Bull, raise your arms) and tune the point values so the tiers spread out.

**Definition of done:** point the car at a teammate, click Judge, and see boxes on the stream plus a score, tier and evidence tags within 3 s, with the Mac offline.
**Demo-able:** "the car rates your shower status, and shows its evidence."
**Risks:** slow inference next to the bridge (drop to `imgsz=320`, pose every 3rd frame, CoreML export) · scores feel random (show the evidence; tune the weights) · weights not downloaded before going on the car WiFi.

---

## Sprint 3: Call-out + sponsors (1:00–3:30, polish until 5:30)

**Goal:** verdict → best-matching recorded roast → plays on the chosen Mac output → Stank Board updates.

**Voices (Everyone, 1:00–2:30)**
- [ ] Write ~40–80 lines across tiers (**Fresh** compliments, Questionable, Shower Recommended, Code Brown Alert) and topics (hoodie, snacks, sleepy…). Two people sign off on tone.
- [ ] Record each person reading ~10–15 lines in Voice Memos → export `.m4a` → drag onto the dashboard (saved to `clips/`). Name them `<tier>-<topic>-<n>.m4a`.
- [x] Clip upload, listing and random playback with a computer-voice fallback (Ethan, `bridge.py` + `dashboard.html`)

**Audio output (Jake)**
- [ ] **Speaker picker** in the dashboard: `enumerateDevices()` → `audiooutput` dropdown → `player.setSinkId(id)` + a *Test sound* button. **VERIFY** in the demo browser. Fallback: Python `sounddevice` playback in the bridge, or just set the Mac's system output.
- [ ] Pick clips by the verdict (tier from the filename → Moss match on evidence) instead of at random.

**Moss + Firecrawl (proposal: Jake)**
- [ ] `scripts/scrape_roasts.py`: Firecrawl search/scrape → candidate lines. **Curate by hand**, then record the keepers.
- [ ] `scripts/build_index.py`: Moss `roasts` index with `{tier, clip}` metadata.
- [ ] `showerbot/roast.py`: query `"{tier}: {evidence tags}"`, filter recent, fallback to random-by-tier.

**Zo Stank Board (proposal: Andrew)**
- [ ] Zo-hosted service (or a public Zo Space page + API) with `POST/GET/DELETE`, a public auto-refreshing leaderboard ranked by score, no images.
- [ ] `showerbot/board.py`: async post with retry and a shared-secret header. Dashboard delete button for opt-outs.

**Definition of done:** clicking Judge plays a tier-appropriate, non-repeating team-voice roast on the selected speaker, and the entry appears on the public Zo board within ~5 s.
**Risks:** recording eats time (cap it at 45 min; `say` fills gaps) · Moss setup (a random-by-tier fallback always works) · Zo deploy (Space fallback) · Bluetooth latency (test by 3:30).

---

## Sprint 4: Autonomy / driving model (3:30–5:30)

**Owner:** Ricky

- [ ] `showerbot/autonomy.py`: an asyncio task inside `bridge.py` that calls `car.drive()` at ≥ 4 Hz (so the 0.5 s watchdog still protects it).
- [ ] Dashboard **Manual / Auto** toggle. Any manual drive or stop input switches back to Manual instantly.
- [ ] Wander: slow forward. If ultrasonic < 30 cm, back up + random turn.
- [ ] Track: turn toward the person's bbox center. **Don't pan while moving** (pan blocks the UNO ~500 ms).
- [ ] Judge at ~1.5 m → approach → stop at the auto-remind distance → roast → back up + turn ~120° → 15 s no-engage.
- [ ] Safety test: pull the laptop's WiFi mid-drive and confirm the car stops (expect ≤ 4 s). Keep `MAX_SPEED` ~150.

**Definition of done:** 3 full loops in a row (find → judge → roast → retreat) in the demo area, touching no one.
**Go/no-go at 5:15:** if it's flaky, demo **assisted mode** (a human drives, and judging + roasting fire automatically) and show Auto as a short clip.

---

## Stretch (only after Checkpoint 2 passes)

**Sniff module (Scott)**
- [ ] Wire the Seengreat BME688 to the shield's I2C (5 V OK, address 0x77, no conflict with the MPU6050 at 0x68).
- [ ] Patch the **original** ELEGOO sketch: Adafruit BME680 lib + `N=40` reply. Check flash/RAM.
- [ ] Blend into the score at weight 0.3. Treat it as a "sniff test" comedy beat.

**Conversational AI (Andrew)**
- [ ] Haiku generates 1-sentence reactions to live events (walking away, lingering, YOLO evidence tags; text only, no images), spoken via macOS `say`, max 1 per ~20 s.
- [ ] Optional OpenSwarm/Chatforce tie-in.

---

## Minimum Viable Demo ladder

| Level | What's shown | Status |
|---|---|---|
| **MVD-0** | Drive the car to a volunteer → click "Tell them to shower" → a random line plays | ✅ **Already works** with today's code |
| **MVD-1** | + recorded team voices | ✅ playback built; **needs recordings** (+ speaker picker) |
| **MVD-2** ⭐ target 3:30 | + real vision score + Moss-matched line + public Zo Stank Board | Sprints 2–3 |
| MVD-3 | + autonomous hunting | Sprint 4 |
| Bonus | + sniff, + conversational reactions | Stretch |

**Demo script (~2 min):** pitch one-liner → show the Stank Board on the screen → a volunteer steps up → the car approaches → roast in a team voice → the board updates live → a second volunteer gets a **Fresh** compliment → sponsor call-outs (Zo, Moss, Firecrawl) → STOP.

## Submission checklist (by 6:30)
- [ ] Demo GIF in `docs/media/`, linked from the README
- [ ] Public Stank Board URL in the README
- [ ] No `.env` or keys committed; no attendee photos in the repo
- [ ] LICENSE (MIT) added
- [ ] Submission text lists sponsors: Zo, Moss, Firecrawl
- [ ] Checkboxes above reflect reality
