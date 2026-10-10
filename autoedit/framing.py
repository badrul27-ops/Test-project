"""How a source picture is placed on the project canvas (one implementation for preview AND export).

Modes
  crop      Fill / Crop        - scale until the canvas is covered, crop the rest around a focal point.
  fit_blur  Fit / Background   - whole picture, remaining canvas = blurred copy of the picture.
  fit_color Fit / Colour       - whole picture on a chosen colour.
  fit_bars  Fit / Black bars   - whole picture on black (explicit choice only).
  blur      legacy (projects saved before v7): blurred background only when the shapes differ a lot,
            otherwise crop - kept so old projects look the same.
Nothing is ever stretched: width and height are always scaled by the same factor.
"""
import math

import cv2
import numpy as np

FIT_MODES = {
    "crop": "Fill / Crop (full frame)",
    "fit_blur": "Fit / Blurred background",
    "fit_color": "Fit / Colour background",
    "fit_bars": "Fit / Black bars",
    "blur": "Auto (older projects: blurred sides only for very different shapes)",
}
FIT_HELP = {
    "crop": "Covers the whole frame. Edges that don't fit are cropped - move the focal point to choose what "
            "stays in view.",
    "fit_blur": "Shows the whole picture. The unused sides are filled with a soft blurred copy - that's "
                "intentional, not missing video.",
    "fit_color": "Shows the whole picture on a plain colour.",
    "fit_bars": "Shows the whole picture with black bars where the shapes differ.",
    "blur": "Older projects: blurred sides only when the picture's shape is very different, otherwise crop.",
}


def effective_mode(mode, iw, ih, W, H):
    """Resolve legacy 'blur' (threshold) and treat near-identical shapes as crop (no 1-px slivers)."""
    mode = mode or "crop"
    ia, fa = iw / max(1, ih), W / max(1, H)
    r = max(ia, fa) / min(ia, fa)
    if mode == "blur":
        return "fit_blur" if r > 1.45 else "crop"
    if mode not in FIT_MODES:
        return "crop"
    if mode != "crop" and r < 1.01:
        return "crop"
    return mode


def trim_array(img, trim):
    """Remove baked-in borders: trim = [left, top, right, bottom] as fractions of the source."""
    if not trim or not any(trim):
        return img
    h, w = img.shape[:2]
    l, t, r, b = [max(0.0, min(0.45, float(v or 0))) for v in trim]
    x0, y0 = int(round(w * l)), int(round(h * t))
    x1, y1 = max(x0 + 2, int(round(w * (1 - r)))), max(y0 + 2, int(round(h * (1 - b))))
    return np.ascontiguousarray(img[y0:y1, x0:x1])


def hex_color(c, default=(0, 0, 0)):
    try:
        c = (c or "").lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else default
    except ValueError:
        return default


def cover(img, W, H, focus=(0.5, 0.5), interp=None):
    """Scale proportionally to cover W x H, crop around the focal point."""
    ih, iw = img.shape[:2]
    k = max(W / iw, H / ih)
    nw, nh = max(W, int(math.ceil(iw * k))), max(H, int(math.ceil(ih * k)))
    big = cv2.resize(img, (nw, nh), interpolation=interp or (cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC))
    fx, fy = focus if focus else (0.5, 0.5)
    x0 = int(round((nw - W) * min(1.0, max(0.0, float(fx)))))
    y0 = int(round((nh - H) * min(1.0, max(0.0, float(fy)))))
    return np.ascontiguousarray(big[y0:y0 + H, x0:x0 + W])


def fit_rect(iw, ih, W, H):
    k = min(W / iw, H / ih)
    fw, fh = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
    return (W - fw) // 2, (H - fh) // 2, fw, fh


def background(img, W, H, mode, color=None):
    if mode == "fit_blur":
        bg = cover(img, max(8, W // 16), max(8, H // 16), interp=cv2.INTER_AREA)
        bg = cv2.GaussianBlur(bg, (0, 0), 3)
        bg = cv2.resize(bg, (W, H), interpolation=cv2.INTER_LINEAR)
        return cv2.convertScaleAbs(bg, alpha=0.55)
    c = hex_color(color) if mode == "fit_color" else (0, 0, 0)
    out = np.empty((H, W, 3), np.uint8)
    out[:] = c
    return out


def place(img, W, H, mode="crop", focus=(0.5, 0.5), color=None):
    """Source picture (RGB array) -> W x H canvas, using the framing mode."""
    ih, iw = img.shape[:2]
    mode = effective_mode(mode, iw, ih, W, H)
    if mode == "crop":
        return cover(img, W, H, focus)
    bg = background(img, W, H, mode, color)
    x0, y0, fw, fh = fit_rect(iw, ih, W, H)
    k = fw / iw
    fg = cv2.resize(img, (fw, fh), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    bg[y0:y0 + fh, x0:x0 + fw] = fg
    return bg


def compose_fitted(fg, W, H, mode, color=None):
    """fg already scaled to its fit size (e.g. by FFmpeg) -> centred on the background."""
    fh, fw = fg.shape[:2]
    bg = background(fg, W, H, mode, color)
    x0, y0 = (W - fw) // 2, (H - fh) // 2
    bg[y0:y0 + fh, x0:x0 + fw] = fg
    return bg


def cover_tf(tf, W, H):
    """Raise the zoom just enough that scale / position / rotation never uncover empty canvas edges."""
    z, x, y, r = tf
    a = math.radians(abs(r) % 180)
    a = min(a, math.pi - a)
    ratio = max(W / H, H / W)
    need = math.cos(a) + ratio * math.sin(a) + 2 * max(abs(x), abs(y))
    if z + 1e-6 >= need:
        return tf
    return (need, x, y, r)


# ---------------------------------------------------------------------------- baked-in borders
def _edge_run(lines, inner_std):
    """How many lines from the edge are uniform (low spread) AND look like a border, not artwork:
    the picture must have real detail, and the first line after the band must differ from the band
    across most of its length (a dark scene with one bright object isn't 'framed by bars')."""
    n = 0
    ref = None
    for ln in lines:
        mean, std = float(ln.mean()), float(ln.std())
        if std > 7.0:
            break
        if ref is None:
            ref = mean
        elif abs(mean - ref) > 10:
            break
        n += 1
    if ref is None or inner_std <= 18.0 or n == 0 or n >= len(lines):
        return 0
    edge = lines[n]
    if float((abs(edge - ref) > 12).mean()) < 0.4:
        return 0
    return n


def detect_borders(frames):
    """frames: list of RGB arrays (several for a video). -> ([l, t, r, b] fractions, message) or (None, message).

    A border must be a uniform band along the edge in EVERY frame, at least 1 % of the size, while the
    picture inside has real detail - so a naturally dark scene isn't mistaken for a border."""
    found = []
    for fr in frames:
        g = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY).astype(np.float32)
        h, w = g.shape
        if max(h, w) > 900:
            k = 900 / max(h, w)
            g = cv2.resize(g, (max(2, int(w * k)), max(2, int(h * k))), interpolation=cv2.INTER_AREA)
            h, w = g.shape
        inner = g[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        istd = float(inner.std())
        t = _edge_run([g[i] for i in range(h // 2)], istd)
        b = _edge_run([g[h - 1 - i] for i in range(h // 2)], istd)
        lft = _edge_run([g[:, i] for i in range(w // 2)], istd)
        rgt = _edge_run([g[:, w - 1 - i] for i in range(w // 2)], istd)
        found.append([lft / w, t / h, rgt / w, b / h])
    if not found:
        return None, "Nothing to check."
    common = [min(f[i] for f in found) for i in range(4)]
    common = [round(v, 4) if v >= 0.01 else 0.0 for v in common]
    if not any(common):
        return None, ("No baked-in borders found." if len(frames) == 1 else
                      f"No borders found that are present in all {len(frames)} sampled frames.")
    names = ["left", "top", "right", "bottom"]
    parts = [f"{names[i]} {common[i] * 100:.1f}%" for i in range(4) if common[i]]
    return common, "Uniform border detected: " + ", ".join(parts) + "."
