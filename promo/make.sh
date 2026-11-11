#!/bin/bash
# Build the promo end to end. Narration clips are cached in build/audio; delete them to re-synthesize.
set -euo pipefail
cd "$(dirname "$0")"
[ -x build/.venv/bin/python ] || { python3 -m venv build/.venv && build/.venv/bin/pip -q install numpy; }
python3 tts.py
python3 timeline.py
node render.mjs cues
build/.venv/bin/python mix.py
node render.mjs video
python3 master.py
# the 9:16 cut: same timeline and soundtrack, vertical layout
PROMO_VERTICAL=1 node render.mjs video
python3 master.py proactive-agent-promo-vertical.mp4 video-v.mp4
