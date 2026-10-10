"""Look & atmosphere effects: colour grades, paper texture, vignette, film grain,
dust & scratches, flicker, light leaks, glow, floating particles, overlay video.

Everything is generated procedurally (no stock assets) and depends only on the
frame number, so the preview matches the final render exactly.
"""
import math
import os

import cv2
import numpy as np

ATMOSPHERES = {
    "None": {},
    "Vintage Document": {"grade": ("sepia", 0.35), "paper": 0.55, "grain": 0.35, "dust": 0.35,
                         "vignette": 0.35},
    "Old Film": {"grade": ("sepia", 0.6), "grain": 0.6, "dust": 0.8, "scratches": 0.6,
                 "flicker": 0.5, "vignette": 0.5},
    "Dreamy Glow": {"grade": ("warm", 0.3), "glow": 0.45, "particles": 0.6, "leak": 0.35,
                    "vignette": 0.2},
    "Dark Noir": {"grade": ("bw", 0.85), "contrast": 1.15, "grain": 0.45, "vignette": 0.6,
                  "dust": 0.2},
    "Warm Cinematic": {"grade": ("warm", 0.5), "leak": 0.35, "grain": 0.2, "vignette": 0.25},
    "Cold Thriller": {"grade": ("cold", 0.5), "contrast": 1.08, "grain": 0.3, "vignette": 0.45},
    "Teal & Orange": {"grade": ("teal_orange", 0.5), "contrast": 1.05, "vignette": 0.25},
    "Floating Dust (subtle)": {"particles": 0.45, "grain": 0.15, "vignette": 0.2},
}

# colour matrices in RGB order (out = M @ in)
_GRADES = {
    "sepia": np.array([[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]]),
    "bw": np.array([[0.299, 0.587, 0.114]] * 3),
    "warm": np.array([[1.08, 0.02, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.86]]),
    "cold": np.array([[0.88, 0.0, 0.02], [0.0, 1.0, 0.03], [0.0, 0.04, 1.12]]),
    "teal_orange": np.array([[1.10, 0.05, -0.08], [-0.02, 1.0, 0.02], [-0.06, 0.08, 1.02]]),
}


def _sat_matrix(s):
    lum = np.array([0.299, 0.587, 0.114])
    return (1 - s) * np.tile(lum, (3, 1)) + s * np.eye(3)


class Atmosphere:
    def __init__(self, W, H, fps, preset_look=None, atmosphere="None", strength=1.0,
                 overlay=None, overlay_opacity=0.5, overlay_source=None, seed=11):
        self.W, self.H, self.fps = W, H, fps
        cfg = dict(ATMOSPHERES.get(atmosphere, {}))
        k = max(0.0, float(strength))
        preset_look = preset_look or {}
        self.seed = seed
        g = lambda key: float(cfg.get(key, 0.0)) * k  # noqa: E731

        # --- colour: grade + saturation + contrast folded into one matrix
        M = np.eye(3)
        if "grade" in cfg:
            name, amt = cfg["grade"]
            amt = min(1.0, amt * k)
            M = (1 - amt) * np.eye(3) + amt * _GRADES[name]
        M = M @ _sat_matrix(float(preset_look.get("saturation", 1.0)))
        c = 1.0 + (float(cfg.get("contrast", 1.0)) - 1.0) * k
        self.offset = 128.0 * (1 - c)
        self.M = M * c
        self.color = not (np.allclose(self.M, np.eye(3)) and abs(self.offset) < 0.5)
        self.flicker = g("flicker")

        # --- multiply mask: vignette * paper texture
        vig = max(float(preset_look.get("vignette", 0.0)), g("vignette"))
        mask = np.ones((H, W), np.float32)
        if vig > 0:
            y, x = np.ogrid[-1:1:complex(0, H), -1:1:complex(0, W)]
            r = np.sqrt(x * x + y * y) / math.sqrt(2)
            mask *= (1.0 - min(vig, 0.95) * np.clip((r - 0.3) / 0.7, 0, 1) ** 1.5).astype(np.float32)
        paper = g("paper")
        if paper > 0:
            rng = np.random.default_rng(seed + 1)
            tex = np.zeros((H, W), np.float32)
            for scale, wgt in ((64, 0.5), (16, 0.3), (4, 0.2)):
                n = rng.standard_normal((max(2, H // scale), max(2, W // scale))).astype(np.float32)
                tex += cv2.resize(n, (W, H), interpolation=cv2.INTER_CUBIC) * wgt
            tex = (tex - tex.min()) / (np.ptp(tex) + 1e-6)
            mask *= (1.0 - paper * 0.35 * tex).astype(np.float32)
        self.mask = None
        if (mask < 0.999).any():
            m8 = np.clip(mask * 255, 0, 255).astype(np.uint8)
            self.mask = cv2.merge([m8, m8, m8])
            if paper > 0:  # paper is slightly warm
                tint = np.clip(mask * 255, 0, 255)
                self.mask = cv2.merge([np.clip(tint * 1.0, 0, 255).astype(np.uint8),
                                       np.clip(tint * 0.97, 0, 255).astype(np.uint8),
                                       np.clip(tint * 0.90, 0, 255).astype(np.uint8)])

        # --- film grain tiles
        self.grain = g("grain")
        self.grain_tiles = []
        if self.grain > 0:
            rng = np.random.default_rng(seed + 2)
            for _ in range(6):
                n = rng.normal(128, 38, (H, W)).astype(np.float32)
                n = cv2.GaussianBlur(n, (0, 0), 0.7 * max(1.0, H / 1080))
                n8 = np.clip(n, 0, 255).astype(np.uint8)
                self.grain_tiles.append(cv2.merge([n8, n8, n8]))

        self.dust = g("dust")
        self.scratches = g("scratches")
        self.leak = g("leak")
        self.glow = g("glow")

        # --- floating particles
        self.particles = []
        pa = g("particles")
        if pa > 0:
            rng = np.random.default_rng(seed + 3)
            n = int(10 + 45 * pa)
            u = H / 1080
            for _ in range(n):
                r = int(max(2, rng.uniform(2, 7) * u))
                size = r * 4 + 1
                yy, xx = np.mgrid[:size, :size] - size // 2
                blob = np.exp(-(xx * xx + yy * yy) / (2 * (r * 0.7) ** 2))
                bright = rng.uniform(60, 170) * min(1.0, pa + 0.3)
                col = np.array([1.0, 0.95, 0.82])
                sprite = np.clip(blob[..., None] * bright * col, 0, 255).astype(np.uint8)
                self.particles.append({
                    "x": rng.uniform(0, W), "y": rng.uniform(0, H), "vy": rng.uniform(8, 35) * u,
                    "amp": rng.uniform(5, 30) * u, "ph": rng.uniform(0, 6.28), "sprite": sprite,
                })

        # --- overlay video (user-supplied, screen blended)
        self.overlay_src = overlay_source if (overlay and os.path.isfile(overlay)) else None
        self.overlay_op = float(overlay_opacity)

        self.active = any([self.color, self.mask is not None, self.grain, self.dust, self.scratches,
                           self.flicker, self.leak, self.glow, self.particles, self.overlay_src])

    # ------------------------------------------------------------------
    def apply(self, f, t):
        if not self.active:
            return f
        fi = int(round(t * self.fps))
        W, H = self.W, self.H
        if self.color or self.flicker:
            M = self.M
            off = self.offset
            if self.flicker:
                fl = 1.0 + self.flicker * 0.06 * (math.sin(fi * 1.7) * 0.6 + math.sin(fi * 0.37 + 1) * 0.4
                                                  + (((fi * 7919) % 13) / 13 - 0.5) * 0.8)
                M = M * fl
            Mx = np.hstack([M, np.full((3, 1), off)]).astype(np.float32)
            f = cv2.transform(f, Mx)
        if self.mask is not None:
            f = cv2.multiply(f, self.mask, scale=1.0 / 255)
        if self.leak > 0:
            f = cv2.add(f, self._leak(t))
        if self.glow > 0:
            sw, sh = max(8, W // 8), max(8, H // 8)
            small = cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), 2.5)
            big = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
            f = cv2.addWeighted(f, 1.0, big, 0.45 * self.glow, 0)
        if self.particles:
            f = self._particles(f, t)
        if self.overlay_src is not None:
            ov = self.overlay_src.frame_index(fi)
            if ov is not None and ov.shape == f.shape:
                inv = cv2.multiply(255 - f, 255 - ov, scale=1.0 / 255)
                scr = 255 - inv
                f = cv2.addWeighted(f, 1 - self.overlay_op, scr, self.overlay_op, 0)
        if self.dust > 0 or self.scratches > 0:
            f = self._dust(f, fi)
        if self.grain > 0 and self.grain_tiles:
            tile = self.grain_tiles[(fi * 5) % len(self.grain_tiles)]
            k = 0.35 * self.grain
            f = cv2.addWeighted(f, 1.0, tile, k, -128 * k)
        return f

    def _leak(self, t):
        sw, sh = max(8, self.W // 10), max(8, self.H // 10)
        y, x = np.mgrid[0:sh, 0:sw].astype(np.float32)
        out = np.zeros((sh, sw), np.float32)
        for i, (sp, ph) in enumerate(((0.05, 0.0), (0.033, 2.1))):
            cx = sw * (0.5 + 0.55 * math.sin(t * sp * 6.28 + ph))
            cy = sh * (0.5 + 0.45 * math.cos(t * sp * 4.1 + ph * 1.3))
            rad = sw * (0.35 + 0.1 * i)
            out += np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * rad * rad))
        pulse = 0.75 + 0.25 * math.sin(t * 0.7)
        a = np.clip(out * 90 * self.leak * pulse, 0, 255)
        leak = cv2.merge([a, a * 0.55, a * 0.2]).astype(np.uint8)
        return cv2.resize(leak, (self.W, self.H), interpolation=cv2.INTER_LINEAR)

    def _particles(self, f, t):
        f = np.array(f, copy=True)
        W, H = self.W, self.H
        for p in self.particles:
            s = p["sprite"]
            h, w = s.shape[:2]
            y = (p["y"] - p["vy"] * t) % (H + h) - h
            x = p["x"] + p["amp"] * math.sin(t * 0.6 + p["ph"])
            x0, y0 = int(x), int(y)
            xs, ys = max(0, x0), max(0, y0)
            xe, ye = min(W, x0 + w), min(H, y0 + h)
            if xe <= xs or ye <= ys:
                continue
            f[ys:ye, xs:xe] = cv2.add(f[ys:ye, xs:xe], s[ys - y0:ye - y0, xs - x0:xe - x0])
        return f

    def _dust(self, f, fi):
        f = np.ascontiguousarray(f).copy()
        W, H = self.W, self.H
        u = max(1, int(round(H / 1080 * 2)))
        rng = np.random.default_rng(self.seed * 100003 + fi // 2)
        if self.dust > 0:
            for _ in range(rng.poisson(2.5 * self.dust)):
                x, y = int(rng.uniform(0, W)), int(rng.uniform(0, H))
                r = int(rng.uniform(1, 3.5) * u)
                c = int(rng.choice([25, 230]))
                cv2.circle(f, (x, y), r, (c, c, c), -1, lineType=cv2.LINE_AA)
            if rng.random() < 0.08 * self.dust:  # a hair
                x, y = int(rng.uniform(0, W)), int(rng.uniform(0, H))
                pts = np.array([[x + int(rng.normal(0, 12 * u)), y + i * 6 * u] for i in range(8)], np.int32)
                cv2.polylines(f, [pts], False, (30, 30, 30), u, lineType=cv2.LINE_AA)
        if self.scratches > 0:
            rs = np.random.default_rng(self.seed * 7 + fi // 12)
            for _ in range(rs.poisson(1.2 * self.scratches)):
                x = int(rs.uniform(0, W) + math.sin(fi * 0.3) * 3)
                c = int(rs.uniform(170, 230))
                cv2.line(f, (x, 0), (x + int(rs.normal(0, 6)), H), (c, c, c), 1, lineType=cv2.LINE_AA)
        return f

    def close(self):
        if self.overlay_src is not None:
            self.overlay_src.close()


# ---------------------------------------------------------------------- per-clip adjustments
CLIP_FILTERS = ["None", "Black & white", "Sepia", "Warm", "Cold", "Teal & orange"]
_FILTER_GRADE = {"Black & white": "bw", "Sepia": "sepia", "Warm": "warm", "Cold": "cold",
                 "Teal & orange": "teal_orange"}
ADJUST_DEFAULTS = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "blur": 0.0, "filter": "None",
                   "filter_amount": 0.8}


def clip_has_adjust(clip):
    a = clip.get("adjust") or {}
    return any(a.get(k, v) != v for k, v in ADJUST_DEFAULTS.items() if k != "filter_amount")


def clip_adjust(frame, clip):
    """Per-clip look: brightness / contrast / saturation / filter in one colour matrix,
    then optional blur. Returns the frame unchanged when nothing is set."""
    a = clip.get("adjust") or {}
    if not a or not clip_has_adjust(clip):
        return frame
    M = np.eye(3)
    flt = _FILTER_GRADE.get(a.get("filter", "None"))
    if flt:
        amt = float(a.get("filter_amount", 0.8))
        M = (1 - amt) * np.eye(3) + amt * _GRADES[flt]
    M = M @ _sat_matrix(float(a.get("saturation", 1.0)))
    c = float(a.get("contrast", 1.0))
    b = float(a.get("brightness", 0.0))
    off = 128.0 * (1 - c) + b * 160.0
    Mx = np.hstack([M * c, np.full((3, 1), off)]).astype(np.float32)
    out = cv2.transform(frame, Mx)
    r = float(a.get("blur", 0.0))
    if r > 0.05:
        sig = r * frame.shape[0] / 540.0
        out = cv2.GaussianBlur(out, (0, 0), sig)
    return out


# ---------------------------------------------------------------- timed per-scene effects (instructions.EFFECTS)
_VIG = {}


def vignette(frame, amount):
    """Darken the corners. amount 0..1."""
    H, W = frame.shape[:2]
    key = (W, H)
    if key not in _VIG:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / np.sqrt(2)
        _VIG[key] = np.clip((d - 0.35) / 0.65, 0, 1) ** 1.6
    m = 1.0 - float(amount) * 0.85 * _VIG[key]
    return np.clip(frame.astype(np.float32) * m[:, :, None], 0, 255).astype(np.uint8)


_EFFECT_ADJUST = {"brightness": lambda v: {"brightness": v}, "contrast": lambda v: {"contrast": v},
                  "saturation": lambda v: {"saturation": v},
                  "black_white": lambda v: {"filter": "Black & white", "filter_amount": v},
                  "sepia": lambda v: {"filter": "Sepia", "filter_amount": v},
                  "warm": lambda v: {"filter": "Warm", "filter_amount": v},
                  "cold": lambda v: {"filter": "Cold", "filter_amount": v},
                  "blur": lambda v: {"blur": v}}


def apply_effects(frame, effects, lt):
    """Effects active at `lt` seconds after the scene start (each has start/end in scene seconds)."""
    for e in effects or []:
        if not (e.get("start", 0) <= lt < e.get("end", 1e9)):
            continue
        typ, v = e.get("type"), float(e.get("amount", 0))
        if typ == "vignette":
            frame = vignette(frame, v)
        elif typ in _EFFECT_ADJUST:
            frame = clip_adjust(frame, {"adjust": _EFFECT_ADJUST[typ](v)})
    return frame
