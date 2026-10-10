"""Read real stream facts with FFmpeg (no ffprobe needed): size, rotation, sample/display aspect
ratio, frame rate, duration, audio. Used to frame rotated media correctly and to VERIFY exports."""
import re
import subprocess

from .util import ffmpeg_exe, popen_kwargs

_CACHE = {}


def probe(path):
    try:
        import os
        key = (path, os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return None
    if key in _CACHE:
        return _CACHE[key]
    try:
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=30, **popen_kwargs())
        txt = r.stderr.decode("utf-8", "replace")
    except Exception:
        return None
    info = {"duration": None, "w": 0, "h": 0, "rotation": 0, "sar": (1, 1), "dar": None, "fps": None,
            "vcodec": None, "acodec": None, "pix_fmt": None}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    vm = re.search(r"Stream #\S+.*?Video:\s*([^,\s]+)[^\n]*", txt)
    if vm:
        line = vm.group(0)
        info["vcodec"] = vm.group(1)
        sm = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
        if sm:
            info["w"], info["h"] = int(sm.group(1)), int(sm.group(2))
        sar = re.search(r"SAR (\d+):(\d+) DAR (\d+):(\d+)", line)
        if sar:
            info["sar"] = (int(sar.group(1)), int(sar.group(2)))
            info["dar"] = (int(sar.group(3)), int(sar.group(4)))
        fm = re.search(r",\s*([\d.]+) fps", line)
        if fm:
            info["fps"] = float(fm.group(1))
        pm = re.search(r"Video:\s*[^,]+,\s*([a-z0-9]+)", line)
        if pm:
            info["pix_fmt"] = pm.group(1)
    rot = re.search(r"rotation of (-?[\d.]+) degrees", txt) or re.search(r"rotate\s*:\s*(-?\d+)", txt)
    if rot:
        info["rotation"] = int(round(float(rot.group(1)))) % 360
    am = re.search(r"Stream #\S+.*?Audio:\s*([^,\s]+)", txt)
    if am:
        info["acodec"] = am.group(1)
    # displayed size: rotation swaps, non-square pixels stretch the width
    w, h = info["w"], info["h"]
    if info["sar"] and info["sar"][1] and info["sar"] != (1, 1) and info["sar"][0]:
        w = int(round(w * info["sar"][0] / info["sar"][1]))
    if info["rotation"] in (90, 270):
        w, h = h, w
    info["display_w"], info["display_h"] = w, h
    _CACHE[key] = info
    return info


def check_export(path, W, H, fps, duration, tol=0.6):
    """-> list of problems (empty = output verified)."""
    info = probe(path)
    if not info:
        return ["output file can't be read"]
    bad = []
    if (info["w"], info["h"]) != (W, H):
        bad.append(f"size is {info['w']}x{info['h']}, expected {W}x{H}")
    if info["sar"] not in ((1, 1), (0, 1), None):
        bad.append(f"pixels aren't square (SAR {info['sar'][0]}:{info['sar'][1]})")
    if info["rotation"]:
        bad.append(f"has rotation metadata ({info['rotation']}°)")
    if info["fps"] and abs(info["fps"] - fps) > 0.05:
        bad.append(f"frame rate is {info['fps']}, expected {fps}")
    if info["duration"] is None or abs(info["duration"] - duration) > tol:
        bad.append(f"duration is {info['duration']}, expected about {duration:.2f}s")
    if not info["acodec"]:
        bad.append("no audio track")
    if info["vcodec"] != "h264":
        bad.append(f"video codec is {info['vcodec']}, expected h264")
    return bad
