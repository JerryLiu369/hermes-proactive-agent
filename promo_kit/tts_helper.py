#!/usr/bin/env python3
"""Minimal Gemini 3.8 TTS caller; reads its API key from the private env file."""
from __future__ import annotations

import argparse
import base64
import json
import re
import stat
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any

ENV_FILE = Path("/home/ubuntu/.config/gemini-tts.env")
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash-tts"
MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")
VOICES = (
    ("Achernar", "Soft"), ("Achird", "Friendly"), ("Algenib", "Gravelly"),
    ("Algieba", "Smooth"), ("Alnilam", "Firm"), ("Aoede", "Breezy"),
    ("Autonoe", "Bright"), ("Callirrhoe", "Easy-going"), ("Charon", "Informative"),
    ("Despina", "Smooth"), ("Enceladus", "Breathy"), ("Erinome", "Clear"),
    ("Fenrir", "Excitable"), ("Gacrux", "Mature"), ("Iapetus", "Clear"),
    ("Kore", "Firm"), ("Laomedeia", "Upbeat"), ("Leda", "Youthful"),
    ("Orus", "Firm"), ("Puck", "Upbeat"), ("Pulcherrima", "Forward"),
    ("Rasalgethi", "Informative"), ("Sadachbia", "Lively"),
    ("Sadaltager", "Knowledgeable"), ("Schedar", "Even"), ("Sulafat", "Warm"),
    ("Umbriel", "Easy-going"), ("Vindemiatrix", "Gentle"), ("Zephyr", "Bright"),
    ("Zubenelgenubi", "Casual"),
)


def load_api_key() -> str:
    try:
        mode = stat.S_IMODE(ENV_FILE.stat().st_mode)
    except OSError as exc:
        raise RuntimeError(f"Cannot read credential file: {ENV_FILE}") from exc
    if mode != 0o600:
        raise RuntimeError(f"Credential file must have mode 0600: {ENV_FILE}")
    values: dict[str, str] = {}
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
    key = values.get("GEMINI_TTS_API_KEY") or values.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(f"No GEMINI_TTS_API_KEY or GOOGLE_API_KEY in {ENV_FILE}")
    return key


def request_audio(text: str, voice_name: str, model: str, style: str, timeout: float) -> tuple[bytes, str]:
    key = load_api_key()
    part: dict[str, Any] = {"text": text}
    if style:
        part["speechMetadata"] = {"style": style}
    payload = {
        "contents": [{"role": "user", "parts": [part]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "responseFormat": {"audio": {"mimeType": "AUDIO_L16", "sampleRate": 24000}},
            "speechConfig": {"voiceConfig": {"voice": voice_name}},
        },
    }
    url = f"{API_ROOT}/{model}:generateContent"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-goog-api-key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error", {})
            status = detail.get("status", "unknown")
        except Exception:
            status = "unknown"
        raise RuntimeError(f"Gemini API returned HTTP {exc.code} ({status})") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Gemini API request failed ({type(exc).__name__})") from None

    candidates = result.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") if candidates else None
    inline = next(
        (part.get("inlineData") or part.get("inline_data") for part in (parts or [])
         if part.get("inlineData") or part.get("inline_data")),
        None,
    )
    if not inline or not inline.get("data"):
        raise RuntimeError("Gemini response did not contain inline audio data")
    try:
        audio = base64.b64decode(inline["data"], validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Gemini response contained invalid base64 audio") from exc
    mime = str(inline.get("mimeType") or inline.get("mime_type") or "unknown")
    return audio, mime


def write_wav(audio: bytes, mime: str, output: Path) -> tuple[int, int, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        output.write_bytes(audio)
    else:
        rate_match = re.search(r"(?:rate|sample_rate)\s*=\s*(\d+)", mime, re.IGNORECASE)
        channels_match = re.search(r"channels\s*=\s*(\d+)", mime, re.IGNORECASE)
        sample_rate = int(rate_match.group(1)) if rate_match else 24000
        channels = int(channels_match.group(1)) if channels_match else 1
        sample_width = 2  # AUDIO_L16 is signed 16-bit little-endian PCM.
        frame_width = channels * sample_width
        if len(audio) % frame_width:
            raise RuntimeError("Raw PCM byte count is not aligned to the advertised channel count")
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(sample_width)
            wav.setframerate(sample_rate)
            wav.writeframes(audio)
    try:
        with wave.open(str(output), "rb") as wav:
            if wav.getcomptype() != "NONE":
                raise RuntimeError("Output WAV is not uncompressed PCM")
            return wav.getnframes(), wav.getframerate(), wav.getnchannels()
    except wave.Error as exc:
        raise RuntimeError(f"Could not validate output WAV: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", help="Exact transcript to synthesize")
    parser.add_argument("--voice-name", "--voice_name", dest="voice_name", help="Gemini prebuilt voice name")
    parser.add_argument("--output", help="Destination .wav file")
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL)
    parser.add_argument("--style", default="", help="Optional speech_metadata.style value")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--list-voices", action="store_true", help="Print Google’s prebuilt voice names")
    args = parser.parse_args()
    if args.list_voices:
        for name, quality in VOICES:
            print(f"{name}\t{quality}")
        return 0
    if not args.text or not args.text.strip():
        parser.error("--text is required and must not be empty")
    if not args.voice_name:
        parser.error("--voice-name is required")
    if not args.output:
        parser.error("--output is required")
    output = Path(args.output).expanduser()
    if output.suffix.lower() != ".wav":
        parser.error("--output must have a .wav suffix")
    try:
        audio, mime = request_audio(args.text, args.voice_name, args.model, args.style, args.timeout)
        frames, rate, channels = write_wav(audio, mime, output)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    duration = frames / rate
    print(
        f"model={args.model} voice={args.voice_name} mime={mime} "
        f"wav={output} channels={channels} sample_rate={rate} "
        f"frames={frames} duration_seconds={duration:.6f} size_bytes={output.stat().st_size}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
