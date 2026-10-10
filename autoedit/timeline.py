"""Multi-track timeline.

Tracks (top to bottom): V2 B-roll · V1 scenes · Text cards · Captions · A1 Voice · A2 Music · A3 SFX.

Editing (all undoable - the window commits after each change):
  click            select (properties panel follows) + move the playhead
  ruler drag       scrub
  V1 edge drag     retime a cut (snaps to pauses)          V1 body drag   reorder visuals
  V2 / Text body   move                                     V2 / Text edge trim
  SFX drag         move the effect in time
  right-click      split / duplicate / delete / replace ... (handled by the window)
  Delete key       delete the selected item
A plain click never changes anything: drags start only after the mouse moves a few pixels.
"""
import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from .media_bin import loader
from .util import fmt_time

# per-clip "visual" properties that move with the image when V1 visuals are reordered
# (clip range and framing belong to the picture, not to the narration slot)
VISUAL_KEYS = ("media", "motion", "motion_amount", "motion_ease", "zoom", "x", "y", "rot", "opacity", "keyframes",
               "adjust", "clip_in", "clip_out", "short_clip", "fit", "focus", "trim", "bg_color", "asset_id")


def visual_locked(s):
    return bool(s.get("locked")) or "primary_visual" in (s.get("locked_fields") or [])

TRACKS = [  # key, label, sub, height
    ("broll", "V2", "B-roll", 30),
    ("scene", "V1", "Scenes", 46),
    ("text", "T", "Text", 18),
    ("caption", "CC", "Captions", 18),
    ("voice", "A1", "Voice", 32),
    ("music", "A2", "Music", 20),
    ("sfx", "A3", "SFX", 20),
]
COLORS = {"broll": (46, 96, 110), "text": (120, 86, 30), "caption": (60, 64, 80), "sfx": (170, 120, 240)}


class TimelineWidget(QWidget):
    seek = Signal(float)
    selected = Signal(str, int)            # kind, index (index -1 for whole-track kinds)
    edited = Signal(str)                   # something moved/trimmed -> label for undo
    request = Signal(str, str, int)        # action, kind, index (context menu / Delete key)
    media_dropped = Signal(str, str, int, float)   # path, track kind, index, time
    view_changed = Signal()
    # compat with the old single-track widget
    cut_released = Signal()

    RULER, GAP = 20, 2
    GUTTER = 70
    DRAG_PX = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_tracks()
        self.setMinimumHeight(self._y_end + 6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.project = None
        self.scenes, self.duration, self.peaks, self.snaps = [], 0.0, None, []
        self.captions = []
        self.pps = 20.0
        self.offset = 0.0
        self.playhead = 0.0
        self.sel = ("", -1)
        self.drag = None
        self.snap_on = True
        self.fitted = True
        self._drop_hint = None
        loader().updated.connect(self.update)

    # ---------------- geometry
    def layout_tracks(self, compact=False):
        """compact=True (short windows): thinner tracks so the preview gets more height."""
        self.compact = compact
        self.track_y = {}
        y = self.RULER + 4
        for key, _, _, h in TRACKS:
            if compact:
                h = max(16, int(round(h * 0.72)))
            self.track_y[key] = (y, h)
            y += h + self.GAP
        self._y_end = y

    def set_compact(self, on):
        if bool(on) != getattr(self, "compact", False):
            self.layout_tracks(bool(on))
            self.setMinimumHeight(self._y_end + 6)
            self.updateGeometry()
            self.update()

    def track_at(self, y):
        for key, (ty, h) in self.track_y.items():
            if ty <= y <= ty + h:
                return key
        return None

    # ---------------- data
    def set_project(self, project, peaks=None, captions=None):
        self.project = project
        self.scenes = project["scenes"]
        self.duration = max(project.get("duration") or 0.0, 0.1)
        words = project.get("words") or []
        self.snaps = [(words[i]["e"] + words[i + 1]["s"]) / 2 for i in range(len(words) - 1)] if words else []
        if peaks is not None:
            self.peaks = peaks
        if captions is not None:
            self.captions = captions
        if self.fitted:
            self.fit()
        else:
            self.offset = min(self.offset, self.max_offset())
            self.view_changed.emit()
        self.update()

    def items(self, kind):
        p = self.project or {}
        if kind == "scene":
            return p.get("scenes", [])
        if kind == "broll":
            return p.get("broll", [])
        if kind == "text":
            return p.get("overlays", [])
        if kind == "sfx":
            return p.get("sfx_events") or []
        if kind == "caption":
            return self.captions
        return []

    def fit(self):
        self.fitted = True
        self.pps = max(0.5, (self.width() - self.GUTTER - 12) / max(self.duration, 0.1))
        self.offset = 0.0
        self.update()
        self.view_changed.emit()

    def show_seconds(self, secs, start=0.0):
        """Zoom so about `secs` seconds are visible (long videos: clips wide enough to grab)."""
        if self.duration <= secs * 1.2:
            return self.fit()
        self.fitted = False
        self.pps = max(0.5, (self.width() - self.GUTTER - 12) / secs)
        self.offset = min(max(0.0, start), self.max_offset())
        self.update()
        self.view_changed.emit()

    def visible_seconds(self):
        return max(0.1, (self.width() - self.GUTTER - 12) / self.pps)

    def max_offset(self):
        return max(0.0, self.duration - self.visible_seconds())

    def set_offset(self, t):
        t = min(max(0.0, t), self.max_offset())
        if abs(t - self.offset) > 1e-6:
            self.offset = t
            if self.max_offset() > 0:
                self.fitted = False
            self.update()
            self.view_changed.emit()

    def zoom(self, factor):
        c = self.t_of(self.GUTTER + (self.width() - self.GUTTER) / 2)
        self.pps = min(400.0, max(0.5, self.pps * factor))
        self.offset = min(max(0.0, c - (self.width() - self.GUTTER) / 2 / self.pps), self.max_offset())
        self.fitted = False
        self.update()
        self.view_changed.emit()

    def resizeEvent(self, e):
        if self.fitted:
            self.fit()
        else:
            self.offset = min(self.offset, self.max_offset())
            self.view_changed.emit()
        super().resizeEvent(e)

    def x_of(self, t):
        return self.GUTTER + 6 + (t - self.offset) * self.pps

    def t_of(self, x):
        return self.offset + (x - self.GUTTER - 6) / self.pps

    def set_playhead(self, t, follow=False):
        self.playhead = t
        if follow and not self.fitted:
            x = self.x_of(t)
            if x > self.width() - 40 or x < self.GUTTER:
                self.offset = min(max(0.0, t - 2.0), self.max_offset())
                self.view_changed.emit()
        self.update()

    def set_selected(self, kind, idx=None):
        if idx is None:            # old API: set_selected(scene_index)
            kind, idx = "scene", kind
        self.sel = (kind, idx)
        self.update()

    # ---------------- painting
    def paintEvent(self, e):
        p = QPainter(self)
        W, G = self.width(), self.GUTTER
        p.fillRect(self.rect(), QColor(18, 20, 26))
        p.save()
        p.setClipRect(G, 0, W - G, self.height())
        self._paint_ruler(p, W, G)
        for key, _, _, _ in TRACKS:
            y, h = self.track_y[key]
            p.fillRect(QRectF(G, y, W - G, h), QColor(24, 27, 34))
            getattr(self, "_paint_" + key)(p, y, h, W, G)
        # playhead
        x = self.x_of(self.playhead)
        p.setPen(QPen(QColor(255, 176, 32), 2))
        p.drawLine(int(x), 0, int(x), self.height())
        p.setBrush(QColor(255, 176, 32))
        p.drawEllipse(QPointF(x, 6), 5, 5)
        if self._drop_hint is not None:
            k, t = self._drop_hint
            y, h = self.track_y[k]
            p.setPen(QPen(QColor(161, 149, 250), 2, Qt.DashLine))
            p.drawLine(int(self.x_of(t)), y, int(self.x_of(t)), y + h)
        p.restore()
        # gutter labels
        p.fillRect(QRectF(0, 0, G, self.height()), QColor(28, 31, 38))
        p.setPen(QColor(50, 56, 70))
        p.drawLine(G, 0, G, self.height())
        for key, lab, sub, _h in TRACKS:
            y, h = self.track_y[key]
            active = self.sel[0] == key
            if active:
                p.fillRect(QRectF(0, y, G, h), QColor(60, 52, 120))
            f = QFont("Segoe UI", 8)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(236, 238, 244))
            if h >= 30:
                p.drawText(QRectF(8, y + 2, G - 10, 16), Qt.AlignVCenter, lab)
                p.setFont(QFont("Segoe UI", 7))
                p.setPen(QColor(180, 186, 203))
                p.drawText(QRectF(8, y + 16, G - 10, 14), Qt.AlignVCenter, sub)
            else:
                p.drawText(QRectF(8, y, G - 10, h), Qt.AlignVCenter, f"{lab}  ")
                p.setFont(QFont("Segoe UI", 7))
                p.setPen(QColor(180, 186, 203))
                p.drawText(QRectF(30, y, G - 32, h), Qt.AlignVCenter, sub)
        if not self.scenes:
            p.setPen(QColor(180, 186, 203))
            p.setFont(QFont("Segoe UI", 9))
            y, h = self.track_y["scene"]
            p.drawText(QRectF(G, y, W - G, h), Qt.AlignCenter,
                       "Timeline is empty - add a voiceover and images, then choose Analyze voiceover")
        p.end()

    def _paint_ruler(self, p, W, G):
        p.setPen(QColor(150, 157, 175))
        p.setFont(QFont("Segoe UI", 8))
        step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300) if s * self.pps >= 64), 600)
        t = (int(self.offset / step)) * step
        while self.x_of(t) < W:
            x = self.x_of(t)
            if x >= G:
                p.drawLine(int(x), 13, int(x), self.RULER)
                p.drawText(int(x) + 3, 11, fmt_time(t)[:-3] if step >= 1 else fmt_time(t))
            t += step

    def _clip_rect(self, y, h, a, b):
        x0, x1 = self.x_of(a), self.x_of(b)
        return QRectF(x0, y + 1, max(2.0, x1 - x0 - 1), h - 2)

    def _sel_border(self, p, r, kind, i):
        if self.sel == (kind, i):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(161, 149, 250), 2))
            p.drawRect(r.adjusted(1, 1, -1, -1))

    def _paint_scene(self, p, y, h, W, G):
        for k, s in enumerate(self.scenes):
            r = self._clip_rect(y, h, s["start"], s["end"])
            if r.right() < G or r.left() > W:
                continue
            base = QColor(50, 58, 88) if k % 2 == 0 else QColor(62, 54, 96)
            if not s.get("media"):
                base = QColor(96, 40, 44)
            p.fillRect(r, base)
            pm = loader().thumb(s["media"], 96, h - 16) if s.get("media") else None
            p.save()
            p.setClipRect(r.intersected(QRectF(G, 0, W - G, self.height())))
            if pm is not None and r.width() > 18:
                p.drawPixmap(int(r.left()) + 1, y + 1, pm)
            p.setPen(QColor(240, 241, 245))
            if r.width() > 22:
                p.fillRect(QRectF(r.left(), y + h - 15, r.width(), 14), QColor(0, 0, 0, 160))
                tag = f"{k + 1}  {s['end'] - s['start']:.1f}s" + ("  ◆" if s.get("keyframes") else "") + \
                      ("  fx" if s.get("adjust") else "")
                p.drawText(QRectF(r.left() + 3, y + h - 15, r.width() - 4, 14), Qt.AlignVCenter, tag)
            p.restore()
            self._sel_border(p, r, "scene", k)
            if k < len(self.scenes) - 1 and s.get("transition", "cut") != "cut":
                cx = r.right() + 1
                p.setBrush(QBrush(QColor(125, 211, 252)))
                p.setPen(Qt.NoPen)
                p.drawPolygon(QPolygonF([QPointF(cx, y + 4), QPointF(cx + 5, y + 10), QPointF(cx, y + 16),
                                         QPointF(cx - 5, y + 10)]))
            self._paint_keys(p, s, y, h)
        if self.drag and self.drag.get("mode") == "reorder" and self.drag.get("moved"):
            j = self.drag["target"]
            if 0 <= j < len(self.scenes):
                r = self._clip_rect(y, h, self.scenes[j]["start"], self.scenes[j]["end"])
                p.setPen(QPen(QColor(255, 176, 32), 2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r)

    def _paint_keys(self, p, clip, y, h):
        kfs = clip.get("keyframes") or []
        if not kfs:
            return
        p.setBrush(QBrush(QColor(255, 214, 0)))
        p.setPen(QPen(QColor(30, 30, 30), 1))
        for kf in kfs:
            cx = self.x_of(clip["start"] + kf["t"])
            cy = y + 8
            p.drawPolygon(QPolygonF([QPointF(cx, cy - 5), QPointF(cx + 5, cy), QPointF(cx, cy + 5),
                                     QPointF(cx - 5, cy)]))

    def _paint_broll(self, p, y, h, W, G):
        for i, c in enumerate(self.items("broll")):
            r = self._clip_rect(y, h, c["start"], c["end"])
            if r.right() < G or r.left() > W:
                continue
            p.fillRect(r, QColor(*COLORS["broll"]))
            pm = loader().thumb(c.get("media"), 60, h - 4)
            p.save()
            p.setClipRect(r)
            if pm is not None:
                p.drawPixmap(int(r.left()) + 1, y + 2, pm)
            p.setPen(QColor(240, 241, 245))
            p.drawText(r.adjusted(64 if pm is not None else 4, 0, -2, 0), Qt.AlignVCenter,
                       os.path.basename(c.get("media", "")))
            p.restore()
            self._sel_border(p, r, "broll", i)
            self._paint_keys(p, c, y, h)

    def _paint_text(self, p, y, h, W, G):
        for i, o in enumerate(self.items("text")):
            r = self._clip_rect(y, h, o["start"], o["end"])
            if r.right() < G or r.left() > W:
                continue
            p.fillRect(r, QColor(*COLORS["text"]))
            p.save()
            p.setClipRect(r)
            p.setPen(QColor(255, 244, 220))
            p.setFont(QFont("Segoe UI", 7))
            p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter, f"{o.get('type', '')}: {o.get('text', '')}")
            p.restore()
            self._sel_border(p, r, "text", i)

    def _paint_caption(self, p, y, h, W, G):
        p.setFont(QFont("Segoe UI", 7))
        for i, c in enumerate(self.captions):
            r = self._clip_rect(y, h, c["start"], c["end"])
            if r.right() < G or r.left() > W:
                continue
            p.fillRect(r, QColor(*COLORS["caption"]) if i % 2 else QColor(52, 56, 72))
            if r.width() > 20:
                p.save()
                p.setClipRect(r)
                p.setPen(QColor(226, 229, 238))
                p.drawText(r.adjusted(3, 0, -2, 0), Qt.AlignVCenter, c["text"])
                p.restore()
            self._sel_border(p, r, "caption", i)

    def _paint_wave(self, p, y, h, W, G, peaks, color, loop_len=None, gain=1.0):
        if peaks is None or len(peaks) < 2:
            return
        per = 100.0
        p.setPen(color)
        mid = y + h / 2
        n = len(peaks)
        for x in range(G, W, 2):
            t0, t1 = self.t_of(x), self.t_of(x + 2)
            if t1 < 0 or t0 > self.duration:
                continue
            i0, i1 = int(t0 * per), int(t1 * per) + 1
            if loop_len:
                i0, i1 = i0 % n, i1 % n
                if i1 <= i0:
                    i1 = i0 + 1
            if i1 <= 0 or i0 >= n:
                continue
            v = float(peaks[max(0, i0):min(n, i1)].max()) * gain
            hh = min(1.0, v) * (h / 2 - 2)
            p.drawLine(x, int(mid - hh), x, int(mid + hh))

    def _paint_voice(self, p, y, h, W, G):
        p.fillRect(QRectF(G, y, W - G, h), QColor(16, 36, 32))
        self._paint_wave(p, y, h, W, G, self.peaks, QColor(52, 211, 153))
        if self.sel[0] == "voice":
            p.setPen(QPen(QColor(161, 149, 250), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(G + 1, y + 1, W - G - 2, h - 2))

    def _paint_music(self, p, y, h, W, G):
        music = (self.project or {}).get("music")
        if not music:
            p.setPen(QColor(120, 126, 142))
            p.setFont(QFont("Segoe UI", 7))
            p.drawText(QRectF(G + 6, y, 400, h), Qt.AlignVCenter, "No music - add it in the Audio tab")
            return
        p.fillRect(QRectF(self.x_of(0), y, self.duration * self.pps, h), QColor(22, 32, 48))
        pk = loader().peaks(music)
        self._paint_wave(p, y, h, W, G, pk, QColor(96, 165, 250), loop_len=True, gain=0.8)
        if self.sel[0] == "music":
            p.setPen(QPen(QColor(161, 149, 250), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(G + 1, y + 1, W - G - 2, h - 2))

    def _paint_sfx(self, p, y, h, W, G):
        proj = self.project or {}
        ev = proj.get("sfx_events") or []
        if not proj.get("sfx_planned"):
            p.setPen(QColor(120, 126, 142))
            p.setFont(QFont("Segoe UI", 7))
            p.drawText(QRectF(G + 6, y, 600, h), Qt.AlignVCenter,
                       "Effects are chosen at render - use 'Plan sound effects' to see and edit them here")
        p.setFont(QFont("Segoe UI", 7))
        for i, e in enumerate(ev):
            d = float(e.get("dur") or 0.8)
            r = self._clip_rect(y, h, e["t"], e["t"] + max(0.25, min(d, 2.0)))
            col = QColor(*COLORS["sfx"]) if e.get("enabled", True) else QColor(80, 80, 90)
            p.fillRect(r, col)
            p.save()
            p.setClipRect(r)
            p.setPen(QColor(20, 16, 30))
            p.drawText(r.adjusted(3, 0, 0, 0), Qt.AlignVCenter, e.get("cat", ""))
            p.restore()
            self._sel_border(p, r, "sfx", i)
        if self.sel == ("sfx", -1):
            p.setPen(QPen(QColor(161, 149, 250), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(G + 1, y + 1, W - G - 2, h - 2))

    # ---------------- hit testing
    def _hit(self, x, y):
        """-> (kind, index, part) part: 'body' | 'left' | 'right' | 'cut' | 'track'"""
        kind = self.track_at(y)
        if kind is None or x < self.GUTTER:
            return None, -1, None
        t = self.t_of(x)
        if kind == "scene":
            for i in range(1, len(self.scenes)):
                if abs(self.x_of(self.scenes[i]["start"]) - x) <= 5:
                    return "scene", i, "cut"
            for k, s in enumerate(self.scenes):
                if s["start"] <= t < s["end"]:
                    return "scene", k, "body"
            return "scene", (len(self.scenes) - 1 if self.scenes else -1), "body"
        if kind in ("voice", "music"):
            return kind, -1, "track"
        its = self.items(kind)
        for i in range(len(its) - 1, -1, -1):
            it = its[i]
            a = it["t"] if kind == "sfx" else it["start"]
            b = (a + max(0.25, min(float(it.get("dur") or 0.8), 2.0))) if kind == "sfx" else it["end"]
            if self.x_of(a) - 3 <= x <= self.x_of(b) + 3:
                if kind in ("broll", "text"):
                    if abs(x - self.x_of(a)) <= 5:
                        return kind, i, "left"
                    if abs(x - self.x_of(b)) <= 5:
                        return kind, i, "right"
                return kind, i, "body"
        return kind, -1, "track"

    def _snap(self, t, x_px, exclude=None):
        if not self.snap_on:
            return t
        cands = list(self.snaps) + [s["start"] for s in self.scenes] + [self.playhead, 0.0, self.duration]
        for k in ("broll", "text"):
            for i, it in enumerate(self.items(k)):
                if exclude == (k, i):
                    continue
                cands += [it["start"], it["end"]]
        best = min(cands, key=lambda c: abs(c - t)) if cands else t
        return best if abs(self.x_of(best) - x_px) <= 8 else t

    # ---------------- mouse
    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if x < self.GUTTER:
            k = self.track_at(y)
            if k in ("voice", "music", "sfx"):
                self.sel = (k, -1)
                self.selected.emit(k, -1)
                self.update()
            return
        if e.button() == Qt.RightButton:
            kind, i, part = self._hit(x, y)
            if kind:
                if i >= 0 or kind in ("voice", "music", "sfx"):
                    self.sel = (kind, i)
                    self.selected.emit(kind, i)
                self._context(e, kind, i)
            return
        if e.button() != Qt.LeftButton:
            return
        if y < self.RULER + 2:
            self.drag = {"mode": "scrub"}
            self.seek.emit(min(max(0.0, self.t_of(x)), self.duration))
            return
        kind, i, part = self._hit(x, y)
        t = min(max(0.0, self.t_of(x)), self.duration)
        self.drag = {"mode": "pending", "kind": kind, "i": i, "part": part, "x0": x, "t0": t, "moved": False}
        if kind == "scene" and part == "cut":
            self.drag["orig"] = (self.scenes[i - 1]["end"], self.scenes[i]["start"])
            return
        if kind and (i >= 0 or kind in ("voice", "music", "sfx")):
            self.sel = (kind, i)
            self.selected.emit(kind, i)
        if kind in ("broll", "text") and i >= 0:
            it = self.items(kind)[i]
            self.drag["orig"] = (it["start"], it["end"])
        elif kind == "sfx" and i >= 0:
            self.drag["orig"] = self.items("sfx")[i]["t"]
        if part != "cut":
            self.seek.emit(t)
        self.update()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        d = self.drag
        if d is None:
            kind, i, part = self._hit(x, y)
            self.setCursor(Qt.SizeHorCursor if part in ("cut", "left", "right") else
                           (Qt.OpenHandCursor if kind in ("broll", "text", "sfx") and i >= 0 else Qt.ArrowCursor))
            return
        if d["mode"] == "scrub":
            self.seek.emit(min(max(0.0, self.t_of(max(x, self.GUTTER))), self.duration))
            return
        if d["mode"] == "pending":
            if abs(x - d["x0"]) < self.DRAG_PX:
                return
            k, part = d["kind"], d["part"]
            if k == "scene" and part == "cut":
                d["mode"] = "cut"
            elif k == "scene" and part == "body" and d["i"] >= 0:
                d["mode"], d["target"] = "reorder", d["i"]
            elif k in ("broll", "text") and d["i"] >= 0:
                d["mode"] = {"body": "move", "left": "trim_l", "right": "trim_r"}[part]
            elif k == "sfx" and d["i"] >= 0:
                d["mode"] = "move_sfx"
            else:
                d["mode"] = "scrub"
        d["moved"] = True
        t = self.t_of(x)
        m = d["mode"]
        if m == "scrub":
            self.seek.emit(min(max(0.0, t), self.duration))
        elif m == "cut":
            i = d["i"]
            a, b = self.scenes[i - 1], self.scenes[i]
            if self.snap_on and self.snaps:
                near = min(self.snaps, key=lambda g: abs(g - t))
                if abs(self.x_of(near) - x) <= 8:
                    t = near
            t = min(max(t, a["start"] + 0.3), b["end"] - 0.3)
            a["end"] = b["start"] = round(t, 3)
        elif m == "reorder":
            tt = min(max(0.0, t), self.duration)
            d["target"] = next((k for k, s in enumerate(self.scenes) if s["start"] <= tt < s["end"]),
                               len(self.scenes) - 1)
        elif m in ("move", "trim_l", "trim_r"):
            it = self.items(d["kind"])[d["i"]]
            a0, b0 = d["orig"]
            dt = t - d["t0"]
            if m == "move":
                ln = b0 - a0
                ns = self._snap(min(max(0.0, a0 + dt), max(0.0, self.duration - ln)), self.x_of(a0 + dt),
                                exclude=(d["kind"], d["i"]))
                it["start"], it["end"] = round(ns, 3), round(ns + ln, 3)
            elif m == "trim_l":
                ns = self._snap(min(max(0.0, a0 + dt), b0 - 0.3), x, exclude=(d["kind"], d["i"]))
                it["start"] = round(ns, 3)
            else:
                ne = self._snap(max(a0 + 0.3, min(self.duration + 0.4, b0 + dt)), x, exclude=(d["kind"], d["i"]))
                it["end"] = round(ne, 3)
        elif m == "move_sfx":
            ev = self.items("sfx")[d["i"]]
            ev["t"] = round(self._snap(min(max(0.0, d["orig"] + (t - d["t0"])), self.duration), x), 3)
        self.update()

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        if not d or not d.get("moved"):
            self.update()
            return
        m = d["mode"]
        if m == "cut":
            i = d["i"]
            if (self.scenes[i - 1]["end"], self.scenes[i]["start"]) != d["orig"]:
                self.cut_released.emit()
                self.edited.emit("Move cut")
        elif m == "reorder" and d["target"] != d["i"]:
            if not self.reorder_visuals(d["i"], d["target"]):
                self.request.emit("locked_visual", "scene", d["i"])
                self.update()
                return
            self.sel = ("scene", d["target"])
            self.selected.emit("scene", d["target"])
            self.edited.emit("Reorder visuals")
        elif m in ("move", "trim_l", "trim_r"):
            it = self.items(d["kind"])[d["i"]]
            if (it["start"], it["end"]) != d["orig"]:
                self.edited.emit({"move": "Move clip", "trim_l": "Trim clip", "trim_r": "Trim clip"}[m])
        elif m == "move_sfx":
            if self.items("sfx")[d["i"]]["t"] != d["orig"]:
                self.project["sfx_planned"] = True
                self.edited.emit("Move sound effect")
        self.update()

    def reorder_visuals(self, i, j):
        """Move scene i's visual to position j; the visuals in between shift along. Scene timing
        (from the narration) stays where it is. Returns False (nothing moved) if a locked scene is in the way."""
        sc = self.scenes
        if any(visual_locked(s) for s in sc[min(i, j):max(i, j) + 1]):
            return False
        vis = [{k: s.get(k) for k in VISUAL_KEYS} for s in sc]
        v = vis.pop(i)
        vis.insert(j, v)
        for s, vv in zip(sc, vis):
            for k in VISUAL_KEYS:
                if vv.get(k) is None:
                    s.pop(k, None)
                else:
                    s[k] = vv[k]
        return True

    def mouseDoubleClickEvent(self, e):
        kind, i, part = self._hit(e.position().x(), e.position().y())
        if kind == "scene" and part == "body" and i >= 0:
            self.request.emit("replace", "scene", i)
        elif kind == "caption" and i >= 0:
            self.request.emit("edit", "caption", i)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel[1] >= 0:
            self.request.emit("delete", self.sel[0], self.sel[1])
            return
        super().keyPressEvent(e)

    def _context(self, e, kind, i):
        m = QMenu(self)
        R = lambda a: (lambda: self.request.emit(a, kind, i))  # noqa: E731
        if kind == "scene" and i >= 0:
            m.addAction("Split at playhead", R("split"))
            m.addAction("Duplicate visual to next scene", R("duplicate"))
            m.addAction("Replace visual…", R("replace"))
            m.addAction("Remove visual", R("remove_visual"))
            m.addSeparator()
            m.addAction("Delete scene (merge into previous)", R("delete"))
        elif kind in ("broll", "text", "sfx") and i >= 0:
            if kind == "sfx":
                m.addAction("Preview sound", R("preview"))
            m.addAction("Duplicate", R("duplicate"))
            m.addAction("Delete", R("delete"))
        elif kind == "caption" and i >= 0:
            m.addAction("Edit caption text…", R("edit"))
        elif kind == "broll":
            m.addAction("Add media here…", lambda: self.request.emit("add_here", "broll", int(self.t_of(e.position().x()) * 1000)))
        elif kind == "text":
            m.addAction("Add text card here", lambda: self.request.emit("add_here", "text", int(self.t_of(e.position().x()) * 1000)))
        elif kind == "sfx":
            m.addAction("Add sound effect here", lambda: self.request.emit("add_here", "sfx", int(self.t_of(e.position().x()) * 1000)))
        else:
            return
        m.exec(e.globalPosition().toPoint())

    def wheelEvent(self, e):
        dlt = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            x = e.position().x()
            t = self.t_of(x)
            self.pps = min(400.0, max(0.5, self.pps * (1.25 if dlt > 0 else 0.8)))
            self.offset = min(max(0.0, t - (x - self.GUTTER - 6) / self.pps), self.max_offset())
        else:
            self.offset = min(max(0.0, self.offset - dlt / 120 * 60 / self.pps), self.max_offset())
        self.fitted = False
        self.update()
        self.view_changed.emit()

    # ---------------- drops from the media bin / explorer
    def _drop_path(self, e):
        md = e.mimeData()
        if md.hasFormat("application/x-autoedit-media"):
            return bytes(md.data("application/x-autoedit-media")).decode("utf-8")
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile():
                    return u.toLocalFile()
        return None

    def dragEnterEvent(self, e):
        if self._drop_path(e):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        k = self.track_at(e.position().y())
        if k in ("scene", "broll"):
            self._drop_hint = (k, max(0.0, self.t_of(e.position().x())))
            e.acceptProposedAction()
        else:
            self._drop_hint = None
            e.ignore()
        self.update()

    def dragLeaveEvent(self, e):
        self._drop_hint = None
        self.update()

    def dropEvent(self, e):
        path = self._drop_path(e)
        k = self.track_at(e.position().y())
        self._drop_hint = None
        if path and k in ("scene", "broll"):
            t = max(0.0, self.t_of(e.position().x()))
            i = next((j for j, s in enumerate(self.scenes) if s["start"] <= t < s["end"]), -1)
            self.media_dropped.emit(path, k, i, t)
            e.acceptProposedAction()
        self.update()
