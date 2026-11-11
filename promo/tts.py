#!/usr/bin/env python3
"""Synthesize every narration line (cached by text+voice+style) and trim edge silence."""
import array, hashlib, json, sys, time, wave
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "promo_kit"))
from tts_helper import request_audio, write_wav  # noqa: E402

OUT = HERE / "build" / "audio"

def trim(path: Path, head=0.04, tail=0.10, thresh=500):
    """Cut edge silence, keeping the natural tail, and fade both edges so a clip never starts or ends on a click."""
    with wave.open(str(path)) as w:
        rate = w.getframerate(); data = array.array("h", w.readframes(w.getnframes()))
    idx = [i for i in range(0, len(data), 48) if abs(data[i]) > thresh]
    a = max(0, idx[0] - int(head * rate)); b = min(len(data), idx[-1] + int(tail * rate))
    seg = data[a:b]
    fin, fout = min(len(seg), int(.008 * rate)), min(len(seg), int(.08 * rate))
    for i in range(fin): seg[i] = int(seg[i] * i / fin)
    for i in range(fout): seg[-1 - i] = int(seg[-1 - i] * i / fout)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(seg.tobytes())
    return (b - a) / rate

def one(item, voice, style):
    lid, text = item[:2]
    key = hashlib.sha1(f"{voice}|{style}|{text}".encode()).hexdigest()[:10]
    raw = OUT / f"{lid}-{key}.raw.wav"; final = OUT / f"{lid}.wav"
    if not raw.exists():
        for attempt in range(8):
            try:
                audio, mime = request_audio(text, voice, "gemini-3.8-flash-tts", style, 120); break
            except RuntimeError as exc:
                if "429" not in str(exc) or attempt == 7: raise
                time.sleep(15 * (attempt + 1))
        write_wav(audio, mime, raw)
    final.write_bytes(raw.read_bytes())
    return lid, trim(final)

if __name__ == "__main__":
    spec = json.loads((HERE / "script.json").read_text())
    items = [l for s in spec["scenes"] for l in s["lines"]]
    only = set(sys.argv[1:])
    with ThreadPoolExecutor(1) as ex:
        for lid, dur in ex.map(lambda it: one(it, spec["voice"], spec["style"]), [i for i in items if not only or i[0] in only]):
            print(f"{lid}\t{dur:.2f}s")
