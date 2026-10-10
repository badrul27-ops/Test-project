"""Editing instructions (v7.1) - verification 1-10 of the brief, measured on REAL exports
(decoded frames and audio), plus parser / import checks.

    python -m tests.test_edit_instructions
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autoedit import instructions as I  # noqa: E402
from autoedit import mediainfo  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit.engine import Compositor  # noqa: E402
from autoedit.render import render_all  # noqa: E402
from autoedit.util import ffmpeg_exe  # noqa: E402

SR = 48000
FPS = 30
W, H = 854, 480
SENTENCES = ["A man walks into the old station.", "He buys a ticket at the counter.",
             "The train arrives at the platform.", "She opened the letter.", "She read it twice.",
             "She knew what to do next.", "The airport was crowded that night.", "Planes waited on the runway.",
             "She opened the door and left."]
TINTS = [(220, 40, 40), (40, 200, 60), (50, 70, 230), (230, 200, 30), (40, 210, 210), (200, 60, 200)]


def ff(*a):
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *a], check=True)


def write_wav(path, a, sr=SR):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())


def read_audio(path):
    r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       stdout=subprocess.PIPE, check=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768


def frame(path, t):
    n = int(round(t * FPS))
    r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", path, "-vf", f"select=eq(n\\,{n})", "-vsync", "0",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, check=True)
    return np.frombuffer(r.stdout, np.uint8)[: W * H * 3].reshape(H, W, 3)


def which_tint(img):
    c = img[H // 4: 3 * H // 4, W // 4: 3 * W // 4].reshape(-1, 3).mean(axis=0)
    d = [np.abs(c - np.array(t)).sum() for t in TINTS]
    return int(np.argmin(d)) + 1, c


def band_env(x, f0, hop=0.002):
    """Energy envelope of a narrow band around f0 (Hz), one value per `hop` seconds."""
    n = int(SR * 0.004)
    tt = np.arange(n) / SR
    k = np.exp(-2j * np.pi * f0 * tt) * np.hanning(n)
    y = np.abs(np.convolve(x, k, mode="same"))
    step = int(SR * hop)
    return y[::step], hop


def onset(x, f0, after=0.0, frac=0.5):
    env, hop = band_env(x, f0)
    i0 = int(after / hop)
    pk = env[i0:].max()
    i = i0 + int(np.argmax(env[i0:] > frac * pk))
    return i * hop, pk


def build(tmp):
    media = os.path.join(tmp, "media")
    os.makedirs(os.path.join(media, "alt"))
    rng = np.random.default_rng(4)
    for i, tint in enumerate(TINTS, 1):
        base = (rng.random((45, 80, 1)) * 70 - 35)
        a = np.clip(np.array(tint, np.float32)[None, None, :] + base, 0, 255).astype(np.uint8)
        Image.fromarray(a).resize((1600, 900), Image.BICUBIC).save(os.path.join(media, f"{i:03d}.png"))
    Image.fromarray(np.full((90, 160, 3), 128, np.uint8)).save(os.path.join(media, "alt", "003.png"))   # same name
    ff("-f", "lavfi", "-i", "color=c=0xFF00FF:s=640x360:r=30:d=6", "-f", "lavfi", "-i",
       "sine=frequency=1000:sample_rate=48000:duration=6", "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264",
       "-preset", "ultrafast", "-c:a", "aac", os.path.join(media, "006.mp4"))
    ff("-f", "lavfi", "-i", "color=c=0x00FFFF:s=640x360:r=30:d=2", "-pix_fmt", "yuv420p", "-c:v", "libx264",
       "-preset", "ultrafast", os.path.join(media, "007_short.mp4"))
    sfx = os.path.join(tmp, "sfx")
    os.makedirs(sfx)
    tt = np.arange(int(0.4 * SR)) / SR
    write_wav(os.path.join(sfx, "paper.wav"), 0.5 * np.sin(2 * np.pi * 3000 * tt))
    # voice: 180 Hz "speech" per word, 0.5 s pause between sentences
    words, parts, t = [], [np.zeros(int(0.3 * SR), np.float32)], 0.3
    for sent in SENTENCES:
        for wd in sent.split():
            d = 0.22 + 0.025 * len(wd)
            n = int(d * SR)
            x = np.arange(n) / SR
            parts.append((0.3 * np.sin(2 * np.pi * 180 * x) * np.hanning(n)).astype(np.float32))
            words.append({"w": wd, "s": round(t, 3), "e": round(t + d, 3)})
            t += d
            parts.append(np.zeros(int(0.06 * SR), np.float32))
            t += 0.06
        parts.append(np.zeros(int(0.5 * SR), np.float32))
        t += 0.5
    a = np.concatenate(parts)
    voice = os.path.join(tmp, "voice.wav")
    write_wav(voice, a)
    dur = round(len(a) / SR, 3)
    p = prj.new_project()
    p.update(name="ins", voiceover=voice, media_dir=media, sfx_dir=sfx, output_dir=os.path.join(tmp, "out"),
             words=words, duration=dur, script_text=" ".join(SENTENCES))
    p["settings"].update(scene_mode="smart", auto_vary=False, hook_fast_cuts=False, sfx_mode="explicit_only",
                         loudness=None, captions=False, resolution="Draft", enc_quality="Draft", encoder="CPU (x264)",
                         motion_level="None", transition_default="cut", builtin_sfx=False)
    os.makedirs(p["output_dir"])
    prj.replan(p, log=lambda m: None)
    return p


def time_of(p, phrase, occ=1):
    o = I.find_occurrences(p["words"], phrase)[occ - 1]
    return p["words"][o[0]]["s"], o


def scene(p, sid):
    return next(s for s in p["scenes"] if s.get("id") == sid)


def export(p, tag):
    p = dict(p, name=tag)
    outs = render_all(p, log=lambda m: None)
    assert len(outs) == 1
    return outs[0]


def main():
    tmp = tempfile.mkdtemp(prefix="ae_ins_")
    p = build(tmp)
    log = []
    sid = p["scenes"][1]["id"]
    rec = I.record(p, sid)

    # ---------------- 1: narration-linked visual + precedence over automatic choices
    rec["instruction_text"] = ("Use 003.png when the narrator says “She opened the letter.” Hold it until “She knew "
                               "what to do next.” Slowly zoom toward the letter. Play paper.wav at “opened,” quietly "
                               "below the narration. Add no B-roll. Cut to the next scene. Lock the visual and timing.")
    I.parse_record(rec)
    assert not rec["unresolved"], rec["unresolved"]
    ok, msg = I.apply(p, sid, log.append)
    assert not ok and any(i["level"] == "choice" and "files are named 003.png" in i["msg"] for i in rec["issues"]), \
        rec["issues"]
    opts = next(i for i in rec["issues"] if i["key"] == "primary_visual")["options"]
    rec["choices"]["primary_visual"] = next(k for k, o in enumerate(opts) if "alt" not in o)
    ok, msg = I.apply(p, sid, log.append)
    assert ok, msg
    a_exp = I.cut_before(p["words"], time_of(p, "She opened the letter")[1][0])
    b_exp = I.cut_after(p["words"], time_of(p, "She knew what to do next")[1][1], p["duration"])
    sc = scene(p, sid)
    assert abs(sc["start"] - I.snap(a_exp, FPS)) < 1e-6 and abs(sc["end"] - I.snap(b_exp, FPS)) < 1e-6, sc
    assert sc["media"].endswith(os.path.join("media", "003.png")) and sc["motion"] == "zoom_in"
    assert sc["provenance"]["visual"] == f"instruction {sid}" and sc["locked_fields"] == ["primary_visual", "timing"]
    assert any("set the focal point" in i["msg"].lower() for i in rec["issues"])       # "toward the letter": honest
    # regenerate: the automatic planner must not override the instruction
    prj.replan(p, log=lambda m: None)
    sc = scene(p, sid)
    assert sc["media"].endswith(os.path.join("media", "003.png")) and abs(sc["start"] - I.snap(a_exp, FPS)) < 1e-6, (sc["media"], sc["start"], a_exp, [(s["id"], s["start"]) for s in p["scenes"]])
    assert sum(1 for s in p["scenes"] if s["media"] == sc["media"]) == 1
    out1 = export(p, "check1")
    for t in (sc["start"] + 0.1, (sc["start"] + sc["end"]) / 2, sc["end"] - 0.1):
        n, c = which_tint(frame(out1, t))
        assert n == 3, (t, n, c)
    prev = p["scenes"][p["scenes"].index(sc) - 1]
    assert which_tint(frame(out1, sc["start"] - 0.1))[0] != 3 and prev["media"] != sc["media"]
    print(f"  1. 003.png shows exactly while “She opened the letter … what to do next” is spoken "
          f"({sc['start']:.3f}-{sc['end']:.3f} s, checked on exported frames); kept after re-planning")

    # ---------------- 6: SFX at its anchor (within one frame), gain + fade in the mix
    t_open = p["words"][time_of(p, "She opened the letter")[1][0] + 1]["s"]      # “opened”
    ev = [e for e in p["sfx_events"] if e.get("source") == sid]
    assert len(ev) == 1 and abs(ev[0]["t"] - t_open) < 1e-3 and ev[0]["rel_db"] == -14.0
    x1 = read_audio(out1)
    t_on, pk1 = onset(x1, 3000, after=t_open - 1.0)
    assert abs(t_on - t_open) <= 1.0 / FPS, (t_on, t_open)
    rec["settings"]["sfx"][0]["gain_db"] = -24.0
    rec["settings"]["sfx"][0]["fade_in"] = 0.2
    assert I.apply(p, sid, log.append, reviewed=True)[0]
    out6 = export(p, "check6")
    x6 = read_audio(out6)
    env1, hop = band_env(x1, 3000)
    env6, _ = band_env(x6, 3000)
    i0 = int(t_open / hop)
    steady = slice(i0 + int(0.25 / hop), i0 + int(0.35 / hop))
    gain_meas = 20 * np.log10(env6[steady].mean() / env1[steady].mean())
    assert abs(gain_meas - (-10.0)) < 1.0, gain_meas
    early = env6[i0 + int(0.01 / hop): i0 + int(0.05 / hop)].mean() / env6[steady].mean()
    assert early < 0.3, early
    print(f"  6. paper.wav starts at {t_on:.3f} s for “opened” at {t_open:.3f} s (|Δ| ≤ 1 frame); "
          f"-14 → -24 dB measured {gain_meas:+.1f} dB; 0.2 s fade-in: first 50 ms at {early:.0%} of full level")

    # ---------------- 3: repeated phrases, duplicate names, ordinal references -> explicit choices
    sid2 = p["scenes"][-1]["id"]
    r2 = I.record(p, sid2)
    r2["instruction_text"] = "Use the fifth image when the narrator says “She opened”."
    I.parse_record(r2)
    ok, _ = I.apply(p, sid2, log.append)
    msgs = [i["msg"] for i in r2["issues"] if i["level"] == "choice"]
    assert not ok and any("appears 2 times" in m for m in msgs) and any("Confirm the candidate" in m for m in msgs), msgs
    occ_opts = next(i for i in r2["issues"] if i["key"] == "narration_anchor")["options"]
    assert len(occ_opts) == 2 and "letter" in occ_opts[0] and "door" in occ_opts[1]
    cand = next(i for i in r2["issues"] if i["key"] == "primary_visual")["options"][0].split(" ")[0]
    r2["choices"].update(narration_anchor=1, primary_visual=0)
    ok, msg = I.apply(p, sid2, log.append)
    assert ok, msg
    s2 = scene(p, sid2)
    assert os.path.basename(s2["media"]) == cand and abs(s2["start"] - I.snap(I.cut_before(
        p["words"], time_of(p, "She opened", 2)[1][0]), FPS)) < 1e-6
    print("  3. “She opened” (spoken twice), two files named 003.png and “the fifth image” each produced a "
          "choice; nothing was applied until chosen")

    # ---------------- 2: Inherit / Automatic / None differ and persist
    sid3 = p["scenes"][0]["id"]
    r3 = I.record(p, sid3)
    r3["settings"]["motion"] = {"mode": "none"}
    r3["settings"]["sfx_mode"] = "none"
    r3["settings"]["effects_mode"] = "inherit"
    assert I.apply(p, sid3, log.append)[0]
    s3 = scene(p, sid3)
    assert s3["motion"] == "none" and s3["sfx_mode"] == "none"
    p["sfx_events"].append({"t": s3["start"] + 0.5, "file": os.path.join(tmp, "sfx", "paper.wav"), "cat": "auto",
                            "enabled": True})
    from autoedit.render import _drop_in_silent_scenes
    kept = _drop_in_silent_scenes(p, [e for e in p["sfx_events"] if not e.get("source")], lambda m: None)
    assert not kept, "None must suppress automatic effects in the scene"
    p["sfx_events"] = [e for e in p["sfx_events"] if e.get("source")]
    r3["settings"]["motion"] = {"mode": "auto"}
    assert I.apply(p, sid3, log.append)[0] and scene(p, sid3)["provenance"]["motion"] == "automatic"
    path = os.path.join(tmp, "proj.json")
    prj.save(p, path)
    q = prj.load(path)
    rq = q["edit_instructions"]
    assert rq[sid3]["settings"]["motion"] == {"mode": "auto"} and rq[sid3]["settings"]["sfx_mode"] == "none"
    assert rq[sid3]["settings"]["effects_mode"] == "inherit" and rq[sid]["settings"]["locked_fields"] == [
        "primary_visual", "timing"]
    print("  2. motion None vs Automatic vs Inherit and SFX None (suppresses automatic effects) differ and "
          "survive save + reload")

    # ---------------- 4: motion - preview == export, no empty canvas, combined move
    sid4 = next(s["id"] for s in p["scenes"] if s["id"] not in (sid, sid2, sid3) and s["media"].endswith(".png")
                and os.sep + "alt" + os.sep not in s["media"])
    r4 = I.record(p, sid4)
    r4["settings"]["motion"] = {"mode": "explicit", "type": "zoom_out+pan_right", "strength": "strong",
                                "ease": "linear"}
    r4["settings"]["framing"] = {"mode": "explicit", "fit": "crop", "focus": [0.3, 0.4]}
    assert I.apply(p, sid4, log.append)[0]
    s4 = scene(p, sid4)
    assert s4["motion"] == "zoom_out+pan_right" and s4["motion_ease"] == "linear", (s4["media"], s4.get("motion"))
    out4 = export(p, "check4")
    comp = Compositor(p, W, H, FPS, total=p["duration"] + 0.4, preview=True)
    for t in (s4["start"] + 1 / FPS, (s4["start"] + s4["end"]) / 2, s4["end"] - 2 / FPS):
        t = round(t * FPS) / FPS
        ex, pv = frame(out4, t), comp.frame_at(t)
        q4 = 10 * np.log10(255 ** 2 / max(1e-6, np.mean((ex.astype(float) - pv.astype(float)) ** 2)))
        assert q4 > 32, q4
        for band in (ex[:, :3], ex[:, -3:], ex[:3], ex[-3:]):
            assert band.mean() > 25, "empty canvas at an edge"
    comp.close()
    f_a, f_b = frame(out4, s4["start"] + 2 / FPS), frame(out4, s4["end"] - 3 / FPS)
    dd = np.abs(f_a.astype(int) - f_b.astype(int)).mean()
    assert dd > 3, ("motion should move the picture", dd, s4["start"], s4["end"], s4["media"])
    print("  4. zoom out + pan right (strong, linear, focal point 30%/40%): preview = export (PSNR > 32 dB), "
          "no empty edges at start/middle/end")

    # ---------------- 5: B-roll insert only on its interval, narration untouched
    sid5 = next(s["id"] for s in p["scenes"] if s["start"] <= time_of(p, "airport")[0] < s["end"])
    r5 = I.record(p, sid5)
    r5["instruction_text"] = "Insert clip 006.mp4 while the narrator mentions the airport for 1.5 seconds."
    I.parse_record(r5)
    assert I.apply(p, sid5, log.append)[0], r5["issues"]
    br = [b for b in p["broll"] if b.get("source") == sid5]
    assert len(br) == 1 and br[0]["mode"] == "insert"
    t_air = time_of(p, "airport")[0]
    out5 = export(p, "check5")
    mag = lambda img: img[..., 0].mean() > 200 and img[..., 1].mean() < 60 and img[..., 2].mean() > 200  # noqa
    assert mag(frame(out5, br[0]["start"] + 0.1)) and mag(frame(out5, br[0]["end"] - 0.1))
    assert not mag(frame(out5, br[0]["start"] - 0.1)) and not mag(frame(out5, br[0]["end"] + 0.1))
    a4, a5 = read_audio(out4), read_audio(out5)
    n = min(len(a4), len(a5))
    assert np.abs(a4[:n] - a5[:n]).max() < 1e-3 and abs(len(a4) - len(a5)) < SR * 0.05, "narration changed"
    # overlay with its own sound, and a too-short clip -> explicit choice
    r5["settings"]["b_roll"][0].update(mode="overlay", position="top_right", size=0.3, audio_gain_db=-6.0)
    r5["settings"]["b_roll"].append({"asset_filename": "007_short.mp4", "mode": "insert", "anchor": None,
                                     "start_seconds": t_air + 2.0, "end_seconds": t_air + 6.0, "end_anchor": None,
                                     "duration_seconds": None, "clip_in": None, "clip_out": None, "size": 0.35,
                                     "position": "top_right", "audio_gain_db": None, "short": None})
    I.apply(p, sid5, log.append, reviewed=True)
    short = [i for i in r5["issues"] if "provides 2.0 seconds" in i["msg"]]
    assert short and "needs 4.0 seconds" in short[0]["msg"], r5["issues"]
    assert I._status(r5) == "review"
    out5b = export(p, "check5b")
    fr = frame(out5b, br[0]["start"] + 0.5)
    corner, centre = fr[10:100, W - 200:W - 20], fr[H // 2 - 40:H // 2 + 40, W // 2 - 40:W // 2 + 40]
    assert corner[..., 1].mean() < 70 and corner[..., 0].mean() > 180, corner.mean(axis=(0, 1))
    assert not (centre[..., 0].mean() > 200 and centre[..., 1].mean() < 60)
    x5 = read_audio(out5b)
    on1k, _ = onset(x5, 1000, after=br[0]["start"] - 0.5)
    assert abs(on1k - br[0]["start"]) < 0.05, (on1k, br[0]["start"])
    print(f"  5. B-roll 006.mp4 on screen only {br[0]['start']:.2f}-{br[0]['end']:.2f} s (frames just outside "
          "show the scene); narration audio identical with/without it; overlay mode sits top-right with its own "
          "sound when enabled; a 2 s clip for a 4 s insert asks for a decision")
    r5["settings"]["b_roll"] = r5["settings"]["b_roll"][:1]
    I.apply(p, sid5, log.append, reviewed=True)

    # ---------------- 7: transitions - no drift, no missing frames, applied once
    k7 = next(k for k, s in enumerate(p["scenes"][:-1]) if not s.get("locked") and
              p["scenes"][k + 1]["end"] - p["scenes"][k + 1]["start"] > 1.5 and s["end"] - s["start"] > 1.5)
    s7 = p["scenes"][k7]
    r7 = I.record(p, s7["id"])
    r7["settings"]["transition_out"] = {"mode": "explicit", "type": "crossfade", "duration_seconds": 0.6}
    starts_before = [(s["id"], s["start"], s["end"]) for s in p["scenes"]]
    assert I.apply(p, s7["id"], log.append)[0]
    assert [(s["id"], s["start"], s["end"]) for s in p["scenes"]] == starts_before, "transition moved a cut"
    out7 = export(p, "check7")
    inf = mediainfo.probe(out7)
    total = p["duration"] + 0.4
    n_frames = int(round(total * FPS))
    r = subprocess.run([ffmpeg_exe(), "-i", out7, "-map", "0:v", "-f", "null", "-"], stderr=subprocess.PIPE)
    import re
    got = int(re.findall(r"frame=\s*(\d+)", r.stderr.decode())[-1])
    assert got == n_frames and abs(inf["duration"] - total) < 0.05, (got, n_frames, inf["duration"])
    cut = s7["end"]
    A_, B_ = frame(out7, cut - 0.45), frame(out7, cut + 0.45)
    mid = frame(out7, cut)
    da = np.abs(mid.astype(int) - A_.astype(int)).mean()
    db = np.abs(mid.astype(int) - B_.astype(int)).mean()
    dab = np.abs(A_.astype(int) - B_.astype(int)).mean()
    assert da > 0.2 * dab and db > 0.2 * dab, (da, db, dab)
    # the other boundaries were not given a transition: a straight cut there (no blend frame)
    c2 = next(s["end"] for s in p["scenes"][:-1] if s is not s7 and s.get("transition") == "cut" and
              s["media"].endswith(".png") and p["scenes"][p["scenes"].index(s) + 1]["media"].endswith(".png"))
    import math
    nb = math.ceil(c2 * FPS - 1e-6)                       # first frame of the next scene
    fr = [frame(out7, n / FPS).astype(int) for n in range(nb - 2, nb + 3)]
    diffs = [np.abs(a - b).mean() for a, b in zip(fr, fr[1:])]
    assert int(np.argmax(diffs)) == 1 and sorted(diffs)[-2] < 0.35 * max(diffs), diffs   # one clean jump
    print(f"  7. crossfade 0.6 s at {cut:.2f} s: every cut time unchanged, {got} frames = {total:.2f} s × {FPS} fps "
          "(no missing/extra frames), the boundary frame is a blend of both scenes")

    # ---------------- 8: locks survive regeneration; a changed voiceover asks for review
    prj.replan(p, log=lambda m: None)
    sc = scene(p, sid)
    assert sc["locked_fields"] == ["primary_visual", "timing"] and sc["media"].endswith(os.path.join("media", "003.png"))
    old_times = (sc["start"], sc["end"])
    p["words"] = [dict(w, s=round(w["s"] + 0.7, 3), e=round(w["e"] + 0.7, 3)) for w in p["words"]]
    p["duration"] = round(p["duration"] + 0.7, 3)
    msgs = []
    prj.replan(p, log=msgs.append)
    rec = I.record(p, sid)
    assert rec["stale"] and any("Review its locked times" in i["msg"] for i in rec["issues"]), rec["issues"]
    assert any("need review" in m for m in msgs)
    sc = scene(p, sid)
    assert (sc["start"], sc["end"]) == old_times, ((sc["start"], sc["end"]), old_times)
    print(f"  8. locked visual + timing kept through re-planning; after the narration moved 0.7 s the locked scene "
          f"kept {sc['start']:.2f}-{sc['end']:.2f} s and says: “{[i['msg'] for i in rec['issues'] if i['key'] == 'stale'][0][:70]}…”")

    # ---------------- 9: JSON / CSV import and export
    data = I.export_json(p)
    jp = os.path.join(tmp, "ins.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    inc, errs, prev = I.load_import(p, jp)
    assert not errs, errs
    for sid_, d in inc["scenes"].items():
        mine = I.records(p)[sid_]["settings"]
        assert {k: v for k, v in d["settings"].items()} == {k: v for k, v in mine.items()}, sid_
    bad = {"schema_version": 1, "scenes": [{"scene_id": "S900", "motion": {"mode": "wobble"}, "sfx_mode": "none"},
                                           {"scene_id": "bad id!"},
                                           {"scene_id": "S901", "effects_mode": "explicit",
                                            "effects": [{"type": "lens_flare"}]},
                                           {"scene_id": "S902", "sfx_mode": "explicit",
                                            "sfx": [{"asset_filename": "paper.wav", "gain_db": "30%"}]}]}
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    inc, errs, _ = I.load_import(p, jp)
    joined = " | ".join(errs)
    assert "motion needs a 'mode'" in joined and "scene_id: 'bad id!'" in joined and "lens_flare" in joined \
        and "gain_db must be a number" in joined and not inc["scenes"], joined
    with open(jp, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert "not valid JSON" in I.load_import(p, jp)[1][0]
    with open(jp, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 99, "scenes": []}, f)
    assert "schema_version 99" in I.load_import(p, jp)[1][0]
    cp = os.path.join(tmp, "ins.csv")
    with open(cp, "w", encoding="utf-8") as f:
        f.write(",".join(I.CSV_COLUMNS) + "\n")
        f.write(f"{sid3},,,,follow_narration,,,none,,cut,,none,,,,\n")
        f.write("S950,missing.png,The train arrives at the platform,,follow_narration,,,inherit,fill,dissolve,0.5,"
                "paper.wav,arrives,0.1,-12,\n")
        f.write("S951,001.png,,,absolute,5,3,,,,,,,,,\n")
        f.write("S952,001.png,,,teleport,,,,,,,,,,,\n")
    inc, errs, prev = I.load_import(p, cp)
    assert any("row 4, end_seconds" in e for e in errs) and any("row 5, timing_mode" in e for e in errs), errs
    assert inc["scenes"][sid3]["settings"]["motion"] == {"mode": "none"} and inc["scenes"][sid3]["settings"][
        "sfx_mode"] == "none" and inc["scenes"][sid3]["settings"]["effects_mode"] == "inherit"
    I.commit_import(p, inc)
    ok, msg = I.apply(p, "S950", log.append)
    assert not ok and "missing.png is missing" in msg, msg
    assert I.records(p)["S950"]["status"] == "missing"
    ok, _ = I.apply(p, sid3, log.append, reviewed=True)
    assert ok and scene(p, sid3)["motion"] == "none", (ok, scene(p, sid3)["media"], scene(p, sid3).get("motion"), I.records(p)[sid3]["settings"]["motion"])
    print(f"  9. JSON export → import gives identical settings; malformed JSON, schema 99, unknown modes/effects, "
          f"'30%' as a gain, bad CSV rows ({len(errs)} row/field errors) are rejected; a missing asset is reported, "
          "explicit None imports as None")

    # ---------------- 10: validation blocks a fatal problem; one export = one file
    del I.records(p)["S950"]
    p["sfx_events"].append({"t": 1.0, "file": os.path.join(tmp, "sfx", "gone.wav"), "source": sid3, "rel_db": -10})
    try:
        export(p, "blocked")
        raise AssertionError("missing SFX should block the export")
    except RuntimeError as e:
        assert "gone.wav is missing" in str(e)
    p["sfx_events"] = [e for e in p["sfx_events"] if not e["file"].endswith("gone.wav")]
    before = set(os.listdir(p["output_dir"]))
    out10 = export(p, "final")
    assert set(os.listdir(p["output_dir"])) - before == {os.path.basename(out10)}
    assert not mediainfo.check_export(out10, W, H, FPS, p["duration"] + 0.4)
    # old project without instructions still loads and renders its plan
    old = os.path.join(tmp, "old.json")
    with open(old, "w", encoding="utf-8") as f:
        json.dump({"name": "old", "settings": {"formats": ["16:9"]}, "scenes": [], "words": []}, f)
    assert prj.load(old)["edit_instructions"] == {}
    print("  10. a missing explicitly requested sound blocks the export (nothing written); a normal export makes "
          "exactly one verified file; older projects load with no instructions")
    shutil.rmtree(tmp, ignore_errors=True)
    print("EDIT INSTRUCTIONS OK")


if __name__ == "__main__":
    main()
