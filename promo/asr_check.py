#!/usr/bin/env python3
"""Ask a Gemini text model to transcribe narration clips and flag delivery problems (QA only)."""
import base64, json, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "promo_kit"))
from tts_helper import load_api_key  # noqa: E402

MODEL = "gemini-3.8-flash"
PROMPT = ("You are checking a Mandarin narration clip for a product video. Expected text: {text}\n"
          "Reply as compact JSON: {{\"transcript\": ..., \"matches\": true/false, \"issues\": [mispronunciations, "
          "skipped or extra words, odd pauses, glitches, wrong tone], \"delivery\": one short sentence on voice quality}}")

def check(wav: Path, text: str) -> dict:
    body = {"contents": [{"role": "user", "parts": [
        {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(wav.read_bytes()).decode()}},
        {"text": PROMPT.format(text=text)}]}],
        "generationConfig": {"responseMimeType": "application/json"}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": load_api_key()})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    return json.loads(out["candidates"][0]["content"]["parts"][0]["text"])

if __name__ == "__main__":
    text = sys.argv[1]
    for p in sys.argv[2:]:
        print(Path(p).name, json.dumps(check(Path(p), text), ensure_ascii=False))
