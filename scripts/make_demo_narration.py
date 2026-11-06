#!/usr/bin/env python3
"""Generate the demo voiceover with Piper (free, offline, open-source TTS).

Reads docs/demo/narration.json ({caption title or card name: line}) and writes
docs/demo/narration/<key>.wav plus durations.json, which the demo spec uses to
hold each caption long enough and make_demo_video.sh mixes into the video.

    pip install piper-tts
    # voice: https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-lessac-medium.tar.gz
    PIPER_VOICE=/path/en-us-lessac-medium.onnx python3 scripts/make_demo_narration.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import wave

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(root, "docs/demo/narration")
voice = os.environ.get("PIPER_VOICE")
piper = os.environ.get("PIPER_BIN") or shutil.which("piper")
if not voice or not piper:
    sys.exit("Set PIPER_VOICE to a .onnx voice and put piper on PATH (or set PIPER_BIN).")

lines = json.load(open(os.path.join(root, "docs/demo/narration.json")))
os.makedirs(out, exist_ok=True)
durations = {}
for key, text in lines.items():
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
    wav = os.path.join(out, slug + ".wav")
    subprocess.run([piper, "-m", voice, "-f", wav, "--length_scale", "1.05"], input=text.encode(),
                   check=True, capture_output=True)
    with wave.open(wav) as w:
        durations[key] = {"file": slug + ".wav", "seconds": round(w.getnframes() / w.getframerate(), 3)}
    print(f"{durations[key]['seconds']:5.1f}s  {key}")
json.dump(durations, open(os.path.join(out, "durations.json"), "w"), indent=2)
