"""
ShowerBot bridge for the Elegoo Smart Robot Car Kit V4.

Holds the TCP connection to the car (port 100), answers the car's heartbeat, polls
the ultrasonic and line sensors, owns the camera and runs YOLO person detection on
it, reads the smell sensor, and decides when to insult somebody.

The rule the whole thing exists to enforce: speak only when the air is foul AND a
person is in frame.

Setup:
  1. Car's shield switch on "cam", car running Elegoo's stock Arduino firmware.
  2. Join the laptop to the car's ELEGOO-XXXX WiFi network.
  3. pip install -r requirements.txt  &&  bash tools/setup.sh
  4. python bridge.py            (or: python bridge.py --car-host 192.168.4.1)
  5. Open http://localhost:8080

Audio clips: drop .m4a / .mp3 / .wav files into the "clips" folder next to this
file, or drag them onto the dashboard. The laptop plays them.
No car? No sensor? The whole pipeline still runs:
     python bridge.py --camera 0 --smell fake

Public Stank Board on Zo (optional): set ZO_BOARD_URL and ZO_INGEST_KEY (in the
environment or .env) and the leaderboard is mirrored there, text only.
"""
import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

from aiohttp import WSMsgType, web

import faces as faces_mod
import nose as nose_mod
import reactor as reactor_mod
import vision as vision_mod
import voice as voice_mod
import zoboard as zoboard_mod

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv is optional; plain environment variables work too

CAR_HOST = "192.168.4.1"
CAR_PORT = 100
DASHBOARD_PORT = 8080

# If the dashboard stops sending drive updates for this long, the car stops.
# This is the safety net for a closed tab, a dropped WebSocket, or a stuck key.
DRIVE_TIMEOUT = 0.5

# Pan servo (camera + ultrasonic). On this car 0° points right, so "forward" is
# the middle. If the camera isn't straight at center, adjust PAN_CENTER (the
# firmware moves in 10° steps; for smaller errors, re-seat the servo horn).
PAN_CENTER = 90
PAN_MIN, PAN_MAX = 10, 170  # the firmware clamps to this range

# Vision pushes updates far faster than a human can read them. Coalesce state
# broadcasts to this interval so the WebSocket is not flooded.
BROADCAST_INTERVAL = 0.1

# Stock firmware "rocker" directions (command N=102, parameter D1).
DIRECTIONS = {
    "forward": 1, "back": 2, "left": 3, "right": 4,
    "forward_left": 5, "back_left": 6, "forward_right": 7, "back_right": 8,
}

FRAME_RE = re.compile(r"\{[^{}]*\}")      # car frames are brace-delimited
REPLY_RE = re.compile(r"^\{(\w+)_(.*)\}$")  # replies look like {<H tag>_<value>}

# BME688 poll (patched firmware, N=24): every this many ultrasonic polls, ~3 s.
# The firmware only samples every 3 s, so asking faster just repeats a reading.
ENV_POLL_EVERY = 10


def parse_env(value):
    """Turn an N=24 D1=4 reply value into a nose reading, or None.

    The firmware sends "<temp 0.01 C>,<humidity 0.01 %RH>,<pressure Pa>,<gas ohms>",
    or "none" before its first sample. Gas is 0 until the heater is stable, which
    nose.intensity() already skips.
    """
    try:
        t, h, p, g = (int(x) for x in value.split(","))
    except ValueError:
        return None
    return {"gas_ohms": g, "temp_c": t / 100, "rh": h / 100, "hpa": round(p / 100, 1)}


class Hub:
    """The one place state lives, and the one thing that talks to dashboards.

    `update()` is deliberately synchronous so vision and nose callbacks can poke it
    from anywhere without awaiting. Sending is done by a single coalescing loop.
    """

    def __init__(self):
        self.clients = set()
        self.dirty = asyncio.Event()
        self.state = {
            "connected": False,
            "distance": None,
            "line": [None, None, None],
            "look": 0,
            "people": 0,
            "person": False,
            "near": False,
            "vision": {"fps": 0.0, "online": False, "enabled": False},
            "smell": {"score": 0.0, "stinky": False, "raw": {}, "baseline": None,
                      "warming": True, "warmup_left": nose_mod.WARMUP_S, "source": "none"},
            "muted": False,
            "voice": "none",
            "last_reaction": None,
        }

    def update(self, **changes):
        self.state.update(changes)
        self.dirty.set()

    def event(self, payload):
        """Fire-and-forget message outside the coalesced state stream."""
        asyncio.create_task(self._send(json.dumps(payload)))

    async def _send(self, msg):
        for ws in list(self.clients):
            try:
                await ws.send_str(msg)
            except Exception:
                self.clients.discard(ws)

    async def broadcast_loop(self):
        while True:
            await self.dirty.wait()
            self.dirty.clear()
            await self._send(json.dumps({"type": "state", **self.state}))
            # Anything that changes during this nap rides along in the next send.
            await asyncio.sleep(BROADCAST_INTERVAL)


class Car:
    def __init__(self, host, port, hub, smell=None):
        self.host, self.port = host, port
        self.hub = hub
        self.smell = smell  # a nose CarSmellSource when --smell car, else None
        self.writer = None
        self.write_lock = asyncio.Lock()
        self.last_drive = 0.0
        self.moving = False

    # ---------- commands ----------
    async def send(self, cmd):
        if not self.writer:
            return False
        frame = cmd if isinstance(cmd, str) else json.dumps(cmd, separators=(",", ":"))
        async with self.write_lock:
            try:
                self.writer.write(frame.encode())
                await self.writer.drain()
                return True
            except (ConnectionError, OSError):
                return False

    async def drive(self, direction, speed):
        code = DIRECTIONS.get(direction)
        if code is None:
            return
        speed = max(0, min(255, int(speed)))
        self.last_drive = time.monotonic()
        self.moving = True
        await self.send({"H": "drv", "N": 102, "D1": code, "D2": speed})

    async def stop(self):
        self.moving = False
        await self.send({"H": "stop", "N": 100})  # clear functions, standby

    async def look(self, offset):
        """Point the camera `offset` degrees from forward: negative = left, positive = right."""
        angle = round((PAN_CENTER - int(offset)) / 10) * 10  # firmware snaps to 10° anyway
        angle = max(PAN_MIN, min(PAN_MAX, angle))
        await self.send({"H": "pan", "N": 5, "D1": 1, "D2": angle})
        self.hub.update(look=PAN_CENTER - angle)

    # ---------- connection lifecycle ----------
    async def run(self):
        while True:
            try:
                print(f"Connecting to car at {self.host}:{self.port} ...")
                reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=5
                )
                print("Car connected.")
                self.hub.update(connected=True)
                await self.look(0)  # face forward on every (re)connect
                tasks = [
                    asyncio.create_task(self.read_loop(reader)),
                    asyncio.create_task(self.poll_loop()),
                    asyncio.create_task(self.watchdog()),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    if t.exception():
                        print(f"Car link dropped: {t.exception()!r}")
            except (OSError, asyncio.TimeoutError) as e:
                print(f"Can't reach the car ({e!r}). Is the laptop on the ELEGOO WiFi?")

            if self.writer:
                self.writer.close()
            self.writer = None
            self.moving = False
            self.hub.update(connected=False, distance=None, line=[None, None, None])
            await asyncio.sleep(2)

    async def read_loop(self, reader):
        buf = ""
        while True:
            # The car sends {Heartbeat} every second, so 5 s of silence means it's gone.
            chunk = await asyncio.wait_for(reader.read(512), timeout=5)
            if not chunk:
                raise ConnectionError("car closed the connection")
            buf += chunk.decode(errors="ignore")
            last = 0
            for m in FRAME_RE.finditer(buf):
                await self.handle(m.group())
                last = m.end()
            buf = buf[last:][-512:]

    async def handle(self, frame):
        if frame == "{Heartbeat}":
            await self.send("{Heartbeat}")  # miss 3 of these and the car drops us
            return
        m = REPLY_RE.match(frame)
        if not m:
            return
        tag, value = m.groups()
        try:
            if tag == "dist":
                self.hub.update(distance=int(value))
            elif tag in ("L0", "L1", "L2"):
                line = list(self.hub.state["line"])
                line[int(tag[1])] = int(value)
                self.hub.update(line=line)
            elif tag == "env" and self.smell:
                reading = parse_env(value)
                if reading:
                    self.smell.push(reading)
        except ValueError:
            pass  # e.g. {dist_ok} acknowledgements

    async def poll_loop(self):
        n = 0
        while True:
            await self.send({"H": "dist", "N": 21, "D1": 2})  # ultrasonic distance, cm
            await asyncio.sleep(0.3)
            n += 1
            if self.smell and n % ENV_POLL_EVERY == 0:
                await self.send({"H": "env", "N": 24, "D1": 4})  # BME688: t,h,p,gas
            if n % 3 == 0:  # line sensors change less often; poll them slower
                for i in range(3):  # 0 = left, 1 = middle, 2 = right
                    await self.send({"H": f"L{i}", "N": 22, "D1": i})
                    await asyncio.sleep(0.08)

    async def watchdog(self):
        while True:
            await asyncio.sleep(0.1)
            if self.moving and time.monotonic() - self.last_drive > DRIVE_TIMEOUT:
                await self.stop()


class ShowerBot:
    """Ties vision + nose + reactor + voice together."""

    def __init__(self, hub, vis, nose, reactor, voice):
        self.hub = hub
        self.vision = vis
        self.nose = nose
        self.reactor = reactor
        self.voice = voice
        self.last_roast = ""  # last automatic roast, shown on the Zo board
        hub.update(voice=voice.backend, vision=vis.snapshot())

    # Both callbacks are sync -- they are called from vision/nose internals.
    def on_vision(self, snap):
        self.hub.update(people=snap["people"], person=snap["person"],
                        near=snap.get("near", False), vision=snap)
        self.evaluate()

    def on_smell(self, snap):
        self.hub.update(smell=snap)
        self.evaluate()

    def evaluate(self):
        # Someone just stepped close: ask before anything else, so a roast can't
        # talk over the consent prompt.
        ask = self.reactor.ask_consent(self.hub.state["near"])
        if ask:
            self.speak(ask)
            return
        reaction = self.reactor.consider(
            person=self.hub.state["person"],
            stinky=self.hub.state["smell"]["stinky"],
        )
        if reaction:
            self.speak(reaction)

    def consented(self):
        reaction = self.reactor.thank()
        if reaction:
            self.speak(reaction)

    def manual(self, text=None):
        reaction = self.reactor.manual(text=text)
        if reaction:
            self.speak(reaction)

    def speak(self, reaction):
        payload = reaction.as_dict()
        if reaction.reason == "auto":
            self.last_roast = reaction.text
        self.hub.update(last_reaction=payload)
        self.hub.event({"type": "reaction", **payload})
        asyncio.create_task(self._say(payload))

    async def _say(self, payload):
        backend = await self.voice.say(payload["text"])
        # Tell the dashboard which backend actually made the noise -- if it says
        # "none", the browser speaks the line itself as a last resort.
        self.hub.event({"type": "spoken", "backend": backend, **payload})

    def set_muted(self, muted):
        self.reactor.muted = bool(muted)
        self.hub.update(muted=self.reactor.muted)


HERE = Path(__file__).parent
CLIPS_DIR = HERE / "clips"
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".caf"}
MAX_UPLOAD_MB = 50


def list_clips():
    CLIPS_DIR.mkdir(exist_ok=True)
    return sorted(p.name for p in CLIPS_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def safe_name(name):
    name = Path(name or "clip").name  # drop any directory parts
    stem = re.sub(r"[^\w\- ]", "", Path(name).stem).strip() or "clip"
    ext = Path(name).suffix.lower()
    candidate = f"{stem}{ext}"
    n = 2
    while (CLIPS_DIR / candidate).exists():  # never overwrite an existing clip
        candidate = f"{stem} {n}{ext}"
        n += 1
    return candidate


def make_app(car, bot, hub, source, board, zo):
    async def index(request):
        return web.FileResponse(HERE / "dashboard.html")

    async def leaderboard_page(request):
        return web.FileResponse(HERE / "leaderboard.html")

    async def leaderboard_list(request):
        return web.json_response(board.ranked())

    async def leaderboard_delete(request):
        if not board.delete(request.match_info["id"]):
            return web.json_response({"error": "no such entry"}, status=404)
        hub.event({"type": "leaderboard", "entries": board.ranked()})
        zo.remove(request.match_info["id"])
        return web.json_response({"ok": True})

    async def stream(request):
        """Re-serve the camera as MJPEG, annotated, to as many viewers as we like."""
        resp = web.StreamResponse(headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={vision_mod.BOUNDARY}",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        })
        await resp.prepare(request)
        boundary = f"--{vision_mod.BOUNDARY}\r\n".encode()
        try:
            async for jpeg in bot.vision.frames():
                await resp.write(boundary
                                 + b"Content-Type: image/jpeg\r\n"
                                 + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                                 + jpeg + b"\r\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            print(f"stream: viewer dropped ({e!r})")
        return resp

    async def smell_push(request):
        """Ingest for an ESP32 riding on the car: POST {"tvoc": 220, "eco2": 850}."""
        if source.name != "http":
            return web.json_response({"error": "bridge not started with --smell http"},
                                     status=409)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "expected a JSON object"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "expected a JSON object"}, status=400)
        source.push(data)
        return web.json_response({"ok": True})

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        hub.clients.add(ws)
        await ws.send_str(json.dumps({"type": "state", **hub.state}))
        await ws.send_str(json.dumps({"type": "smell_history", "points": bot.nose.history()}))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                kind = data.get("type")
                if kind == "drive":
                    await car.drive(data.get("dir"), data.get("speed", 150))
                elif kind == "stop":
                    await car.stop()
                elif kind == "look":
                    await car.look(data.get("offset", 0))
                elif kind == "say_now":
                    bot.manual(text=(data.get("text") or "").strip() or None)
                elif kind == "mute":
                    bot.set_muted(data.get("muted", False))
                elif kind == "fake_smell":
                    if isinstance(source, nose_mod.FakeSmellSource):
                        source.spike(float(data.get("level", 90)),
                                     float(data.get("seconds", 8)))
                elif kind == "recalibrate":
                    bot.nose.meter.recalibrate()
                    hub.update(smell=bot.nose.snapshot())
        finally:
            hub.clients.discard(ws)
            if not hub.clients:
                await car.stop()  # last dashboard closed: stop the car
        return ws

    async def clips_list(request):
        return web.json_response(list_clips())

    async def clips_upload(request):
        CLIPS_DIR.mkdir(exist_ok=True)
        saved, skipped = [], []
        reader = await request.multipart()
        async for part in reader:
            if not part.filename:
                continue
            if Path(part.filename).suffix.lower() not in AUDIO_EXTS:
                skipped.append(part.filename)
                continue
            name = safe_name(part.filename)
            with open(CLIPS_DIR / name, "wb") as f:
                while chunk := await part.read_chunk():
                    f.write(chunk)
            saved.append(name)
        return web.json_response({"saved": saved, "skipped": skipped, "clips": list_clips()})

    async def start(app):
        board.load()
        print(f"Leaderboard loaded: {len(board.entries)} people")
        zo.sync(board.ranked())
        app["tasks"] = [
            asyncio.create_task(zo.run()),
            asyncio.create_task(hub.broadcast_loop()),
            asyncio.create_task(car.run()),
            asyncio.create_task(bot.vision.run()),
            asyncio.create_task(bot.nose.run()),
        ]

    async def stop(app):
        for t in app["tasks"]:
            t.cancel()
        await bot.voice.stop()

    CLIPS_DIR.mkdir(exist_ok=True)
    app = web.Application(client_max_size=MAX_UPLOAD_MB * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/stream", stream)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/clips", clips_list)
    app.router.add_post("/api/clips", clips_upload)
    app.router.add_static("/clips/", CLIPS_DIR)
    app.router.add_get("/leaderboard", leaderboard_page)
    app.router.add_get("/api/leaderboard", leaderboard_list)
    app.router.add_delete("/api/leaderboard/{id}", leaderboard_delete)
    faces_mod.CAPTURES_DIR.mkdir(exist_ok=True)
    app.router.add_static("/captures/", faces_mod.CAPTURES_DIR)
    app.router.add_post("/smell", smell_push)
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    return app


def main():
    parser = argparse.ArgumentParser(description="ShowerBot bridge")
    parser.add_argument("--car-host", default=CAR_HOST)
    parser.add_argument("--car-port", type=int, default=CAR_PORT)
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT)
    parser.add_argument("--bind", default="0.0.0.0",
                        help="0.0.0.0 lets teammates and the car's sensor board reach us")
    parser.add_argument("--camera-url", default=vision_mod.CAMERA_URL)
    parser.add_argument("--camera", type=int, default=None,
                        help="use a local webcam by index instead of the car (try 0)")
    parser.add_argument("--no-vision", action="store_true",
                        help="proxy the camera but skip YOLO")
    parser.add_argument("--model", default=str(vision_mod.DEFAULT_MODEL))
    parser.add_argument("--smell", choices=["fake", "serial", "http", "car"], default="fake",
                        help="car = BME688 on the UNO over the car link (patched firmware)")
    parser.add_argument("--serial-port", default=None)
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--no-capture", action="store_true",
                        help="don't photograph faces for the leaderboard")
    args = parser.parse_args()

    use_yolo, why = vision_mod.probe(not args.no_vision)
    print(f"Vision: {'person detection on' if use_yolo else 'OFF -- ' + why}")

    hub = Hub()
    source = nose_mod.make_source(args.smell, args.serial_port)
    car = Car(args.car_host, args.car_port, hub,
              smell=source if isinstance(source, nose_mod.CarSmellSource) else None)
    vis = vision_mod.Vision(camera_url=args.camera_url, camera_index=args.camera,
                            enabled=use_yolo, model_path=args.model)
    nose = nose_mod.Nose(source, on_update=lambda s: None)
    voice = voice_mod.Voice(enabled=not args.no_audio)
    reactor = reactor_mod.Reactor()

    bot = ShowerBot(hub, vis, nose, reactor, voice)
    vis.on_update = bot.on_vision

    board = faces_mod.Leaderboard()
    zo = zoboard_mod.ZoBoard(
        os.environ.get("ZO_BOARD_URL"), os.environ.get("ZO_INGEST_KEY"),
        status_fn=lambda: {"connected": hub.state["connected"], "distance": hub.state["distance"]},
    )
    capture_on, why = faces_mod.probe(use_yolo and not args.no_capture, args.model)
    if not use_yolo and not args.no_capture:
        why = "needs person detection"
    print(f"Face capture: {'on' if capture_on else 'OFF -- ' + why}")
    if capture_on:
        capturer = faces_mod.Capturer(
            faces_mod.FaceEmbedder(), board,
            score_fn=lambda: hub.state["smell"]["score"],
            on_change=lambda rows: (hub.event({"type": "leaderboard", "entries": rows}),
                                    zo.sync(rows, bot.last_roast)),
            on_consent=bot.consented,
        )
        vis.on_frame = capturer.on_frame
    nose.on_update = bot.on_smell
    bot.nose = nose
    hub.update(smell=nose.snapshot())

    print(f"Dashboard: http://localhost:{args.port}")
    print(f"Leaderboard: http://localhost:{args.port}/leaderboard")
    web.run_app(make_app(car, bot, hub, source, board, zo),
                host=args.bind, port=args.port, print=None)


if __name__ == "__main__":
    main()
