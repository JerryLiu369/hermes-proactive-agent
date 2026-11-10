# Promo-video preparation: objective facts and environment

This file is a technical fact sheet for the video branch. It does not prescribe a story, storyboard, visual style, wording, pacing, or creative/implementation choices.

## Branch boundary

- This work is on `promo-video`, created from `main` at `8c6630fbb09920c83199cd73275e27d13437645a`.
- This is an independent video-production branch; it is not intended to merge back into `main`.
- Video-project files and assets may be placed on this branch.

## Gemini TTS

### Verified route and models

- The temporary API key is stored only in `/home/ubuntu/.config/gemini-tts.env`, mode `0600`; it is not in the repository. The root `.gitignore` ignores `.env*`.
- Authenticated `GET https://generativelanguage.googleapis.com/v1beta/models` returned HTTP `200` (50 models). IDs with TTS in the live response were:
  - `gemini-3.8-flash-tts` — latest listed here; `generateContent` call tested successfully.
  - `gemini-3.8-flash-lite-tts`
  - `gemini-3.1-flash-tts-preview`
  - `gemini-2.5-flash-preview-tts`
  - `gemini-2.5-pro-preview-tts`
- The live model listing also included `gemini-3.8-flash` (text model), but that entry alone does not establish audio-output support.
- A minimal TTS `POST` to `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent` returned HTTP `200` for the key. The test requested voice `Kore` and `AUDIO_L16`; the response MIME was `audio/l16; rate=24000; channels=1` (headerless 24 kHz mono signed 16-bit little-endian PCM).
- A separate raw-PCM response was wrapped into a standard RIFF/WAVE file with FFmpeg and probed as 24 kHz, mono, 16-bit PCM, duration `1.720 s`. Test WAV: `/home/ubuntu/.cache/promo-video-tts/gemini-3.8-flash-tts-short.wav`.
- The checked-in helper was also run through its CLI: it wrote `/home/ubuntu/.cache/promo-video-tts/helper-smoke.wav`, validated as RIFF/WAVE, 24 kHz, mono, 16-bit PCM, duration `1.960 s`.
- Vertex AI Express Mode was also checked. `POST https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-3.8-flash-tts:generateContent` returned HTTP `403 PERMISSION_DENIED`; the publisher-model collection GETs returned `v1: 404` and `v1beta1: 401 UNAUTHENTICATED`. The successful route for this key is the Generative Language API above. Google’s Express Mode supported-model table does not list the TTS model: [Express Mode model list](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/express-mode/overview#models).
- Authenticated local `/v1/models` checks: AxonHub (`127.0.0.1:56673`) returned 12 models, including `gemini-3.8-flash`; CPA (`127.0.0.1:52718`) returned 5 models, including `gemini-3.8-flash` and `gemini-3.8-flash-high`. Neither listed a TTS-specific model. The returned model objects had no audio/modality capability fields, so local `/models` listings do not confirm audio support.

### Helper usage

`promo_kit/tts_helper.py` reads the key from the private env file, sends the key in the `x-goog-api-key` header, requests raw `AUDIO_L16`, wraps it in a standard WAV, and prints the sample rate and duration.

```bash
python3 promo_kit/tts_helper.py \
  --text "你好，欢迎你。" \
  --voice-name Kore \
  --output "$HOME/.cache/promo-video-tts/claude-tts-check.wav"
```

- `--voice-name` also accepts the spelling `--voice_name`.
- Default model: `gemini-3.8-flash-tts`; `--model` may select `gemini-3.8-flash-lite-tts`.
- `--style` is optional. `--list-voices` prints the prebuilt voice names and their documented descriptors.
- Google’s [Gemini TTS reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/text-to-speech/overview) states unary output defaults to 16-bit PCM WAV at 24 kHz mono; `AUDIO_L16` is headerless 16-bit signed little-endian PCM at 24 kHz mono. The API ignores the requested `sampleRate` field.

### Prebuilt voice names (Google documentation)

| Voice | Documented descriptor | Voice | Documented descriptor |
| --- | --- | --- | --- |
| Achernar | Soft | Achird | Friendly |
| Algenib | Gravelly | Algieba | Smooth |
| Alnilam | Firm | Aoede | Breezy |
| Autonoe | Bright | Callirrhoe | Easy-going |
| Charon | Informative | Despina | Smooth |
| Enceladus | Breathy | Erinome | Clear |
| Fenrir | Excitable | Gacrux | Mature |
| Iapetus | Clear | Kore | Firm |
| Laomedeia | Upbeat | Leda | Youthful |
| Orus | Firm | Puck | Upbeat |
| Pulcherrima | Forward | Rasalgethi | Informative |
| Sadachbia | Lively | Sadaltager | Knowledgeable |
| Schedar | Even | Sulafat | Warm |
| Umbriel | Easy-going | Vindemiatrix | Gentle |
| Zephyr | Bright | Zubenelgenubi | Casual |

Google’s [3.8 Flash TTS model page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash-tts) lists 30 prebuilt voices; the table above follows the documented names and descriptors.

## 1080p / 30 fps rendering environment

- Host: Ubuntu `24.04.5 LTS`, `aarch64`.
- FFmpeg: `6.1.1-3ubuntu5`; `ass` and `subtitles` filters are present; H.264 `libx264` encoder is present.
- Hard-sub test passed: a one-second `1920x1080`, `30/1 fps`, `yuv420p`, H.264 MP4 with Chinese text rendered through the ASS filter and `Noto Sans CJK SC` (30 frames; no FFmpeg warnings). Test file: `/home/ubuntu/.cache/promo-video-env-check/hardsub-check.mp4`.
- Installed packages include `fontconfig`, `fonts-noto-cjk`, and `ffmpeg`. Chinese families reported by `fc-list :lang=zh`: `Noto Sans CJK SC`, `Noto Serif CJK SC`, `Noto Sans Mono CJK SC` (also HK/JP/KR/TC variants). Latin families include `DejaVu Sans`, `DejaVu Sans Mono`, and `DejaVu Serif`.
- Node.js: `/usr/local/bin/node`, `v22.23.4`. Python: `/usr/bin/python3`, `3.12.3`.
- Chromium Snap is installed at `/snap/bin/chromium`, version `157.0.8103.38`.
- The earlier headless failures were caused by Snap confinement, not the test page: the kernel audit log recorded `apparmor="DENIED" operation="symlink"` for `SingletonLock` when the custom profile directory was under `/home/ubuntu/.cache/` (AppArmor profile `snap.chromium.chromium`). No host policy change or existing browser profile is needed.
- Fix: keep the browser profile isolated under Snap's per-app common directory, e.g. `/home/ubuntu/snap/chromium/common/promo-video-render-check/isolated-profile`. With this path Chromium started successfully; Neko and other browser profiles/services were not touched.

### Chromium/browser-renderer acceptance

The local, offline acceptance input is `/home/ubuntu/snap/chromium/common/promo-video-render-check/acceptance-card.html`; its dedicated profile is `.../isolated-profile`. It uses only a neutral test card, local CSS/JavaScript/canvas and system fonts.

Exact launch form used:

```bash
/snap/bin/chromium \
  --headless=new --no-sandbox --disable-dev-shm-usage --disable-gpu \
  --user-data-dir="$HOME/snap/chromium/common/promo-video-render-check/isolated-profile" \
  --window-size=1920,1080 --force-device-scale-factor=1 \
  --virtual-time-budget=3000 --dump-dom \
  --screenshot="$HOME/snap/chromium/common/promo-video-render-check/chromium-1920x1080.png" \
  "file:///home/ubuntu/snap/chromium/common/promo-video-render-check/acceptance-card.html"
```

- Verified on the installed Chromium `157.0.8103.38`: exit `0`; dumped DOM contains `data-js="ok"` and `JS_OK`; screenshot is exactly `1920x1080` (`101782` bytes).
- `fc-match 'Noto Sans CJK SC'` resolves to `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`. Visual inspection confirms Chinese text and the JavaScript status are legible in the screenshot.
- Acceptance screenshot: `/home/ubuntu/.cache/promo-video-render/acceptance/chromium-1920x1080.png`.
- A two-second H.264 acceptance clip with an ASS hard-subtitle test line was encoded from that screenshot. It is a technical test card, not a promotional video: `/home/ubuntu/.cache/promo-video-render/acceptance/renderer-acceptance-1080p30.mp4`.
- `ffprobe` verified: `h264`, `1920x1080`, `yuv420p`, `avg_frame_rate=30/1`, `r_frame_rate=30/1`, `60` decoded frames, duration `2.000000` s, size `202730` bytes. A frame extracted at `0.5` s was visually checked; Chinese test subtitle and page text render without visible encoding damage. Frame: `/home/ubuntu/.cache/promo-video-render/acceptance/video-frame-0.5s.png`.

Reproduce the short hard-subtitle encode and probe:

```bash
OUT="$HOME/.cache/promo-video-render/acceptance"
ffmpeg -hide_banner -loglevel warning -y -loop 1 -framerate 30 \
  -i "$OUT/chromium-1920x1080.png" \
  -vf "ass=$OUT/burned-subtitle-check.ass" -t 2 \
  -c:v libx264 -preset ultrafast -crf 22 -pix_fmt yuv420p -r 30 \
  -movflags +faststart "$OUT/renderer-acceptance-1080p30.mp4"
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,nb_read_frames,duration \
  -show_entries format=format_name,duration,size -of json \
  "$OUT/renderer-acceptance-1080p30.mp4"
```

All browser profiles, page source and media test artifacts stay outside the repository. This acceptance test does not produce or prescribe the promotional video.
- Example FFmpeg H.264 output settings for an image sequence plus narration and burned-in ASS subtitles:

```bash
ffmpeg -framerate 30 -i 'frames/%06d.png' -i narration.wav \
  -vf 'subtitles=subtitle.ass' \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r 30 \
  -c:a aac -b:a 192k -movflags +faststart output.mp4
```

The `subtitles`/`ass` video filter burns subtitle pixels into each video frame; it does not create a selectable subtitle track. Use a UTF-8 SRT/ASS source and verify the rendered stream with `ffprobe` (codec, dimensions, frame rate, pixel format, duration). Quote filter paths and escape special characters if a path contains them.

## Repository mechanisms (derived from checked-in code)

| Mechanism | Objective implementation fact | Source |
| --- | --- | --- |
| 60s / 120s turn governance | Defaults are a 60-second soft threshold and 120-second hard threshold; the soft threshold marks a warning zone and the hard threshold marks the turn red/restricted. | `proactive-agent/config.py` (`turn_soft_seconds`, `turn_hard_seconds`), `proactive-agent/secretary.py` (`zone`) |
| 30s long-command handoff | Default `terminal_auto_detach_seconds` is 30; the terminal-tool middleware arms a timer and requests a yield through Hermes, leaving the command running in the background. Default governed platform is Weixin. | `proactive-agent/config.py`, `proactive-agent/secretary.py` (`arm_detach`) |
| Subagent user/environment memory | Handoff assembly reads `USER.md` and `MEMORY.md` and includes their available text in the subagent briefing; Hermes’ configured character limits apply. | `proactive-agent/handoff.py` (`_memory_text`) |
| Repeated-operation breaker | Default repeat limit is 5. The worker hook compares the same tool-call signature plus the same result, warns before the limit, then stops the worker at the limit. | `proactive-agent/config.py` (`worker_repeat_limit`), `proactive-agent/workers.py` (`post_tool_call`) |
| `needs_decision` return | Worker reports can use `status: needs_decision`; question/options are recorded and surfaced to the parent agent for a user decision. | `proactive-agent/reports.py`, `proactive-agent/ledger.py` |
| `unreported` state machine | Background-task results are marked `unreported` until surfaced in a parent turn; seen/asked state is tracked so pending results and open questions can be surfaced again. Restart recovery marks still-running recorded work as lost and notifies the parent. | `proactive-agent/reports.py` (`pending_notes`, `recover_lost`), `proactive-agent/ledger.py` (`show_unreported`, `mark_unreported`) |
| SQLite task ledger | Tasks and notes are stored in `ledger.db` under Hermes plugin-data when that storage is available; a Hermes restart can read task status, changed files, result/report state, and open questions. Code has an in-memory fallback only if persistent plugin-data setup fails. | `proactive-agent/ledger.py` (`_open_default`, schema, `finish`) |

## Scope boundary

`promo_kit/` provides only objective project facts and technical helper scripts. Video structure, visual style, wording, pacing, and implementation choices are entirely Claude’s to decide.
