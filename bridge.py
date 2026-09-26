"""
ShowerBot bridge for the Elegoo Smart Robot Car Kit V4.

Holds the TCP connection to the car (port 100), answers the car's heartbeat,
polls the ultrasonic and line sensors, and serves the dashboard at
http://localhost:8080 over a WebSocket.

Setup:
  1. Car's shield switch on "cam", car running Elegoo's stock Arduino firmware.
  2. Join the laptop to the car's ELEGOO-XXXX WiFi network.
  3. pip install aiohttp
  4. python bridge.py            (or: python bridge.py --car-host 192.168.4.1)
  5. Open http://localhost:8080

Audio clips: drop .m4a / .mp3 / .wav files into the "clips" folder next to this
file, or drag them onto the dashboard. The laptop plays them.
"""
import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from aiohttp import WSMsgType, web

CAR_HOST = "192.168.4.1"
CAR_PORT = 100
DASHBOARD_PORT = 8080

# If the dashboard stops sending drive updates for this long, the car stops.
# This is the safety net for a closed tab, a dropped WebSocket, or a stuck key.
DRIVE_TIMEOUT = 0.5

# Stock firmware "rocker" directions (command N=102, parameter D1).
DIRECTIONS = {
    "forward": 1, "back": 2, "left": 3, "right": 4,
    "forward_left": 5, "back_left": 6, "forward_right": 7, "back_right": 8,
}

FRAME_RE = re.compile(r"\{[^{}]*\}")      # car frames are brace-delimited
REPLY_RE = re.compile(r"^\{(\w+)_(.*)\}$")  # replies look like {<H tag>_<value>}


class Car:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.writer = None
        self.write_lock = asyncio.Lock()
        self.clients = set()
        self.state = {"connected": False, "distance": None, "line": [None, None, None]}
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

    async def pan(self, angle):
        angle = max(0, min(180, int(angle)))
        await self.send({"H": "pan", "N": 5, "D1": 1, "D2": angle})

    # ---------- connection lifecycle ----------
    async def run(self):
        while True:
            try:
                print(f"Connecting to car at {self.host}:{self.port} ...")
                reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=5
                )
                print("Car connected.")
                await self.set_state(connected=True)
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
            await self.set_state(connected=False, distance=None, line=[None, None, None])
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
                await self.set_state(distance=int(value))
            elif tag in ("L0", "L1", "L2"):
                line = list(self.state["line"])
                line[int(tag[1])] = int(value)
                await self.set_state(line=line)
        except ValueError:
            pass  # e.g. {dist_ok} acknowledgements

    async def poll_loop(self):
        n = 0
        while True:
            await self.send({"H": "dist", "N": 21, "D1": 2})  # ultrasonic distance, cm
            await asyncio.sleep(0.3)
            n += 1
            if n % 3 == 0:  # line sensors change less often; poll them slower
                for i in range(3):  # 0 = left, 1 = middle, 2 = right
                    await self.send({"H": f"L{i}", "N": 22, "D1": i})
                    await asyncio.sleep(0.08)

    async def watchdog(self):
        while True:
            await asyncio.sleep(0.1)
            if self.moving and time.monotonic() - self.last_drive > DRIVE_TIMEOUT:
                await self.stop()

    # ---------- dashboard updates ----------
    async def set_state(self, **changes):
        self.state.update(changes)
        msg = json.dumps({"type": "state", **self.state})
        for ws in list(self.clients):
            try:
                await ws.send_str(msg)
            except Exception:
                self.clients.discard(ws)


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


def make_app(car):
    async def index(request):
        return web.FileResponse(HERE / "dashboard.html")

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        car.clients.add(ws)
        await ws.send_str(json.dumps({"type": "state", **car.state}))
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
                elif kind == "pan":
                    await car.pan(data.get("angle", 90))
        finally:
            car.clients.discard(ws)
            await car.stop()  # dashboard closed or dropped: stop the car
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

    async def start_car(app):
        app["car_task"] = asyncio.create_task(car.run())

    async def stop_car(app):
        app["car_task"].cancel()

    CLIPS_DIR.mkdir(exist_ok=True)
    app = web.Application(client_max_size=MAX_UPLOAD_MB * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/clips", clips_list)
    app.router.add_post("/api/clips", clips_upload)
    app.router.add_static("/clips/", CLIPS_DIR)
    app.on_startup.append(start_car)
    app.on_cleanup.append(stop_car)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ShowerBot bridge")
    parser.add_argument("--car-host", default=CAR_HOST)
    parser.add_argument("--car-port", type=int, default=CAR_PORT)
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT)
    args = parser.parse_args()

    print(f"Dashboard: http://localhost:{args.port}")
    web.run_app(make_app(Car(args.car_host, args.car_port)),
                host="127.0.0.1", port=args.port, print=None)
