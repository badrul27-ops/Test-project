"""Quick self-checks:  python -m tests.test_core   (run from the AutoEditor folder)"""
import os
import random
import tempfile
import time

import numpy as np
from PIL import Image

from autoedit.captions import CaptionRenderer, make_chunks
from autoedit.presets import PRESETS
from autoedit.scenes import split_scenes
from autoedit.transcribe import apply_script
from autoedit.visuals import ImageSource, blend


def fake_words(minutes=10, seed=1):
    rng = random.Random(seed)
    vocab = "the war began when germany sent a secret message to mexico and britain read it".split()
    words, t = [], 0.3
    while t < minutes * 60:
        n = rng.randint(6, 18)
        for i in range(n):
            d = rng.uniform(0.18, 0.45)
            w = rng.choice(vocab) + ("." if i == n - 1 else ("," if rng.random() < 0.08 else ""))
            words.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
            t += d + rng.uniform(0.02, 0.09)
        t += rng.uniform(0.25, 1.4)
    return words, t + 0.5


def check_scenes():
    words, dur = fake_words(10)
    for name, pr in PRESETS.items():
        t0 = time.time()
        sp = split_scenes(words, dur, pr)
        el = time.time() - t0
        lens = [b - a for a, b in sp]
        assert abs(sp[0][0]) < 1e-6 and abs(sp[-1][1] - dur) < 2e-3
        assert all(sp[i][1] == sp[i + 1][0] for i in range(len(sp) - 1))
        print(f"  auto  {name:22s} {len(sp):4d} scenes  {min(lens):.1f}-{max(lens):.1f}s  ({el:.2f}s)")
    t0 = time.time()
    sp = split_scenes(words, dur, PRESETS["Documentary"], n_scenes=120)
    lens = [b - a for a, b in sp]
    assert len(sp) == 120
    print(f"  fixed 120 scenes {min(lens):.1f}-{max(lens):.1f}s ({time.time() - t0:.2f}s)")


def check_script():
    words = [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate("the zimerman telegram was sent".split())]
    out = apply_script(words, "The Zimmermann Telegram was sent.")
    assert [w["w"] for w in out] == ["The", "Zimmermann", "Telegram", "was", "sent."], out
    print("  script correction ok")


def check_visuals():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "tést image.png")  # non-ASCII path
    Image.new("RGBA", (800, 1200), (200, 50, 50, 128)).save(p)
    for W, H in ((1920, 1080), (1080, 1920)):
        for m in ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"):
            s = ImageSource(p, W, H, m, 0.15)
            a, b = s.frame(0), s.frame(1)
            assert a.shape == (H, W, 3) and b.shape == (H, W, 3)
    f1 = np.zeros((720, 1280, 3), np.uint8)
    f2 = np.full((720, 1280, 3), 255, np.uint8)
    for k in ("cut", "crossfade", "dip_black", "slide", "whip", "zoom"):
        for p_ in (0, 0.3, 0.5, 0.7, 1):
            assert blend(f1, f2, k, p_).shape == f1.shape
    print("  visuals ok")


def check_captions():
    words, _ = fake_words(1)
    ch = make_chunks(words, 4, 26)
    assert sum(len(c["words"]) for c in ch) == len(words)
    for style in ("Modern (word highlight)", "Boxed word (TikTok)", "Documentary", "Minimal"):
        for W, H in ((1920, 1080), (1080, 1920)):
            cr = CaptionRenderer(words, style, W, H)
            fr = np.zeros((H, W, 3), np.uint8)
            for t in np.arange(0, 20, 0.37):
                cr.overlay(fr, float(t))
    print("  captions ok")


def check_timestamps():
    from autoedit.scenes import parse_timestamp, timestamp_spans
    assert parse_timestamp("0-03.png") == 3.0 and parse_timestamp("1-15.jpg") == 75.0
    assert parse_timestamp("0-03.5.png") == 3.5 and parse_timestamp("01-02-03.png") == 3723.0
    assert parse_timestamp("scene_01.jpg") is None
    words, dur = fake_words(1)
    media = [f"{m}-{s:02d}.png" for m, s in ((0, 0), (0, 7), (0, 15), (0, 31), (0, 44))]
    msgs = []
    spans, files = timestamp_spans(words, dur, media, snap=True, log=msgs.append)
    assert len(spans) == 5 and spans[0][0] == 0 and abs(spans[-1][1] - dur) < 1e-6
    assert all(spans[i][1] == spans[i + 1][0] for i in range(4))
    # a cut snapped forward past the next timestamp, or duplicate timestamps: never a zero/negative scene
    w2 = [{"w": "a", "s": 0, "e": 5.4}, {"w": "b", "s": 5.6, "e": 9.0}]
    for media2, snap in ((["0-00.png", "0-05.png", "0-05.3.png"], True), (["0-00.png", "0-03.png", "0-03.png"], False)):
        msgs = []
        spans, files = timestamp_spans(w2, 10.0, media2, snap=snap, log=msgs.append)
        assert all(b > a for a, b in spans) and len(files) == len(spans) == 2, spans
        assert any("NOT used" in m for m in msgs), msgs
    try:
        timestamp_spans(w2, 10.0, ["1-05.png", "1-10.png"], log=lambda *_: None)
        raise AssertionError("timestamps all after the end must be a clear error")
    except ValueError:
        pass
    from autoedit.project import estimate_words
    est = estimate_words("one two three.", 0.3)
    assert all(0 <= x["s"] <= x["e"] <= 0.3 for x in est), est
    print("  timestamps ok")


def check_fx_and_compositor():
    from autoedit.engine import Compositor
    from autoedit.fx import ATMOSPHERES, Atmosphere
    f = np.full((360, 640, 3), 120, np.uint8)
    for name in ATMOSPHERES:
        a = Atmosphere(640, 360, 30, {"vignette": 0.2}, name, 1.0)
        for t in (0, 0.5, 3.3):
            out = a.apply(f, t)
            assert out.shape == f.shape and out.dtype == np.uint8
        assert np.array_equal(a.apply(f, 1.0), a.apply(f, 1.0)), name  # deterministic
    d = tempfile.mkdtemp()
    imgs = []
    for i in range(3):
        p = os.path.join(d, f"s{i}.jpg")
        Image.new("RGB", (1344, 768), (40 * i, 90, 160)).save(p)
        imgs.append(p)
    words, dur = fake_words(0.3)
    proj = {"settings": {"preset": "Documentary", "caption_style": "Modern (word highlight)",
                         "captions": True, "atmosphere": "Old Film", "atmo_strength": 1.0},
            "words": words, "duration": dur,
            "scenes": [{"start": dur * k / 3, "end": dur * (k + 1) / 3, "media": imgs[k], "text": "",
                        "motion": "zoom_in", "transition": "crossfade", "zoom": 1.1, "x": 0.02,
                        "y": 0.0, "rot": 2.0} for k in range(3)]}
    for prev in (True, False):
        c = Compositor(proj, 640, 360, 30, total=dur, preview=prev)
        for t in np.arange(0, dur, 0.5):
            assert c.frame_at(float(t)).shape == (360, 640, 3)
        c.close()
    print("  fx + compositor ok")


def check_v3():
    from autoedit.audio import plan_sfx, scan_sfx
    from autoedit.captions import CaptionRenderer, available_fonts
    from autoedit.editorial import (apply_hook_cuts, auto_chapters, description_text, quality_report,
                                    vary_preset)
    from autoedit.overlays import OverlayRenderer
    lib = scan_sfx(None)
    assert len(lib) >= 25 and "typing" in lib and "whoosh" in lib, sorted(lib)
    words, dur = fake_words(3)
    scenes = [{"start": dur * k / 30, "end": dur * (k + 1) / 30, "media": "", "text": "",
               "motion": "zoom_in", "transition": ["whip", "dip_black", "cut"][k % 3]} for k in range(30)]
    ev = plan_sfx(scenes, words, None, "Cinematic",
                  overlays=[{"type": "label", "start": 5.0, "end": 8.0, "text": "x"}])
    assert ev and any(e["cat"] == "typing" for e in ev), ev[:3]
    assert "Roboto Black" in available_fonts()
    fr = np.zeros((1920, 1080, 3), np.uint8)
    for anim in ("None", "Pop-in", "Word bounce"):
        cr = CaptionRenderer(words, "Modern (word highlight)", 1080, 1920,
                             {"caption_font": "Lato Black", "highlight_color": "#FF0000", "caption_anim": anim,
                              "caption_position": "Top", "caption_size": 130})
        for t in np.arange(0, 10, 0.07):
            cr.overlay(fr.copy(), float(t))
    ovs = [{"type": k, "start": 1.0, "end": 4.0, "text": "Hello world", "sub": "Sub"}
           for k in ("hook", "title", "chapter", "label", "lower_third")]
    for W, H in ((1920, 1080), (1080, 1920)):
        o = OverlayRenderer(ovs, W, H, {})
        for t in (1.05, 1.5, 2.5, 3.9):
            assert o.apply(np.zeros((H, W, 3), np.uint8), t).shape == (H, W, 3)
    from autoedit.presets import PRESETS
    v = vary_preset(PRESETS["Documentary"], 5)
    assert v["motions"] != PRESETS["Documentary"]["motions"]
    proj = {"words": words, "duration": dur, "settings": {}, "overlays": [], "script": "",
            "scenes": [dict(s, media=__file__.replace(".py", ".jpg")) for s in scenes]}
    proj["scenes"][0]["end"] = proj["scenes"][1]["start"] = 7.0
    apply_hook_cuts(proj)
    assert proj["scenes"][0]["end"] <= 7.0
    ch = auto_chapters(proj)
    assert ch and ch[0]["start"] == 0
    txt, _ = description_text(proj, ch)
    assert txt.startswith("Chapters:\n0:00")
    items, score = quality_report(proj)
    assert 0 <= score <= 100 and any(l == "fix" for l, _, _ in items)  # missing media flagged
    print("  v3 (sfx, fonts, captions, overlays, variety, hook, chapters, quality) ok")


def check_review_fixes():
    """Regressions: instruction edge cases, safe output names, SFX level 'None' with explicit effects."""
    from autoedit import instructions as I
    from autoedit import project as P
    from autoedit.render import _safe_name, build_audio
    from autoedit.util import run_ffmpeg
    tmp = tempfile.mkdtemp()
    for n in ("001.png", "002.png"):
        Image.new("RGB", (64, 36), (90, 90, 90)).save(os.path.join(tmp, n))
    voice = os.path.join(tmp, "vo.wav")
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=220:d=6", "-ar", "48000", voice])
    p = P.new_project()
    p.update(media_dir=tmp, voiceover=voice, output_dir=tmp, duration=6.0,
             words=[{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4}
                    for i, w in enumerate("the cat sat on the mat today and then she left home".split())])
    p["scenes"] = [{"id": "S001", "start": 0.0, "end": 6.0, "media": os.path.join(tmp, "001.png"), "text": "",
                    "motion": "none", "transition": "cut"}]
    # end phrase not in the recording: reported, nothing applied (used to crash with TypeError)
    rec = I.record(p, "S001")
    s = rec["settings"]
    s.update(narration_anchor={"text": "cat", "occurrence": 1}, end_anchor={"text": "banana"},
             effects_mode="explicit", effects=[{"type": "saturation", "start_offset": 0}])
    ok, msg = I.apply(p, "S001", log=lambda *_: None)
    assert not ok and "banana" in msg, msg
    # effects in a scene with two pictures: each picture gets its own part, times relative to itself
    s.update(narration_anchor=None, end_anchor=None, timing_mode="absolute", start_seconds=0.0, end_seconds=6.0,
             primary_visual={"asset_filename": "001.png"},
             segments=[{"ref": "002.png", "start_seconds": 4.0}],
             effects=[{"type": "saturation", "start_offset": 3.0, "end_offset": 5.0}])
    ok, msg = I.apply(p, "S001", log=lambda *_: None)
    assert ok, msg
    a_, b_ = sorted(p["scenes"], key=lambda x: x["start"])
    assert [(e["start"], e["end"]) for e in a_["effects"]] == [(3.0, 4.0)], a_["effects"]
    assert [(e["start"], e["end"]) for e in b_["effects"]] == [(0.0, 1.0)], b_["effects"]
    # badly shaped imported settings are listed, not crashed on later
    bad = I._check_settings(dict(I.blank_settings(), primary_visual="001.png", narration_anchor={"occurrence": 2}))
    assert any("primary_visual" in x for x in bad) and any("narration_anchor" in x for x in bad), bad
    # file names that Windows can't store
    assert _safe_name({"name": "Part 1: A/B?"}) == "Part 1_ A_B_" and _safe_name({"name": " .. "}) == "video"
    # SFX level 'None' plus an explicit effect from an instruction: mixes instead of crashing
    sfx = os.path.join(tmp, "hit.wav")
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=900:d=0.3", "-ar", "48000", sfx])
    p["settings"]["sfx_level"] = "None"
    p["sfx_events"] = [{"t": 1.0, "file": sfx, "cat": "impact", "rel_db": -6.0, "source": "S001"}]
    work = os.path.join(tmp, "_autoedit_work")
    os.makedirs(work, exist_ok=True)
    out, _total, use = build_audio(p, work, log=lambda *_: None)
    assert os.path.isfile(out) and len(use) == 1
    assert not [f for f in os.listdir(work) if f.endswith(".part.wav")]
    print("  review fixes (instructions, names, SFX level None) ok")


if __name__ == "__main__":
    check_review_fixes()
    check_scenes()
    check_script()
    check_visuals()
    check_captions()
    check_timestamps()
    check_fx_and_compositor()
    check_v3()
    print("ALL OK")
