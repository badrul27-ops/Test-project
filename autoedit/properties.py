"""Right-hand Properties panel: settings for whatever is selected on the timeline.

  V1 scene / V2 clip  ->  Video (scale, position, rotation, opacity) · Animation (motion preset,
                          keyframes) · Effects (per-clip look) · Transition (scenes only) ·
                          Framing (fill/fit mode, focal point, baked-in borders, clip in/out, lock)
  Text card           ->  text, type, timing
  Caption block       ->  edit the words (timing is kept)
  Sound effect        ->  time, level, on/off, preview
  Voice / Music track ->  level, fades, ducking, preview

Rules (same as the rest of the app): controls are loaded under QSignalBlocker, and a
control writes to the project only from its own user-edit signal. The panel then emits
`changed(label, kind)` so the window records an undo step and refreshes the preview.
"""
import os

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
                               QSlider, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from .app_parts_base import form, label
from .framing import FIT_HELP, FIT_MODES, cover_tf
from .fx import ADJUST_DEFAULTS, CLIP_FILTERS
from .overlays import OVERLAY_TYPES
from .presets import TRANSITIONS
from .scenes import ALL_MOTIONS
from .util import fmt_time, is_video
from .visuals import KEY_DEFAULTS, KEY_PROPS, clip_state

MOTION_NAMES = {"zoom_in": "Slow zoom in", "zoom_out": "Slow zoom out", "pan_left": "Pan left (camera moves left)",
                "pan_right": "Pan right (camera moves right)", "pan_up": "Pan up", "pan_down": "Pan down",
                "static": "Almost still (very slow 3% push)", "subtle": "Subtle camera movement",
                "none": "None - no preset movement"}
TRANSITION_NAMES = {"cut": "Cut", "crossfade": "Crossfade", "dip_black": "Dip to black", "slide": "Slide",
                    "whip": "Whip pan", "zoom": "Zoom through"}
KF_NEAR = 0.04          # seconds: playhead this close to a keyframe edits that keyframe

# slider specs: prop -> (label, min, max, scale, unit)
TF_SPECS = [("zoom", "Scale", 50, 300, 100.0, "%"), ("x", "Position X", -50, 50, 100.0, "%"),
            ("y", "Position Y", -50, 50, 100.0, "%"), ("rot", "Rotation", -45, 45, 1.0, "°"),
            ("opacity", "Opacity", 0, 100, 100.0, "%")]
ADJ_SPECS = [("brightness", "Brightness", -50, 50, 100.0), ("contrast", "Contrast", 50, 150, 100.0),
             ("saturation", "Saturation", 0, 200, 100.0), ("blur", "Blur", 0, 100, 10.0),
             ("filter_amount", "Filter strength", 0, 100, 100.0)]


def _slider(lo, hi):
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    return s


def _row(w, lab):
    r = QHBoxLayout()
    r.setSpacing(6)
    r.addWidget(w, 1)
    r.addWidget(lab)
    return r


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sc.setWidget(w)
    return sc


def _spin(lo, hi, step=0.1, suffix="", dec=2):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class PropertiesPanel(QWidget):
    changed = Signal(str, str)        # undo label, kind: tf|keys|motion|adjust|transition|timing|text|caption|sfx|audio
    request = Signal(str)             # replace | remove_visual | motion_all | effects_all | preview_transition |
    #                                   preview_sfx | preview_music | delete
    seek = Signal(float)

    def __init__(self, get_project, get_playhead):
        super().__init__()
        self.get_project, self.get_playhead = get_project, get_playhead
        self.kind, self.idx = "", -1
        self.title = QLabel("Properties")
        self.title.setObjectName("PanelTitle")
        self.sub = label("Select a clip or a track on the timeline.", "Help")
        self.stack = QStackedWidget()
        self.pages = {}
        for name, fn in (("empty", self._page_empty), ("visual", self._page_visual), ("text", self._page_text),
                         ("caption", self._page_caption), ("sfx", self._page_sfx), ("voice", self._page_voice),
                         ("music", self._page_music), ("sfxtrack", self._page_sfxtrack)):
            w = fn()
            self.pages[name] = w
            self.stack.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addWidget(self.stack, 1)

    # ================================================================ pages
    def _page_empty(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(label("Click a scene (V1), a B-roll clip (V2), a text card, a caption, a sound "
                            "effect, or the Voice / Music track label on the timeline to edit it here.", "Help"))
        lay.addStretch(1)
        return w

    def _page_visual(self):
        self.vtabs = QTabWidget()
        self.vtabs.setObjectName("PropTabs")
        self.vtabs.setDocumentMode(True)
        # ---- Video
        v = QWidget()
        vl = QVBoxLayout(v)
        vl.setContentsMargins(4, 8, 4, 4)
        self.v_info = label("", "Help")
        vl.addWidget(self.v_info)
        f = form()
        self.tf_w = {}
        for key, name, lo, hi, scale, unit in TF_SPECS:
            s = _slider(lo, hi)
            lab = QLabel("")
            lab.setFixedWidth(46)
            self.tf_w[key] = (s, lab, scale, unit)
            s.valueChanged.connect(lambda val, k=key: self._tf_edit(k, val))
            f.addRow(name, _row(s, lab))
        vl.addLayout(f)
        self.kf_note = label("", "Help")
        vl.addWidget(self.kf_note)
        # V2 clip timing
        self.b_box = QWidget()
        bf = form()
        self.b_start = _spin(0, 36000, 0.1, " s")
        self.b_end = _spin(0, 36000, 0.1, " s")
        self.b_fade = _spin(0, 3, 0.05, " s")
        self.b_start.valueChanged.connect(lambda x: self._broll_time("start", x))
        self.b_end.valueChanged.connect(lambda x: self._broll_time("end", x))
        self.b_fade.valueChanged.connect(lambda x: self._broll_time("fade", x))
        bf.addRow("Starts at", self.b_start)
        bf.addRow("Ends at", self.b_end)
        bf.addRow("Fade in/out", self.b_fade)
        QVBoxLayout(self.b_box).addLayout(bf)
        self.b_box.layout().setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.b_box)
        # V1 scene timing (moves the cut shared with the neighbouring scene)
        self.s_box = QWidget()
        sf = form()
        self.s_start = _spin(0, 36000, 0.05, " s")
        self.s_end = _spin(0, 36000, 0.05, " s")
        self.s_start.valueChanged.connect(lambda x: self._scene_time("start", x))
        self.s_end.valueChanged.connect(lambda x: self._scene_time("end", x))
        bs = QPushButton("Start at playhead")
        bs.setToolTip("Move this scene's start (and the previous scene's end) to the playhead")
        bs.clicked.connect(lambda: self._scene_time("start", self.get_playhead()))
        be = QPushButton("End at playhead")
        be.setToolTip("Move this scene's end (and the next scene's start) to the playhead")
        be.clicked.connect(lambda: self._scene_time("end", self.get_playhead()))
        sr1, sr2 = QHBoxLayout(), QHBoxLayout()
        sr1.addWidget(self.s_start, 1)
        sr1.addWidget(bs)
        sr2.addWidget(self.s_end, 1)
        sr2.addWidget(be)
        sf.addRow("Starts at", sr1)
        sf.addRow("Ends at", sr2)
        QVBoxLayout(self.s_box).addLayout(sf)
        self.s_box.layout().setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.s_box)
        r = QHBoxLayout()
        self.b_replace = QPushButton("Replace…")
        self.b_replace.setToolTip("Choose another image/clip (Ctrl+R)")
        self.b_replace.clicked.connect(lambda: self.request.emit("replace"))
        self.b_remove = QPushButton("Remove visual")
        self.b_remove.setToolTip("Scene keeps its timing but shows no image (the file is not deleted)")
        self.b_remove.clicked.connect(lambda: self.request.emit("remove_visual"))
        self.b_delete = QPushButton("Delete clip")
        self.b_delete.clicked.connect(lambda: self.request.emit("delete"))
        for b in (self.b_replace, self.b_remove, self.b_delete):
            r.addWidget(b)
        vl.addLayout(r)
        b = QPushButton("Reset video settings")
        b.setToolTip("Scale 100 %, centred, no rotation, fully visible, no keyframes")
        b.clicked.connect(self._reset_tf)
        vl.addWidget(b)
        vl.addStretch(1)
        self.vtabs.addTab(_scroll(v), "Video")

        # ---- Animation
        a = QWidget()
        al = QVBoxLayout(a)
        al.setContentsMargins(4, 8, 4, 4)
        f = form()
        self.motion = QComboBox()
        for m in ALL_MOTIONS:
            self.motion.addItem(MOTION_NAMES.get(m, m), m)
        self.motion.activated.connect(lambda _i: self._clip_set("motion", self.motion.currentData(), "Clip motion",
                                                                "motion"))
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 50)
        self.amount.setDecimals(0)
        self.amount.setSuffix(" %")
        self.amount.setSpecialValueText("Preset default")
        self.amount.setKeyboardTracking(False)
        self.amount.setToolTip("How far the preset moves (0 = use the editing preset's amount)")
        self.amount.valueChanged.connect(lambda x: self._clip_set("motion_amount", None if x == 0 else x / 100.0,
                                                                  "Motion amount", "motion"))
        f.addRow("Motion preset", self.motion)
        f.addRow("Amount", self.amount)
        al.addLayout(f)
        self.motion_note = label("", "Help")
        al.addWidget(self.motion_note)
        b = QPushButton("Apply this motion to all scenes")
        b.setToolTip("Every still image gets this motion preset and amount (video clips are left as they are)")
        b.clicked.connect(lambda: self.request.emit("motion_all"))
        al.addWidget(b)
        al.addWidget(label("Keyframes", "Section"))
        al.addWidget(label("A keyframe stores Scale, Position, Rotation and Opacity at one moment of the "
                           "clip. With two or more, the clip moves smoothly between them. When a clip has "
                           "keyframes, the Video sliders edit the keyframe at the playhead (and add one if "
                           "there is none). Keyframes are shown as ◆ on the timeline. Keyframes add to the "
                           "motion preset - choose 'None' above to move the clip only with keyframes.", "Help"))
        self.kf_list = QListWidget()
        self.kf_list.setMinimumHeight(90)
        self.kf_list.itemDoubleClicked.connect(self._kf_jump)
        al.addWidget(self.kf_list)
        r = QHBoxLayout()
        self.kf_add = QPushButton("◆ Add at playhead")
        self.kf_add.clicked.connect(self._kf_add)
        self.kf_del = QPushButton("Delete")
        self.kf_del.clicked.connect(self._kf_delete)
        self.kf_clear = QPushButton("Clear all")
        self.kf_clear.clicked.connect(self._kf_clear)
        for b in (self.kf_add, self.kf_del, self.kf_clear):
            r.addWidget(b)
        al.addLayout(r)
        r = QHBoxLayout()
        bp = QPushButton("◀ Previous")
        bp.clicked.connect(lambda: self._kf_step(-1))
        bn = QPushButton("Next ▶")
        bn.clicked.connect(lambda: self._kf_step(1))
        r.addWidget(bp)
        r.addWidget(bn)
        al.addLayout(r)
        al.addStretch(1)
        self.vtabs.addTab(_scroll(a), "Animation")

        # ---- Effects (per clip)
        e = QWidget()
        el = QVBoxLayout(e)
        el.setContentsMargins(4, 8, 4, 4)
        f = form()
        self.filter = QComboBox()
        self.filter.addItems(CLIP_FILTERS)
        self.filter.activated.connect(lambda _i: self._adj_set("filter", self.filter.currentText()))
        f.addRow("Filter", self.filter)
        self.adj_w = {}
        for key, name, lo, hi, scale in ADJ_SPECS:
            s = _slider(lo, hi)
            lab = QLabel("")
            lab.setFixedWidth(46)
            self.adj_w[key] = (s, lab, scale)
            s.valueChanged.connect(lambda val, k=key: self._adj_slider(k, val))
            f.addRow(name, _row(s, lab))
        el.addLayout(f)
        r = QHBoxLayout()
        b = QPushButton("Reset effects")
        b.clicked.connect(self._adj_reset)
        b2 = QPushButton("Apply to all scenes")
        b2.setToolTip("Copy this clip's filter and adjustments to every scene")
        b2.clicked.connect(lambda: self.request.emit("effects_all"))
        r.addWidget(b)
        r.addWidget(b2)
        el.addLayout(r)
        el.addWidget(label("These apply to this clip only. The whole-video atmosphere is in the Effects tab "
                           "on the left.", "Help"))
        el.addStretch(1)
        self.vtabs.addTab(_scroll(e), "Effects")

        # ---- Transition
        t = QWidget()
        tl = QVBoxLayout(t)
        tl.setContentsMargins(4, 8, 4, 4)
        f = form()
        self.trans = QComboBox()
        for x in TRANSITIONS:
            self.trans.addItem(TRANSITION_NAMES.get(x, x), x)
        self.trans.activated.connect(lambda _i: self._clip_set("transition", self.trans.currentData(),
                                                               "Clip transition", "transition"))
        f.addRow("Transition to the next scene", self.trans)
        tl.addLayout(f)
        self.trans_note = label("", "Help")
        tl.addWidget(self.trans_note)
        self.b_trans_prev = QPushButton("▶ Preview transition")
        self.b_trans_prev.setToolTip("Plays 1.5 s before to 1.5 s after this cut")
        self.b_trans_prev.clicked.connect(lambda: self.request.emit("preview_transition"))
        tl.addWidget(self.b_trans_prev)
        tl.addStretch(1)
        self.trans_tab = _scroll(t)
        self.vtabs.addTab(self.trans_tab, "Transition")
        self.vtabs.addTab(_scroll(self._framing_tab()), "Framing")
        return self.vtabs

    # ---------------------------------------------------------------- framing (v7)
    def _framing_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 8, 4, 4)
        self.lock_cb = QCheckBox("Lock this scene")
        self.lock_cb.setToolTip("Re-planning / re-analysing never changes a locked scene's visual, times or look")
        self.lock_cb.toggled.connect(lambda c: self._clip_set("locked", bool(c), "Lock scene" if c else
                                                             "Unlock scene", "lock"))
        lay.addWidget(self.lock_cb)
        f = form()
        self.fit_combo = QComboBox()
        self.fit_combo.addItem("Project default", None)
        for k in ("crop", "fit_blur", "fit_color", "fit_bars"):
            self.fit_combo.addItem(FIT_MODES[k], k)
        self.fit_combo.activated.connect(lambda _i: self._clip_set("fit", self.fit_combo.currentData(),
                                                                   "Framing mode", "framing"))
        f.addRow("Framing", self.fit_combo)
        self.fit_help = label("", "Help")
        f.addRow("", self.fit_help)
        self.focus_x, self.focus_y = _slider(0, 100), _slider(0, 100)
        self.focus_lab = QLabel("")
        for sl in (self.focus_x, self.focus_y):
            sl.valueChanged.connect(self._focus_edit)
        f.addRow("Focal point ↔", self.focus_x)
        f.addRow("Focal point ↕", _row(self.focus_y, self.focus_lab))
        from .gui_panels import ColorButton
        self.bg_btn = ColorButton("#000000")
        self.bg_btn.changed.connect(lambda c: self._clip_set("bg_color", c, "Background colour", "framing"))
        f.addRow("Background colour", self.bg_btn)
        lay.addLayout(f)
        self.clamp_note = label("", "Help")
        lay.addWidget(self.clamp_note)
        lay.addWidget(label("Baked-in borders", "Section"))
        lay.addWidget(label("Some pictures already contain black or white bars. Detection looks for a uniform "
                            "band along the edges (in several frames for a video) and never trims dark "
                            "artwork that has detail. Trimming is reversible and the file isn't changed.", "Help"))
        r = QHBoxLayout()
        self.b_detect = QPushButton("Detect borders")
        self.b_detect.clicked.connect(self._detect_borders)
        self.b_trim_apply = QPushButton("Trim them")
        self.b_trim_apply.clicked.connect(self._apply_trim)
        self.b_trim_clear = QPushButton("Remove trim")
        self.b_trim_clear.clicked.connect(lambda: self._clip_set("trim", None, "Remove border trim", "framing")
                                          or self.reload())
        for b in (self.b_detect, self.b_trim_apply, self.b_trim_clear):
            r.addWidget(b)
        lay.addLayout(r)
        self.trim_msg = label("", "Help")
        lay.addWidget(self.trim_msg)
        self._pending_trim = None
        # video clip in/out + short clip policy
        self.clip_box = QWidget()
        cf = form()
        self.clip_in = _spin(0, 36000, 0.1, " s")
        self.clip_out = _spin(0, 36000, 0.1, " s")
        self.clip_out.setSpecialValueText("End of clip")
        self.clip_in.valueChanged.connect(lambda x: self._clip_set("clip_in", round(x, 2) or None,
                                                                   "Clip in point", "framing"))
        self.clip_out.valueChanged.connect(lambda x: self._clip_set("clip_out", round(x, 2) or None,
                                                                    "Clip out point", "framing"))
        self.short_combo = QComboBox()
        for t, v in (("Project default", None), ("Freeze the last frame", "freeze"),
                     ("Loop the clip (approved repeat)", "loop"), ("Slow the clip down (voice unchanged)", "slow")):
            self.short_combo.addItem(t, v)
        self.short_combo.activated.connect(lambda _i: self._clip_set("short_clip", self.short_combo.currentData(),
                                                                     "Short clip handling", "framing"))
        cf.addRow("Clip starts at", self.clip_in)
        cf.addRow("Clip ends at", self.clip_out)
        cf.addRow("If the clip is too short", self.short_combo)
        QVBoxLayout(self.clip_box).addLayout(cf)
        self.clip_box.layout().setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.clip_box)
        self.clip_note = label("", "Help")
        lay.addWidget(self.clip_note)
        lay.addStretch(1)
        return w

    def _focus_edit(self, _v=None):
        fx, fy = self.focus_x.value() / 100.0, self.focus_y.value() / 100.0
        self.focus_lab.setText(f"{self.focus_x.value()}%, {self.focus_y.value()}%")
        self._clip_set("focus", None if (fx, fy) == (0.5, 0.5) else [fx, fy], "Focal point", "framing")

    def _frames_for(self, m):
        import numpy as np
        from .visuals import load_image_rgb, probe_duration, readable_video
        if not is_video(m):
            return [np.asarray(load_image_rgb(m, (1200, 1200)))]
        import cv2
        cap = cv2.VideoCapture(readable_video(m))
        d = probe_duration(m) or 1.0
        out = []
        for k in range(5):
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000 * d * (k + 0.5) / 5)
            ok, fr = cap.read()
            if ok:
                out.append(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
        cap.release()
        return out

    def _detect_borders(self):
        from .framing import detect_borders
        it = self.item()
        if it is None or not it.get("media") or not os.path.isfile(it["media"]):
            return
        try:
            trim, msg = detect_borders(self._frames_for(it["media"]))
        except Exception as e:      # unreadable file: say so, change nothing
            trim, msg = None, f"Couldn't check this file: {e}"
        self._pending_trim = trim
        self.trim_msg.setText(msg + (" Press 'Trim them' to remove them (Remove trim undoes it)." if trim else ""))
        self.b_trim_apply.setEnabled(bool(trim))

    def _apply_trim(self):
        if self._pending_trim:
            self._clip_set("trim", list(self._pending_trim), "Trim baked-in borders", "framing")
            self.reload()

    def _load_framing(self, it, scene):
        p = self.get_project()
        st = p.get("settings", {})
        m = it.get("media", "")
        self.lock_cb.setVisible(scene)
        with QSignalBlocker(self.lock_cb):
            self.lock_cb.setChecked(bool(it.get("locked")))
        mode = it.get("fit")
        with QSignalBlocker(self.fit_combo):
            i = self.fit_combo.findData(mode)
            self.fit_combo.setItemText(0, f"Project default ({FIT_MODES.get(st.get('fit', 'crop'), 'Fill')})")
            self.fit_combo.setCurrentIndex(i if i >= 0 else 0)
        eff = mode or st.get("fit", "crop")
        self.fit_help.setText(FIT_HELP.get(eff, ""))
        fx, fy = it.get("focus") or (0.5, 0.5)
        for sl, v in ((self.focus_x, fx), (self.focus_y, fy)):
            with QSignalBlocker(sl):
                sl.setValue(int(round(float(v) * 100)))
        self.focus_lab.setText(f"{self.focus_x.value()}%, {self.focus_y.value()}%")
        crop = eff in ("crop", "blur")
        self.focus_x.setEnabled(crop)
        self.focus_y.setEnabled(crop)
        self.bg_btn.setEnabled(eff == "fit_color")
        self.bg_btn.set(it.get("bg_color") or st.get("bg_color", "#000000"))
        tf = (float(it.get("zoom", 1.0)), float(it.get("x", 0.0)), float(it.get("y", 0.0)), float(it.get("rot", 0)))
        W, H = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}.get(st.get("aspect", "16:9"), (16, 9))
        if scene and eff != "fit_bars":
            z = cover_tf(tf, W, H)[0]
            self.clamp_note.setText(f"Scale is raised to {z * 100:.0f}% in the video so moving/rotating never "
                                    "shows empty edges." if z > tf[0] + 1e-3 else "")
        else:
            self.clamp_note.setText("")
        trim = it.get("trim")
        self.trim_msg.setText(("Border trim on: " + ", ".join(f"{n} {v * 100:.1f}%" for n, v in
                                                            zip(("left", "top", "right", "bottom"), trim) if v))
                              if trim and any(trim) else "")
        self.b_trim_clear.setEnabled(bool(trim and any(trim)))
        self.b_trim_apply.setEnabled(False)
        self._pending_trim = None
        self.b_detect.setEnabled(bool(m))
        vid = is_video(m)
        self.clip_box.setVisible(vid)
        if vid:
            from .visuals import probe_duration
            d = probe_duration(m) or 0
            for sp in (self.clip_in, self.clip_out):
                sp.setMaximum(max(0.1, d))
            with QSignalBlocker(self.clip_in), QSignalBlocker(self.clip_out), QSignalBlocker(self.short_combo):
                self.clip_in.setValue(float(it.get("clip_in") or 0))
                self.clip_out.setValue(float(it.get("clip_out") or 0))
                i = self.short_combo.findData(it.get("short_clip"))
                self.short_combo.setCurrentIndex(i if i >= 0 else 0)
            avail = (it.get("clip_out") or d) - (it.get("clip_in") or 0)
            need = it["end"] - it["start"]
            self.clip_note.setText(f"Clip length used: {avail:.1f} s · scene: {need:.1f} s" +
                                   (" - the clip is SHORTER than the scene; choose what happens above, trim the "
                                    "scene, or use another clip. The narration is never sped up or slowed."
                                    if avail < need - 0.05 else ""))
        else:
            self.clip_note.setText("")

    def _page_text(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        f = form()
        self.t_type = QComboBox()
        for k, v in OVERLAY_TYPES.items():
            self.t_type.addItem(v, k)
        self.t_type.activated.connect(lambda _i: self._item_set("type", self.t_type.currentData(), "Text card type"))
        self.t_text = QLineEdit()
        self.t_text.editingFinished.connect(lambda: self._item_set("text", self.t_text.text(), "Text card text"))
        self.t_sub = QLineEdit()
        self.t_sub.setPlaceholderText("Optional second line")
        self.t_sub.editingFinished.connect(lambda: self._item_set("sub", self.t_sub.text(), "Text card text"))
        self.t_start = _spin(0, 36000, 0.1, " s")
        self.t_end = _spin(0, 36000, 0.1, " s")
        self.t_start.valueChanged.connect(lambda x: self._item_time("start", x))
        self.t_end.valueChanged.connect(lambda x: self._item_time("end", x))
        f.addRow("Type", self.t_type)
        f.addRow("Text", self.t_text)
        f.addRow("Second line", self.t_sub)
        f.addRow("Starts at", self.t_start)
        f.addRow("Ends at", self.t_end)
        lay.addLayout(f)
        b = QPushButton("Delete text card")
        b.clicked.connect(lambda: self.request.emit("delete"))
        lay.addWidget(b)
        lay.addWidget(label("Drag the card on the T track to move it, or its edges to trim. Font and colour: "
                            "Captions tab.", "Help"))
        lay.addStretch(1)
        return _scroll(w)

    def _page_caption(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.c_info = label("", "Help")
        self.c_text = QPlainTextEdit()
        self.c_text.setMaximumHeight(90)
        b = QPushButton("Apply text")
        b.setProperty("primary", True)
        b.clicked.connect(self._caption_apply)
        lay.addWidget(self.c_info)
        lay.addWidget(self.c_text)
        lay.addWidget(b)
        lay.addWidget(label("Fix a mis-heard word here. The block keeps its timing; with the same number of "
                            "words each word keeps its own highlight timing. Caption style, font, colours and "
                            "position are in the Captions tab.", "Help"))
        lay.addStretch(1)
        return _scroll(w)

    def _page_sfx(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.s_info = label("", "Help")
        lay.addWidget(self.s_info)
        f = form()
        self.s_on = QCheckBox("Play this effect")
        self.s_on.toggled.connect(lambda c: self._sfx_set("enabled", bool(c), "Sound effect on/off"))
        self.s_time = _spin(0, 36000, 0.05, " s", 2)
        self.s_time.valueChanged.connect(lambda x: self._sfx_set("t", round(x, 3), "Move sound effect"))
        self.s_gain = _spin(-30, 20, 1.0, " dB", 1)
        self.s_gain.setToolTip("Your adjustment on top of the automatic level (effects are matched to the voice)")
        self.s_gain.valueChanged.connect(lambda x: self._sfx_set("gain", x, "Sound effect level"))
        f.addRow("", self.s_on)
        f.addRow("Plays at", self.s_time)
        f.addRow("Level adjustment", self.s_gain)
        lay.addLayout(f)
        r = QHBoxLayout()
        b = QPushButton("▶ Preview sound")
        b.clicked.connect(lambda: self.request.emit("preview_sfx"))
        b2 = QPushButton("Delete")
        b2.clicked.connect(lambda: self.request.emit("delete"))
        r.addWidget(b)
        r.addWidget(b2)
        lay.addLayout(r)
        lay.addStretch(1)
        return _scroll(w)

    def _page_sfxtrack(self):
        from .presets import SFX_LEVELS
        w = QWidget()
        lay = QVBoxLayout(w)
        self.st_info = label("", "Help")
        lay.addWidget(self.st_info)
        f = form()
        self.st_level = QComboBox()
        for k in SFX_LEVELS:
            self.st_level.addItem(k, k)
        self.st_level.activated.connect(lambda _i: self._setting("sfx_level", self.st_level.currentData(),
                                                                 "SFX level"))
        self.st_db = _spin(-20, 12, 0.5, " dB", 1)
        self.st_db.setToolTip("Raises or lowers every sound effect in the export")
        self.st_db.valueChanged.connect(lambda x: self._setting("sfx_db", x, "SFX volume"))
        f.addRow("Amount", self.st_level)
        f.addRow("SFX volume", self.st_db)
        lay.addLayout(f)
        b = QPushButton("Plan sound effects now")
        b.setToolTip("Choose the effects now so they appear on A3 and play in the preview")
        b.clicked.connect(lambda: self.request.emit("plan_sfx"))
        lay.addWidget(b)
        lay.addWidget(label("Effects are level-matched to your voice at render, then this volume is added. "
                            "Click an effect on A3 to preview it, change its own level, move or delete it.",
                            "Help"))
        lay.addStretch(1)
        return _scroll(w)

    def _page_voice(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.vo_info = label("", "Help")
        lay.addWidget(self.vo_info)
        f = form()
        self.vo_db = _spin(-20, 12, 0.5, " dB", 1)
        self.vo_db.valueChanged.connect(lambda x: self._setting("voice_db", x, "Voice level"))
        f.addRow("Voice level", self.vo_db)
        lay.addLayout(f)
        lay.addWidget(label("Applied in the render (before loudness normalisation). Press Space to hear the "
                            "voice in the preview.", "Help"))
        lay.addStretch(1)
        return _scroll(w)

    def _page_music(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.mu_info = label("", "Help")
        lay.addWidget(self.mu_info)
        f = form()
        self.mu_db = _spin(-40, 0, 1.0, " dB", 1)
        self.mu_db.valueChanged.connect(lambda x: self._setting("music_db", x, "Music volume"))
        self.mu_in = _spin(0, 10, 0.5, " s", 1)
        self.mu_in.valueChanged.connect(lambda x: self._setting("music_fade_in", x, "Music fade in"))
        self.mu_out = _spin(0, 15, 0.5, " s", 1)
        self.mu_out.valueChanged.connect(lambda x: self._setting("music_fade_out", x, "Music fade out"))
        self.mu_duck = QCheckBox("Lower the music while the voice speaks (ducking)")
        self.mu_duck.toggled.connect(lambda c: self._setting("music_duck", bool(c), "Music ducking"))
        f.addRow("Music volume", self.mu_db)
        f.addRow("Fade in", self.mu_in)
        f.addRow("Fade out", self.mu_out)
        f.addRow("", self.mu_duck)
        lay.addLayout(f)
        b = QPushButton("▶ Preview music")
        b.clicked.connect(lambda: self.request.emit("preview_music"))
        lay.addWidget(b)
        lay.addWidget(label("The music loops to the video length. The preview plays it at the chosen volume "
                            "without ducking; the render ducks it under the voice.", "Help"))
        lay.addStretch(1)
        return _scroll(w)

    # ================================================================ selection / loading
    def show_item(self, kind, idx):
        self.kind, self.idx = kind, idx
        self.reload()

    def item(self):
        p = self.get_project() or {}
        lst = {"scene": p.get("scenes"), "broll": p.get("broll"), "text": p.get("overlays"),
               "sfx": p.get("sfx_events")}.get(self.kind)
        if lst is not None and 0 <= self.idx < len(lst):
            return lst[self.idx]
        return None

    def reload(self):
        p = self.get_project() or {}
        k = self.kind
        it = self.item()
        if k in ("scene", "broll") and it is not None:
            self._load_visual(it)
        elif k == "text" and it is not None:
            self._load_text(it)
        elif k == "caption" and self._caption() is not None:
            self._load_caption()
        elif k == "sfx" and self.idx < 0:
            self._load_sfxtrack(p)
        elif k == "sfx" and it is not None:
            self._load_sfx(it)
        elif k == "voice" and p.get("voiceover"):
            self._load_voice(p)
        elif k == "music":
            self._load_music(p)
        else:
            self.title.setText("Properties")
            self.sub.setText("Select a clip or a track on the timeline.")
            self.stack.setCurrentWidget(self.pages["empty"])
            self.kind, self.idx = "", -1

    def sync_playhead(self):
        """Keyframed clips: show the values at the playhead (display only)."""
        if self.kind in ("scene", "broll") and self.item() is not None and self.item().get("keyframes"):
            self._load_tf(self.item())
            self._kf_highlight()

    def _local_t(self, it):
        return min(max(0.0, self.get_playhead() - it["start"]), max(0.0, it["end"] - it["start"]))

    def _load_tf(self, it):
        tf, op = clip_state(it, self._local_t(it))
        vals = {"zoom": tf[0], "x": tf[1], "y": tf[2], "rot": tf[3], "opacity": op}
        for key, (s, lab, scale, unit) in self.tf_w.items():
            v = int(round(float(vals[key]) * scale))
            with QSignalBlocker(s):
                s.setValue(v)
            lab.setText(f"{v}{unit}")

    def _load_visual(self, it):
        scene = self.kind == "scene"
        p = self.get_project()
        n = len(p.get("scenes", []))
        m = it.get("media", "")
        what = f"Scene {self.idx + 1}" if scene else f"V2 clip {self.idx + 1}"
        self.title.setText(what)
        self.sub.setText(f"{fmt_time(it['start'])} – {fmt_time(it['end'])} · {it['end'] - it['start']:.2f} s · "
                         f"{os.path.basename(m) if m else '(no visual)'}")
        self.v_info.setText("Video clip" if is_video(m) else ("Still image" if m else "No image - use Replace…"))
        self._load_tf(it)
        kfs = it.get("keyframes") or []
        self.kf_note.setText(f"This clip has {len(kfs)} keyframe(s): sliders show and edit the value at the "
                             "playhead." if kfs else "")
        self.b_box.setVisible(not scene)
        self.s_box.setVisible(scene)
        if scene:
            for w, v in ((self.s_start, it["start"]), (self.s_end, it["end"])):
                with QSignalBlocker(w):
                    w.setValue(float(v))
            self.s_start.setEnabled(self.idx > 0)
            self.s_end.setEnabled(self.idx < n - 1)
        for w, v in ((self.b_start, it["start"]), (self.b_end, it["end"]), (self.b_fade, it.get("fade", 0.25))):
            with QSignalBlocker(w):
                w.setValue(float(v))
        self.b_remove.setVisible(scene)
        self.b_remove.setEnabled(bool(m))
        self.b_delete.setText("Delete scene (merge)" if scene else "Delete clip")
        self.b_delete.setToolTip("Merge this scene into the previous one" if scene else "Remove this clip from V2")
        mot = it.get("motion", "zoom_in" if scene else "static")
        with QSignalBlocker(self.motion):
            i = self.motion.findData(mot)
            self.motion.setCurrentIndex(i if i >= 0 else 0)
        amt = it.get("motion_amount")
        with QSignalBlocker(self.amount):
            self.amount.setValue(0 if amt in (None, "") else round(float(amt) * 100))
        self.motion.setEnabled(not is_video(m))
        self.amount.setEnabled(not is_video(m))
        self.motion_note.setText("Video clips play as recorded - motion presets apply to still images. Use "
                                 "keyframes to move a video clip." if is_video(m) else "")
        self._fill_keys(it)
        a = dict(ADJUST_DEFAULTS)
        a.update(it.get("adjust") or {})
        with QSignalBlocker(self.filter):
            self.filter.setCurrentText(a["filter"])
        for key, (s, lab, scale) in self.adj_w.items():
            v = int(round(float(a[key]) * scale))
            with QSignalBlocker(s):
                s.setValue(v)
            lab.setText(self._adj_text(key, v))
        self.vtabs.setTabVisible(3, scene)
        self._load_framing(it, scene)
        if scene:
            last = self.idx >= n - 1
            with QSignalBlocker(self.trans):
                i = self.trans.findData(it.get("transition", "cut"))
                self.trans.setCurrentIndex(i if i >= 0 else 0)
            self.trans.setEnabled(not last)
            self.b_trans_prev.setEnabled(not last)
            self.trans_note.setText("The last scene has no transition after it." if last else
                                    f"Between scene {self.idx + 1} and {self.idx + 2}. Slide, whip and zoom add a "
                                    "whoosh when sound effects are on.")
        self.stack.setCurrentWidget(self.pages["visual"])

    def _adj_text(self, key, v):
        if key == "blur":
            return f"{v / 10:.1f}"
        if key == "brightness":
            return f"{v:+d}"
        return f"{v}%"

    def _fill_keys(self, it):
        with QSignalBlocker(self.kf_list):
            self.kf_list.clear()
            for kf in sorted(it.get("keyframes") or [], key=lambda k: k["t"]):
                txt = (f"◆ {kf['t']:.2f}s   scale {float(kf.get('zoom', 1)) * 100:.0f}%  "
                       f"x {float(kf.get('x', 0)) * 100:+.0f}  y {float(kf.get('y', 0)) * 100:+.0f}  "
                       f"rot {float(kf.get('rot', 0)):+.0f}°  opacity {float(kf.get('opacity', 1)) * 100:.0f}%")
                li = QListWidgetItem(txt)
                li.setData(Qt.UserRole, kf["t"])
                self.kf_list.addItem(li)
        has = bool(it.get("keyframes"))
        self.kf_del.setEnabled(has)
        self.kf_clear.setEnabled(has)
        self._kf_highlight()

    def _kf_highlight(self):
        it = self.item()
        if it is None:
            return
        lt = self._local_t(it)
        near = self._kf_near(it, lt)
        self.kf_add.setText("◆ Update at playhead" if near is not None else "◆ Add at playhead")
        with QSignalBlocker(self.kf_list):
            for r in range(self.kf_list.count()):
                li = self.kf_list.item(r)
                li.setSelected(near is not None and abs(li.data(Qt.UserRole) - near["t"]) < 1e-6)

    def _load_text(self, o):
        self.title.setText("Text card")
        self.sub.setText(f"{fmt_time(o['start'])} – {fmt_time(o['end'])}")
        with QSignalBlocker(self.t_type):
            i = self.t_type.findData(o.get("type", "label"))
            self.t_type.setCurrentIndex(max(0, i))
        for w, v in ((self.t_text, o.get("text", "")), (self.t_sub, o.get("sub", ""))):
            with QSignalBlocker(w):
                w.setText(v or "")
        for w, v in ((self.t_start, o["start"]), (self.t_end, o["end"])):
            with QSignalBlocker(w):
                w.setValue(float(v))
        self.stack.setCurrentWidget(self.pages["text"])

    def _caption(self):
        from .captions import caption_spans
        p = self.get_project() or {}
        spans = caption_spans(p.get("words") or [], p.get("settings", {}).get("caption_style",
                                                                               "Modern (word highlight)"))
        return spans[self.idx] if 0 <= self.idx < len(spans) else None

    def _load_caption(self):
        c = self._caption()
        self.title.setText(f"Caption {self.idx + 1}")
        self.sub.setText(f"{fmt_time(c['start'])} – {fmt_time(c['end'])}")
        self.c_info.setText(f"Words {c['i0'] + 1}–{c['i1'] + 1} of the transcript")
        self.c_text.setPlainText(c["text"])
        self.stack.setCurrentWidget(self.pages["caption"])

    def _load_sfx(self, e):
        self.title.setText(f"Sound effect: {e.get('cat', '')}")
        self.sub.setText(os.path.basename(e.get("file", "")) + ("" if os.path.isfile(e.get("file", "")) else
                                                                "  ⚠ file missing"))
        self.s_info.setText(f"Why: {e.get('reason', '-')}")
        with QSignalBlocker(self.s_on):
            self.s_on.setChecked(e.get("enabled", True))
        with QSignalBlocker(self.s_time):
            self.s_time.setValue(float(e["t"]))
        with QSignalBlocker(self.s_gain):
            self.s_gain.setValue(float(e.get("gain", 0.0)))
        self.stack.setCurrentWidget(self.pages["sfx"])

    def _load_sfxtrack(self, p):
        st = p.get("settings", {})
        ev = p.get("sfx_events") or []
        self.title.setText("Sound-effects track (A3)")
        self.sub.setText(f"{sum(1 for e in ev if e.get('enabled', True))} effects planned" if p.get("sfx_planned")
                         else "Effects are chosen at render")
        self.st_info.setText("")
        with QSignalBlocker(self.st_level):
            i = self.st_level.findData(st.get("sfx_level", "Normal"))
            self.st_level.setCurrentIndex(max(0, i))
        with QSignalBlocker(self.st_db):
            self.st_db.setValue(float(st.get("sfx_db", 0.0) or 0.0))
        self.stack.setCurrentWidget(self.pages["sfxtrack"])

    def _load_voice(self, p):
        self.title.setText("Voice track (A1)")
        self.sub.setText(os.path.basename(p.get("voiceover", "")))
        self.vo_info.setText(f"Length {fmt_time(p.get('duration') or 0)} · {len(p.get('words') or [])} words")
        with QSignalBlocker(self.vo_db):
            self.vo_db.setValue(float(p["settings"].get("voice_db", 0.0)))
        self.stack.setCurrentWidget(self.pages["voice"])

    def _load_music(self, p):
        st = p["settings"]
        self.title.setText("Music track (A2)")
        m = p.get("music")
        self.sub.setText(os.path.basename(m) if m else "No music - add it in the Audio tab")
        self.mu_info.setText("" if m else "Choose a music file in the Audio tab first.")
        for w, key, d in ((self.mu_db, "music_db", -20), (self.mu_in, "music_fade_in", 1.5),
                          (self.mu_out, "music_fade_out", 3.0)):
            with QSignalBlocker(w):
                w.setValue(float(st.get(key, d)))
        with QSignalBlocker(self.mu_duck):
            self.mu_duck.setChecked(bool(st.get("music_duck", True)))
        self.stack.setCurrentWidget(self.pages["music"])

    # ================================================================ edits (user signals only)
    def _clip_set(self, key, value, lab, kind):
        it = self.item()
        if it is None or it.get(key) == value:
            return
        it[key] = value
        if key == "locked" and self.kind == "scene":
            if value:
                it["status_before_lock"] = it.get("status")
                it["status"] = "locked"
            else:
                it["status"] = it.pop("status_before_lock", None) or "manual"
        self.changed.emit(lab, kind)
        if kind in ("framing", "lock"):
            self._load_framing(it, self.kind == "scene")

    def _kf_near(self, it, lt):
        for kf in it.get("keyframes") or []:
            if abs(kf["t"] - lt) <= KF_NEAR:
                return kf
        return None

    def _tf_edit(self, key, val):
        it = self.item()
        s, lab, scale, unit = self.tf_w[key]
        lab.setText(f"{val}{unit}")
        if it is None:
            return
        v = round(val / scale, 4)
        if it.get("keyframes"):
            lt = round(self._local_t(it), 3)
            kf = self._kf_near(it, lt)
            if kf is None:
                kf = self._state_kf(it, lt)
                it["keyframes"].append(kf)
                it["keyframes"].sort(key=lambda k: k["t"])
            kf[key] = v
            self._fill_keys(it)
            self.changed.emit("Edit keyframe", "keys")
        else:
            if it.get(key, KEY_DEFAULTS[key]) == v:
                return
            it[key] = v
            self.changed.emit("Clip " + {"zoom": "scale", "x": "position", "y": "position", "rot": "rotation",
                                         "opacity": "opacity"}[key], "tf")

    def _state_kf(self, it, lt):
        tf, op = clip_state(it, lt)
        return {"t": round(lt, 3), "zoom": round(tf[0], 4), "x": round(tf[1], 4), "y": round(tf[2], 4),
                "rot": round(tf[3], 3), "opacity": round(op, 4)}

    def _reset_tf(self):
        it = self.item()
        if it is None:
            return
        before = {k: it.get(k) for k in KEY_PROPS + ("keyframes",)}
        it.update(zoom=1.0, x=0.0, y=0.0, rot=0.0, opacity=1.0)
        it.pop("keyframes", None)
        if before != {k: it.get(k) for k in KEY_PROPS + ("keyframes",)}:
            self.reload()
            self.changed.emit("Reset clip", "keys")

    def _scene_time(self, key, x):
        """Move a cut between two scenes; the narration (and total length) never changes."""
        sc = self.get_project().get("scenes") or []
        k = self.idx
        if self.kind != "scene" or not (0 <= k < len(sc)):
            return
        if key == "start":
            if k == 0:
                return
            a, b = sc[k - 1], sc[k]
        else:
            if k >= len(sc) - 1:
                return
            a, b = sc[k], sc[k + 1]
        x = round(min(max(x, a["start"] + 0.3), b["end"] - 0.3), 3)
        if abs(a["end"] - x) < 1e-6:
            return
        a["end"] = b["start"] = x
        self.reload()
        self.changed.emit("Scene timing", "cut")

    def _broll_time(self, key, x):
        it = self.item()
        if it is None or self.kind != "broll":
            return
        if key == "end" and x <= it["start"] + 0.1:
            x = it["start"] + 0.1
        if key == "start" and x >= it["end"] - 0.1:
            x = it["end"] - 0.1
        if abs(float(it.get(key, 0.25 if key == "fade" else 0)) - x) < 1e-6:
            return
        it[key] = round(x, 3)
        self.changed.emit("V2 clip timing", "timing")

    def _kf_add(self):
        it = self.item()
        if it is None:
            return
        lt = round(self._local_t(it), 3)
        kfs = it.setdefault("keyframes", [])
        kf = self._kf_near(it, lt)
        cur = {k: round(self.tf_w[k][0].value() / self.tf_w[k][2], 4) for k in KEY_PROPS}
        if kf is None:
            kf = {"t": lt}
            kfs.append(kf)
            kfs.sort(key=lambda k: k["t"])
        kf.update(cur)
        self._fill_keys(it)
        self.kf_note.setText(f"This clip has {len(kfs)} keyframe(s): sliders show and edit the value at the playhead.")
        self.changed.emit("Add keyframe", "keys")

    def _selected_kf(self, it):
        li = self.kf_list.currentItem() or (self.kf_list.selectedItems() or [None])[0]
        if li is None:
            return self._kf_near(it, self._local_t(it))
        t = li.data(Qt.UserRole)
        return next((k for k in it.get("keyframes") or [] if abs(k["t"] - t) < 1e-6), None)

    def _kf_delete(self):
        it = self.item()
        if it is None:
            return
        kf = self._selected_kf(it)
        if kf is None:
            return
        it["keyframes"].remove(kf)
        if not it["keyframes"]:
            # keep the look the clip had at that moment as its static value
            for k in KEY_PROPS:
                it[k] = kf.get(k, it.get(k, KEY_DEFAULTS[k]))
            it.pop("keyframes")
        self.reload()
        self.changed.emit("Delete keyframe", "keys")

    def _kf_clear(self):
        it = self.item()
        if it is None or not it.get("keyframes"):
            return
        it.pop("keyframes")
        self.reload()
        self.changed.emit("Clear keyframes", "keys")

    def _kf_jump(self, li):
        it = self.item()
        if it is not None:
            self.seek.emit(it["start"] + float(li.data(Qt.UserRole)))

    def _kf_step(self, d):
        it = self.item()
        if it is None or not it.get("keyframes"):
            return
        lt = self._local_t(it)
        ts = sorted(k["t"] for k in it["keyframes"])
        nxt = [t for t in ts if t > lt + 1e-3] if d > 0 else [t for t in ts if t < lt - 1e-3]
        if nxt:
            self.seek.emit(it["start"] + (nxt[0] if d > 0 else nxt[-1]))

    def _adj_set(self, key, value):
        it = self.item()
        if it is None:
            return
        a = it.setdefault("adjust", {})
        if a.get(key, ADJUST_DEFAULTS[key]) == value:
            return
        a[key] = value
        self.changed.emit("Clip effect: " + key.replace("_", " "), "adjust")

    def _adj_slider(self, key, val):
        s, lab, scale = self.adj_w[key]
        lab.setText(self._adj_text(key, val))
        self._adj_set(key, round(val / scale, 3))

    def _adj_reset(self):
        it = self.item()
        if it is None or not it.get("adjust"):
            return
        it.pop("adjust")
        self.reload()
        self.changed.emit("Reset clip effects", "adjust")

    def _item_set(self, key, value, lab):
        it = self.item()
        if it is None or (it.get(key) or "") == (value or ""):
            return
        it[key] = value
        self.changed.emit(lab, "text")

    def _item_time(self, key, x):
        it = self.item()
        if it is None:
            return
        if key == "end":
            x = max(x, it["start"] + 0.2)
        else:
            x = min(x, it["end"] - 0.2)
        if abs(it[key] - x) < 1e-6:
            return
        it[key] = round(x, 3)
        self.changed.emit("Text card timing", "text")

    def _caption_apply(self):
        from .captions import edit_caption_words
        c = self._caption()
        p = self.get_project()
        new = " ".join(self.c_text.toPlainText().split())
        if c is None or new == c["text"]:
            return
        p["words"] = edit_caption_words(p["words"], c["i0"], c["i1"], new)
        self.changed.emit("Edit caption text", "caption")
        self.reload()

    def _sfx_set(self, key, value, lab):
        it = self.item()
        if it is None or it.get(key) == value:
            return
        it[key] = value
        if key == "t":
            p = self.get_project()
            p["sfx_events"].sort(key=lambda e: e["t"])
            self.idx = p["sfx_events"].index(it)
        self.get_project()["sfx_planned"] = True
        self.changed.emit(lab, "sfx")

    def _setting(self, key, value, lab):
        st = self.get_project()["settings"]
        if st.get(key) == value:
            return
        st[key] = value
        self.changed.emit(lab, "audio")
