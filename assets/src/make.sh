#!/usr/bin/env bash
# Rebuild every image in assets/ from the pages in this folder. Needs Node 22+, ffmpeg and Chromium
# (CHROME=/path/to/chrome when it is not /snap/bin/chromium).
set -euo pipefail
cd "$(dirname "$0")"
out=..
r() { node render.mjs "$@" >/dev/null; echo "  $3"; }

r still banner.html "$out/banner.png" 1280 400 2
r still 'banner.html?lang=zh' "$out/banner_zh.png" 1280 400 2
r still 'banner.html?cover=1' "$out/cover.png" 1200 600 2
for n in 1 2 3 4; do
  r still "promise.html?n=$n" "$out/promise-$n.png" 1280 720 2
  r still "promise.html?n=$n&lang=zh" "$out/promise-${n}_zh.png" 1280 720 2
done
# start at 11 s, once the whole exchange is on screen
r gif demo.html "$out/demo.gif" 1200 600 12 14 11
r gif 'demo.html?lang=zh' "$out/demo_zh.gif" 1200 600 12 14 11
