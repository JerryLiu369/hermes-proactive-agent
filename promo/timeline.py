#!/usr/bin/env python3
"""Lay narration lines end to end into scenes and write build/timeline.js for the page."""
import json, re, wave
from pathlib import Path
HERE = Path(__file__).resolve().parent
GAP = {"problems": 0.16, "ch2": 0.38}
DEFAULT_GAP = 0.26

def caption(text: str) -> str:
    text = re.sub(r"[，。；：、]+$", "", text)
    return re.sub(r"[，；：]", "  ", text).replace("——", "  ")

def build():
    spec = json.loads((HERE / "script.json").read_text())
    t, scenes, lines = 0.0, [], []
    for sc in spec["scenes"]:
        start = t
        t += sc["pre"]
        rel = {}
        for i, (lid, text, en) in enumerate(sc["lines"]):
            if i:
                t += GAP.get(sc["id"], DEFAULT_GAP)
            with wave.open(str(HERE / "build" / "audio" / f"{lid}.wav")) as w:
                d = w.getnframes() / w.getframerate()
            lines.append({"id": lid, "start": round(t, 3), "dur": round(d, 3), "text": text, "cap": caption(text), "en": en})
            rel[lid] = {"start": round(t - start, 3), "dur": round(d, 3), "text": text}
            t += d
        t += sc["post"]
        scenes.append({"id": sc["id"], "start": round(start, 3), "dur": round(t - start, 3), "lines": rel})
    tl = {"fps": 30, "duration": round(t, 3), "scenes": scenes, "lines": lines}
    (HERE / "build" / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1))
    (HERE / "build" / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl, ensure_ascii=False) + ";\n")
    (HERE / "subtitles.srt").write_text(srt(lines), encoding="utf-8")
    return tl


def stamp(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def srt(lines):
    out = []
    for i, ln in enumerate(lines, 1):
        out.append(f"{i}\n{stamp(ln['start'])} --> {stamp(ln['start'] + ln['dur'] + .2)}\n{ln['cap'].replace('  ', '，')}\n{ln['en']}\n")
    return "\n".join(out)

if __name__ == "__main__":
    tl = build()
    for s in tl["scenes"]:
        print(f"{s['id']:<11}{s['start']:7.2f} +{s['dur']:.2f}")
    print("total", tl["duration"])
