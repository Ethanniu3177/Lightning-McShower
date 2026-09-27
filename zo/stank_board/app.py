"""
Stank Board: the public ShowerBot leaderboard, hosted on Zo.

The laptop's bridge.py pushes verdicts here. This service only stores and
serves them; it never talks to the car. No images, ever.

API:
  POST   /api/verdicts             X-Stank-Key: <secret>
         {event_id, ts, alias, score, tier, evidence, roast_line}
  GET    /api/board?limit=20       public: top scores + latest roasts + car status
  DELETE /api/verdicts/{event_id}  X-Stank-Key: <secret>   (opt-out)
  POST   /api/status               X-Stank-Key: <secret>   {connected, distance}

Run:
  pip install aiohttp
  STANK_KEY=<secret> python app.py          (PORT defaults to 8787)
"""
import json
import os
import sqlite3
import time
from pathlib import Path

from aiohttp import web

HERE = Path(__file__).parent
DB_PATH = Path(os.environ.get("STANK_DB", HERE / "stank.db"))
STANK_KEY = os.environ.get("STANK_KEY", "")
PORT = int(os.environ.get("PORT", 8787))

TIERS = {"Fresh", "Questionable", "Shower Recommended", "Code Brown Alert"}
CAR_LIVE_SECS = 15  # the bridge reports every ~5 s; older than this = offline


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verdicts (
                event_id   TEXT PRIMARY KEY,
                ts         REAL NOT NULL,
                alias      TEXT NOT NULL,
                score      INTEGER NOT NULL,
                tier       TEXT NOT NULL,
                evidence   TEXT NOT NULL,
                roast_line TEXT NOT NULL
            )""")


def clean(text, limit):
    return str(text or "").strip()[:limit]


def authorized(request):
    return bool(STANK_KEY) and request.headers.get("X-Stank-Key") == STANK_KEY


def row_to_dict(row):
    d = dict(row)
    d["evidence"] = json.loads(d["evidence"])
    return d


async def index(request):
    return web.FileResponse(HERE / "index.html")


async def post_verdict(request):
    if not authorized(request):
        raise web.HTTPUnauthorized(text="bad or missing X-Stank-Key")
    try:
        data = await request.json()
        event_id = clean(data["event_id"], 64)
        score = max(0, min(100, int(data["score"])))
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise web.HTTPBadRequest(text="need JSON with event_id and score")
    if not event_id:
        raise web.HTTPBadRequest(text="empty event_id")
    tier = data.get("tier") if data.get("tier") in TIERS else "Questionable"
    evidence = [clean(t, 32) for t in (data.get("evidence") or [])][:8]
    with db() as conn:
        # One row per leaderboard id: retries and higher scores update it in place.
        conn.execute(
            """INSERT INTO verdicts VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET
                 ts = excluded.ts, alias = excluded.alias, score = excluded.score,
                 tier = excluded.tier, evidence = excluded.evidence,
                 roast_line = CASE WHEN excluded.roast_line != '' THEN excluded.roast_line
                                   ELSE verdicts.roast_line END""",
            (event_id, float(data.get("ts") or time.time()),
             clean(data.get("alias"), 40) or "Mystery Hacker",
             score, tier, json.dumps(evidence), clean(data.get("roast_line"), 200)),
        )
    return web.json_response({"ok": True, "event_id": event_id})


async def delete_verdict(request):
    if not authorized(request):
        raise web.HTTPUnauthorized(text="bad or missing X-Stank-Key")
    with db() as conn:
        n = conn.execute("DELETE FROM verdicts WHERE event_id = ?",
                         (request.match_info["event_id"],)).rowcount
    return web.json_response({"ok": True, "deleted": n})


async def post_status(request):
    if not authorized(request):
        raise web.HTTPUnauthorized(text="bad or missing X-Stank-Key")
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(text="need JSON")
    request.app["car_status"].update(
        connected=bool(data.get("connected")),
        distance=data.get("distance"),
        seen=time.time(),
    )
    return web.json_response({"ok": True})


async def get_board(request):
    try:
        limit = max(1, min(100, int(request.query.get("limit", 20))))
    except ValueError:
        limit = 20
    with db() as conn:
        top = conn.execute(
            "SELECT * FROM verdicts ORDER BY score DESC, ts ASC LIMIT ?", (limit,)).fetchall()
        latest = conn.execute(
            "SELECT * FROM verdicts ORDER BY ts DESC LIMIT 8").fetchall()
        total = conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]
    status = request.app["car_status"]
    live = time.time() - status.get("seen", 0) < CAR_LIVE_SECS
    return web.json_response(
        {
            "top": [row_to_dict(r) for r in top],
            "latest": [row_to_dict(r) for r in latest],
            "total": total,
            "car": {"live": live and status["connected"], "bridge_online": live},
        },
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )


def make_app():
    init_db()
    app = web.Application(client_max_size=64 * 1024)
    app["car_status"] = {}  # updated in place by POST /api/status
    app.router.add_get("/", index)
    app.router.add_get("/api/board", get_board)
    app.router.add_post("/api/verdicts", post_verdict)
    app.router.add_delete("/api/verdicts/{event_id}", delete_verdict)
    app.router.add_post("/api/status", post_status)
    return app


if __name__ == "__main__":
    if not STANK_KEY:
        print("WARNING: STANK_KEY is not set, so every write will be rejected.")
    print(f"Stank Board on http://0.0.0.0:{PORT}")
    web.run_app(make_app(), host="0.0.0.0", port=PORT, print=None)
