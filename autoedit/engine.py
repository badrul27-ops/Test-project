"""Compositor: builds any frame of the edit at time t.

Used both by the preview player (small size, seekable) and the final render
(full size, sequential), so what you see in the preview is what you get.
"""
import bisect
import os

import cv2
import numpy as np

from .captions import CaptionRenderer
from .framing import cover_tf
from .fx import Atmosphere, apply_effects, clip_adjust
from .overlays import OverlayRenderer, effective_overlays
from .presets import PRESETS, transition_duration
from .util import is_video
from .visuals import (ImageSource, SeekVideoSource, VideoSource, blend, clip_state, clip_transform,
                      probe_duration)


def broll_clips(project):
    """V2 track clips (B-roll / cutaways placed over the main scenes), sorted by start."""
    out = [c for c in (project.get("broll") or []) if c.get("media") and c["end"] > c["start"]]
    return sorted(out, key=lambda c: c["start"])


def timeline_windows(scenes, preset, total):
    """Transition durations per cut and the extended time range of each scene."""
    n = len(scenes)
    tdur = []
    for k in range(n - 1):
        kind = scenes[k].get("transition", "cut")
        d = transition_duration(kind, preset)
        if kind != "cut" and scenes[k].get("transition_s") not in (None, ""):
            d = max(0.0, float(scenes[k]["transition_s"]))      # this boundary's own duration
        la = scenes[k]["end"] - scenes[k]["start"]
        lb = scenes[k + 1]["end"] - scenes[k + 1]["start"]
        tdur.append(max(0.0, min(d, 0.45 * la, 0.45 * lb)))
    ext = []
    for k in range(n):
        a = scenes[k]["start"] - (tdur[k - 1] / 2 if k > 0 else 0)
        b = (scenes[k]["end"] + tdur[k] / 2) if k < n - 1 else max(total, scenes[k]["end"])
        ext.append((max(0.0, a), max(b, a + 0.1)))
    return tdur, ext


class _MessageSource:
    """Preview-only placeholder that says why a scene is blank."""

    def __init__(self, W, H, msg):
        import cv2
        self.img = np.full((H, W, 3), 28, np.uint8)
        scale = max(0.4, W / 1100)
        (tw, th), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        cv2.putText(self.img, msg, (max(5, (W - tw) // 2), H // 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (90, 170, 255), 1, cv2.LINE_AA)

    def frame(self, p):
        return self.img

    def close(self):
        pass


class Compositor:
    def __init__(self, project, W, H, fps=30, total=None, preview=False, captions=True,
                 atmosphere=True, cuts_only=False, log=print):
        self.p = project
        self.W, self.H, self.fps = W, H, fps
        self.preview = preview
        self.log = log
        st = project["settings"]
        self.preset = dict(PRESETS.get(st.get("preset"), PRESETS["Documentary"]))
        if st.get("transition_seconds"):                      # project guideline default
            self.preset["transition_duration"] = float(st["transition_seconds"])
        self.scenes = project["scenes"]
        self.total = total or (project.get("duration", 0) + 0.4)
        self.tdur, self.ext = timeline_windows(self.scenes, self.preset, self.total)
        if cuts_only:
            self.tdur = [0.0] * len(self.tdur)
            self.ext = [(s["start"], s["end"]) for s in self.scenes]
        self.starts = [s["start"] for s in self.scenes]
        ov = st.get("overlay") or ""
        ov_src = None
        if atmosphere and ov and os.path.isfile(ov):
            nfr = self.total * fps
            ov_src = (SeekVideoSource(ov, W, H, self.total, fps, clip_duration=probe_duration(ov), short="loop")
                      if preview
                      else VideoSource(ov, W, H, nfr / fps, fps, loop=True))
        look = {"vignette": self.preset.get("vignette", 0), "saturation": self.preset.get("saturation", 1)}
        self.fx = Atmosphere(W, H, fps, look,
                             st.get("atmosphere", "None") if atmosphere else "None",
                             st.get("atmo_strength", 1.0), ov if atmosphere else None,
                             st.get("overlay_opacity", 0.5), ov_src)
        self.caps = CaptionRenderer(project["words"] if (captions and st.get("captions", True)) else [],
                                    st.get("caption_style", "Modern (word highlight)"), W, H, opts=st)
        self.ovl = OverlayRenderer(effective_overlays(project) if captions else [], W, H, st)
        if self.caps.enabled:
            self.caps.hidden = self.ovl.hidden_ranges()
        self._cap_allowed = bool(captions)
        self._capr = {}
        self.sources = {}
        self.broll = broll_clips(project)
        self.bsources = {}
        self.blank = np.full((H, W, 3), 12, np.uint8)
        self.fit = st.get("fit", "crop") or "crop"           # framing.py modes
        self.bg_color = st.get("bg_color", "#000000")
        self.short = st.get("short_clip", "auto")
        self._last_k = 0

    # ------------------------------------------------------------ sources
    def invalidate(self, k=None):
        keys = list(self.sources) if k is None else [k]
        for j in keys:
            s = self.sources.pop(j, None)
            if s is not None:
                s.close()

    def _source(self, k):
        if k in self.sources:
            return self.sources[k]
        sc = self.scenes[k]
        m = sc.get("media")
        a, b = self.ext[k]
        tf = clip_transform(sc)
        src = None
        try:
            if m and os.path.isfile(m):
                frame_kw = self._frame_kw(sc)
                if is_video(m):
                    cls = SeekVideoSource if self.preview else VideoSource
                    src = cls(m, self.W, self.H, b - a, self.fps, probe_duration(m), tf=tf,
                              clip_in=sc.get("clip_in") or 0.0, clip_out=sc.get("clip_out"),
                              short=sc.get("short_clip") or self.short, **frame_kw)
                else:
                    motion = sc.get("motion", "zoom_in")      # "none" = perfectly still image
                    amt = sc.get("motion_amount")
                    amt = self.preset["zoom_amount"] if amt in (None, "") else float(amt)
                    src = ImageSource(m, self.W, self.H, motion, amt, seed=k, tf=tf,
                                      ease=sc.get("motion_ease") or "smooth", **frame_kw)
            elif m:
                self.log(f"Missing file for scene {k + 1}: {m}")
        except Exception as e:
            self.log(f"Could not load scene {k + 1} media ({os.path.basename(m)}): {e}")
        if src is None and self.preview:
            src = _MessageSource(self.W, self.H, f"Scene {k + 1}: " + (
                ("can't open " + os.path.basename(m)) if m else "no image/clip assigned"))
        self.sources[k] = src
        return src

    def _frame_kw(self, c):
        """Per-clip framing (falls back to the project's): mode, focal point, border trim, colour."""
        return {"fit": c.get("fit") or self.fit, "focus": c.get("focus"), "trim": c.get("trim"),
                "bg_color": c.get("bg_color") or self.bg_color}

    def _scene_frame(self, k, t):
        src = self._source(k)
        if src is None:
            return self.blank
        sc = self.scenes[k]
        a, b = self.ext[k]
        tf, op = clip_state(sc, t - sc["start"])
        if (sc.get("fit") or self.fit) != "fit_bars":
            tf = cover_tf(tf, self.W, self.H)            # scale / pan / rotate never uncover the canvas
        if isinstance(src, (VideoSource, SeekVideoSource)):
            if isinstance(src, VideoSource) and int(round((t - a) * self.fps)) < src.idx:
                # went backwards on a sequential reader -> reopen
                self.invalidate(k)
                src = self._source(k)
            fr = src.frame_index(int(round((t - a) * self.fps)), tf)
        elif isinstance(src, _MessageSource):
            return src.frame(0)
        else:
            fr = src.frame((t - a) / (b - a), tf)
        fr = clip_adjust(fr, sc)
        if sc.get("effects"):
            fr = apply_effects(fr, sc["effects"], t - sc["start"])
        if op < 0.999:
            fr = cv2.convertScaleAbs(fr, alpha=op)
        return fr

    # ------------------------------------------------------------ V2 (B-roll) track
    def _bsource(self, i):
        if i in self.bsources:
            return self.bsources[i]
        c = self.broll[i]
        m = c["media"]
        src = None
        try:
            if os.path.isfile(m):
                frame_kw = self._frame_kw(c)
                if is_video(m):
                    cls = SeekVideoSource if self.preview else VideoSource
                    src = cls(m, self.W, self.H, c["end"] - c["start"], self.fps, probe_duration(m),
                              clip_in=c.get("clip_in") or 0.0, clip_out=c.get("clip_out"),
                              short=c.get("short_clip") or self.short, **frame_kw)
                else:
                    motion = c.get("motion", "zoom_in")
                    amt = c.get("motion_amount")
                    amt = self.preset["zoom_amount"] if amt in (None, "") else float(amt)
                    src = ImageSource(m, self.W, self.H, motion, amt, seed=100 + i, **frame_kw)
            else:
                self.log(f"Missing V2 clip file: {m}")
        except Exception as e:
            self.log(f"Could not load V2 clip {os.path.basename(m)}: {e}")
        self.bsources[i] = src
        return src

    def _broll_over(self, frame, t):
        for i, c in enumerate(self.broll):
            if not (c["start"] <= t < c["end"]):
                if i in self.bsources and (t > c["end"] + 1 or t < c["start"] - 1):
                    s = self.bsources.pop(i)
                    if s is not None:
                        s.close()
                continue
            src = self._bsource(i)
            if src is None:
                continue
            lt = t - c["start"]
            dur = c["end"] - c["start"]
            tf, op = clip_state(c, lt)
            if isinstance(src, (VideoSource, SeekVideoSource)):
                if isinstance(src, VideoSource) and int(round(lt * self.fps)) < src.idx:
                    src.close()
                    self.bsources.pop(i, None)
                    src = self._bsource(i)
                fr = src.frame_index(int(round(lt * self.fps)), tf)
            else:
                fr = src.frame(lt / max(dur, 1e-3), tf)
            fr = clip_adjust(fr, c)
            fade = float(c.get("fade", 0.25))
            env = 1.0 if fade <= 0 else min(1.0, lt / fade, (dur - lt) / fade)
            alpha = max(0.0, min(1.0, op * env))
            if c.get("mode") == "overlay":
                frame = self._overlay_box(frame, fr, c, alpha)
            elif alpha >= 0.999:
                frame = fr
            elif alpha > 0.001:
                frame = cv2.addWeighted(frame, 1 - alpha, fr, alpha, 0)
        return frame

    def _overlay_box(self, frame, fr, c, alpha):
        """Picture-in-picture: the clip scaled to `size` of the frame width at a named position."""
        from .instructions import POSITIONS
        size = max(0.05, min(1.0, float(c.get("size") or 0.35)))
        w, h = max(2, int(round(self.W * size))), max(2, int(round(self.H * size)))
        small = cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA)
        px, py = POSITIONS.get(c.get("position") or "top_right", (0.96, 0.05))
        x0 = int(round(min(max(0, px * self.W - w * px), self.W - w)))
        y0 = int(round(min(max(0, py * self.H - h * py), self.H - h)))
        out = np.array(frame, copy=True)
        roi = out[y0:y0 + h, x0:x0 + w]
        out[y0:y0 + h, x0:x0 + w] = small if alpha >= 0.999 else cv2.addWeighted(roi, 1 - alpha, small, alpha, 0)
        return out

    def _gc(self, k):
        keep = {k - 1, k, k + 1}
        for j in [j for j in self.sources if j not in keep]:
            s = self.sources.pop(j)
            if s is not None:
                s.close()

    # ------------------------------------------------------------ frames
    def scene_at(self, t):
        return max(0, bisect.bisect_right(self.starts, t) - 1)

    def frame_at(self, t, captions=True):
        # cut times are stored frame-snapped to 6 decimals (instructions.snap); frame i is drawn at i/fps, e.g.
        # 221/30 = 7.3666666… < 7.366667 - round the same way, or such a cut would show up one frame late
        t = round(float(t), 6)
        sc = self.scenes
        n = len(sc)
        if n == 0:
            return self.blank.copy()
        k = self.scene_at(t)
        td = self.tdur
        if k < n - 1 and td[k] > 0 and t >= sc[k]["end"] - td[k] / 2:
            a0 = sc[k]["end"] - td[k] / 2
            frame = blend(self._scene_frame(k, t), self._scene_frame(k + 1, t),
                          sc[k]["transition"], (t - a0) / td[k])
        elif k > 0 and td[k - 1] > 0 and t < sc[k]["start"] + td[k - 1] / 2:
            a0 = sc[k]["start"] - td[k - 1] / 2
            frame = blend(self._scene_frame(k - 1, t), self._scene_frame(k, t),
                          sc[k - 1]["transition"], (t - a0) / td[k - 1])
        else:
            frame = self._scene_frame(k, t)
        if k != self._last_k:
            self._gc(k)
            self._last_k = k
        if self.broll:
            frame = self._broll_over(frame, t)
        frame = self.fx.apply(frame, t)
        if self.ovl.items:
            frame = self.ovl.apply(frame, t)
        if captions:
            cr = self._captions_for(sc[k])
            if cr is not None and cr.enabled:
                frame = cr.overlay(np.array(frame, copy=True), t)
        return np.ascontiguousarray(frame, dtype=np.uint8)

    def _captions_for(self, scene):
        """Scene caption override: 'off', or 'on' (+ position) - otherwise the project's captions."""
        mode = scene.get("captions")
        if mode == "off" or not self._cap_allowed:
            return None
        pos = scene.get("caption_position")
        if mode != "on" or (not pos and self.caps.enabled):
            return self.caps if (self.caps.enabled or mode != "on") else self._cap_renderer(pos)
        return self._cap_renderer(pos)

    def _cap_renderer(self, pos):
        st = self.p["settings"]
        key = pos or st.get("caption_position") or "Bottom"
        if key not in self._capr:
            opts = dict(st, caption_position=key)
            style = st.get("caption_style", "Modern (word highlight)")
            if style == "None":
                style = "Modern (word highlight)"
            r = CaptionRenderer(self.p["words"], style, self.W, self.H, opts=opts)
            if r.enabled:
                r.hidden = self.ovl.hidden_ranges()
            self._capr[key] = r
        return self._capr[key]

    def close(self):
        self.invalidate()
        for s in self.bsources.values():
            if s is not None:
                s.close()
        self.bsources = {}
        self.fx.close()
