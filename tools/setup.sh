#!/usr/bin/env bash
# One-time setup. RUN THIS ON REAL WIFI, DAYS BEFORE THE DEMO.
#
# Two of these three steps need the internet, and the laptop will be joined to the
# car's ELEGOO access point on the day. Neither PyTorch nor the YOLO weights nor
# ElevenLabs will download from there.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"

echo "==> Installing Python dependencies (ultralytics pulls PyTorch, >1 GB)"
"$PY" -m pip install -r requirements.txt

echo "==> Fetching YOLO weights into models/"
mkdir -p models
WEIGHTS="models/yolo11n.pt"
if [ -f "$WEIGHTS" ]; then
  echo "    already have $WEIGHTS"
else
  curl -fL --progress-bar -o "$WEIGHTS" \
    https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt
fi

echo "==> Warming the model (first load builds caches)"
"$PY" - <<'PYCHECK'
from ultralytics import YOLO
import numpy as np
m = YOLO("models/yolo11n.pt")
m.predict(np.zeros((320, 320, 3), dtype="uint8"), imgsz=320, verbose=False)
print("    YOLO loads and runs.")
PYCHECK

echo "==> Rendering ElevenLabs lines into audio/"
if [ -n "${ELEVENLABS_API_KEY:-}" ]; then
  "$PY" tools/gen_voices.py
else
  echo "    ELEVENLABS_API_KEY not set -- skipping."
  echo "    The demo will fall back to the macOS 'say' voice."
fi

echo
echo "Done. Try it with no car and no sensor:"
echo "    python bridge.py --camera 0 --smell fake"
