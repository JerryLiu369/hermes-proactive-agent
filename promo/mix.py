#!/usr/bin/env python3
"""Build the soundtrack: narration placed on the timeline, a synthesized ambient score that ducks under the
voice, and soft UI sound effects from build/cues.json. Writes build/voice.wav and build/bed.wav (48 kHz)."""
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
B = HERE / "build"
SR = 48000
rng = np.random.default_rng(7)


def load48(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-af", "aresample=48000:resampler=soxr",
                          "-f", "f32le", "-ac", "1", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def write(path: Path, x: np.ndarray):
    x = np.clip(x, -1, 1)
    st = x if x.ndim == 2 else np.stack([x, x], 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((st * 32767).astype("<i2").tobytes())


def env_adsr(n, a, r):
    e = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    if na: e[:na] = np.linspace(0, 1, na) ** 2
    if nr: e[-nr:] *= np.linspace(1, 0, nr) ** 2
    return e


def lowpass(x, fc):
    a = np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x); acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc; y[i] = acc
    return y


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


tl = json.loads((B / "timeline.json").read_text())
cues = json.loads((B / "cues.json").read_text())
N = int((tl["duration"] + .5) * SR)
S = {s["id"]: s for s in tl["scenes"]}

# ---------------------------------------------------------------- narration
voice = np.zeros(N)
for ln in tl["lines"]:
    x = load48(B / "audio" / f"{ln['id']}.wav")
    i = int(ln["start"] * SR); voice[i:i + len(x)] += x[:N - i]
voice *= .85 / max(1e-9, np.abs(voice).max())
write(B / "voice.wav", voice)

# duck envelope: 1 when silent, ~0.4 under the voice, smoothed
act = np.zeros(N)
for ln in tl["lines"]:
    act[int((ln["start"] - .15) * SR):int((ln["start"] + ln["dur"] + .1) * SR)] = 1
k = int(.45 * SR); kern = np.hanning(k); kern /= kern.sum()      # slow enough that the bed does not pump between sentences
duck = 1 - .60 * np.convolve(act, kern, mode="same")

# ---------------------------------------------------------------- score
t = np.arange(N) / SR
music = np.zeros((N, 2))


def pad_note(f, start, dur, gain, pan=0.0):
    i0, n = int(start * SR), int(dur * SR)
    n = min(n, N - i0)
    if n <= 0: return
    tt = np.arange(n) / SR
    sig = np.zeros(n)
    for det, g in ((-0.11, .5), (0.0, 1), (0.13, .5)):        # three slightly detuned voices
        ff = f * 2 ** (det / 12)
        sig += g * (np.sin(2 * np.pi * ff * tt) + .18 * np.sin(4 * np.pi * ff * tt) + .05 * np.sin(6 * np.pi * ff * tt))
    sig *= (1 + .08 * np.sin(2 * np.pi * .23 * tt + f)) * env_adsr(n, min(1.6, dur / 2.5), min(2.2, dur / 2)) * gain / 2
    l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    music[i0:i0 + n, 0] += sig * l; music[i0:i0 + n, 1] += sig * r


def pluck(f, start, gain, pan=0.0):
    i0 = int(start * SR); n = min(int(1.6 * SR), N - i0)
    if n <= 0: return
    tt = np.arange(n) / SR
    sig = (np.sin(2 * np.pi * f * tt) + .3 * np.sin(4 * np.pi * f * tt) * np.exp(-tt * 9)) * np.exp(-tt * 3.2)
    sig *= np.minimum(1, tt / .004) * gain
    l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    music[i0:i0 + n, 0] += sig * l; music[i0:i0 + n, 1] += sig * r


def bass(f, start, gain):
    """A soft plucked bass note: the low end the pads and plucks lack."""
    i0 = int(start * SR); n = min(int(1.5 * SR), N - i0)
    if n <= 0: return
    tt = np.arange(n) / SR
    sig = (np.sin(2 * np.pi * f * tt) + .22 * np.sin(4 * np.pi * f * tt) * np.exp(-tt * 6)) * np.exp(-tt * 2.4)
    sig *= np.minimum(1, tt / .008) * gain
    music[i0:i0 + n, 0] += sig * .707; music[i0:i0 + n, 1] += sig * .707


def kick(start, gain):
    """A felt, low thump on the downbeat; it marks the tempo without turning the bed into a beat."""
    i0 = int(start * SR); n = min(int(.3 * SR), N - i0)
    if n <= 0: return
    tt = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(np.geomspace(92, 44, n)) / SR
    sig = np.sin(ph) * np.exp(-tt * 13) * np.minimum(1, tt / .004) * gain
    music[i0:i0 + n, 0] += sig * .707; music[i0:i0 + n, 1] += sig * .707


# chords in F: Fmaj9, Dm9, Bbmaj9, Csus2 / C6
CH = {"F": [41, 53, 57, 60, 64, 67], "Dm": [38, 50, 53, 57, 60, 64], "Bb": [34, 46, 50, 53, 57, 60],
      "C": [36, 48, 50, 55, 57, 62], "Am": [33, 45, 52, 55, 59, 60]}
BAR = 60 / 84 * 4
intro_end = S["title"]["start"]

# cold open + problems: a low, uneasy drone that slowly gathers
pad_note(midi(29), 0.0, intro_end + 1.5, .10)                        # F1
pad_note(midi(36), 0.4, intro_end + 1.0, .07, -.3)                   # C2
pad_note(midi(62), S["cold"]["start"] + 3.5, S["cold"]["dur"] + 1, .025, .4)
for c, (st, du) in zip(["Dm", "Bb", "Am"], [(S["problems"]["start"], 4.6), (S["problems"]["start"] + 4.4, 4.6),
                                           (S["problems"]["start"] + 8.8, intro_end - S["problems"]["start"] - 8.8 + .8)]):
    for j, m in enumerate(CH[c][1:5]):
        pad_note(midi(m), st, du + .8, .032, (-.5, .5, -.2, .2)[j])

# from the title on: the bloom and a calm progression with plucked arpeggios
prog = ["F", "Dm", "Bb", "C"]
cur, ci = intro_end, 0
end_t = S["end"]["start"]
pattern = [0, 2, 1, 3, 2, 4, 3, 1]
while cur < end_t:
    c = prog[ci % 4]; du = min(2 * BAR, end_t - cur)
    notes = CH[c]
    pad_note(midi(notes[0]), cur, du + 1.2, .085)
    for j, m in enumerate(notes[1:]):
        pad_note(midi(m), cur, du + 1.2, .03, (-.6, .6, -.3, .3, 0)[j])
    eighth = BAR / 8
    for s8 in range(int(du / eighth)):
        when = cur + s8 * eighth
        if when < intro_end + 2.0: continue
        f = midi(notes[1 + pattern[s8 % 8]] + 12)
        pluck(f, when, .05 if s8 % 2 == 0 else .032, (-.4, .4)[s8 % 2])
    beat = BAR / 4
    for b in range(int(round(du / beat))):
        when = cur + b * beat
        if when < intro_end + 2.0: continue
        if b % 4 == 0: bass(midi(notes[0]), when, .10); kick(when, .06)
        elif b % 4 == 2: bass(midi(notes[0]), when, .07)
        elif b == 7: bass(midi(notes[0] + 7), when + beat / 2, .05)      # pickup into the next chord
    cur += du; ci += 1
for j, m in enumerate(CH["F"]):                                       # resolve and ring out
    pad_note(midi(m), end_t, S["end"]["dur"] + .5, (.09 if j == 0 else .034), (0, -.6, .6, -.3, .3, 0)[j])
for j, m in enumerate([72, 76, 79, 84]):
    pluck(midi(m), end_t + .25 + j * .18, .045, (-.4, .4, -.2, .2)[j])

fade = np.ones(N)
fe = int(S["end"]["start"] * SR + S["end"]["dur"] * SR - 1.6 * SR)
fade[fe:] = np.linspace(1, 0, N - fe) ** 1.5
fade[:int(.8 * SR)] = np.linspace(0, 1, int(.8 * SR))
# lift the score across each section change, where there is no voice to carry the moment
lift = np.ones(N)
for sid in ("title", "ch1", "ch2", "ch3", "ch4", "after", "principles", "end"):
    a, b = int((S[sid]["start"] - .7) * SR), int((S[sid]["start"] + 1.1) * SR)
    lift[a:b] += .3 * np.hanning(b - a)
music *= (1.22 * duck * fade * lift)[:, None]

# ---------------------------------------------------------------- sound effects
sfx = np.zeros((N, 2))


def place(sig, when, gain, pan=0.0):
    i0 = int(when * SR); n = min(len(sig), N - i0)
    if n <= 0: return
    l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    sfx[i0:i0 + n, 0] += sig[:n] * gain * l; sfx[i0:i0 + n, 1] += sig[:n] * gain * r


def tone(f0, f1, dur, decay, harm=0.0):
    n = int(dur * SR); tt = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(np.geomspace(f0, f1, n)) / SR
    return (np.sin(ph) + harm * np.sin(2 * ph)) * np.exp(-tt * decay) * np.minimum(1, tt / .003)


def noise(dur, fc, decay):
    n = int(dur * SR); tt = np.arange(n) / SR
    return lowpass(rng.standard_normal(n), fc) * np.exp(-tt * decay)


def add(*parts):
    out = np.zeros(max(len(p) for p in parts))
    for p in parts: out[:len(p)] += p
    return out


FX = {
    "pop": lambda: add(tone(980, 720, .09, 45) * .55, noise(.02, 3200, 300) * .18),
    "tick": lambda: add(tone(1500, 1400, .05, 120) * .32, noise(.012, 4500, 500) * .3),
    "warn": lambda: np.concatenate([tone(midi(81), midi(81), .14, 18, .12), tone(midi(77), midi(77), .45, 9, .12)]) * .36,
    "stop": lambda: add(tone(150, 52, .45, 9) * .7, noise(.08, 700, 40) * .3),
    "stamp": lambda: add(tone(120, 45, .35, 12), noise(.12, 1600, 35) * .9),
    "ok": lambda: np.concatenate([tone(midi(84), midi(84), .11, 16, .1), tone(midi(91), midi(91), .6, 7, .1)]) * .32,
    "whoosh": lambda: lowpass(rng.standard_normal(int(.55 * SR)), 1800) * np.sin(np.linspace(0, np.pi, int(.55 * SR))) ** 2 * .35,
    "glitch": lambda: add(tone(420, 45, .7, 4, .3) * .7, noise(.35, 3000, 6) * .45),
}
last = {}
for i, c in enumerate(cues):
    pan = (-.25, .25)[i % 2] if c["k"] in ("pop", "tick") else 0.0
    # a run of the same effect gets quieter, so a list of items does not turn into clatter
    crowded = c["k"] in ("pop", "tick") and c["t"] - last.get(c["k"], -9) < 1.3
    last[c["k"]] = c["t"]
    place(FX[c["k"]](), c["t"], .5 * c["g"] * (.65 if crowded else 1), pan)

bed = music * 1.0 + sfx * .8
write(B / "bed.wav", bed)
print("voice peak", np.abs(voice).max().round(3), "bed peak", np.abs(bed).max().round(3), "seconds", round(N / SR, 2))
