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

NAR=$OUT/narration/durations.json
cardlen() { # name default -> seconds (narration + padding when a voiceover exists)
  python3 -c "import json,os,sys;d=json.load(open('$NAR')) if os.path.exists('$NAR') else {};print(max($2, d.get('$1',{}).get('seconds',0)+1.4))"
}
for c in intro:5 s01:3.5 s02:3.5 s03:3.5 outro:6; do card "${c%%:*}" "$(cardlen "${c%%:*}" "${c##*:}")"; done
scene 01; scene 02; scene 03
printf "file '%s'\n" "$TMP/intro.mp4" "$TMP/s01.mp4" "$TMP/scene01.mp4" "$TMP/s02.mp4" "$TMP/scene02.mp4" \
  "$TMP/s03.mp4" "$TMP/scene03.mp4" "$TMP/outro.mp4" > "$TMP/all.txt"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$TMP/all.txt" -c copy "$TMP/silent.mp4"

if [ -f "$NAR" ]; then
  # Voiceover: each card line starts 0.4s into its card, each scene line when its caption appeared.
  python3 - "$TMP" "$OUT" > "$TMP/mix.args" <<'PY'
import json, subprocess, sys
tmp, out = sys.argv[1], sys.argv[2]
nar = json.load(open(f"{out}/narration/durations.json"))
dur = lambda f: float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]))
clips, t = [], 0.0
for seg in ["intro", "s01", "scene01", "s02", "scene02", "s03", "scene03", "outro"]:
    if seg.startswith("scene"):
        n = seg[-2:]
        first = float(open(f"{out}/frames/{n}/index.txt").readline().split()[1])
        for b in json.load(open(f"{out}/frames/{n}/beats.json")):
            if b["title"] in nar:
                clips.append((nar[b["title"]]["file"], t + b["ts"] - first + 0.15))
    elif seg in nar:
        clips.append((nar[seg]["file"], t + 0.4))
    t += dur(f"{tmp}/{seg}.mp4")
args = []
for f, _ in clips:
    args += ["-i", f"{out}/narration/{f}"]
chains = "".join(f"[{i+1}:a]adelay={int(s*1000)}:all=1[a{i}];" for i, (_, s) in enumerate(clips))
mix = "".join(f"[a{i}]" for i in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0:duration=longest,apad[aout]"
print("\n".join(args + ["-filter_complex", chains + mix]))
PY
  mapfile -t MIX < "$TMP/mix.args"
  ffmpeg -loglevel error -y -i "$TMP/silent.mp4" "${MIX[@]}" -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 160k \
    -shortest -movflags +faststart "$OUT/vati-rsl-demo.mp4"
else
  ffmpeg -loglevel error -y -i "$TMP/silent.mp4" -c copy -movflags +faststart "$OUT/vati-rsl-demo.mp4"
fi
ffprobe -v error -show_entries format=duration -of default=nw=1 "$OUT/vati-rsl-demo.mp4"
echo "wrote $OUT/vati-rsl-demo.mp4"
