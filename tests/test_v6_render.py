"""End-to-end render check for the v6 workspace features (section 9 of the upgrade brief).

    python -m tests.test_v6_render

Builds a short project from generated media (spoken voice via espeak-ng if installed,
otherwise a tone-burst "voice"), 5 images, 1 video clip, a music bed (pure 5 kHz tone so
it can be measured separately), sound effects, captions, a crossfade, keyframes on one
scene and a V2 clip - then renders Draft and Final in 16:9 and 9:16 and measures:

  * sizes / durations of every output
  * keyframed zoom is visible in the export (frame difference inside the scene)
  * V2 clip is composited
  * captions are drawn (pixels differ from a caption-free frame)
  * the crossfade frame is a blend of both scenes
  * each SFX adds energy at its time (vs the same mix with SFX off)
  * music is present, fades in, and is ducked under the voice (5 kHz band)
  * Draft is faster than Final
  * cancelling a render removes only the partial output, never source media
Needs no network. Whisper is not used (word timings are generated).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autoedit import project as prj  # noqa: E402
from autoedit.engine import Compositor  # noqa: E402
from autoedit.render import Cancelled, build_audio, render, render_all  # noqa: E402
from autoedit.util import ffmpeg_exe  # noqa: E402

SR = 48000
SENTENCES = ["The storm arrived without any warning at all.",
             "Nobody in the small town was ready for it.",
             "By morning the river had crossed the old bridge.",
             "Then something strange happened near the church.",
             "People still talk about that night today."]


def ff(*args):
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def read_wav(path):
    with wave.open(path) as w:
        n, ch, sr = w.getnframes(), w.getnchannels(), w.getframerate()
        a = np.frombuffer(w.readframes(n), np.int16).astype(np.float32) / 32768
    return (a.reshape(-1, ch).mean(axis=1) if ch > 1 else a), sr


def write_wav(path, a, sr=SR):
    a = np.clip(a, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32767).astype(np.int16).tobytes())


def make_voice(tmp):
    """Voice = sentences with 0.9 s pauses between. Returns (path, words, duration)."""
    parts, words, t = [], [], 0.3
    parts.append(np.zeros(int(0.3 * SR), np.float32))
    for k, sent in enumerate(SENTENCES):
        seg = None
        if shutil.which("espeak-ng"):
            p = os.path.join(tmp, f"s{k}.wav")
            subprocess.run(["espeak-ng", "-s", "150", "-w", p, sent], check=True)
            ff("-i", p, "-ar", str(SR), "-ac", "1", p + ".48k.wav")
            seg, _ = read_wav(p + ".48k.wav")
            nz = np.nonzero(np.abs(seg) > 0.01)[0]
            seg = seg[nz[0]:nz[-1]] if len(nz) else seg
        if seg is None:              # fallback: tone bursts shaped like syllables
            n = len(sent.split())
            tt = np.arange(int(0.32 * n * SR)) / SR
            seg = 0.3 * np.sin(2 * np.pi * 180 * tt) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * tt)) ** 2
        seg = seg.astype(np.float32) * (0.5 / max(1e-3, np.abs(seg).max()))
        dur = len(seg) / SR
        toks = sent.split()
        tot = sum(len(x) + 1 for x in toks)
        acc = t
        for x in toks:
            d = dur * (len(x) + 1) / tot
            words.append({"w": x, "s": round(acc, 3), "e": round(acc + d * 0.9, 3)})
            acc += d
        parts.append(seg)
        t += dur
        gap = 0.9
        parts.append(np.zeros(int(gap * SR), np.float32))
        t += gap
    a = np.concatenate(parts)
    path = os.path.join(tmp, "voice.wav")
    write_wav(path, a)
    return path, words, len(a) / SR


def make_media(tmp):
    folder = os.path.join(tmp, "media")
    os.makedirs(folder)
    cols = [(200, 60, 50), (40, 140, 90), (50, 80, 200), (220, 180, 40), (150, 60, 170)]
    for i, c in enumerate(cols):
        im = Image.new("RGB", (1600, 900), c)
        d = ImageDraw.Draw(im)
        for k in range(0, 1600, 100):          # stripes, so motion is measurable
            d.rectangle([k, 0, k + 40, 900], fill=tuple(min(255, v + 50) for v in c))
        d.ellipse([650, 300, 950, 600], fill=(255, 255, 255))
        im.save(os.path.join(folder, f"{i + 1:02d}_image.jpg"), quality=90)
    clip = os.path.join(folder, "06_clip.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:d=6", "-pix_fmt", "yuv420p", "-c:v", "libx264",
       "-preset", "ultrafast", clip)
    return folder


def make_music(tmp, dur):
    t = np.arange(int((dur + 5) * SR)) / SR
    a = 0.25 * np.sin(2 * np.pi * 5000 * t)
    p = os.path.join(tmp, "music.wav")
    write_wav(p, a)
    return p


def band(a, sr, t0, t1, f=5000, bw=150):
    seg = a[int(t0 * sr):int(t1 * sr)]
    if len(seg) < 256:
        return 0.0
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    fr = np.fft.rfftfreq(len(seg), 1 / sr)
    return float(spec[(fr > f - bw) & (fr < f + bw)].sum() / len(seg))


def tone(a, sr, t0, t1, f=5000):
    """Level of the 5 kHz music tone only (lock-in: speech hardly correlates with a pure tone)."""
    s = a[int(t0 * sr):int(t1 * sr)]
    tt = np.arange(len(s)) / sr
    return float(2 * abs(np.sum(s * np.exp(-2j * np.pi * f * tt))) / max(1, len(s)))


def rms(a, sr, t0, t1):
    seg = a[int(max(0, t0) * sr):int(t1 * sr)]
    return float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate:format=duration", "-of", "csv=p=0:s=,", path],
                         capture_output=True, text=True).stdout.split()
    w, h, r = out[0].split(",")
    return int(w), int(h), r, float(out[1])


def frame(path, t, W, H):
    raw = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.int16)


def main():
    tmp = tempfile.mkdtemp(prefix="ae_v6_")
    print("work folder:", tmp)
    voice, words, dur = make_voice(tmp)
    folder = make_media(tmp)
    music = make_music(tmp, dur)
    before = {f: os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder)}
    p = prj.new_project()
    p.update(name="v6test", voiceover=voice, media_dir=folder, music=music, output_dir=os.path.join(tmp, "out"),
             words=words, duration=round(dur, 3))
    st = p["settings"]
    st.update(scene_mode="match_media", sfx_level="Cinematic", atmosphere="None", resolution="HD",
              aspect="16:9", batch_export=True, batch_formats=["9:16"], fit="blur",
              formats=["16:9", "9:16"], music_db=-14, music_fade_in=2.0, music_fade_out=2.0, music_duck=True,
              encoder="CPU (x264)", fps=30, hook_fast_cuts=False, auto_vary=False)
    prj.replan(p, log=lambda m: None)
    sc = p["scenes"]
    assert len(sc) == 6, len(sc)
    for s in sc:
        s["transition"] = "cut"
        if s["motion"] != "none":
            s["motion"] = "none"              # truly still, so keyframes are the only movement
        s["zoom"], s["x"], s["y"], s["rot"] = 1.0, 0.0, 0.0, 0.0
    sc[0]["transition"] = "crossfade"
    k = 2
    L = sc[k]["end"] - sc[k]["start"]
    sc[k]["keyframes"] = [{"t": 0.0, "zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0, "opacity": 1.0},
                          {"t": round(L, 3), "zoom": 1.6, "x": 0.1, "y": 0.0, "rot": 6.0, "opacity": 1.0}]
    br_t = sc[3]["start"] + 0.3
    p["broll"] = [{"media": os.path.join(folder, "06_clip.mp4"), "start": round(br_t, 3), "end": round(br_t + 1.5, 3),
                   "motion": "none", "zoom": 0.5, "x": 0.25, "y": -0.25, "rot": 0.0, "opacity": 1.0, "fade": 0.0}]
    from autoedit.render import plan_project_sfx
    ev = plan_project_sfx(p, log=lambda m: None)
    if len(ev) < 2:        # make sure there are effects to measure
        from autoedit.audio import scan_sfx
        lib = scan_sfx(None, True)
        for t0, cat in ((sc[1]["start"], "whoosh" if "whoosh" in lib else sorted(lib)[0]),
                        (sc[4]["start"], "impact" if "impact" in lib else sorted(lib)[0])):
            ev.append({"t": round(t0, 3), "file": lib[cat][0], "cat": cat, "gain": 0.0, "rule_gain": 0.0,
                       "dur": None, "enabled": True, "reason": "test"})
        p["sfx_events"], p["sfx_planned"] = sorted(ev, key=lambda e: e["t"]), True
    print(f"project: {dur:.1f}s voice, {len(sc)} scenes, {len(p['sfx_events'])} SFX, 1 V2 clip, keyframes on "
          f"scene {k + 1}, crossfade after scene 1")
    os.makedirs(p["output_dir"], exist_ok=True)

    # ---- renders + timing
    logs = []
    t0 = time.time()
    drafts = render_all(p, log=logs.append, draft=True, formats=["16:9"])
    t_draft = time.time() - t0
    t0 = time.time()
    finals = render_all(p, log=logs.append)
    t_final_both = time.time() - t0
    t0 = time.time()
    final169 = render(p, "16:9", log=logs.append)
    t_final = time.time() - t0
    print(f"  draft 16:9 {t_draft:.1f}s · final 16:9 {t_final:.1f}s (both formats {t_final_both:.1f}s)")
    assert t_draft < t_final, "draft was not faster than final"
    for path, (W, H) in ((drafts[0], (854, 480)), (finals[0], (1280, 720)), (finals[1], (720, 1280))):
        w_, h_, r, d = probe(path)
        assert (w_, h_) == (W, H), (path, w_, h_)
        assert abs(d - (dur + 0.4)) < 0.3, (path, d)
        print(f"  {os.path.basename(path)}: {w_}x{h_} {r} fps {d:.2f}s")

    sq = render(dict(p, name="square"), "1:1", log=logs.append, draft=True)
    w_, h_, r, d = probe(sq)
    assert (w_, h_) == (480, 480) and abs(d - (dur + 0.4)) < 0.3, (w_, h_, d)
    print(f"  {os.path.basename(sq)}: {w_}x{h_} {d:.2f}s (1:1 export)")
    W, H = 1280, 720
    # ---- keyframed motion visible in the export
    s = sc[k]
    a = frame(final169, s["start"] + 0.15, W, H)
    b = frame(final169, s["end"] - 0.15, W, H)
    comp = Compositor(p, W, H, 30, preview=False, captions=False, log=lambda m: None)
    ref_a = comp.frame_at(s["start"] + 0.15, captions=False).astype(np.int16)
    comp.close()
    diff_kf = np.abs(a - b).mean()
    print(f"  keyframes: mean frame change across scene {k + 1} = {diff_kf:.1f} (static scene would be ~0)")
    assert diff_kf > 8
    # static scene for comparison (captions change too, so compare top half)
    s2 = sc[1]
    c1 = frame(final169, s2["start"] + 0.9, W, H)[:H // 2]      # after the crossfade
    c2 = frame(final169, s2["end"] - 0.3, W, H)[:H // 2]
    print(f"  static scene 2 top-half change = {np.abs(c1 - c2).mean():.1f}")
    assert np.abs(c1 - c2).mean() < 3
    # ---- captions drawn (bottom area differs from caption-free render of the same frame)
    capdiff = np.abs(a[int(H * 0.6):] - ref_a[int(H * 0.6):]).mean()
    print(f"  captions: bottom-area difference vs caption-free frame = {capdiff:.1f}")
    assert capdiff > 1.0
    # ---- V2 clip composited (top-right quadrant shows the test clip)
    v2 = frame(final169, br_t + 0.7, W, H)
    no_v2 = frame(final169, br_t - 0.2, W, H)
    q = (slice(0, H // 2), slice(W // 2, W))
    print(f"  V2 clip: top-right change when the clip is on = {np.abs(v2[q] - no_v2[q]).mean():.1f}")
    assert np.abs(v2[q] - no_v2[q]).mean() > 15
    # ---- crossfade frame is a blend
    cut = sc[0]["end"]
    mid = frame(final169, cut, W, H)[:H // 2].mean(axis=(0, 1))
    pre = frame(final169, cut - 0.9, W, H)[:H // 2].mean(axis=(0, 1))
    post = frame(final169, cut + 0.9, W, H)[:H // 2].mean(axis=(0, 1))
    between = all(min(x, y) - 6 <= m <= max(x, y) + 6 for m, x, y in zip(mid, pre, post))
    print(f"  crossfade: colour at cut {mid.round()} between {pre.round()} and {post.round()} -> {between}")
    assert between and np.abs(mid - pre).max() > 10 and np.abs(mid - post).max() > 10

    # ---- audio: SFX energy, music presence / fades / ducking
    aud = os.path.join(tmp, "final_audio.wav")
    ff("-i", final169, "-vn", "-ac", "1", "-ar", str(SR), aud)
    fa, sr = read_wav(aud)
    p2 = dict(p, settings=dict(st, sfx_level="None"))
    work2 = os.path.join(tmp, "nosfx")
    os.makedirs(work2)
    mix2, _, _ = build_audio(p2, work2, log=lambda m: None)
    ns, _ = read_wav(mix2) if wave.open(mix2).getframerate() == SR else (None, None)
    if ns is None:
        ff("-i", mix2, "-ac", "1", "-ar", str(SR), mix2 + ".48k.wav")
        ns, _ = read_wav(mix2 + ".48k.wav")
    audible = 0
    for e in [e for e in p["sfx_events"] if e.get("enabled", True)]:
        w_with = rms(fa, sr, e["t"] - 0.05, e["t"] + 0.5)
        w_without = rms(ns, sr, e["t"] - 0.05, e["t"] + 0.5)
        gain_db = 20 * np.log10(max(w_with, 1e-6) / max(w_without, 1e-6))
        ok = gain_db > 1.0
        audible += ok
        print(f"  SFX {e['cat']:<12} at {e['t']:6.2f}s: {gain_db:+5.1f} dB vs no-SFX mix {'✓' if ok else '(quiet)'}")
    assert audible >= max(1, int(0.6 * len(p["sfx_events"]))), "most sound effects are not audible"
    # SFX track volume (Audio tab) raises every effect in the export mix
    p3 = dict(p, settings=dict(st, sfx_db=6.0))
    work3 = os.path.join(tmp, "sfxup")
    os.makedirs(work3)
    mix3, _, used3 = build_audio(p3, work3, log=lambda m: None)
    _, _, used1 = build_audio(p, os.path.join(tmp, "out", "_autoedit_work"), log=lambda m: None)
    assert all(abs(b["vol_db"] - a["vol_db"] - 6.0) < 0.15 for a, b in zip(used1, used3)), "sfx_db not applied"
    print("  SFX volume +6 dB -> every effect's mix level +6 dB")
    # music: 5 kHz band. fade-in: first 0.3 s quieter than 2.5-3.5 s (a pause or speech-free zone preferred)
    # classify 0.25 s windows by the (original) voice level: speaking vs pause
    v, _ = read_wav(voice)
    speak, quiet = [], []
    for t0 in np.arange(1.0, dur - 1.0, 0.25):
        vr = rms(v, SR, t0, t0 + 0.25)
        (speak if vr > 0.03 else quiet if vr < 0.003 else []).append(tone(fa, sr, t0, t0 + 0.25))
    m_speech, m_pause = float(np.median(speak)), float(np.median(quiet))
    m_start, m_end = tone(fa, sr, 0.0, 0.25), tone(fa, sr, dur + 0.15, dur + 0.4)
    duck_db = 20 * np.log10(m_pause / max(m_speech, 1e-9))
    print(f"  music (5 kHz tone level): pauses {m_pause:.3f} vs speech {m_speech:.3f} -> ducked {duck_db:.1f} dB; "
          f"first 0.25 s {m_start:.3f} (fade-in), last 0.25 s {m_end:.3f} (fade-out)")
    assert m_pause > 0.01, "music not present"
    assert duck_db > 3, "music not ducked under the voice"
    assert m_start < m_pause * 0.5, "no music fade-in"
    assert m_end < m_pause * 0.5, "no music fade-out"

    # ---- automatic motion (category + level) is visible in an export
    import copy as _copy
    from autoedit.presets import PRESETS
    from autoedit.profiles import apply_category
    from autoedit.scenes import apply_motion_level
    pm = _copy.deepcopy(p)
    apply_category(pm["settings"], "Long Story History")
    pm["settings"]["motion_level"] = "Dynamic"
    pm["name"], pm["broll"] = "motion", []
    for s in pm["scenes"]:
        s.pop("keyframes", None)
    apply_motion_level(pm["scenes"], PRESETS["Long Story History"], "Dynamic", seed=3)
    mv = render(pm, "16:9", log=lambda m: None, draft=True)
    s1 = pm["scenes"][1]
    fa_ = frame(mv, s1["start"] + 0.9, 854, 480)[:240]
    fb_ = frame(mv, s1["end"] - 0.3, 854, 480)[:240]
    amts = sorted({s.get("motion_amount") for s in pm["scenes"] if s["motion"] != "none"})
    print(f"  automatic motion ({pm['scenes'][1]['motion']}, Dynamic): scene 2 frame change "
          f"{np.abs(fa_ - fb_).mean():.1f}; {len(amts)} different motion amounts across scenes")
    assert np.abs(fa_ - fb_).mean() > 3 and len(amts) > 2

    # ---- cancel: partial output removed, sources untouched
    n = {"i": 0}

    def stop():
        n["i"] += 1
        return n["i"] > 20
    try:
        render(dict(p, name="cancelled"), "16:9", should_stop=stop, log=lambda m: None)
        raise AssertionError("render was not cancelled")
    except Cancelled:
        pass
    assert not os.path.exists(os.path.join(p["output_dir"], "cancelled_16x9.mp4"))
    after = {f: os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder)}
    assert after == before and os.path.isfile(voice) and os.path.isfile(music)
    print("  cancel: partial file removed; voiceover, music and all media untouched")
    print("V6 RENDER OK")
    return tmp


if __name__ == "__main__":
    main()
