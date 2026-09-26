#!/usr/bin/env python3
"""
Pre-render every line in lines.py to audio/ with ElevenLabs.

RUN THIS ON REAL WIFI, BEFORE THE DEMO.

During the demo the laptop is joined to the car's ELEGOO access point, which has
no route to the internet -- a live API call would hang and then fail on stage. So
the clips get baked to disk here, and voice.py only ever plays local files.

  export ELEVENLABS_API_KEY=sk_...
  python tools/gen_voices.py                 # generate anything missing
  python tools/gen_voices.py --list          # show what would be generated
  python tools/gen_voices.py --force         # re-render everything (costs credits)
  python tools/gen_voices.py --voice <id>    # pick a different voice

Already-rendered clips are skipped, so re-running after adding a line is cheap.

Hand-recorded clips ("record our voices - all") can live in audio/ too: add an
entry to audio/manifest.json with the exact line text and this script will leave
it alone.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lines  # noqa: E402

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
MANIFEST = AUDIO_DIR / "manifest.json"

# "Rachel" -- a clear default. Browse voices at elevenlabs.io/app/voice-library
# and pass --voice to use one with more attitude.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"


def slug(text, limit=48):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-")


def load_manifest():
    if not MANIFEST.exists():
        return {"clips": []}
    try:
        return json.loads(MANIFEST.read_text())
    except json.JSONDecodeError:
        print(f"! {MANIFEST} is not valid JSON; starting a fresh one")
        return {"clips": []}


def main():
    ap = argparse.ArgumentParser(description="Pre-render ShowerBot lines to audio/")
    ap.add_argument("--voice", default=os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true", help="re-render clips that exist")
    ap.add_argument("--list", action="store_true", help="show the plan and exit")
    args = ap.parse_args()

    AUDIO_DIR.mkdir(exist_ok=True)
    manifest = load_manifest()
    # Anything already in the manifest that is not one of our generated lines is a
    # hand-recorded clip. Keep it.
    known = {(c.get("tier"), c.get("text")) for c in manifest["clips"]}
    wanted = lines.all_lines()

    plan = []
    for tier, text in wanted:
        path = AUDIO_DIR / f"{tier}-{slug(text)}.mp3"
        plan.append((tier, text, path, path.exists()))

    todo = [p for p in plan if args.force or not p[3]]
    print(f"{len(plan)} line(s); {len(todo)} to render; voice={args.voice}")
    if args.list:
        for tier, text, path, exists in plan:
            print(f"  [{'have' if exists else ' -- '}] {tier:<7} {path.name}")
        return 0

    if todo:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            print("! ELEVENLABS_API_KEY is not set.\n"
                  "  The demo still works -- voice.py falls back to the macOS `say`\n"
                  "  voice -- but you will not get the ElevenLabs delivery.")
            return 1
        try:
            from elevenlabs.client import ElevenLabs
        except ImportError:
            print("! pip install elevenlabs")
            return 1

        client = ElevenLabs(api_key=key)
        for i, (tier, text, path, _) in enumerate(todo, 1):
            print(f"  [{i}/{len(todo)}] {tier}: {text[:58]}")
            try:
                audio = client.text_to_speech.convert(
                    voice_id=args.voice, model_id=args.model,
                    output_format=OUTPUT_FORMAT, text=text,
                )
                path.write_bytes(b"".join(audio))
            except Exception as e:
                print(f"    ! failed ({e!r}); skipping")

    # Rebuild the manifest from whatever actually landed on disk.
    clips = [c for c in manifest["clips"]
             if (c.get("tier"), c.get("text")) not in {(t, x) for t, x, _, _ in plan}
             and (AUDIO_DIR / c.get("file", "")).exists()]
    for tier, text, path, _ in plan:
        if path.exists():
            clips.append({"tier": tier, "text": text, "file": path.name})
    MANIFEST.write_text(json.dumps(
        {"voice_id": args.voice, "model": args.model, "clips": clips}, indent=2) + "\n")
    print(f"Wrote {MANIFEST} with {len(clips)} clip(s).")
    if len(clips) < len(plan):
        print(f"  ({len(plan) - len(clips)} line(s) have no audio and will use `say`.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
