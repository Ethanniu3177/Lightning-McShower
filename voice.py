"""
Speaking out loud, on the Mac, with no internet.

The laptop is joined to the car's ELEGOO access point during the demo, which means
there is no route to the ElevenLabs API. So generation and playback are split:

  tools/gen_voices.py   runs ONLINE, ahead of time -> audio/*.mp3 + audio/manifest.json
  voice.py (this file)  runs at demo time and only ever touches the local disk

Backends, in the order they are tried:
  1. a cached ElevenLabs clip for that exact line      (afplay)
  2. macOS `say`                                       (always there)
  3. nothing -- the dashboard still shows the text and falls back to the browser

That chain is the point: a teammate who never ran gen_voices.py still gets a
talking robot, just in the stock Mac voice.
"""
import asyncio
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
AUDIO_DIR = HERE / "audio"
MANIFEST = "manifest.json"

# Stock macOS voice used when there is no cached clip. `say -v '?'` lists the rest.
SAY_VOICE = "Samantha"
SAY_RATE = 190


class Voice:
    """Plays one line at a time. Never blocks the event loop."""

    def __init__(self, audio_dir=AUDIO_DIR, enabled=True):
        self.dir = Path(audio_dir)
        self.enabled = enabled
        self.clips = {}       # line text -> Path of a cached mp3
        self.proc = None
        self.backend = "none"
        self.load()

    def load(self):
        """Read audio/manifest.json, keeping only clips whose file is actually there."""
        self.clips = {}
        path = self.dir / MANIFEST
        if not path.exists():
            self.backend = "say" if self.have_say() else "none"
            return
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"voice: ignoring unreadable {path} ({e})")
            data = {}
        for clip in data.get("clips", []):
            f = self.dir / clip.get("file", "")
            if f.exists():
                self.clips[clip.get("text", "")] = f
        self.backend = "elevenlabs" if self.clips else ("say" if self.have_say() else "none")
        print(f"voice: {len(self.clips)} cached clip(s), backend={self.backend}")

    @staticmethod
    def have_say():
        return shutil.which("say") is not None

    @property
    def speaking(self):
        return self.proc is not None and self.proc.returncode is None

    async def say(self, text):
        """Speak `text`. Returns the backend used: elevenlabs | say | busy | off | none."""
        if not self.enabled:
            return "off"
        # The reactor has its own cooldown, but the manual button can be mashed.
        # Dropping the new line beats two robots talking over each other.
        if self.speaking:
            return "busy"

        clip = self.clips.get(text)
        if clip is not None and shutil.which("afplay"):
            cmd = ["afplay", str(clip)]
            backend = "elevenlabs"
        elif self.have_say():
            cmd = ["say", "-v", SAY_VOICE, "-r", str(SAY_RATE), text]
            backend = "say"
        else:
            return "none"

        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            print(f"voice: could not play ({e})")
            return "none"
        # Reap it in the background so callers are not stuck waiting for the audio.
        asyncio.create_task(self._reap(self.proc))
        return backend

    async def _reap(self, proc):
        try:
            await proc.wait()
        except asyncio.CancelledError:
            pass
        finally:
            if self.proc is proc:
                self.proc = None

    async def stop(self):
        if self.speaking:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
