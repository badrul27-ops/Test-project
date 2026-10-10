"""Fixed render benchmark: python -m tools.bench_render [out_dir] [--quality Standard] [--x264 PRESET:CRF]

Builds the same sample project every time (60 s narration, 12 large photos 3000x2000, 1 video
clip, music, sound effects, captions) and renders ONE 16:9 1920x1080 30 fps video on the CPU
encoder, reporting time per stage. Used to measure before/after changes - numbers depend on the PC.
"""
import json
import os
import platform
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autoedit import project as prj  # noqa: E402
from autoedit import render as R  # noqa: E402
from autoedit.engine import Compositor  # noqa: E402
from autoedit.util import ffmpeg_exe  # noqa: E402

SR = 48000


def build(tmp):
    import wave
    rng = np.random.default_rng(1)
    media = os.path.join(tmp, "media")
    os.makedirs(media, exist_ok=True)
    for i in range(12):
        im = Image.fromarray((rng.random((2000, 3000, 3)) * 60 + 40 + i * 10).astype(np.uint8))
        d = ImageDraw.Draw(im)
        d.ellipse([1000, 500, 2000, 1500], fill=(240, 240, 240))
        im.save(os.path.join(media, f"{i + 1:03d}.jpg"), quality=88)
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1920x1080:rate=30:d=8", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    "-preset", "ultrafast", os.path.join(media, "013.mp4")], check=True)
    words, t = [], 0.4
    sent = "the ancient hunters cooked acorns by the fire and their teeth slowly decayed".split()
    k = 0
    while t < 59:
        w = sent[k % len(sent)] + ("." if k % len(sent) == len(sent) - 1 else "")
        words.append({"w": w, "s": round(t, 3), "e": round(t + 0.3, 3)})
        t += 0.38 + (0.6 if w.endswith(".") else 0)
        k += 1
    dur = 60.0
    tt = np.arange(int(dur * SR)) / SR
    voice = 0.25 * np.sin(2 * np.pi * 180 * tt) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * tt)) ** 2
    music = 0.1 * np.sin(2 * np.pi * 440 * tt)
    for name, a in (("voice.wav", voice), ("music.wav", music)):
        with wave.open(os.path.join(tmp, name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((a * 32767).astype(np.int16).tobytes())
    p = prj.new_project()
    p.update(name="bench", voiceover=os.path.join(tmp, "voice.wav"), music=os.path.join(tmp, "music.wav"),
             media_dir=media, output_dir=os.path.join(tmp, "out"), words=words, duration=dur)
    p["settings"].update(scene_mode="match_media", quality="1080p", fps=30, encoder="CPU (x264)",
                         formats=["16:9"], sfx_level="Normal", hook_fast_cuts=False, auto_vary=False,
                         atmosphere="None")
    prj.replan(p, log=lambda m: None)
    return p


def main():
    args = [a for a in sys.argv[1:]]
    opts = {}
    for k in ("--quality", "--x264"):
        if k in args:
            i = args.index(k)
            opts[k] = args[i + 1]
            del args[i:i + 2]
    tmp = args[0] if args else tempfile.mkdtemp(prefix="ae_bench_")
    os.makedirs(tmp, exist_ok=True)
    t0 = time.time()
    p = build(tmp)
    t_build = time.time() - t0
    os.makedirs(p["output_dir"], exist_ok=True)
    if "--quality" in opts:
        p["settings"]["enc_quality"] = opts["--quality"]
    if "--x264" in opts:          # exact encoder settings (e.g. the old default 'fast:20', or a reference 'veryfast:4')
        preset, crf = opts["--x264"].split(":")
        try:
            from autoedit import exportcfg
            exportcfg.x264_args = lambda q: ["-c:v", "libx264", "-preset", preset, "-crf", crf,
                                             "-pix_fmt", "yuv420p", "-profile:v", "high"]
        except ImportError:       # older versions
            pass
    stats = {"compose": 0.0, "frames": 0}
    orig = Compositor.frame_at

    def timed(self, t, *a, **k):
        s = time.time()
        r = orig(self, t, *a, **k)
        stats["compose"] += time.time() - s
        stats["frames"] += 1
        return r
    Compositor.frame_at = timed
    log = []
    t0 = time.time()
    work = os.path.join(p["output_dir"], "_autoedit_work")
    os.makedirs(work, exist_ok=True)
    ta = time.time()
    mix, total, _ = R.build_audio(p, work, log=log.append)
    t_mix = time.time() - ta
    tr = time.time()
    out = R.render(p, "16:9", log=log.append, mix_path=mix, total=total)
    t_render = time.time() - tr
    Compositor.frame_at = orig
    res = {"cpu": platform.processor() or platform.machine(), "cores": os.cpu_count(),
           "input_seconds": p["duration"], "assets": len(p["scenes"]), "output": "1920x1080 30fps 16:9",
           "mix_s": round(t_mix, 2), "render_s": round(t_render, 2),
           "compose_s": round(stats["compose"], 2), "frames": stats["frames"],
           "encode_wait_s": round(t_render - stats["compose"], 2),
           "total_s": round(time.time() - t0, 2), "fixture_build_s": round(t_build, 2), "file": out,
           "size_mb": round(os.path.getsize(out) / 1e6, 1), "settings": opts or "default"}
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main()
