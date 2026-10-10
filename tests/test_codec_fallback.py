"""Clips whose codec this PC's OpenCV can't decode (e.g. PNG / ProRes in .mov) must still
preview, thumbnail and report their length - via a cached H.264 preview copy made with FFmpeg.

    python -m tests.test_codec_fallback
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autoedit import visuals as V  # noqa: E402
from autoedit.util import ffmpeg_exe  # noqa: E402


def main():
    tmp = tempfile.mkdtemp()
    clip = os.path.join(tmp, "alpha_clip.mov")
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:d=3", "-c:v", "png", clip], check=True)
    before = os.path.getsize(clip)
    real = V._cv_can_read
    V._cv_can_read = lambda p: not p.endswith(".mov")        # simulate the Windows OpenCV build
    try:
        V.PROXY_DIR = os.path.join(tmp, "proxies")
        d = V.probe_duration(clip)
        assert d is not None and abs(d - 3.0) < 0.2, d
        src = V.SeekVideoSource(clip, 320, 180, 3.0, 30, d)
        f1, f2 = src.frame_index(10).copy(), src.frame_index(60).copy()
        src.close()
        assert f1.mean() > 5 and np.abs(f1.astype(int) - f2.astype(int)).mean() > 2, "proxy frames empty/static"
        from autoedit.media_bin import make_thumb
        import autoedit.media_bin as MB
        MB.CACHE_DIR = os.path.join(tmp, "thumbs")
        img = make_thumb(clip, 104, 58)
        assert img.width() == 104 or img.height() == 58
        assert os.path.getsize(clip) == before, "original clip was modified"
        print(f"  duration {d:.2f}s via FFmpeg, preview frames + thumbnail via proxy, original untouched")
    finally:
        V._cv_can_read = real
    print("CODEC FALLBACK OK")


if __name__ == "__main__":
    main()
