"""
Mirror the local leaderboard (faces.py) to the public Stank Board hosted on Zo.

The car never talks to Zo; the laptop does. Only text goes up -- a stable alias,
the peak score, a tier and the last roast line. Photos and face fingerprints
never leave the laptop.

sync() and remove() only put work on a queue, so a slow or offline board never
blocks the car. A background task posts with retry. Entries are keyed by the
leaderboard id, so a retry or a higher score just updates the same row.

Config (environment or .env):
  ZO_BOARD_URL    e.g. https://stank-board-<handle>.zocomputer.io
  ZO_INGEST_KEY   the same secret as STANK_KEY on the Zo service
If ZO_BOARD_URL is unset, this stays off and the bridge works as before.
"""
import asyncio

import aiohttp

RETRY_DELAYS = (1, 2, 5, 10, 30)  # then keep trying every 30 s
STATUS_EVERY = 5  # seconds between "car is live" pings

TIERS = [(80, "Code Brown Alert"), (55, "Shower Recommended"), (30, "Questionable"), (0, "Fresh")]


def tier_for(score):
    return next(name for floor, name in TIERS if score >= floor)


def to_verdict(entry, roast_line=""):
    """A leaderboard row, minus anything that could identify a face."""
    score = round(entry["peak_score"])
    return {
        "event_id": entry["id"],
        "ts": entry["last_seen"],
        "alias": f"Suspect #{entry['id'][:4].upper()}",
        "score": score,
        "tier": tier_for(score),
        "evidence": [f"seen {entry['sightings']}x"],
        "roast_line": roast_line or "",
    }


class ZoBoard:
    def __init__(self, url, key, status_fn=None):
        self.url = (url or "").rstrip("/")
        self.key = key or ""
        self.status_fn = status_fn  # returns {"connected": bool, "distance": int|None}
        self.queue = asyncio.Queue()
        self.sent = {}  # id -> score last pushed, so unchanged rows aren't resent

    @property
    def enabled(self):
        return bool(self.url)

    def sync(self, rows, roast_line=""):
        """Push new or changed rows, and delete rows that left the local board."""
        if not self.enabled:
            return
        ids = set()
        for entry in rows:
            ids.add(entry["id"])
            verdict = to_verdict(entry, roast_line)
            if self.sent.get(entry["id"]) != verdict["score"]:
                self.sent[entry["id"]] = verdict["score"]
                self.queue.put_nowait(("POST", "/api/verdicts", verdict))
        for gone in set(self.sent) - ids:
            self.remove(gone)

    def remove(self, id_):
        if self.enabled:
            self.sent.pop(id_, None)
            self.queue.put_nowait(("DELETE", f"/api/verdicts/{id_}", None))

    async def run(self):
        if not self.enabled:
            print("Zo Stank Board: off (set ZO_BOARD_URL to turn it on)")
            return
        print(f"Zo Stank Board: mirroring to {self.url}")
        headers = {"X-Stank-Key": self.key}
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            await asyncio.gather(self.post_loop(session), self.status_loop(session))

    async def request(self, session, method, path, payload=None):
        """True when done (success, or a client error that retrying won't fix)."""
        try:
            async with session.request(method, f"{self.url}{path}", json=payload) as r:
                if r.status < 400:
                    return True
                if r.status < 500:
                    print(f"Zo Stank Board rejected {method} {path}: {r.status} {await r.text()}")
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        return False

    async def post_loop(self, session):
        while True:
            method, path, payload = await self.queue.get()
            attempt = 0
            while not await self.request(session, method, path, payload):
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                attempt += 1
                print(f"Zo Stank Board unreachable; retrying {method} {path} in {delay}s")
                await asyncio.sleep(delay)

    async def status_loop(self, session):
        while True:
            if self.status_fn:
                await self.request(session, "POST", "/api/status", self.status_fn())
            await asyncio.sleep(STATUS_EVERY)
