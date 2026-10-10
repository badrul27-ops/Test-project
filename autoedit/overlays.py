"""Editorial text layer: hook title, title cards, chapter cards, place/date labels,
lower thirds. Animated (fade, punch-in, typewriter reveal, slide-in)."""
import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .captions import BOLD_FONTS, DOC_FONTS, blend_rgba, hex_rgb, resolve_font

OVERLAY_TYPES = {
    "hook": "Hook title (big, first seconds)",
    "title": "Title card (full screen)",
    "chapter": "Chapter card (full screen)",
    "label": "Place / date label (typewriter)",
    "lower_third": "Lower third (name / note)",
}
DEFAULT_DUR = {"hook": 2.8, "title": 3.0, "chapter": 2.5, "label": 3.5, "lower_third": 4.0}
FULLSCREEN = {"title", "chapter", "hook"}


def _ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _rgba_to_pm(img):
    arr = np.asarray(img)
    a = arr[:, :, 3:4].astype(np.float32) / 255.0
    return arr[:, :, :3].astype(np.float32) * a, a


class OverlayRenderer:
    def __init__(self, overlays, W, H, opts=None):
        opts = opts or {}
        self.W, self.H = W, H
        self.vertical = H > W
        self.items = sorted([dict(o) for o in (overlays or []) if o.get("text") or o.get("sub")],
                            key=lambda o: o["start"])
        base = W if self.vertical else H
        self.u = base / 1080.0
        fbold = resolve_font(opts.get("title_font") or opts.get("caption_font"), BOLD_FONTS)
        fdoc = resolve_font(None, DOC_FONTS)
        self.fonts = {}
        self._fb, self._fd = fbold, fdoc
        self.accent = hex_rgb(opts.get("highlight_color", "#FFD600"), (255, 214, 0))
        self.cache = {}

    def hidden_ranges(self):
        return [(o["start"], o["end"]) for o in self.items if o.get("type") in FULLSCREEN]

    def _font(self, bold, size):
        key = (bold, int(size))
        if key not in self.fonts:
            path = self._fb if bold else self._fd
            self.fonts[key] = ImageFont.truetype(path, int(size)) if path else ImageFont.load_default()
        return self.fonts[key]

    # -------------------------------------------------------------- blocks
    def _wrap(self, text, font, maxw):
        words = text.split()
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if cur and font.getlength(t) > maxw:
                lines.append(cur)
                cur = w
            else:
                cur = t
        if cur:
            lines.append(cur)
        return lines or [""]

    def _block_center(self, o):
        """Title / chapter / hook text block (centered)."""
        W, u = self.W, self.u
        typ = o["type"]
        maxw = W * (0.84 if self.vertical else 0.78)
        big = {"hook": 104, "title": 96, "chapter": 84}[typ] * u
        text = o.get("text", "")
        if typ in ("hook", "chapter"):
            text = text.upper()
        f_big = self._font(True, big)
        lines = self._wrap(text, f_big, maxw)
        while len(lines) > 3 and big > 30 * u:
            big *= 0.85
            f_big = self._font(True, big)
            lines = self._wrap(text, f_big, maxw)
        f_sub = self._font(False, big * 0.42)
        sub = o.get("sub", "")
        kicker = ""
        if typ == "chapter":
            kicker, sub = (sub or "").upper(), ""
        asc, desc = f_big.getmetrics()
        lh = int((asc + desc) * 1.05)
        widths = [f_big.getlength(l) for l in lines]
        sub_lines = self._wrap(sub, f_sub, maxw) if sub else []
        sa, sd = f_sub.getmetrics()
        slh = int((sa + sd) * 1.15)
        kf = self._font(True, big * 0.34)
        ka, kd = kf.getmetrics()
        kh = int((ka + kd) * 1.6) if kicker else 0
        bw = int(max(widths + [f_sub.getlength(s) for s in sub_lines] + [kf.getlength(kicker) + 40 * u]) + 60 * u)
        bh = int(kh + lh * len(lines) + (slh * len(sub_lines) + 18 * u if sub_lines else 0) + 50 * u)
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        y = 25 * u
        if kicker:
            kw = kf.getlength(kicker)
            d.text(((bw - kw) / 2, y), kicker, font=kf, fill=self.accent + (255,))
            ly = y + ka + kd + 10 * u
            d.line([(bw / 2 - kw / 2, ly), (bw / 2 + kw / 2, ly)], fill=self.accent + (255,), width=max(2, int(3 * u)))
            y += kh
        stroke = max(2, int(big * 0.06)) if typ == "hook" else 0
        for l, w in zip(lines, widths):
            d.text(((bw - w) / 2, y), l, font=f_big, fill=(255, 255, 255, 255),
                   stroke_width=stroke, stroke_fill=(0, 0, 0, 255) if stroke else None)
            y += lh
        if sub_lines:
            y += 18 * u
            for s in sub_lines:
                sw = f_sub.getlength(s)
                d.text(((bw - sw) / 2, y), s, font=f_sub, fill=(225, 225, 225, 255))
                y += slh
        return _rgba_to_pm(img)

    def _block_label(self, o, nchars):
        u = self.u
        text = o.get("text", "").upper()[:nchars]
        full = o.get("text", "").upper()
        f = self._font(True, 40 * u)
        pad = 18 * u
        w = int(f.getlength(full) + pad * 2 + 14 * u)
        a, dsc = f.getmetrics()
        h = int(a + dsc + pad * 1.4)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w, h], fill=(12, 12, 14, 185))
        d.rectangle([0, 0, int(6 * u), h], fill=self.accent + (255,))
        d.text((pad + 8 * u, pad * 0.6), text, font=f, fill=(255, 255, 255, 255))
        if nchars < len(full):  # cursor
            cx = pad + 8 * u + f.getlength(text) + 3 * u
            d.rectangle([cx, pad * 0.7, cx + 4 * u, pad * 0.6 + a], fill=(255, 255, 255, 220))
        return _rgba_to_pm(img)

    def _block_lower(self, o):
        u = self.u
        f1 = self._font(True, 50 * u)
        f2 = self._font(False, 34 * u)
        t1, t2 = o.get("text", ""), o.get("sub", "")
        maxw = self.W * 0.6
        w = int(max(f1.getlength(t1), f2.getlength(t2) if t2 else 0, 200 * u) + 60 * u)
        w = int(min(w, maxw + 60 * u))
        a1, d1 = f1.getmetrics()
        a2, d2 = f2.getmetrics()
        h = int(a1 + d1 + (a2 + d2 + 10 * u if t2 else 0) + 36 * u)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w, h], fill=(10, 10, 12, 200))
        d.rectangle([0, 0, int(10 * u), h], fill=self.accent + (255,))
        d.text((30 * u, 16 * u), t1, font=f1, fill=(255, 255, 255, 255))
        if t2:
            d.text((30 * u, 16 * u + a1 + d1 + 6 * u), t2, font=f2, fill=self.accent + (255,))
        return _rgba_to_pm(img)

    def _get(self, i, key, fn):
        k = (i, key)
        if k not in self.cache:
            if len(self.cache) > 80:
                self.cache.clear()
            self.cache[k] = fn()
        return self.cache[k]

    # -------------------------------------------------------------- apply
    def apply(self, frame, t):
        active = [(i, o) for i, o in enumerate(self.items) if o["start"] <= t < o["end"]]
        if not active:
            return frame
        frame = np.array(frame, copy=True)
        W, H = self.W, self.H
        for i, o in active:
            typ = o.get("type", "title")
            p, q = t - o["start"], o["end"] - t
            alpha = max(0.0, min(1.0, p / 0.3, q / 0.3))
            if typ in ("title", "chapter", "hook"):
                dim = {"title": 0.7, "chapter": 0.65, "hook": 0.45}[typ] * alpha
                if typ != "hook":
                    small = cv2.resize(frame, (max(8, W // 12), max(8, H // 12)), interpolation=cv2.INTER_AREA)
                    blur = cv2.resize(cv2.GaussianBlur(small, (0, 0), 1.5), (W, H), interpolation=cv2.INTER_LINEAR)
                    frame = cv2.addWeighted(frame, 1 - alpha, blur, alpha, 0)
                frame = cv2.convertScaleAbs(frame, alpha=1.0 - dim)
                rgbp, a = self._get(i, "c", lambda o=o: self._block_center(o))
                sc = 1.0
                if typ == "hook":
                    sc = 1.14 - 0.14 * _ease(p / 0.35)
                elif typ == "chapter":
                    sc = 0.94 + 0.06 * _ease(p / 0.5)
                if abs(sc - 1) > 1e-3:
                    h, w = a.shape[:2]
                    nw, nh = max(2, int(w * sc)), max(2, int(h * sc))
                    rgbp = cv2.resize(rgbp, (nw, nh), interpolation=cv2.INTER_LINEAR)
                    a = cv2.resize(a[:, :, 0], (nw, nh), interpolation=cv2.INTER_LINEAR)[:, :, None]
                h, w = a.shape[:2]
                cy = 0.42 if self.vertical else 0.5
                frame = blend_rgba(frame, rgbp * alpha, a * alpha, int((W - w) / 2), int(H * cy - h / 2))
            elif typ == "label":
                n = len(o.get("text", ""))
                shown = n if p > 0.9 else int(math.ceil(n * p / 0.9))
                rgbp, a = self._get(i, ("l", shown), lambda o=o, s=shown: self._block_label(o, s))
                x0 = int(W * (0.07 if self.vertical else 0.045))
                y0 = int(H * (0.12 if self.vertical else 0.07))
                qa = max(0.0, min(1.0, q / 0.3))
                frame = blend_rgba(frame, rgbp * qa, a * qa, x0, y0)
            elif typ == "lower_third":
                rgbp, a = self._get(i, "lt", lambda o=o: self._block_lower(o))
                h, w = a.shape[:2]
                xt = int(W * (0.07 if self.vertical else 0.045))
                x0 = int(-w + (xt + w) * _ease(p / 0.45))
                if q < 0.4:
                    x0 = int(xt - (xt + w) * (1 - _ease(q / 0.4)))
                y0 = int(H * (0.30 if self.vertical else 0.62))
                frame = blend_rgba(frame, rgbp, a, x0, y0)
        return frame


def effective_overlays(project):
    """Project overlays + the hook title from settings (if set and not placed manually)."""
    ovs = [dict(o) for o in project.get("overlays", []) or []]
    hook = (project.get("settings", {}).get("hook_title") or "").strip()
    if hook and not any(o.get("type") == "hook" for o in ovs):
        ovs.append({"type": "hook", "start": 0.1, "end": 0.1 + DEFAULT_DUR["hook"], "text": hook, "sub": ""})
    dur = project.get("duration") or 0
    return [o for o in ovs if o["end"] > o["start"] and (not dur or o["start"] < dur + 0.5)]
