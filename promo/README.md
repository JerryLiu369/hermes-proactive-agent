# Promo video

A promo of about 2:10 at 30 fps with Mandarin narration and burned-in Chinese + English captions, in two cuts: landscape
(1920×1080) and vertical (1080×1920). Every frame is drawn by `index.html` + `scenes.js` as a pure function of time, then
captured by headless Chromium. Both cuts share one timeline and one soundtrack; `index.html?v=1` is the vertical layout.

The story runs dark to light: the cold open and the six problems play at night, the lights come on when the plugin is
named, and the cold open's chat returns near the end with the plugin in place.

```bash
./make.sh          # narration → timeline → soundtrack → frames → both MP4s
```

| File | Role |
|---|---|
| `script.json` | Narration lines per scene (Chinese text, English caption), voice and delivery style |
| `PUBLISH.md` | Platform titles, descriptions (Chinese and English), chapters, tags, full narration |
| `super-agent-promo.mp4`, `super-agent-promo-vertical.mp4`, `subtitles.srt` | The two finished cuts and their bilingual subtitles |
| `tts.py` | Gemini TTS for each line (cached in `build/audio`), edge silence trimmed, clip edges faded |
| `asr_check.py` | QA: transcribes clips with a Gemini text model and flags mismatches |
| `timeline.py` | Lays the lines end to end; clip lengths set every scene's timing (`build/timeline.js`, `subtitles.srt`) |
| `index.html`, `scenes.js` | The visuals: paper-and-ink style, one scene per story beat, captions from the timeline; `LV(landscape, vertical)` and `block()` place each scene per orientation |
| `render.mjs` | Chromium over CDP: `stills <scene+sec>…`, `cues` (SFX cue list), `video [from to out]` (frames → H.264), `bench` (per-frame cost); `PROMO_VERTICAL=1` selects the vertical cut, `PROMO_FRAMES=png` lossless frames |
| `mix.py` | Places narration, synthesizes the score (pads, plucks, soft bass; ducks under the voice, lifts at section changes) and UI sound effects |
| `master.py` | Muxes a rendered video + audio, two-pass loudness normalization to −16 LUFS |
| `sheet.sh` | 2×2 contact sheet of stills for quick review |

Plugin note texts shown on screen are copied from `super-agent/*.py`; numbers match `config.DEFAULTS`.

The video carries no links, repository address, install command or "search for it on …" line: Bilibili and Xiaohongshu
refuse off-platform referrals. Keep it that way; links belong only in the YouTube/X descriptions in `PUBLISH.md`.

Render speed: the page loads once and each frame only calls `render(t)` (about 15 ms); the screenshot encode is the
cost (PNG about 250 ms, JPEG q95 about 90 ms per frame on a 2-core box). `make.sh` uses JPEG and renders both cuts at
once: about 14 minutes for both, against about 40 with PNG one after the other. JPEG q95 frames come out at SSIM
0.99 against PNG frames, which the H.264 encode hides. For a small change, render only the changed frames with
`video <from> <to> <file>` and splice them in.
