"""Compare an export with a reference render of the same project: PSNR / SSIM of the picture (FFmpeg
filters, all frames) and audio/video sync (stream durations and start times).

    python -m tools.compare_video test.mp4 reference.mp4
"""
import json
import re
import subprocess
import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from autoedit.util import ffmpeg_exe  # noqa: E402


def streams(path):
    """Per-stream start/duration from a full decode (no ffprobe needed)."""
    out = {}
    for sel, name in (("0:v:0", "video"), ("0:a:0", "audio")):
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path, "-map", sel, "-f", "null", "-"],
                           stderr=subprocess.PIPE, stdout=subprocess.DEVNULL)
        txt = r.stderr.decode("utf-8", "replace")
        tm = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
        h, m, s = tm[-1]
        out[name + "_s"] = round(int(h) * 3600 + int(m) * 60 + float(s), 3)
        st = re.search(r"start: (-?[\d.]+)", txt)
        out["start_s"] = float(st.group(1)) if st else None
    return out


def quality(test, ref):
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", test, "-i", ref, "-lavfi",
                        "[0:v][1:v]ssim;[0:v][1:v]psnr", "-f", "null", "-"], stderr=subprocess.PIPE)
    txt = r.stderr.decode("utf-8", "replace")
    ssim = re.search(r"SSIM .*All:([\d.]+)", txt)
    psnr = re.search(r"PSNR .*average:([\d.inf]+)", txt)
    return {"ssim": float(ssim.group(1)) if ssim else None, "psnr_db": float(psnr.group(1)) if psnr else None}


if __name__ == "__main__":
    t, ref = sys.argv[1], sys.argv[2]
    res = {"test": t, "reference": ref}
    res.update(quality(t, ref))
    res["test_streams"] = streams(t)
    res["ref_streams"] = streams(ref)
    print(json.dumps(res, indent=1))
