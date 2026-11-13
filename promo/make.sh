#!/bin/bash
# Build the promo end to end. Narration clips are cached in build/audio; delete them to re-synthesize.
set -euo pipefail
cd "$(dirname "$0")"
[ -x build/.venv/bin/python ] || { python3 -m venv build/.venv && build/.venv/bin/pip -q install numpy; }
python3 tts.py
python3 timeline.py
node render.mjs cues
build/.venv/bin/python mix.py
# both cuts render at once (separate Chromium profiles); on 2 cores this saves about a fifth.
# Frames are JPEG q95 by default; PROMO_FRAMES=png renders lossless frames at about 2.5x the time.
node render.mjs video > build/render.log 2>&1 & h=$!
PROMO_VERTICAL=1 node render.mjs video > build/render-v.log 2>&1 & v=$!
wait $h; wait $v
python3 master.py
python3 master.py super-agent-promo-vertical.mp4 video-v.mp4
