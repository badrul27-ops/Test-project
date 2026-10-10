"""v7 export acceptance checks 3-7 (measured on real exported files).

  3. 16:9 Full HD export = 1920x1080, square pixels, no unintended borders in Fill mode
  4. portrait / square sources fill a landscape frame (cropped, not stretched, not barred); the preview
     frame matches the exported frame; Fit modes give the background they promise; baked-in border
     detection finds real bars and leaves dark artwork alone
  5. one export action = one file (batch only when enabled); an existing export is never overwritten
  6. resolution / frame-rate metadata match the settings in every orientation; captions in all of them
  7. GPU encoder unavailable -> CPU fallback (said in the log); cancel, missing files and failed
     verification never report success or leave partial files

    python -m tests.test_v7_export
"""
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autoedit import framing  # noqa: E402
from autoedit import mediainfo  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit.engine import Compositor  # noqa: E402
from autoedit.exportcfg import export_formats, summary  # noqa: E402
from autoedit.render import Cancelled, render_all  # noqa: E402
from autoedit.util import ffmpeg_exe  # noqa: E402

SR = 48000


def write_voice(path, dur):
    tt = np.arange(int(dur * SR)) / SR
    a = 0.25 * np.sin(2 * np.pi * 180 * tt) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * tt)) ** 2
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((a * 32767).astype(np.int16).tobytes())


def noise(w, h, seed):
    """Detailed but smooth picture (big random blobs): edges have detail, H.264 keeps it well."""
    rng = np.random.default_rng(seed)
    a = (rng.random((max(2, h // 40), max(2, w // 40), 3)) * 180 + 40).astype(np.uint8)
    return Image.fromarray(a).resize((w, h), Image.BICUBIC)


def frame(path, t, W, H, fps=30):
    """Exactly frame number round(t * fps) of the file (not a nearby keyframe)."""
    n = int(round(t * fps))
    r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", path, "-vf", f"select=eq(n\\,{n})", "-vsync", "0",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE,
                       check=True)
    return np.frombuffer(r.stdout, np.uint8)[: W * H * 3].reshape(H, W, 3)


def psnr(a, b):
    mse = np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def build(tmp):
    media = os.path.join(tmp, "media")
    os.makedirs(media)
    noise(1600, 1200, 1).save(os.path.join(media, "01_landscape43.png"))     # 4:3
    noise(900, 1600, 2).save(os.path.join(media, "02_portrait.png"))         # 9:16
    noise(1200, 1200, 3).save(os.path.join(media, "03_square.png"))          # 1:1
    voice = os.path.join(tmp, "voice.wav")
    write_voice(voice, 6.0)
    words, t = [], 0.3
    for w in "the old bridge fell. a storm came over the hills. nobody saw it coming that night.".split():
        words.append({"w": w, "s": round(t, 3), "e": round(t + 0.3, 3)})
        t += 0.4
    p = prj.new_project()
    p.update(name="v7", voiceover=voice, media_dir=media, output_dir=os.path.join(tmp, "out"), words=words,
             duration=6.0)
    p["settings"].update(scene_mode="match_media", auto_vary=False, hook_fast_cuts=False, sfx_level="None",
                         encoder="CPU (x264)", motion_level="None", loudness=None)
    prj.replan(p, log=lambda m: None)
    for s in p["scenes"]:
        s["transition"] = "cut"
    os.makedirs(p["output_dir"])
    return p


def mid(s):
    return round((s["start"] + s["end"]) / 2 * 30) / 30


def main():
    tmp = tempfile.mkdtemp(prefix="ae_v7_")
    p = build(tmp)
    st = p["settings"]
    assert export_formats(st) == ["16:9"] and summary(st) == "1 video · 16:9 · 1920 × 1080 · 30 fps · Standard", \
        summary(st)
    logs = []
    # ---------------- 3 + 4: Full HD 16:9, Fill
    outs = render_all(p, log=logs.append)
    assert len(outs) == 1 and outs[0].endswith("v7_16x9_1920x1080.mp4"), outs
    info = mediainfo.probe(outs[0])
    assert (info["w"], info["h"]) == (1920, 1080) and info["sar"] in ((1, 1), None) and info["rotation"] == 0, info
    assert info["display_w"] == 1920 and info["display_h"] == 1080
    assert not mediainfo.check_export(outs[0], 1920, 1080, 30, 6.4)
    mp4s = [f for f in os.listdir(p["output_dir"]) if f.endswith(".mp4")]
    assert mp4s == ["v7_16x9_1920x1080.mp4"], mp4s
    assert not [f for f in os.listdir(os.path.join(p["output_dir"], "_autoedit_work")) if f.startswith("rendering_")]
    comp = Compositor(p, 1920, 1080, 30, total=6.4, preview=True)
    for s in p["scenes"]:
        t = mid(s)
        fr = frame(outs[0], t, 1920, 1080)
        for name, band in (("left", fr[:, :8]), ("right", fr[:, -8:]), ("top", fr[:8]), ("bottom", fr[-8:])):
            assert band.mean() > 60 and band.std() > 20, (os.path.basename(s["media"]), name, band.mean(), band.std())
        pv = comp.frame_at(t)
        q = psnr(pv, fr)
        q_off = psnr(np.roll(pv, 12, axis=1), fr)        # same picture 12 px off: what a mismatch looks like
        assert q > 35 and q > q_off + 8, (os.path.basename(s["media"]), q, q_off)
        print(f"  {os.path.basename(s['media']):<20} edges filled (no bars) · preview vs export PSNR {q:.1f} dB "
              f"(12 px misaligned would be {q_off:.1f} dB)")
    comp.close()
    print(f"  check 3: one file {os.path.basename(outs[0])}: 1920x1080, SAR 1:1, no rotation, H.264+AAC, "
          f"{info['fps']} fps - verified before it was named")

    # fit modes (frame level, same code as preview and export)
    port = np.asarray(noise(900, 1600, 5))
    bars = framing.place(port, 1920, 1080, "fit_bars")
    assert bars[:, :300].max() < 3 and bars[:, 960].std() > 20
    col = framing.place(port, 1920, 1080, "fit_color", color="#204080")
    assert tuple(col[540, 50]) == (0x20, 0x40, 0x80)
    blur = framing.place(port, 1920, 1080, "fit_blur")
    assert blur[:, :300].mean() > 20 and blur[:, :300].std() < port.std() * 0.6
    fill = framing.place(port, 1920, 1080, "crop")
    assert fill.shape == (1080, 1920, 3) and fill[:, :8].mean() > 60
    # never stretched: a circle stays a circle after Fill
    circ = Image.new("RGB", (900, 1600), (30, 30, 30))
    from PIL import ImageDraw
    ImageDraw.Draw(circ).ellipse([250, 600, 650, 1000], fill=(250, 250, 250))
    out = framing.place(np.asarray(circ), 1920, 1080, "crop")
    ys, xs = np.nonzero(out[:, :, 0] > 128)
    ratio = (xs.max() - xs.min()) / (ys.max() - ys.min())
    assert 0.97 < ratio < 1.03, ratio
    print(f"  check 4: portrait source in 16:9 - Fill covers the frame (circle stays round, w/h {ratio:.3f}); "
          "Fit/Bars = black sides, Fit/Colour = chosen colour, Fit/Blur = soft blurred sides")
    # baked-in borders
    art = np.asarray(noise(1200, 800, 7)).copy()
    art[:80] = 0
    art[-80:] = 0
    trim, msg = framing.detect_borders([art])
    assert trim and abs(trim[1] - 0.1) < 0.01 and abs(trim[3] - 0.1) < 0.01 and trim[0] == 0, (trim, msg)
    dark = (np.asarray(noise(1200, 800, 8)).astype(np.float32) * 0.18).astype(np.uint8)   # dark, detailed
    assert framing.detect_borders([dark])[0] is None
    night = np.full((800, 1200, 3), 6, np.uint8)
    night[300:500, 500:700] = 200                      # one lamp in a dark picture: uniform, but no detail
    assert framing.detect_borders([night])[0] is None
    moving = [art.copy(), art.copy()]
    moving[1][:80] = np.asarray(noise(1200, 80, 9))    # picture content there in frame 2 -> not a border
    assert framing.detect_borders(moving)[0][1] == 0
    print("  borders: real 10% bars found (top/bottom); dark artwork and a dark night scene are NOT treated as "
          "borders; a band present in only one video frame isn't either")

    # ---------------- 5: second export never overwrites; batch only when enabled
    m0 = os.path.getmtime(outs[0])
    st.update(resolution="Draft", enc_quality="Draft")
    o2 = render_all(p, log=logs.append)
    o3 = render_all(p, log=logs.append)
    assert len(o2) == 1 and len(o3) == 1 and o3[0].endswith("(2).mp4") and os.path.getmtime(outs[0]) == m0, (o2, o3)
    st.update(batch_formats=["9:16"])
    assert export_formats(st) == ["16:9"], "batch formats are ignored while batch export is off"
    st.update(batch_export=True)
    ob = render_all(p, log=logs.append)
    assert len(ob) == 2 and "9x16" in ob[1]
    st.update(batch_export=False, batch_formats=[])
    print("  check 5: one export = one file; a repeat export gets '(2)' and the older file is untouched; "
          "a 2nd format only with Batch export on")

    # ---------------- 6: metadata + captions in every orientation
    for aspect, res, fps, (W, H) in (("9:16", "HD", 25, (720, 1280)), ("1:1", "Draft", 24, (480, 480)),
                                     ("16:9", "HD", 60, (1280, 720))):
        st.update(aspect=aspect, resolution=res, fps=fps, enc_quality="Draft")
        o = render_all(p, log=logs.append)
        assert len(o) == 1 and f"{W}x{H}" in o[0]
        inf = mediainfo.probe(o[0])
        assert (inf["w"], inf["h"]) == (W, H) and abs(inf["fps"] - fps) < 0.05, inf
        assert not mediainfo.check_export(o[0], W, H, fps, 6.4)
        c_on = Compositor(p, W, H, fps, total=6.4, preview=True)
        c_off = Compositor(p, W, H, fps, total=6.4, preview=True, captions=False)
        t = p["words"][3]["s"] + 0.1
        d = np.abs(c_on.frame_at(t).astype(int) - c_off.frame_at(t).astype(int)).sum(axis=2) > 40
        c_on.close()
        c_off.close()
        ys, xs = np.nonzero(d)
        assert len(xs) > 200, aspect
        assert xs.min() > 2 and xs.max() < W - 3 and ys.max() < H - 3, (aspect, xs.min(), xs.max())
        print(f"  check 6: {aspect} {res} {fps} fps -> file is {inf['w']}x{inf['h']} @ {inf['fps']} fps; captions "
              f"drawn inside the frame (x {xs.min()}-{xs.max()} of {W})")
    st.update(aspect="16:9", resolution="Draft", fps=30)

    # ---------------- 7: GPU fallback, cancel, missing file, failed verification
    st["encoder"] = "NVIDIA GPU (NVENC)"
    lg = []
    o = render_all(p, log=lg.append)
    used = [x for x in lg if x.startswith("Encoder:")]
    assert used and (("CPU" in used[0] and any("isn't available" in x for x in lg)) or "NVENC" in used[0]), lg
    assert not mediainfo.check_export(o[0], 854, 480, 30, 6.4)
    print(f"  check 7: NVENC chosen -> '{used[0]}'" + (" (no NVIDIA GPU here: test encode failed, CPU used)"
                                                      if "CPU" in used[0] else ""))
    st["encoder"] = "CPU (x264)"
    before = set(os.listdir(p["output_dir"]))
    n = [0]

    def stop():
        n[0] += 1
        return n[0] > 40
    try:
        render_all(p, log=lambda m: None, should_stop=stop)
        raise AssertionError("cancel didn't stop the render")
    except Cancelled:
        pass
    assert set(os.listdir(p["output_dir"])) == before
    assert not [f for f in os.listdir(os.path.join(p["output_dir"], "_autoedit_work")) if f.startswith("rendering_")]
    keep = p["scenes"][1]["media"]
    p["scenes"][1]["media"] = os.path.join(tmp, "media", "gone.png")
    try:
        render_all(p, log=lambda m: None)
        raise AssertionError("missing file not reported")
    except RuntimeError as e:
        assert "can't be found" in str(e) and "gone.png" in str(e)
    assert set(os.listdir(p["output_dir"])) == before
    p["scenes"][1]["media"] = keep
    orig = mediainfo.check_export
    mediainfo.check_export = lambda *a, **k: ["size is 1x1, expected 854x480"]
    try:
        render_all(p, log=lambda m: None)
        raise AssertionError("failed verification reported success")
    except RuntimeError as e:
        assert "didn't pass its checks" in str(e)
    finally:
        mediainfo.check_export = orig
    assert set(os.listdir(p["output_dir"])) == before
    assert not [f for f in os.listdir(os.path.join(p["output_dir"], "_autoedit_work")) if f.startswith("rendering_")]
    assert all(os.path.isfile(os.path.join(tmp, "media", f)) for f in ("01_landscape43.png", "02_portrait.png"))
    print("  cancel / missing visual file / failed verification: an error is raised, no video file appears, "
          "no partial file is left, source media untouched")
    print("V7 EXPORT OK")


if __name__ == "__main__":
    main()
