"""SFX pipeline integration test: trigger phrase -> plan -> mix -> MP4.

    python -m tests.test_sfx      (from the AutoEditor folder)

Uses a synthetic "voice" (band-limited noise bursts per word) and a test effect
that is a 6 kHz tone - a frequency the voice doesn't contain - so we can measure
the effect's presence in the mixed WAV and in the final MP4's audio stream.
"""
import os
import subprocess
import tempfile
import wave

import numpy as np
from PIL import Image

from autoedit import audio
from autoedit.render import build_audio, render
from autoedit.util import ffmpeg_exe

SR = 48000


def _write_wav(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _fixture(tmp):
    """6 s voice with words every 0.5 s; 'suddenly' at 3.0 s (and again at 3.5 s - a duplicate)."""
    from scipy import signal
    rng = np.random.default_rng(0)
    voice = np.zeros(int(SR * 6.0))
    words = []
    texts = ["the", "war", "was", "long", "and", "then", "suddenly", "suddenly", "it", "ended"]
    for i, w in enumerate(texts):
        s = 0.3 + i * 0.5
        n = int(SR * 0.35)
        sos = signal.butter(4, [200, 2500], "bandpass", fs=SR, output="sos")
        burst = signal.sosfilt(sos, rng.standard_normal(n)) * np.hanning(n) * 0.5
        a = int(SR * s)
        voice[a:a + n] += burst
        words.append({"w": w, "s": round(s, 3), "e": round(s + 0.35, 3)})
    vpath = os.path.join(tmp, "voice.wav")
    _write_wav(vpath, voice)
    lib = os.path.join(tmp, "sfx", "impact")
    os.makedirs(lib)
    t = np.arange(int(SR * 0.4)) / SR
    _write_wav(os.path.join(lib, "tone.wav"), 0.8 * np.sin(2 * np.pi * 6000 * t) * np.exp(-t / 0.2))
    img = os.path.join(tmp, "img.jpg")
    Image.new("RGB", (640, 360), (90, 60, 40)).save(img)
    return vpath, words, os.path.join(tmp, "sfx"), img


def _project(tmp, vpath, words, sfx_dir, img, level):
    return {"name": "sfxtest", "voiceover": vpath, "sfx_dir": sfx_dir, "music": "", "output_dir": tmp,
            "words": words, "duration": 6.0, "overlays": [], "chapters": [], "script": "",
            "scenes": [{"start": 0.0, "end": 6.0, "text": "", "media": img, "motion": "zoom_in",
                        "transition": "cut", "zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0}],
            "settings": {"sfx_level": level, "builtin_sfx": False, "loudness": None, "music_db": -20,
                         "preset": "Documentary", "quality": "720p", "fps": 30,
                         "captions": False, "caption_style": "None", "formats": ["16:9"],
                         "encoder": "CPU (x264)", "seed": 1}}


def _band_db(path, t0, t1, lo=5500, hi=6500):
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         stdout=subprocess.PIPE).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    seg = x[int(t0 * SR):int(t1 * SR)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return 10 * np.log10(spec[(f > lo) & (f < hi)].sum() + 1e-12)


def main():
    tmp = tempfile.mkdtemp()
    vpath, words, sfx_dir, img = _fixture(tmp)
    log = []

    # 1) plan: one impact at ~3.3 s (the first 'suddenly'), duplicate suppressed
    ev = audio.plan_sfx([], words, sfx_dir, "Normal", builtin=False, log=log.append)
    hits = [e for e in ev if e["cat"] == "impact"]
    assert len(hits) == 1, ev
    assert abs(hits[0]["t"] - 3.3) < 0.05 and "suddenly" in hits[0]["reason"], hits
    print("  plan: impact at", hits[0]["t"], "s -", hits[0]["reason"])

    # 2) mixed WAV contains the effect, well above the no-SFX baseline
    p = _project(tmp, vpath, words, sfx_dir, img, "Normal")
    d1 = os.path.join(tmp, "w1")
    os.makedirs(d1)
    mix_on, _, used = build_audio(p, d1, log.append)
    assert used and used[0]["vol_db"] > -40
    p0 = _project(tmp, vpath, words, sfx_dir, img, "None")
    d0 = os.path.join(tmp, "w0")
    os.makedirs(d0)
    mix_off, _, used0 = build_audio(p0, d0, log.append)
    assert used0 == []
    on, off = _band_db(mix_on, 3.3, 3.6), _band_db(mix_off, 3.3, 3.6)
    assert on - off > 25, (on, off)
    print(f"  mix: 6 kHz band at 3.3 s = {on:.1f} dB with SFX vs {off:.1f} dB without")

    # 3) survives the final MP4 mux (with loudness normalisation on)
    p["settings"]["loudness"] = -14.0
    p["sfx_planned"] = False
    out = render(p, "16:9", log=lambda m: None)
    a_on, a_before = _band_db(out, 3.3, 3.6), _band_db(out, 1.0, 1.3)
    assert a_on - a_before > 25, (a_on, a_before)
    print(f"  mp4: SFX band {a_on:.1f} dB at 3.3 s vs {a_before:.1f} dB at 1.0 s -> present in export")

    # 4) manual edits persist: disabled event -> no effect in the mix
    p["settings"]["loudness"] = None
    p["sfx_events"][0]["enabled"] = False
    p["sfx_planned"] = True
    d2 = os.path.join(tmp, "w2")
    os.makedirs(d2)
    mix_dis, _, used2 = build_audio(p, d2, log.append)
    assert used2 == [] and _band_db(mix_dis, 3.3, 3.6) - off < 6
    print("  disabled effect is respected")

    # 5) missing library -> clear message, no crash, no effects
    log.clear()
    ev = audio.plan_sfx([], words, os.path.join(tmp, "nope"), "Cinematic", builtin=False, log=log.append)
    assert ev == [] and any("does not exist" in m for m in log), log
    print("  missing library reported:", [m for m in log if "exist" in m][0])

    # 6) malformed rules file -> defaults + note
    bad = os.path.join(tmp, "bad.json")
    open(bad, "w").write("{ not json")
    r, note = audio.load_rules(bad)
    assert "invalid" in note and r["transitions"]
    print("  malformed rules handled:", note[:60])
    print("SFX OK")


if __name__ == "__main__":
    main()
