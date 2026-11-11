#!/bin/bash
# sheet.sh out.png a.png b.png c.png d.png  -> 2x2 contact sheet at half resolution
out=$1; shift
args=(); for f in "$@"; do args+=(-i "$f"); done
while [ ${#args[@]} -lt 8 ]; do args+=(-f lavfi -i "color=c=gray:s=1920x1080:d=1"); done
ffmpeg -hide_banner -loglevel error -y "${args[@]}" -filter_complex "[0][1][2][3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,scale=1920:1080" -frames:v 1 "$out"
