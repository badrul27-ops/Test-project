"""Frame sources (images with camera motion, video clips) and transitions."""
import math
import os
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageOps

from .util import APP_DIR, ffmpeg_exe, popen_kwargs

PROXY_DIR = os.path.join(APP_DIR, "cache", "proxies")
_CV_OK = {}


def _cv_can_read(path):
    """Can the OpenCV build on this PC decode this clip? (The pip OpenCV for Windows ships a
    reduced FFmpeg: clips stored with e.g. the PNG or ProRes codec open fine in FFmpeg itself
    but fail in OpenCV with 'Could not find decoder for codec_id=...'.)"""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return False
    if key not in _CV_OK:
        cap = cv2.VideoCapture(path)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        _CV_OK[key] = bool(ok)
    return _CV_OK[key]


def preview_proxy(path, log=None):
    """Small H.264 copy of a clip for preview / thumbnails, made once with the full FFmpeg
    and cached in cache/proxies. The original file is never changed; renders use the original."""
    import hashlib
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}".encode("utf-8")).hexdigest()
    out = os.path.join(PROXY_DIR, key + ".mp4")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    os.makedirs(PROXY_DIR, exist_ok=True)
    import threading
    tmp = f"{out}.{os.getpid()}-{threading.get_ident()}.part.mp4"     # thumbnails may build it in parallel
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-an",
           "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
           "-pix_fmt", "yuv420p", tmp]
    try:
        if log:
            log(f"Making a preview copy of {os.path.basename(path)} (its video codec can't be read by the "
                "preview decoder; the render uses the original file)...")
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600,
                       check=True, **popen_kwargs())
        os.replace(tmp, out)
        return out
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None


def readable_video(path, log=None):
    """Path OpenCV can decode: the clip itself, or its cached preview proxy."""
    if not path or _cv_can_read(path):
        return path
    return preview_proxy(path, log) or path


def load_image_rgb(path, need=None):
    """need=(w, h): big JPEGs are decoded at a reduced size that is still >= need (much faster for
    camera-size photos; never smaller than the output needs). Originals are never changed."""
    im = Image.open(path)
    if need and im.format == "JPEG":
        try:
            m = int(max(need) * 1.25)          # either orientation (EXIF rotation is applied after)
            im.draft("RGB", (m, m))
        except Exception:
            pass
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        bg.alpha_composite(im)
        im = bg
    return np.asarray(im.convert("RGB"))


def _ease(p):
    p = min(1.0, max(0.0, p))
    return 0.5 * p + 0.5 * (0.5 - 0.5 * math.cos(math.pi * p))


def ease_curve(p, kind="smooth"):
    p = min(1.0, max(0.0, p))
    if kind == "linear":
        return p
    if kind == "ease_in":
        return p * p
    if kind == "ease_out":
        return 1 - (1 - p) * (1 - p)
    return _ease(p)


def clip_transform(scene):
    """Per-clip user transform: (zoom, x, y, rotation). x/y are fractions of the frame."""
    return (float(scene.get("zoom", 1.0) or 1.0), float(scene.get("x", 0.0) or 0.0),
            float(scene.get("y", 0.0) or 0.0), float(scene.get("rot", 0.0) or 0.0))


KEY_PROPS = ("zoom", "x", "y", "rot", "opacity")
KEY_DEFAULTS = {"zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0, "opacity": 1.0}


def _smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def clip_state(clip, local_t):
    """(tf, opacity) of a clip at `local_t` seconds after its start.
    With keyframes the values are interpolated (eased) between keyframes; without,
    the clip's static values are used."""
    kfs = clip.get("keyframes") or []
    if not kfs:
        tf = clip_transform(clip)
        return tf, float(clip.get("opacity", 1.0) if clip.get("opacity") is not None else 1.0)
    kfs = sorted(kfs, key=lambda k: k["t"])

    def val(k, name):
        v = k.get(name)
        if v is None:
            v = clip.get(name, KEY_DEFAULTS[name])
        return float(v if v is not None else KEY_DEFAULTS[name])
    if local_t <= kfs[0]["t"]:
        a = b = kfs[0]
        u = 0.0
    elif local_t >= kfs[-1]["t"]:
        a = b = kfs[-1]
        u = 0.0
    else:
        i = max(j for j in range(len(kfs)) if kfs[j]["t"] <= local_t)
        a, b = kfs[i], kfs[i + 1]
        span = max(1e-6, b["t"] - a["t"])
        u = _smooth((local_t - a["t"]) / span)
    out = {n: val(a, n) + (val(b, n) - val(a, n)) * u for n in KEY_PROPS}
    return (out["zoom"], out["x"], out["y"], out["rot"]), max(0.0, min(1.0, out["opacity"]))


def is_identity(tf):
    z, x, y, r = tf
    return abs(z - 1) < 1e-4 and abs(x) < 1e-4 and abs(y) < 1e-4 and abs(r) < 1e-3


def _user_matrix(W, H, tf):
    """3x3 matrix applied in output space: zoom + rotate about centre, then shift."""
    z, x, y, r = tf
    a = math.radians(-r)
    c, s = math.cos(a) * z, math.sin(a) * z
    cx, cy = W / 2, H / 2
    return np.array([[c, -s, cx - c * cx + s * cy + x * W],
                     [s, c, cy - s * cx - c * cy + y * H],
                     [0, 0, 1]], dtype=np.float64)


def apply_transform(frame, tf):
    if is_identity(tf):
        return frame
    H, W = frame.shape[:2]
    M = _user_matrix(W, H, tf)[:2]
    return cv2.warpAffine(frame, M, (W, H), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))


def needs_blur_fit(iw, ih, W, H, mode):
    if mode == "crop":
        return False
    ia, fa = iw / ih, W / H
    r = max(ia, fa) / min(ia, fa)
    return r > 1.45


from collections import OrderedDict

_BASE_CACHE = OrderedDict()
_BASE_CACHE_BYTES = 0
_BASE_CACHE_MAX = 700 * 1024 * 1024


def _cache_get(key):
    b = _BASE_CACHE.get(key)
    if b is not None:
        _BASE_CACHE.move_to_end(key)
    return b


def _cache_put(key, arr):
    global _BASE_CACHE_BYTES
    _BASE_CACHE[key] = arr
    _BASE_CACHE_BYTES += arr.nbytes
    while _BASE_CACHE_BYTES > _BASE_CACHE_MAX and len(_BASE_CACHE) > 1:
        _, old = _BASE_CACHE.popitem(last=False)
        _BASE_CACHE_BYTES -= old.nbytes


class ImageSource:
    """A still image with a smooth Ken Burns move (sub-pixel, no jitter).

    fit: "blur" -> images whose shape differs a lot from the frame (e.g. landscape
    image in a 9:16 video) are shown whole over a blurred copy of themselves.
    """

    def __init__(self, path, W, H, motion, zoom_amount, seed=0, tf=(1, 0, 0, 0), fit="crop", focus=None,
                 trim=None, bg_color=None, ease="smooth"):
        self.W, self.H, self.motion, self.tf = W, H, motion, tf
        self.ease = ease or "smooth"
        amt = max(0.02, float(zoom_amount or 0))
        if motion == "static":
            amt = 0.03                 # "almost still": a 3 % push so a still never looks frozen
        elif motion == "none":
            amt = 0.0                  # truly still (use keyframes for exact movement)
        self.Z = 1.0 + amt
        CW, CH = int(math.ceil(W * self.Z)) + 1, int(math.ceil(H * self.Z)) + 1
        try:
            mt = os.path.getmtime(path)
        except OSError:
            mt = 0
        focus = tuple(focus) if focus else None
        trim = tuple(trim) if trim and any(trim) else None
        key = (path, mt, CW, CH, fit, focus, trim, bg_color)
        cached = _cache_get(key)       # decoded + resized once, reused across seeks / renders
        if cached is not None:
            self.B = cached
            img = None
        else:
            img = load_image_rgb(path, need=(CW, CH))
        if img is not None:
            from .framing import cover, effective_mode, place, trim_array
            img = trim_array(img, trim)
            ih, iw = img.shape[:2]
            mode = effective_mode(fit, iw, ih, W, H)
            if mode == "crop":
                # cover the (slightly larger) work canvas; the focal point decides what is kept
                k = max(CW / iw, CH / ih)
                bw, bh = max(int(math.ceil(iw * k)), CW), max(int(math.ceil(ih * k)), CH)
                interp = cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC
                self.B = cv2.resize(img, (bw, bh), interpolation=interp)
                if focus is not None:
                    self.B = cover(self.B, CW, CH, focus)
            else:
                self.B = place(img, CW, CH, mode, color=bg_color)
        if img is not None:
            _cache_put(key, self.B)
        self.bh, self.bw = self.B.shape[:2]
        rng = np.random.default_rng(seed)
        if focus is not None:          # the base is already cropped around the focal point
            self.fx = self.fy = 0.5
        else:
            self.fx = 0.5 + rng.uniform(-0.12, 0.12)
            self.fy = 0.5 + rng.uniform(-0.12, 0.08)
        self.U = _user_matrix(W, H, tf)
        self.ident = is_identity(tf)

    def frame(self, p, tf=None):
        e = ease_curve(p, self.ease)
        W, H, Z = self.W, self.H, self.Z
        m = self.motion or "none"
        pan = None
        if "+" in m:                   # combination, e.g. "zoom_in+pan_right"
            m, pan = m.split("+", 1)
        if m == "zoom_in":
            z = 1.0 + (Z - 1.0) * e
        elif m == "zoom_out":
            z = Z - (Z - 1.0) * e
        elif m == "static":
            z = 1.0 + (Z - 1.0) * e
        elif m == "subtle":
            z = 1.0 + (Z - 1.0) * (0.35 + 0.45 * e)
        else:
            z = Z
        vw, vh = W * Z / z, H * Z / z
        roomx, roomy = self.bw - vw, self.bh - vh
        cx = vw / 2 + roomx * self.fx
        cy = vh / 2 + roomy * self.fy
        pm = pan or m
        if pm in ("pan_left", "pan_right"):
            travel = min(roomx, vw * 0.18)
            d = (e - 0.5) * travel
            # film convention: "pan right" = the camera travels to the right (the view moves right)
            cx = (self.bw / 2 if pan is None else cx) + (d if pm == "pan_right" else -d)
        elif pm in ("pan_up", "pan_down"):
            travel = min(roomy, vh * 0.18)
            d = (e - 0.5) * travel
            cy = (self.bh / 2 if pan is None else cy) + (d if pm == "pan_down" else -d)
        elif m == "subtle":   # slow drift + gentle breathing, like a steady hand-held camera
            cx += roomx * 0.10 * (e - 0.5) + roomx * 0.02 * math.sin(p * 5.1)
            cy += roomy * 0.02 * math.sin(p * 3.7 + 1.0)
        cx = min(max(cx, vw / 2), self.bw - vw / 2)
        cy = min(max(cy, vh / 2), self.bh - vh / 2)
        s = W / vw
        M = np.array([[s, 0, -(cx - vw / 2) * s], [0, s, -(cy - vh / 2) * s], [0, 0, 1]], dtype=np.float64)
        if tf is not None:
            if is_identity(tf):
                return cv2.warpAffine(self.B, M[:2], (W, H), flags=cv2.INTER_LINEAR,
                                      borderMode=cv2.BORDER_REFLECT)
            M = _user_matrix(W, H, tf) @ M
            return cv2.warpAffine(self.B, M[:2], (W, H), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        if self.ident:
            return cv2.warpAffine(self.B, M[:2], (W, H), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REFLECT)
        M = self.U @ M
        return cv2.warpAffine(self.B, M[:2], (W, H), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))

    def close(self):
        self.B = None


def _vf_fill(W, H):
    return [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}", "setsar=1"]


class VideoSource:
    """A video clip decoded by FFmpeg (sequential - used for final render, always from the ORIGINAL file).

    clip_in / clip_out pick the part of the clip to use. When the clip is shorter than the scene:
      short="freeze" hold the last frame, "loop" play it again, "slow" slow the CLIP down to fit
      (the narration is never changed), "auto" = older projects: slow if >= 70 % long, else loop.
    Framing (crop / fit modes, focal point, border trim) matches the preview exactly (framing.py)."""

    def __init__(self, path, W, H, seconds, fps, clip_duration=None, tf=(1, 0, 0, 0), loop=False, fit="crop",
                 focus=None, trim=None, bg_color=None, clip_in=0.0, clip_out=None, short="auto"):
        from .framing import effective_mode, fit_rect
        from .mediainfo import probe
        self.W, self.H, self.tf = W, H, tf
        self.n = max(1, int(math.ceil(seconds * fps)) + 1)
        self.mode, self.bg_color = fit, bg_color
        info = probe(path) or {}
        sw, sh = info.get("display_w") or W, info.get("display_h") or H
        trim = trim if trim and any(trim) else None
        vf = []
        if trim:
            l, t, r, b = [max(0.0, min(0.45, float(x or 0))) for x in trim]
            vf.append(f"crop=iw*{1 - l - r:.5f}:ih*{1 - t - b:.5f}:iw*{l:.5f}:ih*{t:.5f}")
            sw, sh = sw * (1 - l - r), sh * (1 - t - b)
        self.mode = effective_mode(fit, sw, sh, W, H)
        if self.mode == "crop":
            fx, fy = (focus or (0.5, 0.5))
            vf += [f"scale={W}:{H}:force_original_aspect_ratio=increase",
                   f"crop={W}:{H}:(iw-{W})*{float(fx):.4f}:(ih-{H})*{float(fy):.4f}", "setsar=1"]
            self.fw, self.fh = W, H
        else:
            _, _, self.fw, self.fh = fit_rect(sw, sh, W, H)
            self.fw -= self.fw % 2
            self.fh -= self.fh % 2
            vf += [f"scale={self.fw}:{self.fh}", "setsar=1"]
        pre = []
        if clip_in:
            pre += ["-ss", f"{float(clip_in):.3f}"]
        avail = clip_duration
        if clip_duration is not None:
            avail = max(0.05, (float(clip_out) if clip_out else clip_duration) - float(clip_in or 0))
        if clip_out:
            pre_t = ["-t", f"{avail:.3f}"]
        else:
            pre_t = []
        if loop:
            pre = ["-stream_loop", "-1"] + pre
        elif avail and avail < seconds:
            if short == "loop" or (short == "auto" and avail < seconds * 0.7):
                pre = ["-stream_loop", "-1"] + pre
                pre_t = []
            elif short == "slow" or short == "auto":
                vf.insert(0, f"setpts=PTS*{seconds / avail:.5f}")
            # "freeze": nothing - the reader holds the last frame
        vf.append(f"fps={fps}")
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error"] + pre + pre_t + ["-i", path] + [
            "-an", "-vf", ",".join(vf), "-frames:v", str(self.n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.fsize = self.fw * self.fh * 3
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     bufsize=self.fsize * 2, **popen_kwargs())
        self.idx = -1
        self.last = np.zeros((H, W, 3), np.uint8)
        self.cache_i, self.cache_f = None, None

    def _read(self):
        buf = self.proc.stdout.read(self.fsize) if self.proc else b""
        if buf and len(buf) == self.fsize:
            fr = np.frombuffer(buf, np.uint8).reshape(self.fh, self.fw, 3)
            if self.mode != "crop":
                from .framing import compose_fitted
                fr = compose_fitted(fr, self.W, self.H, self.mode, self.bg_color)
            self.last = fr
        self.idx += 1
        return self.last

    def frame_index(self, i, tf=None):
        i = min(max(i, 0), self.n - 1)
        while self.idx < i:
            self._read()
        tf = self.tf if tf is None else tf
        if self.cache_i != (self.idx, tf):
            self.cache_i, self.cache_f = (self.idx, tf), apply_transform(self.last, tf)
        return self.cache_f

    def close(self):
        if self.proc:
            try:
                self.proc.stdout.close()
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            self.proc = None


class SeekVideoSource:
    """Seekable clip reader for the preview player (OpenCV; a small proxy if OpenCV can't read the codec).
    Same framing / clip in-out / short-clip rules as VideoSource, so preview == export."""

    def __init__(self, path, W, H, seconds, fps, clip_duration=None, tf=(1, 0, 0, 0), fit="crop", focus=None,
                 trim=None, bg_color=None, clip_in=0.0, clip_out=None, short="auto"):
        self.W, self.H, self.tf, self.fps = W, H, tf, fps
        self.fit, self.focus, self.trim, self.bg = fit, focus, trim, bg_color
        self.cap = cv2.VideoCapture(readable_video(path))
        self.src_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.cin = float(clip_in or 0)
        full = clip_duration or seconds
        self.cd = max(0.05, (float(clip_out) if clip_out else full) - self.cin)
        self.seconds = seconds
        self.speed, self.loop = 1.0, False
        if self.cd < seconds:
            if short == "loop" or (short == "auto" and self.cd < seconds * 0.7):
                self.loop = True
            elif short == "slow" or short == "auto":
                self.speed = self.cd / seconds
        self.pos = -1
        self.last = np.zeros((H, W, 3), np.uint8)

    def frame_index(self, i, tf=None):
        if tf is not None and tf != self.tf:
            self.tf = tf
            self.pos = -2                      # force re-transform of the current frame
        t = i / self.fps * self.speed
        if self.loop:
            t = t % self.cd
        t = min(t, self.cd - 1.0 / max(1.0, self.src_fps))     # freeze on the last frame
        j = int((self.cin + max(0.0, t)) * self.src_fps)
        if j != self.pos:
            if j < self.pos or j > self.pos + 15 or self.pos == -2:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, j)
                self.pos = j - 1
            ok, fr = True, None
            while self.pos < j and ok:
                ok, fr = self.cap.read()
                self.pos += 1
            if ok and fr is not None:
                from .framing import place, trim_array
                fr = trim_array(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), self.trim)
                fr = place(fr, self.W, self.H, self.fit, self.focus or (0.5, 0.5), self.bg)
                self.last = apply_transform(fr, self.tf)
        return self.last

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def probe_duration(path):
    """Clip duration in seconds (OpenCV, or FFmpeg when OpenCV can't read the codec). None if unknown."""
    try:
        if _cv_can_read(path):
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            if fps > 0 and n > 0:
                return n / fps
    except Exception:
        pass
    try:
        import re
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=30, **popen_kwargs())
        m = re.search(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- transitions

def _shift(img, dx):
    """Shift horizontally by dx pixels (positive = right), black fill."""
    W = img.shape[1]
    out = np.zeros_like(img)
    dx = int(round(dx))
    if dx >= W or dx <= -W:
        return out
    if dx >= 0:
        out[:, dx:] = img[:, :W - dx]
    else:
        out[:, :W + dx] = img[:, -dx:]
    return out


def _zoom(img, z):
    if abs(z - 1.0) < 1e-3:
        return img
    H, W = img.shape[:2]
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, z)
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def blend(a, b, kind, p):
    """Mix outgoing frame a and incoming frame b at transition progress p (0..1)."""
    p = min(1.0, max(0.0, p))
    if kind == "crossfade":
        return cv2.addWeighted(a, 1 - p, b, p, 0)
    if kind == "dip_black":
        if p < 0.5:
            return cv2.convertScaleAbs(a, alpha=1 - p * 2)
        return cv2.convertScaleAbs(b, alpha=(p - 0.5) * 2)
    W = a.shape[1]
    if kind == "slide":
        e = _ease(p)
        off = e * W
        out = _shift(a, -off)
        bb = _shift(b, W - off)
        return cv2.add(out, bb)
    if kind == "whip":
        e = 0.5 - 0.5 * math.cos(math.pi * p)
        off = e * W
        out = cv2.add(_shift(a, -off), _shift(b, W - off))
        k = int(W * 0.08 * math.sin(math.pi * p)) | 1
        if k > 1:
            out = cv2.blur(out, (k, 1))
        return out
    if kind == "zoom":
        za = 1.0 + 0.6 * p * p
        zb = 1.35 - 0.35 * _ease(p)
        return cv2.addWeighted(_zoom(a, za), 1 - p, _zoom(b, zb), p, 0)
    return b if p >= 0.5 else a


