"""Burned-in captions with the spoken word highlighted (drawn with Pillow).

Options: font, text/highlight colours, position, size, and pop animations.
In 9:16 the captions stay inside the Shorts/Reels safe zone (clear of the
buttons on the right and the title/description at the bottom).
"""
import bisect
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .util import APP_DIR, find_font

BOLD_FONTS = ["Montserrat-ExtraBold.ttf", "Montserrat-Black.ttf", "Roboto-Black.ttf", "Lato-Black.ttf",
              "arialbd.ttf", "DejaVuSans-Bold.ttf"]
DOC_FONTS = ["Montserrat-SemiBold.ttf", "SourceSansPro-Semibold.ttf", "segoeuib.ttf", "arialbd.ttf",
             "DejaVuSans-Bold.ttf"]
REG_FONTS = ["Montserrat-Medium.ttf", "Roboto-Medium.ttf", "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]

# label -> candidate files (bundled fonts are in the app's fonts/ folder; the rest ship with Windows)
FONT_CHOICES = {
    "Auto (by style)": None,
    "Montserrat ExtraBold": ["Montserrat-ExtraBold.ttf", "Montserrat-Black.ttf"],
    "Roboto Black": ["Roboto-Black.ttf"],
    "Roboto Bold": ["Roboto-Bold.ttf"],
    "Lato Black": ["Lato-Black.ttf"],
    "Lato Bold": ["Lato-Bold.ttf"],
    "Source Sans Black": ["SourceSansPro-Black.ttf"],
    "Source Sans Bold": ["SourceSansPro-Bold.ttf"],
    "Impact": ["impact.ttf"],
    "Arial Black": ["ariblk.ttf"],
    "Arial Bold": ["arialbd.ttf"],
    "Segoe UI Black": ["seguibl.ttf"],
    "Segoe UI Bold": ["segoeuib.ttf"],
    "Bahnschrift": ["bahnschrift.ttf"],
    "Verdana Bold": ["verdanab.ttf"],
    "Tahoma Bold": ["tahomabd.ttf"],
    "Trebuchet Bold": ["trebucbd.ttf"],
    "Georgia Bold (classic)": ["georgiab.ttf"],
    "Cambria Bold (serif)": ["cambriab.ttf"],
    "Courier Bold (typewriter)": ["courbd.ttf"],
    "Comic Sans Bold": ["comicbd.ttf"],
}

CAPTION_POSITIONS = ["Bottom", "Middle", "Top"]
CAPTION_ANIMS = ["None", "Pop-in", "Word bounce"]


def available_fonts():
    """Font labels that exist on this PC, plus any extra .ttf/.otf in the fonts folder."""
    out = [k for k, v in FONT_CHOICES.items() if v is None or find_font(v)]
    d = os.path.join(APP_DIR, "fonts")
    known = {f.lower() for v in FONT_CHOICES.values() if v for f in v}
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".ttf", ".otf")) and f.lower() not in known:
                out.append("File: " + f)
    return out


def resolve_font(label, fallback):
    if label and label.startswith("File: "):
        p = os.path.join(APP_DIR, "fonts", label[6:])
        if os.path.isfile(p):
            return p
    cands = FONT_CHOICES.get(label) if label else None
    return (find_font(cands) if cands else None) or find_font(fallback)


def hex_rgb(h, default=(255, 255, 255)):
    try:
        h = str(h).strip().lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return default


YELLOW = (255, 214, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def style_params(style, W, H):
    vertical = H > W
    base = W if vertical else H
    if style.startswith("Modern"):
        return dict(fonts=BOLD_FONTS, size=int(base * (0.080 if vertical else 0.066)), upper=True,
                    max_words=3 if vertical else 4, max_chars=15 if vertical else 26,
                    stroke=0.10, highlight="color", box=False)
    if style.startswith("Boxed"):
        return dict(fonts=BOLD_FONTS, size=int(base * (0.076 if vertical else 0.062)), upper=True,
                    max_words=3 if vertical else 4, max_chars=15 if vertical else 26,
                    stroke=0.08, highlight="box", box=False)
    if style.startswith("Documentary"):
        return dict(fonts=DOC_FONTS, size=int(base * (0.056 if vertical else 0.045)), upper=False,
                    max_words=5 if vertical else 9, max_chars=26 if vertical else 48,
                    stroke=0.0, highlight=None, box=False, shadow=True)
    return dict(fonts=REG_FONTS, size=int(base * (0.052 if vertical else 0.042)), upper=False,
                max_words=5 if vertical else 8, max_chars=26 if vertical else 44,
                stroke=0.0, highlight=None, box=True)


def position_y(pos, vertical, style):
    """Vertical centre of the caption block as a fraction of height (safe zones)."""
    pos = (pos or "Bottom").lower()
    if vertical:  # Shorts/Reels: keep out of bottom ~25% (title, buttons) and top ~12%
        return {"top": 0.22, "middle": 0.50}.get(pos, 0.64)
    low = 0.88 if style.startswith(("Documentary", "Minimal")) else 0.81
    return {"top": 0.14, "middle": 0.50}.get(pos, low)


def make_chunks(words, max_words, max_chars):
    chunks, cur = [], []
    for w in words:
        if cur:
            gap = w["s"] - cur[-1]["e"]
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            prev = cur[-1]["w"]
            if (len(cur) >= max_words or chars > max_chars or gap > 0.45
                    or prev.endswith((".", "!", "?", "…")) or (prev.endswith((",", ";", ":")) and len(cur) >= 2)):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    out = []
    for k, c in enumerate(chunks):
        start, end = c[0]["s"], c[-1]["e"]
        nxt = chunks[k + 1][0]["s"] if k + 1 < len(chunks) else end + 0.6
        out.append({"words": c, "start": start, "end": min(nxt, end + 0.6)})
    return out


def _ease_out_back(x):
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _params(style, W, H, opts):
    """Style parameters after the user's caption size - shared by the renderer and caption_spans."""
    p = style_params(style, W, H)
    scale = max(40, min(200, float((opts or {}).get("caption_size", 100) or 100))) / 100.0
    p["size"] = max(10, int(p["size"] * scale))
    if scale > 1.15:
        p["max_chars"] = int(p["max_chars"] / (scale ** 0.7))
    return p


class CaptionRenderer:
    def __init__(self, words, style, W, H, opts=None):
        opts = opts or {}
        self.enabled = bool(words) and style != "None"
        self.W, self.H = W, H
        self.hidden = []   # (start, end) ranges where captions are hidden (full-screen cards)
        if not self.enabled:
            return
        self.p = _params(style, W, H, opts)
        path = resolve_font(opts.get("caption_font"), self.p["fonts"])
        self.font = ImageFont.truetype(path, self.p["size"]) if path else ImageFont.load_default()
        self.text_col = hex_rgb(opts.get("caption_color", "#FFFFFF"), WHITE)
        self.hi_col = hex_rgb(opts.get("highlight_color", "#FFD600"), YELLOW)
        lum = 0.299 * self.hi_col[0] + 0.587 * self.hi_col[1] + 0.114 * self.hi_col[2]
        self.box_text = BLACK if lum > 150 else WHITE
        self.vertical = H > W
        self.cy = position_y(opts.get("caption_position"), self.vertical, style)
        mg = opts.get("caption_margin")
        if mg not in (None, "", 0):          # safe margin from the top/bottom edge, % of the height
            mg = max(2.0, min(30.0, float(mg))) / 100.0
            pos = (opts.get("caption_position") or "Bottom").lower()
            if pos == "top":
                self.cy = mg + 0.05
            elif pos != "middle":
                self.cy = 1.0 - mg - 0.05
        self.anim = opts.get("caption_anim", "None") or "None"
        self.maxw = W * (0.78 if self.vertical else 0.86)
        self.chunks = make_chunks(words, self.p["max_words"], self.p["max_chars"])
        self.starts = [c["start"] for c in self.chunks]
        self.cache = {}

    def _text(self, w):
        t = w["w"]
        return t.upper() if self.p["upper"] else t

    def _render(self, ci, active):
        p, font = self.p, self.font
        words = [self._text(w) for w in self.chunks[ci]["words"]]
        space = font.getlength(" ")
        widths = [font.getlength(t) for t in words]
        maxw = self.maxw
        lines, cur, curw = [], [], 0.0
        for i, wd in enumerate(widths):
            add = wd if not cur else curw + space + wd
            if cur and add > maxw:
                lines.append(cur)
                cur, curw = [i], wd
            else:
                cur.append(i)
                curw = add
        if cur:
            lines.append(cur)
        if len(lines) == 2:  # balance two lines so there is no single orphan word
            n = len(widths)

            def lw_(a, b):
                return sum(widths[a:b]) + space * (b - a - 1)
            best = min(range(1, n), key=lambda k: max(lw_(0, k), lw_(k, n)))
            if max(lw_(0, best), lw_(best, n)) <= maxw:
                lines = [list(range(0, best)), list(range(best, n))]
        asc, desc = font.getmetrics()
        lh = int((asc + desc) * 1.08)
        sw = int(p["size"] * p["stroke"])
        pad = sw + int(p["size"] * 0.35)
        lw = [sum(widths[i] for i in ln) + space * (len(ln) - 1) for ln in lines]
        cw = int(max(lw) + pad * 2)
        ch = int(lh * len(lines) + pad * 2)
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if p.get("box"):
            d.rounded_rectangle([pad * 0.4, pad * 0.4, cw - pad * 0.4, ch - pad * 0.4],
                                radius=int(p["size"] * 0.3), fill=(0, 0, 0, 150))
        sh = sd = None
        if p.get("shadow"):
            sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sh)
        y = pad
        for li, ln in enumerate(lines):
            x = (cw - lw[li]) / 2
            for i in ln:
                color = self.text_col
                sw_use = sw
                if i == active and p["highlight"] == "color":
                    color = self.hi_col
                if i == active and p["highlight"] == "box":
                    r = int(p["size"] * 0.18)
                    d.rounded_rectangle([x - r, y - r * 0.2, x + widths[i] + r, y + asc + desc * 0.6 + r * 0.6],
                                        radius=r, fill=self.hi_col + (255,))
                    color = self.box_text
                    sw_use = 0
                if sd is not None:
                    off = max(2, int(p["size"] * 0.06))
                    sd.text((x + off, y + off), words[i], font=font, fill=(0, 0, 0, 220))
                d.text((x, y), words[i], font=font, fill=color + (255,),
                       stroke_width=sw_use, stroke_fill=BLACK if sw_use else None)
                x += widths[i] + space
            y += lh
        if sh is not None:
            sh = sh.filter(ImageFilter.GaussianBlur(max(1, p["size"] * 0.05)))
            sh.alpha_composite(img)
            img = sh
        arr = np.asarray(img)
        rgb = arr[:, :, :3].astype(np.float32)
        a = arr[:, :, 3:4].astype(np.float32) / 255.0
        return (rgb * a, a, cw, ch)

    def _scale_for(self, c, t):
        if self.anim == "Pop-in":
            x = (t - c["start"]) / 0.16
            if x < 1:
                return 0.75 + 0.25 * _ease_out_back(max(0.0, x)), min(1.0, max(0.0, x * 1.6))
        elif self.anim == "Word bounce":
            ws = [w["s"] for w in c["words"] if w["s"] <= t]
            if ws:
                x = (t - ws[-1]) / 0.12
                if x < 1:
                    return 1.0 + 0.07 * (1 - x) ** 2, 1.0
        return 1.0, 1.0

    def overlay(self, frame, t):
        if not self.enabled:
            return frame
        for a, b in self.hidden:
            if a <= t < b:
                return frame
        ci = bisect.bisect_right(self.starts, t) - 1
        if ci < 0:
            return frame
        c = self.chunks[ci]
        if t >= c["end"]:
            return frame
        active = 0
        for i, w in enumerate(c["words"]):
            if w["s"] <= t:
                active = i
        key = (ci, active if self.p["highlight"] else -1)
        if key not in self.cache:
            if len(self.cache) > 64:
                self.cache.clear()
            self.cache[key] = self._render(ci, key[1])
        rgbp, a, cw, ch = self.cache[key]
        sc, fade = self._scale_for(c, t)
        if abs(sc - 1.0) > 1e-3:
            nw, nh = max(2, int(cw * sc)), max(2, int(ch * sc))
            rgbp = cv2.resize(rgbp, (nw, nh), interpolation=cv2.INTER_LINEAR)
            a = cv2.resize(a[:, :, 0], (nw, nh), interpolation=cv2.INTER_LINEAR)[:, :, None]
            cw, ch = nw, nh
        if fade < 1.0:
            rgbp = rgbp * fade
            a = a * fade
        x0 = int((self.W - cw) / 2)
        y0 = int(self.H * self.cy - ch / 2)
        y0 = max(0, min(self.H - ch, y0))
        return blend_rgba(frame, rgbp, a, x0, y0)


def blend_rgba(frame, rgbp, a, x0, y0):
    """Alpha-blend premultiplied rgbp/a (float) onto frame at (x0, y0)."""
    H, W = frame.shape[:2]
    h, w = a.shape[:2]
    x1, y1 = min(W, x0 + w), min(H, y0 + h)
    xs, ys = max(0, x0), max(0, y0)
    if x1 <= xs or y1 <= ys:
        return frame
    if not frame.flags.writeable:
        frame = frame.copy()
    reg = frame[ys:y1, xs:x1].astype(np.float32)
    sub_rgb = rgbp[ys - y0:y1 - y0, xs - x0:x1 - x0]
    sub_a = a[ys - y0:y1 - y0, xs - x0:x1 - x0]
    frame[ys:y1, xs:x1] = np.clip(sub_rgb + reg * (1.0 - sub_a), 0, 255).astype(np.uint8)
    return frame


# ---------------------------------------------------------------------- caption track + editing
def caption_spans(words, style="Modern (word highlight)", W=1920, H=1080, opts=None):
    """Caption blocks as shown on screen: [{'start','end','text','i0','i1'}] (word index range)."""
    if not words or style == "None":
        return []
    p = _params(style, W, H, opts)
    chunks = make_chunks(words, p["max_words"], p["max_chars"])
    ids = {id(w): i for i, w in enumerate(words)}
    out = []
    for c in chunks:
        i0, i1 = ids[id(c["words"][0])], ids[id(c["words"][-1])]
        out.append({"start": c["start"], "end": c["end"], "i0": i0, "i1": i1,
                    "text": " ".join(w["w"] for w in c["words"])})
    return out


def project_caption_spans(project):
    """caption_spans for a project: same video shape and caption size as the export."""
    from .exportcfg import dims
    st = project.get("settings") or {}
    W, H = dims(st.get("aspect") or "16:9", st.get("resolution") or "Full HD")
    return caption_spans(project.get("words") or [], st.get("caption_style", "Modern (word highlight)"), W, H, st)


def edit_caption_words(words, i0, i1, new_text):
    """Replace words[i0..i1] with the words of new_text, keeping the block's timing.
    Same word count -> each word keeps its own timing; otherwise timings are spread by length."""
    toks = new_text.split()
    old = words[i0:i1 + 1]
    if not old:
        return words
    if not toks:
        return words[:i0] + words[i1 + 1:]
    if len(toks) == len(old):
        new = [dict(w, w=t) for w, t in zip(old, toks)]
    else:
        s, e = old[0]["s"], old[-1]["e"]
        tot = sum(len(t) + 1 for t in toks)
        new, acc = [], s
        for t in toks:
            d = (e - s) * (len(t) + 1) / tot
            new.append({"w": t, "s": round(acc, 3), "e": round(acc + d * 0.92, 3)})
            acc += d
    return words[:i0] + new + words[i1 + 1:]
