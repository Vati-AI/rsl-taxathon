#!/usr/bin/env bash
# Encode the recorded demo (tests/frontend/demo) into docs/demo/vati-rsl-demo.mp4.
# Prerequisites: run the demo spec first (see tests/frontend/demo/playwright.demo.config.ts).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs/demo
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
VF="scale=1440:900:force_original_aspect_ratio=decrease,pad=1440:900:(ow-iw)/2:(oh-ih)/2:color=white,fps=30,format=yuv420p"

card() { # name seconds
  ffmpeg -loglevel error -y -loop 1 -t "$2" -i "$OUT/cards/$1.png" \
    -vf "$VF,fade=t=in:st=0:d=0.5,fade=t=out:st=$(echo "$2 - 0.5" | bc):d=0.5" \
    -c:v libx264 -preset veryfast -crf 20 "$TMP/$1.mp4"
}

scene() { # frames dir -> mp4 using real frame timestamps
  local dir="$OUT/frames/$1" list="$TMP/$1.txt"
  python3 - "$dir" "$list" <<'PY'
import sys, os
d, out = sys.argv[1], sys.argv[2]
rows = [l.split() for l in open(os.path.join(d, 'index.txt')) if l.strip()]
with open(out, 'w') as f:
    f.write('ffconcat version 1.0\n')
    for i, (name, ts) in enumerate(rows):
        dur = float(rows[i + 1][1]) - float(ts) if i + 1 < len(rows) else 2.0
        f.write(f"file '{os.path.abspath(os.path.join(d, name))}'\nduration {max(dur, 0.034):.3f}\n")
    f.write(f"file '{os.path.abspath(os.path.join(d, rows[-1][0]))}'\n")
PY
  ffmpeg -loglevel error -y -f concat -safe 0 -i "$list" -vf "$VF" -c:v libx264 -preset veryfast -crf 20 "$TMP/scene$1.mp4"
}

card intro 5; card s01 3.5; card s02 3.5; card s03 3.5; card outro 6
scene 01; scene 02; scene 03
printf "file '%s'\n" "$TMP/intro.mp4" "$TMP/s01.mp4" "$TMP/scene01.mp4" "$TMP/s02.mp4" "$TMP/scene02.mp4" \
  "$TMP/s03.mp4" "$TMP/scene03.mp4" "$TMP/outro.mp4" > "$TMP/all.txt"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$TMP/all.txt" -c copy -movflags +faststart "$OUT/vati-rsl-demo.mp4"
ffprobe -v error -show_entries format=duration -of default=nw=1 "$OUT/vati-rsl-demo.mp4"
echo "wrote $OUT/vati-rsl-demo.mp4"
