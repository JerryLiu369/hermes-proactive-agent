#!/usr/bin/env python3
"""Mux a rendered video with the voice (cleaned) and bed tracks, two-pass loudness-normalized to -16 LUFS.
Usage: master.py [out.mp4] [build video, default video.mp4]"""
import json, re, subprocess, sys
from pathlib import Path
B = Path(__file__).resolve().parent / "build"
OUT = B.parent / (sys.argv[1] if len(sys.argv) > 1 else "proactive-agent-promo.mp4")
META = ["-metadata", "title=Hermes Proactive Agent: 让聊天里的助手像秘书一样做事",
        "-metadata", "artist=JerryLiu369",
        "-metadata", "comment=https://github.com/JerryLiu369/hermes-proactive-agent",
        "-metadata", "description=Hermes Agent plugin: responsive chat, informed subagents, no spinning or guessing, checked and recorded results."]
PRE = ("[1:a]highpass=f=70,acompressor=threshold=-20dB:ratio=2.5:attack=6:release=140:makeup=1.4[v];"
       "[v][2:a]amix=inputs=2:normalize=0[m]")
ins = ["-i", str(B / (sys.argv[2] if len(sys.argv) > 2 else "video.mp4")), "-i", str(B / "voice.wav"), "-i", str(B / "bed.wav")]
probe = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *ins, "-filter_complex",
                        PRE + ";[m]loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json[a]", "-map", "[a]", "-f", "null", "-"],
                       capture_output=True, text=True, check=True).stderr
m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe).group(0))
ln = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
      f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", *ins, "-filter_complex", PRE + f";[m]{ln},aresample=48000[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-shortest", *META, str(OUT)], check=True)
print(OUT)
