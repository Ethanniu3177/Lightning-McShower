# Stank Board (Zo service)

The public mirror of the Hall of Stench. The laptop keeps the real leaderboard (`faces.py`, with photos, in `captures/`). [`zoboard.py`](../../zoboard.py) pushes a **text-only** copy of each row here: an alias, the peak score, a tier and the last roast line. Photos and face fingerprints never leave the laptop. This service stores the rows in SQLite and serves the public page. It never talks to the car.

```
car ──TCP──▶ bridge.py + faces.py (laptop) ──HTTPS POST──▶ this service on Zo ◀── public page (polls every 3 s)
```

## Run locally

```bash
pip install -r requirements.txt
STANK_KEY=dev-secret python app.py      # http://localhost:8787
```

Fake a verdict:

```bash
curl -X POST localhost:8787/api/verdicts -H 'X-Stank-Key: dev-secret' -H 'Content-Type: application/json' \
  -d '{"event_id":"test1","alias":"Blue Shirt #4","score":87,"tier":"Code Brown Alert","evidence":["caffeine","hoodie"],"roast_line":"Your hoodie has seen things."}'
```

## Deploy on Zo

1. Get this folder onto your Zo computer (`git clone`/`git pull` the repo there, or ask Zo to copy these three files).
2. Register it as a **service** ([Zo services](https://www.zo.computer/docs/services)): entrypoint `python app.py` in `zo/stank_board/`, with env vars `STANK_KEY=<long random secret>` and `PORT` set to the port Zo gives the service (**VERIFY** whether Zo sets `PORT` itself). Turn on the public HTTP URL.
3. Open the `*.zocomputer.io` URL. The board should say "Nobody judged yet".
4. On the laptop, put the URL and secret in `.env`:
   ```
   ZO_BOARD_URL=https://<your-service>.zocomputer.io
   ZO_INGEST_KEY=<same secret as STANK_KEY>
   ```
5. Run `bridge.py`. The log says `Zo Stank Board: mirroring to …`, any existing leaderboard rows go up right away, and new or worse scores follow within a few seconds.

Generate the secret with `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`.

## Opt-outs

Removing someone from the local leaderboard (the one-click delete) removes them from Zo too. To remove a row by hand:

```bash
curl -X DELETE "$ZO_BOARD_URL/api/verdicts/<event_id>" -H "X-Stank-Key: $ZO_INGEST_KEY"
```
The `event_id` is the local leaderboard id; it's also in `GET /api/board`.

## API

| Method | Path | Auth | Body / notes |
|---|---|---|---|
| `POST` | `/api/verdicts` | `X-Stank-Key` | `{event_id, ts, alias, score, tier, evidence, roast_line}`. Upsert by `event_id`, so retries and rising scores update the same row. |
| `GET` | `/api/board?limit=20` | public | `{top, latest, total, car: {live, bridge_online}}` |
| `DELETE` | `/api/verdicts/{event_id}` | `X-Stank-Key` | Opt-out |
| `POST` | `/api/status` | `X-Stank-Key` | `{connected, distance}`. The bridge sends this every 5 s for the "on patrol" badge. |
