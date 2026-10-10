"""Editing Instructions window: a compact scene table + the selected scene's controls.

Every control writes the scene's INSTRUCTION record (intent). "Apply" (or Auto-apply) turns it into the
timeline that preview and export use; each step is one undo step in the main window."""
import copy
import os

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import instructions as I
from .app_parts_base import form, label
from .framing import FIT_MODES
from .util import fmt_time, is_video

STATUS_COLOR = {"ready": "#4CAF50", "review": "#FFB300", "unresolved": "#FF8C42", "missing": "#FF5252",
                "conflict": "#FF5252", "empty": "#9AA0AE", "draft": "#4FA3FF"}
HELP = """Type what you want for this scene, e.g.
Use 003.png when the narrator says “She opened the letter.” Hold it until “She knew what to do next.”
Slowly zoom in. Play paper.wav at “opened,” quietly below the narration. Add no B-roll.
Cut to the next scene. Lock the visual and timing.
Then press Parse, check what was understood, and Apply."""
SUPPORTED = """Understood phrases (one per sentence):
• Use 003.png / image 7 / the third image  when the narrator says “…”  |  from 1:20 to 1:31  |  for 4 seconds
• Hold it until “…”  |  until the next topic  |  for 3 seconds        • Then use 004.png when she says “…” (more visuals)
• Zoom in / zoom out / pan left|right|up|down (slowly, strongly) toward the top left …   • No motion / keep it still
• Insert clip 006.mp4 while the narrator mentions the airport (for 3 s)   • Overlay 006.mp4 in the top right at 30 % from 0:12 to 0:15 with its sound at -20 dB
• Add no B-roll   • Play paper.wav at “opened”, quietly (0.2 s after) / at -18 dB / fade in 0.2 s   • No sound effects
• Black and white / sepia / warm / cold / blur / vignette / darken / brighten / more contrast (for the first 2 s)
• Show the text “Morocco, 15,000 years ago”   • No effects   • Fill the frame / show the whole image / focus on the top left
• Cut to the next scene / dissolve into the next scene over 0.8 s / fade to black   • No captions / captions at the top
• Lock the visual and timing / lock this scene     • Note: … (kept as a note)
Levels are in dB RELATIVE TO THE NARRATION (0 dB = as loud as the voice; -14 dB = clearly quieter)."""


def _mode_combo(modes=("inherit", "auto", "none", "explicit")):
    c = QComboBox()
    for m in modes:
        c.addItem(I.MODE_NAMES[m], m)
    return c


def _spin(lo, hi, step=0.1, suffix=" s", dec=2, special=None):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    if special:
        s.setSpecialValueText(special)
    return s


def _set(w, v):
    with QSignalBlocker(w):
        if isinstance(w, QComboBox):
            i = w.findData(v)
            w.setCurrentIndex(i if i >= 0 else 0)
        elif isinstance(w, (QDoubleSpinBox, QSpinBox)):
            w.setValue(v if v is not None else w.minimum())
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(v))
        elif isinstance(w, QLineEdit):
            w.setText(v or "")
        elif isinstance(w, QPlainTextEdit):
            if w.toPlainText() != (v or ""):
                w.setPlainText(v or "")


class InstructionsWindow(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Editing instructions - AutoEditor")
        self.setModal(False)
        self.resize(min(1400, max(1000, win.width() - 60)), 820)
        self.sid = None
        self.rows = []
        self._b = self._s = self._e = -1           # selected B-roll / SFX / effect
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search narration, file, ID…")
        self.search.textChanged.connect(self.refresh)
        self.filter = QComboBox()
        self.filter.addItems(["All scenes", "Needs attention", "With instructions"])
        self.filter.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.search, 2)
        top.addWidget(self.filter)
        for text, fn, tip in (("Parse all", self.parse_all, "Read every scene's instruction text"),
                              ("Apply valid instructions", self.apply_valid,
                               "Apply every instruction without blocking problems (one undo step)"),
                              ("Check before export", self.validate, "List what would block or need a decision"),
                              ("Import…", self.do_import, "JSON (complete) or CSV (basic) - previewed first"),
                              ("Export JSON…", self.export_json, "All instructions, schema_version 1"),
                              ("CSV template…", self.export_csv, "Editable CSV with one row per scene")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            top.addWidget(b)
        lay.addLayout(top)
        split = QSplitter(Qt.Horizontal)
        # ---- table
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(["#", "ID", "", "Narration", "Start", "End", "Dur", "Status", "🔒",
                                              "Image · settings"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setIconSize(QSize(64, 36))
        for c, wdt in enumerate((30, 50, 70, 170, 64, 64, 44, 104, 24, 320)):
            self.table.setColumnWidth(c, wdt)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.table.setWordWrap(True)
        self.table.itemSelectionChanged.connect(self._row_changed)
        ll.addWidget(self.table, 1)
        rb = QHBoxLayout()
        for text, fn, tip in (("▲ Visual up", lambda: self.move(-1), "Swap this scene's visual with the previous "
                                                                     "scene (timing stays)"),
                              ("▼ Visual down", lambda: self.move(1), "Swap with the next scene's visual"),
                              ("Copy settings to selected…", self.bulk, "Copy chosen fields of the current scene to "
                                                                        "every selected scene")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            rb.addWidget(b)
        ll.addLayout(rb)
        split.addWidget(left)
        # ---- detail
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("Select a scene")
        self.title.setObjectName("PanelTitle")
        self.sub = label("", "Help")
        rl.addWidget(self.title)
        rl.addWidget(self.sub)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for name, fn in (("Instruction", self._tab_text), ("Visual && timing", self._tab_visual),
                         ("Motion && framing", self._tab_motion), ("B-roll", self._tab_broll),
                         ("Sound", self._tab_sfx), ("Effects", self._tab_effects),
                         ("Transition && captions", self._tab_transition), ("Locks && notes", self._tab_locks)):
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setWidget(fn())
            self.tabs.addTab(sc, name)
        rl.addWidget(self.tabs, 3)
        rl.addWidget(label("Problems and choices", "Section"))
        self.issues = QListWidget()
        self.issues.setMinimumHeight(70)
        rl.addWidget(self.issues, 1)
        self.choice_box = QWidget()
        self.choice_lay = QVBoxLayout(self.choice_box)
        self.choice_lay.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.choice_box)
        bb = QHBoxLayout()
        self.auto = QCheckBox("Apply my edits right away")
        self.auto.setChecked(True)
        self.auto.setToolTip("Off: edits stay as a draft until you press Apply")
        self.b_apply = QPushButton("Apply to timeline")
        self.b_apply.setProperty("primary", True)
        self.b_apply.clicked.connect(lambda: self.apply_current(reviewed=True))
        self.b_accept = QPushButton("Accept warnings")
        self.b_accept.setToolTip("Keep the fallback the warning describes (recorded with the scene)")
        self.b_accept.clicked.connect(self.accept_warnings)
        self.b_play = QPushButton("▶ Preview scene")
        self.b_play.setToolTip("Plays this scene with its narration in the main preview")
        self.b_play.clicked.connect(self.preview)
        bb.addWidget(self.auto)
        bb.addStretch(1)
        for b in (self.b_accept, self.b_play, self.b_apply):
            bb.addWidget(b)
        rl.addLayout(bb)
        split.addWidget(right)
        split.setSizes([800, 600])
        lay.addWidget(split, 1)
        self._stop_at = None
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._watch)
        self.audition_player = None

    # ================================================================ tabs
    def _tab_text(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(HELP)
        self.text.setMinimumHeight(110)
        self.text.textChanged.connect(self._text_changed)
        lay.addWidget(self.text)
        r = QHBoxLayout()
        b = QPushButton("Parse")
        b.setToolTip("Turn the text into settings (shown below and on the other tabs). Nothing changes on the "
                     "timeline until you Apply.")
        b.clicked.connect(self.parse_current)
        r.addWidget(b)
        r.addStretch(1)
        lay.addLayout(r)
        lay.addWidget(label("Understood as", "Section"))
        self.interp = QTableWidget(0, 2)
        self.interp.setHorizontalHeaderLabels(["Your words", "Interpreted setting"])
        self.interp.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.interp.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.interp.verticalHeader().setVisible(False)
        self.interp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.interp.setWordWrap(True)
        self.interp.setMinimumHeight(140)
        lay.addWidget(self.interp, 1)
        more = QPushButton("Supported phrases ▸")
        more.setCheckable(True)
        sup = label(SUPPORTED, "Help")
        sup.setVisible(False)
        more.toggled.connect(sup.setVisible)
        lay.addWidget(more)
        lay.addWidget(sup)
        lay.addWidget(label("Parsing is rule-based and runs on this PC. No language model or online service is "
                            "used; phrases it doesn't know are listed as not understood and never applied.", "Help"))
        return w

    def _tab_visual(self):
        w = QWidget()
        f = form()
        self.vis = QComboBox()
        self.vis.activated.connect(self._vis_changed)
        f.addRow("Primary visual", self.vis)
        self.tmode = QComboBox()
        for k, v in I.TIMING_MODES.items():
            self.tmode.addItem(v, k)
        self.tmode.activated.connect(lambda _i: self._setting("timing_mode", self.tmode.currentData(), "Timing mode"))
        f.addRow("Timing", self.tmode)
        self.anchor = QLineEdit()
        self.anchor.setPlaceholderText("words where this scene starts")
        self.anchor.editingFinished.connect(lambda: self._anchor_changed("narration_anchor", self.anchor, self.occ))
        self.occ = QSpinBox()
        self.occ.setRange(0, 99)
        self.occ.setSpecialValueText("ask if repeated")
        self.occ.setPrefix("occurrence ")
        self.occ.valueChanged.connect(lambda _v: self._anchor_changed("narration_anchor", self.anchor, self.occ))
        r = QHBoxLayout()
        r.addWidget(self.anchor, 1)
        r.addWidget(self.occ)
        f.addRow("Starts at the words", r)
        self.end_anchor = QLineEdit()
        self.end_anchor.setPlaceholderText("words it holds until (optional)")
        self.end_anchor.editingFinished.connect(lambda: self._anchor_changed("end_anchor", self.end_anchor, self.eocc))
        self.eocc = QSpinBox()
        self.eocc.setRange(0, 99)
        self.eocc.setSpecialValueText("ask if repeated")
        self.eocc.setPrefix("occurrence ")
        self.eocc.valueChanged.connect(lambda _v: self._anchor_changed("end_anchor", self.end_anchor, self.eocc))
        r = QHBoxLayout()
        r.addWidget(self.end_anchor, 1)
        r.addWidget(self.eocc)
        f.addRow("Holds until the words", r)
        self.st = _spin(0, 36000, 0.05)
        self.en = _spin(0, 36000, 0.05)
        self.du = _spin(0, 36000, 0.05)
        self.off = _spin(-60, 60, 0.05)
        self.st.valueChanged.connect(lambda v: self._time_typed("start", v))
        self.en.valueChanged.connect(lambda v: self._time_typed("end", v))
        self.du.valueChanged.connect(lambda v: self._time_typed("dur", v))
        self.off.valueChanged.connect(lambda v: self._setting("offset_seconds", round(v, 3), "Offset"))
        f.addRow("Start", self.st)
        f.addRow("End", self.en)
        f.addRow("Duration", self.du)
        f.addRow("Offset from the anchor", self.off)
        self.time_note = label("Typing a start, end or duration switches the timing to Absolute time; the other "
                               "two values follow (end = start + duration).", "Help")
        f.addRow("", self.time_note)
        self.cin = _spin(0, 36000, 0.1, special="clip start")
        self.cout = _spin(0, 36000, 0.1, special="clip end")
        self.cin.valueChanged.connect(lambda v: self._setting("clip_in", round(v, 3) or None, "Clip in"))
        self.cout.valueChanged.connect(lambda v: self._setting("clip_out", round(v, 3) or None, "Clip out"))
        f.addRow("Source clip in", self.cin)
        f.addRow("Source clip out", self.cout)
        self.seg_list = QListWidget()
        self.seg_list.setMaximumHeight(90)
        f.addRow("More visuals in this scene", self.seg_list)
        f.addRow("", label("Add more visuals with 'Then use 004.png when the narrator says “…”' on the Instruction "
                           "tab; each starts where its words are spoken.", "Help"))
        w.setLayout(f)
        return w

    def _tab_motion(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        f = form()
        self.mmode = _mode_combo()
        self.mmode.activated.connect(self._motion_changed)
        self.mtype = QComboBox()
        for m in I.MOTION_TYPES:
            self.mtype.addItem(m.replace("_", " ").replace("+", " + "), m)
        self.mtype.activated.connect(self._motion_changed)
        self.mstr = QComboBox()
        for k in I.MOTION_STRENGTH:
            self.mstr.addItem(k.capitalize(), k)
        self.mstr.activated.connect(self._motion_changed)
        self.mease = QComboBox()
        for k, v in I.EASES.items():
            self.mease.addItem(v, k)
        self.mease.activated.connect(self._motion_changed)
        f.addRow("Motion", self.mmode)
        f.addRow("Type", self.mtype)
        f.addRow("Strength", self.mstr)
        f.addRow("Easing", self.mease)
        self.fmode = _mode_combo(("inherit", "explicit"))
        self.fmode.activated.connect(self._framing_changed)
        self.fit = QComboBox()
        for k in ("crop", "fit_blur", "fit_color", "fit_bars"):
            self.fit.addItem(FIT_MODES[k], k)
        self.fit.activated.connect(self._framing_changed)
        self.fx = _spin(0, 100, 5, " %", 0)
        self.fy = _spin(0, 100, 5, " %", 0)
        self.fx.valueChanged.connect(self._framing_changed)
        self.fy.valueChanged.connect(self._framing_changed)
        f.addRow("Framing", self.fmode)
        f.addRow("Mode", self.fit)
        f.addRow("Focal point ↔", self.fx)
        f.addRow("Focal point ↕", self.fy)
        lay.addLayout(f)
        lay.addWidget(label("Motion always stays inside the picture (no empty edges) and never stretches it. "
                            "AutoEditor can't recognise faces or objects - put the focal point on what matters.",
                            "Help"))
        b = QPushButton("Show start and end frames")
        b.clicked.connect(self.show_frames)
        lay.addWidget(b)
        r = QHBoxLayout()
        self.f_start, self.f_end = QLabel("start"), QLabel("end")
        for l_ in (self.f_start, self.f_end):
            l_.setMinimumSize(240, 135)
            l_.setAlignment(Qt.AlignCenter)
            l_.setStyleSheet("background:#111; border:1px solid #333;")
            r.addWidget(l_)
        lay.addLayout(r)
        lay.addStretch(1)
        return w

    def _list_tab(self, kind, title):
        w = QWidget()
        lay = QVBoxLayout(w)
        f = form()
        mode = _mode_combo(("inherit", "auto", "none", "explicit") if kind == "sfx" else
                           ("inherit", "none", "explicit"))
        mode.activated.connect(lambda _i, k=kind, m=mode: self._list_mode(k, m.currentData()))
        f.addRow(title, mode)
        lay.addLayout(f)
        lst = QListWidget()
        lst.setMaximumHeight(110)
        lst.currentRowChanged.connect(lambda r, k=kind: self._list_pick(k, r))
        lay.addWidget(lst)
        r = QHBoxLayout()
        add, rem = QPushButton("Add"), QPushButton("Remove")
        add.clicked.connect(lambda _c=False, k=kind: self._list_add(k))
        rem.clicked.connect(lambda _c=False, k=kind: self._list_remove(k))
        r.addWidget(add)
        r.addWidget(rem)
        r.addStretch(1)
        lay.addLayout(r)
        return w, lay, mode, lst

    def _tab_broll(self):
        w, lay, self.bmode, self.blist = self._list_tab("b_roll", "B-roll")
        f = form()
        self.b_asset = QComboBox()
        self.b_asset.activated.connect(lambda _i: self._item_set("b_roll", "asset_filename", self.b_asset.currentData()))
        self.b_kind = QComboBox()
        self.b_kind.addItem("Full-frame insert", "insert")
        self.b_kind.addItem("Overlay (picture in picture)", "overlay")
        self.b_kind.activated.connect(lambda _i: self._item_set("b_roll", "mode", self.b_kind.currentData()))
        self.b_anchor = QLineEdit()
        self.b_anchor.setPlaceholderText("starts when these words are spoken (or use times below)")
        self.b_anchor.editingFinished.connect(lambda: self._item_set(
            "b_roll", "anchor", {"text": self.b_anchor.text().strip(), "occurrence": None}
            if self.b_anchor.text().strip() else None))
        self.b_st, self.b_en, self.b_du = _spin(0, 36000, 0.1, special="—"), _spin(0, 36000, 0.1, special="—"), \
            _spin(0, 600, 0.1, special="until the sentence ends")
        self.b_st.valueChanged.connect(lambda v: self._item_set("b_roll", "start_seconds", v or None))
        self.b_en.valueChanged.connect(lambda v: self._item_set("b_roll", "end_seconds", v or None))
        self.b_du.valueChanged.connect(lambda v: self._item_set("b_roll", "duration_seconds", v or None))
        self.b_in, self.b_out = _spin(0, 36000, 0.1, special="clip start"), _spin(0, 36000, 0.1, special="clip end")
        self.b_in.valueChanged.connect(lambda v: self._item_set("b_roll", "clip_in", v or None))
        self.b_out.valueChanged.connect(lambda v: self._item_set("b_roll", "clip_out", v or None))
        self.b_size = _spin(5, 100, 5, " %", 0)
        self.b_size.valueChanged.connect(lambda v: self._item_set("b_roll", "size", round(v / 100, 3)))
        self.b_pos = QComboBox()
        for k in I.POSITIONS:
            self.b_pos.addItem(k.replace("_", " "), k)
        self.b_pos.activated.connect(lambda _i: self._item_set("b_roll", "position", self.b_pos.currentData()))
        self.b_snd = QCheckBox("Play the clip's own sound")
        self.b_gain = _spin(-60, 12, 1, " dB", 1)
        self.b_snd.toggled.connect(lambda c: self._item_set("b_roll", "audio_gain_db",
                                                            self.b_gain.value() if c else None))
        self.b_gain.valueChanged.connect(lambda v: self._item_set("b_roll", "audio_gain_db", v)
                                         if self.b_snd.isChecked() else None)
        self.b_short = QComboBox()
        for t, v in (("Ask me", None), ("Freeze the last frame", "freeze"), ("Loop the clip (approved)", "loop")):
            self.b_short.addItem(t, v)
        self.b_short.activated.connect(lambda _i: self._item_set("b_roll", "short", self.b_short.currentData()))
        for name, wd in (("File", self.b_asset), ("Kind", self.b_kind), ("Starts at the words", self.b_anchor),
                         ("Start (absolute)", self.b_st), ("End (absolute)", self.b_en), ("Duration", self.b_du),
                         ("Source clip in", self.b_in), ("Source clip out", self.b_out), ("Overlay size", self.b_size),
                         ("Overlay position", self.b_pos), ("", self.b_snd), ("Clip sound level", self.b_gain),
                         ("If the clip is too short", self.b_short)):
            f.addRow(name, wd)
        lay.addLayout(f)
        lay.addWidget(label("B-roll never changes the narration. Its own sound is muted unless you switch it on "
                            "(level in dB applied to the clip's sound). Missing B-roll is reported - nothing "
                            "unrelated is put in its place.", "Help"))
        lay.addStretch(1)
        self.b_form = [self.b_asset, self.b_kind, self.b_anchor, self.b_st, self.b_en, self.b_du, self.b_in,
                       self.b_out, self.b_size, self.b_pos, self.b_snd, self.b_gain, self.b_short]
        return w

    def _tab_sfx(self):
        w, lay, self.smode, self.slist = self._list_tab("sfx", "Sound effects")
        f = form()
        self.s_asset = QComboBox()
        self.s_asset.activated.connect(lambda _i: self._item_set("sfx", "asset_filename", self.s_asset.currentData()))
        r = QHBoxLayout()
        r.addWidget(self.s_asset, 1)
        bb = QPushButton("Browse…")
        bb.clicked.connect(self.browse_sfx)
        self.b_aud = QPushButton("▶ Audition")
        self.b_aud.clicked.connect(self.audition)
        r.addWidget(bb)
        r.addWidget(self.b_aud)
        self.s_anchor = QLineEdit()
        self.s_anchor.setPlaceholderText("the word it plays on, e.g. opened")
        self.s_anchor.editingFinished.connect(self._sfx_anchor)
        self.s_occ = QSpinBox()
        self.s_occ.setRange(0, 99)
        self.s_occ.setSpecialValueText("ask if repeated")
        self.s_occ.setPrefix("in scene, occurrence ")
        self.s_occ.valueChanged.connect(lambda _v: self._sfx_anchor())
        self.s_at = _spin(0, 36000, 0.05, special="— (use the words)")
        self.s_at.valueChanged.connect(lambda v: self._item_set("sfx", "start_seconds", v or None))
        self.s_off = _spin(-10, 10, 0.05)
        self.s_off.valueChanged.connect(lambda v: self._item_set("sfx", "offset_seconds", round(v, 3)))
        self.s_gain = _spin(-60, 12, 1, " dB", 1)
        self.s_gain.setToolTip("Relative to the narration: 0 dB = the effect's loud part as loud as the voice's "
                               "loud parts; -14 dB = clearly quieter.")
        self.s_gain.valueChanged.connect(lambda v: self._item_set("sfx", "gain_db", v))
        self.s_fi, self.s_fo = _spin(0, 10, 0.05), _spin(0, 10, 0.05)
        self.s_fi.valueChanged.connect(lambda v: self._item_set("sfx", "fade_in", v))
        self.s_fo.valueChanged.connect(lambda v: self._item_set("sfx", "fade_out", v))
        self.s_trim = _spin(0, 600, 0.05)
        self.s_trim.valueChanged.connect(lambda v: self._item_set("sfx", "trim_in", v))
        self.s_dur = _spin(0, 600, 0.05, special="whole file")
        self.s_dur.valueChanged.connect(lambda v: self._item_set("sfx", "duration", v or None))
        self.s_pan = _spin(-1, 1, 0.1, "", 1)
        self.s_pan.valueChanged.connect(lambda v: self._item_set("sfx", "pan", v))
        oc = QHBoxLayout()
        oc.addWidget(self.s_anchor, 1)
        oc.addWidget(self.s_occ)
        for name, wd in (("File", r), ("Plays on the words", oc), ("Or at time", self.s_at), ("Offset", self.s_off),
                         ("Level (vs narration)", self.s_gain), ("Fade in", self.s_fi), ("Fade out", self.s_fo),
                         ("Skip start of file", self.s_trim), ("Length used", self.s_dur),
                         ("Pan (-1 left … +1 right)", self.s_pan)):
            f.addRow(name, wd)
        lay.addLayout(f)
        lay.addWidget(label("Automatic = AutoEditor proposes effects for review (Sound effects tab); None = no "
                            "effects in this scene; Inherit = the project guideline. Only files on this PC are used.",
                            "Help"))
        lay.addStretch(1)
        self.s_form = [self.s_asset, self.s_anchor, self.s_occ, self.s_at, self.s_off, self.s_gain, self.s_fi,
                       self.s_fo, self.s_trim, self.s_dur, self.s_pan, self.b_aud]
        return w

    def _tab_effects(self):
        w, lay, self.emode, self.elist = self._list_tab("effects", "Effects")
        f = form()
        self.e_type = QComboBox()
        for k, v in I.EFFECTS.items():
            self.e_type.addItem(v["label"], k)
        self.e_type.activated.connect(self._effect_type)
        self.e_amt = _spin(-1, 10, 0.05, "", 2)
        self.e_amt.valueChanged.connect(lambda v: self._effect_param("amount", round(v, 3)))
        self.e_text = QLineEdit()
        self.e_text.editingFinished.connect(lambda: self._effect_param("text", self.e_text.text()))
        self.e_st = _spin(-600, 600, 0.1)
        self.e_en = _spin(-600, 600, 0.1, special="scene end")
        self.e_st.valueChanged.connect(lambda v: self._item_set("effects", "start_offset", v))
        self.e_en.valueChanged.connect(lambda v: self._item_set("effects", "end_offset", v if v != -600 else None))
        self.e_en.setMinimum(-600)
        for name, wd in (("Effect", self.e_type), ("Amount", self.e_amt), ("Text", self.e_text),
                         ("Starts (s into the scene; negative = before its end)", self.e_st),
                         ("Ends", self.e_en)):
            f.addRow(name, wd)
        lay.addLayout(f)
        lay.addWidget(label("Only effects the renderer really draws are offered. Nothing is added automatically.",
                            "Help"))
        lay.addStretch(1)
        self.e_form = [self.e_type, self.e_amt, self.e_text, self.e_st, self.e_en]
        return w

    def _tab_transition(self):
        w = QWidget()
        f = form()
        self.tr_mode = _mode_combo(("inherit", "none", "explicit"))
        self.tr_mode.setItemText(1, "None (cut)")
        self.tr_mode.activated.connect(self._transition_changed)
        self.tr_type = QComboBox()
        for k, v in I.TRANSITIONS.items():
            self.tr_type.addItem(v, k)
        self.tr_type.activated.connect(self._transition_changed)
        self.tr_dur = _spin(0, 5, 0.05, special="default")
        self.tr_dur.valueChanged.connect(self._transition_changed)
        self.tr_note = label("", "Help")
        f.addRow("Transition to the next scene", self.tr_mode)
        f.addRow("Type", self.tr_type)
        f.addRow("Duration", self.tr_dur)
        f.addRow("", self.tr_note)
        self.cap_mode = QComboBox()
        for t, v in (("Inherit (project captions)", "inherit"), ("Off in this scene", "none"),
                     ("On in this scene", "explicit")):
            self.cap_mode.addItem(t, v)
        self.cap_mode.activated.connect(self._captions_changed)
        self.cap_pos = QComboBox()
        for t in ("(project)", "Top", "Middle", "Bottom"):
            self.cap_pos.addItem(t, None if t.startswith("(") else t)
        self.cap_pos.activated.connect(self._captions_changed)
        f.addRow("Captions", self.cap_mode)
        f.addRow("Position", self.cap_pos)
        f.addRow("", label("A transition belongs to ONE boundary (this scene → the next), centred on the cut, so the "
                           "narration and total length never change.", "Help"))
        w.setLayout(f)
        return w

    def _tab_locks(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lock_all = QCheckBox("Lock the whole scene")
        self.lock_all.toggled.connect(lambda c: self._setting("locked", bool(c), "Lock scene"))
        lay.addWidget(self.lock_all)
        lay.addWidget(label("Or lock single fields (re-planning and automatic choices never change them):", "Help"))
        self.lock_cb = {}
        for k in I.FIELDS:
            cb = QCheckBox(I.FIELD_NAMES[k])
            cb.toggled.connect(lambda c, k=k: self._lock_field(k, c))
            self.lock_cb[k] = cb
            lay.addWidget(cb)
        lay.addWidget(label("Locks don't hide problems: a locked scene whose narration moved is still flagged for "
                            "review.", "Help"))
        lay.addWidget(label("Scene notes", "Section"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes for you (kept with the scene; not interpreted)")
        self.notes.setMaximumHeight(110)
        self._ntimer = QTimer(self)
        self._ntimer.setSingleShot(True)
        self._ntimer.setInterval(700)
        self._ntimer.timeout.connect(self._notes_save)
        self.notes.textChanged.connect(self._ntimer.start)
        lay.addWidget(self.notes)
        lay.addStretch(1)
        return w

    # ================================================================ data
    @property
    def p(self):
        return self.win.project

    def rec(self):
        return I.record(self.p, self.sid) if self.sid else None

    def refresh(self, *_):
        p = self.p
        scenes = p.get("scenes") or []
        q = self.search.text().strip().lower()
        f = self.filter.currentIndex()
        rows = []
        on_tl = set()
        for k, s in enumerate(scenes):
            sid = s.get("id") or ""
            on_tl.add(sid)
            st = I.scene_status(p, s)
            if q and q not in (s.get("text") or "").lower() and q not in os.path.basename(s.get("media") or "").lower() \
                    and q not in sid.lower():
                continue
            if f == 1 and st in ("ready", "empty"):
                continue
            if f == 2 and st == "empty":
                continue
            rows.append(("scene", k, sid))
        for sid, rec in I.records(p).items():
            if sid not in on_tl and I.has_content(rec):
                rows.append(("record", -1, sid))
        self.rows = rows
        from .media_bin import loader
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(rows))
            for r, (kind, k, sid) in enumerate(rows):
                if kind == "scene":
                    s = scenes[k]
                    st = I.scene_status(p, s)
                    lk = "🔒" if s.get("locked") else ("◐" if s.get("locked_fields") else "")
                    cells = [str(k + 1), sid, "", (s.get("text") or "")[:160], fmt_time(s["start"]), fmt_time(s["end"]),
                             f"{s['end'] - s['start']:.2f}", I.STATUS_NAMES.get(st, st), lk, self._summary(s)]
                    pm = loader().thumb(s.get("media"), 64, 36)
                else:
                    rec = I.records(p)[sid]
                    st = rec.get("status") or "draft"
                    cells = ["—", sid, "", "(not on the timeline yet) " + rec.get("instruction_text", "")[:120], "", "",
                             "", I.STATUS_NAMES.get(st, st), "", ""]
                    pm = None
                for c, txt in enumerate(cells):
                    it = QTableWidgetItem(txt)
                    if c == 7:
                        it.setForeground(QColor(STATUS_COLOR.get(st, "#D5D9E4")))
                    if c in (3, 9):
                        it.setToolTip(txt)
                    self.table.setItem(r, c, it)
                if pm is not None:
                    self.table.item(r, 2).setIcon(pm)
            self.table.resizeRowsToContents()
            for r in range(self.table.rowCount()):
                self.table.setRowHeight(r, max(44, self.table.rowHeight(r)))
            ids = [r[2] for r in rows]
            if self.sid in ids:
                self.table.selectRow(ids.index(self.sid))
        if self.sid is None and rows:
            self.table.selectRow(0)
            self._row_changed()
        else:
            self.load()

    def _summary(self, s):
        """What the timeline will really do for this scene (the resolved settings)."""
        from .framing import FIT_MODES as FM
        p = self.p
        sid = s.get("id")
        parts = [os.path.basename(s.get("media") or "") or "(no image)"]
        fit = s.get("fit") or p["settings"].get("fit", "crop")
        parts.append(FM.get(fit, fit).split(" (")[0])
        if s.get("focus"):
            parts.append(f"focus {s['focus'][0]:.0%}/{s['focus'][1]:.0%}")
        m = s.get("motion") or "none"
        parts.append("no motion" if m in ("none", "") or is_video(s.get("media") or "") and m == "none" else
                     m.replace("_", " ").replace("+", " + "))
        for b in p.get("broll") or []:
            if b.get("source") == sid:
                parts.append(f"B-roll {os.path.basename(b['media'])} {fmt_time(b['start'])}-{fmt_time(b['end'])}")
        for e in p.get("sfx_events") or []:
            if e.get("source") == sid:
                parts.append(f"SFX {os.path.basename(e['file'])} @{fmt_time(e['t'])} {float(e.get('rel_db', -10)):+.0f} dB")
        for e in s.get("effects") or []:
            parts.append(I.EFFECTS.get(e.get("type"), {}).get("label", e.get("type")))
        tr = s.get("transition", "cut")
        parts.append("→ " + I.TRANSITIONS.get(tr, tr) + (f" {float(s['transition_s']):.2f}s" if s.get("transition_s")
                                                       and tr != "cut" else ""))
        if s.get("captions") == "off":
            parts.append("no captions")
        return " · ".join(parts)

    def _row_changed(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        r = sel[0].row()
        if r >= len(self.rows):
            return
        kind, k, sid = self.rows[r]
        self.sid = sid
        if kind == "scene":
            self.win.select_scene(k)
        self.load()

    def scene(self):
        for k, s in enumerate(self.p.get("scenes") or []):
            if s.get("id") == self.sid:
                return k, s
        return None, None

    # ================================================================ load the selected scene into the controls
    def load(self):
        rec = self.rec()
        if rec is None:
            return
        s = rec["settings"]
        k, sc = self.scene()
        self.title.setText(f"Scene {k + 1} · {self.sid}" if sc else f"{self.sid} (not on the timeline yet)")
        self.sub.setText((f"{fmt_time(sc['start'])}–{fmt_time(sc['end'])} · {os.path.basename(sc.get('media') or '')}"
                          f" · “{(sc.get('text') or '')[:140]}”") if sc else "Give it an anchor (words) or times, "
                                                                            "then Apply.")
        _set(self.text, rec.get("instruction_text", ""))
        self.interp.setRowCount(0)
        for c, m in rec.get("interpretation") or []:
            self._interp_row(c, m, False)
        for c in rec.get("unresolved") or []:
            self._interp_row(c, "NOT understood - not applied", True)
        # visual & timing
        media = self._media()
        with QSignalBlocker(self.vis):
            self.vis.clear()
            self.vis.addItem("(keep / automatic)", None)
            for m in media:
                self.vis.addItem(os.path.relpath(m, self.p.get("media_dir") or os.path.dirname(m))
                                 if self.p.get("media_dir") and m.startswith(self.p["media_dir"]) else os.path.basename(m),
                                 os.path.basename(m))
            ref = (s.get("primary_visual") or {}).get("asset_filename")
            i = self.vis.findData(ref) if ref else 0
            if ref and i < 0:
                self.vis.addItem(f"{ref} (missing)", ref)
                i = self.vis.count() - 1
            self.vis.setCurrentIndex(max(0, i))
        _set(self.tmode, s.get("timing_mode") or "follow_narration")
        na, ea = s.get("narration_anchor") or {}, s.get("end_anchor") or {}
        _set(self.anchor, na.get("text"))
        _set(self.occ, na.get("occurrence") or 0)
        _set(self.end_anchor, ea.get("text"))
        _set(self.eocc, ea.get("occurrence") or 0)
        _set(self.st, s.get("start_seconds") if s.get("start_seconds") is not None else (sc["start"] if sc else 0))
        _set(self.en, s.get("end_seconds") if s.get("end_seconds") is not None else (sc["end"] if sc else 0))
        a, b = self.st.value(), self.en.value()
        _set(self.du, s.get("duration_seconds") or max(0.0, b - a))
        _set(self.off, s.get("offset_seconds") or 0)
        vid = bool(sc and is_video(sc.get("media") or ""))
        for wd in (self.cin, self.cout):
            wd.setEnabled(vid)
        _set(self.cin, s.get("clip_in") or 0)
        _set(self.cout, s.get("clip_out") or 0)
        self.seg_list.clear()
        for sg in s.get("segments") or []:
            self.seg_list.addItem(I._describe_visual(sg))
        # motion & framing
        mo = s.get("motion") or {"mode": "inherit"}
        _set(self.mmode, mo.get("mode", "inherit"))
        _set(self.mtype, mo.get("type", "zoom_in"))
        _set(self.mstr, mo.get("strength", "medium"))
        _set(self.mease, mo.get("ease", "smooth"))
        on = mo.get("mode") == "explicit"
        for wd in (self.mtype, self.mstr, self.mease):
            wd.setEnabled(on)
        fr = s.get("framing") or {"mode": "inherit"}
        _set(self.fmode, fr.get("mode", "inherit"))
        _set(self.fit, fr.get("fit", "crop"))
        fx, fy = fr.get("focus") or (sc.get("focus") if sc and sc.get("focus") else (0.5, 0.5))
        _set(self.fx, round(fx * 100))
        _set(self.fy, round(fy * 100))
        for wd in (self.fit, self.fx, self.fy):
            wd.setEnabled(fr.get("mode") == "explicit")
        # lists
        _set(self.bmode, s.get("b_roll_mode", "inherit"))
        _set(self.smode, s.get("sfx_mode", "inherit"))
        _set(self.emode, s.get("effects_mode", "inherit"))
        self._fill_list("b_roll")
        self._fill_list("sfx")
        self._fill_list("effects")
        # transition & captions
        tr = s.get("transition_out") or {"mode": "inherit"}
        _set(self.tr_mode, tr.get("mode", "inherit"))
        _set(self.tr_type, tr.get("type", "crossfade"))
        _set(self.tr_dur, tr.get("duration_seconds") or 0)
        for wd in (self.tr_type, self.tr_dur):
            wd.setEnabled(tr.get("mode") == "explicit")
        self.tr_note.setText(self._transition_note(sc, k))
        cp = s.get("captions") or {"mode": "inherit"}
        _set(self.cap_mode, cp.get("mode", "inherit"))
        _set(self.cap_pos, cp.get("position"))
        # locks & notes
        _set(self.lock_all, s.get("locked"))
        for f_, cb in self.lock_cb.items():
            _set(cb, f_ in (s.get("locked_fields") or []))
        _set(self.notes, rec.get("notes"))
        self._show_issues(rec)

    def _interp_row(self, c, m, bad):
        r = self.interp.rowCount()
        self.interp.insertRow(r)
        a, b = QTableWidgetItem(c), QTableWidgetItem(m)
        if bad:
            for it in (a, b):
                it.setForeground(QColor("#FF8C42"))
        self.interp.setItem(r, 0, a)
        self.interp.setItem(r, 1, b)
        self.interp.resizeRowToContents(r)

    def _media(self):
        from .project import project_media
        return project_media(self.p)

    def _transition_note(self, sc, k):
        if sc is None:
            return ""
        scenes = self.p["scenes"]
        if k >= len(scenes) - 1:
            return "The last scene has no transition after it."
        from .engine import timeline_windows
        from .presets import PRESETS
        st = self.p["settings"]
        pre = dict(PRESETS.get(st.get("preset"), PRESETS["Documentary"]))
        if st.get("transition_seconds"):
            pre["transition_duration"] = float(st["transition_seconds"])
        td, _ = timeline_windows(scenes[k:k + 2], pre, scenes[k + 1]["end"])
        d = td[0] if td else 0
        fps = int(st.get("fps", 30))
        if d <= 0:
            return "Cut: no overlap."
        want = sc.get("transition_s")
        clamp = f" (asked {float(want):.2f} s - clamped to fit the scenes)" if want and float(want) > d + 1e-3 else ""
        return f"{I.TRANSITIONS.get(sc.get('transition'), sc.get('transition'))}: overlap {d:.2f} s = {round(d * fps)} " \
               f"frames, centred on the cut at {fmt_time(sc['end'])}{clamp}."

    def _show_issues(self, rec):
        self.issues.clear()
        while self.choice_lay.count():
            it = self.choice_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        st = rec.get("status") or I._status(rec)
        self.issues.addItem(f"Status: {I.STATUS_NAMES.get(st, st)}" + (" · applied" if rec.get("applied") else
                                                                       " · not applied yet"))
        for i in rec.get("issues") or []:
            accepted = i.get("accept") and i["msg"] in rec.get("accepted", [])
            it = QListWidgetItem(("✓ accepted: " if accepted else
                                  {"warning": "⚠ ", "choice": "? ", "missing": "✖ ", "conflict": "✖ ",
                                   "error": "✖ "}.get(i["level"], "")) + i["msg"])
            it.setForeground(QColor("#9AA0AE" if accepted else "#FFB300" if i["level"] in ("warning", "choice")
                                    else "#FF5252"))
            self.issues.addItem(it)
            if i["level"] == "choice":
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.addWidget(QLabel(i["msg"].split(".")[0] + ":"))
                cb = QComboBox()
                cb.addItem("Choose…", None)
                for n, o in enumerate(i["options"]):
                    cb.addItem(o, n)
                cb.activated.connect(lambda _i, key=i["key"], c=cb: self._choose(key, c.currentData()))
                rl.addWidget(cb, 1)
                self.choice_lay.addWidget(row)
        self.b_accept.setEnabled(any(i.get("accept") and i["msg"] not in rec.get("accepted", [])
                                     for i in rec.get("issues") or []) or bool(rec.get("unresolved")))

    def _fill_list(self, kind):
        rec = self.rec()
        lst = {"b_roll": self.blist, "sfx": self.slist, "effects": self.elist}[kind]
        items = rec["settings"].get(kind) or []
        cur = {"b_roll": self._b, "sfx": self._s, "effects": self._e}[kind]
        with QSignalBlocker(lst):
            lst.clear()
            for x in items:
                lst.addItem({"b_roll": I._describe_broll, "sfx": I._describe_sfx,
                             "effects": lambda e: I._describe_effect(e) if e.get("type") in I.EFFECTS else
                             str(e.get("type"))}[kind](x))
            if items:
                lst.setCurrentRow(min(max(cur, 0), len(items) - 1))
        self._list_pick(kind, lst.currentRow())

    def _list_pick(self, kind, r):
        rec = self.rec()
        items = rec["settings"].get(kind) or []
        has = 0 <= r < len(items)
        if kind == "b_roll":
            self._b = r
            for wd in self.b_form:
                wd.setEnabled(has)
            if has:
                b = items[r]
                with QSignalBlocker(self.b_asset):
                    self.b_asset.clear()
                    for m in self._media():
                        self.b_asset.addItem(os.path.basename(m), os.path.basename(m))
                    i = self.b_asset.findData(b.get("asset_filename"))
                    if i < 0:
                        self.b_asset.addItem(f"{b.get('asset_filename')} (missing)", b.get("asset_filename"))
                        i = self.b_asset.count() - 1
                    self.b_asset.setCurrentIndex(i)
                _set(self.b_kind, b.get("mode", "insert"))
                _set(self.b_anchor, (b.get("anchor") or {}).get("text"))
                _set(self.b_st, b.get("start_seconds") or 0)
                _set(self.b_en, b.get("end_seconds") or 0)
                _set(self.b_du, b.get("duration_seconds") or 0)
                _set(self.b_in, b.get("clip_in") or 0)
                _set(self.b_out, b.get("clip_out") or 0)
                _set(self.b_size, round(float(b.get("size") or 0.35) * 100))
                _set(self.b_pos, b.get("position", "top_right"))
                _set(self.b_snd, b.get("audio_gain_db") is not None)
                _set(self.b_gain, b.get("audio_gain_db") if b.get("audio_gain_db") is not None else -12)
                _set(self.b_short, b.get("short"))
        elif kind == "sfx":
            self._s = r
            for wd in self.s_form:
                wd.setEnabled(has)
            if has:
                e = items[r]
                with QSignalBlocker(self.s_asset):
                    self.s_asset.clear()
                    for f in I.sfx_files(self.p):
                        self.s_asset.addItem(os.path.basename(f), os.path.basename(f))
                        self.s_asset.setItemData(self.s_asset.count() - 1, f, Qt.ToolTipRole)
                    i = self.s_asset.findData(e.get("asset_filename"))
                    if i < 0:
                        self.s_asset.addItem(f"{e.get('asset_filename')} (missing)", e.get("asset_filename"))
                        i = self.s_asset.count() - 1
                    self.s_asset.setCurrentIndex(i)
                _set(self.s_anchor, (e.get("anchor") or {}).get("text"))
                _set(self.s_occ, (e.get("anchor") or {}).get("occurrence_in_scene") or 0)
                _set(self.s_at, e.get("start_seconds") or 0)
                _set(self.s_off, e.get("offset_seconds") or 0)
                _set(self.s_gain, e.get("gain_db") if e.get("gain_db") is not None else -10)
                _set(self.s_fi, e.get("fade_in") or 0)
                _set(self.s_fo, e.get("fade_out") or 0)
                _set(self.s_trim, e.get("trim_in") or 0)
                _set(self.s_dur, e.get("duration") or 0)
                _set(self.s_pan, e.get("pan") or 0)
        else:
            self._e = r
            for wd in self.e_form:
                wd.setEnabled(has)
            if has:
                e = items[r]
                _set(self.e_type, e.get("type"))
                spec = I.EFFECTS.get(e.get("type"), {})
                txt = spec.get("param") == "text"
                self.e_amt.setVisible(not txt)
                self.e_text.setVisible(txt)
                if not txt:
                    self.e_amt.setRange(spec.get("min", -1), spec.get("max", 10))
                    _set(self.e_amt, (e.get("params") or {}).get("amount", spec.get("default", 0)))
                _set(self.e_text, (e.get("params") or {}).get("text"))
                _set(self.e_st, e.get("start_offset") or 0)
                _set(self.e_en, e.get("end_offset") if e.get("end_offset") is not None else -600)

    # ================================================================ edits
    def _changed(self, label_text, reparse=False):
        rec = self.rec()
        rec["applied"] = False
        if self.auto.isChecked():
            self.apply_current(label_text=label_text)
        else:
            rec["status"] = I._status(rec) if rec.get("issues") else "draft"
            self.win.commit(label_text)
            self.load()
            self._update_row()

    def _setting(self, key, value, label_text):
        rec = self.rec()
        if rec is None or rec["settings"].get(key) == value:
            return
        rec["settings"][key] = value
        self._changed(label_text)

    def _text_changed(self):
        rec = self.rec()
        if rec is not None:
            rec["instruction_text"] = self.text.toPlainText()

    def parse_current(self):
        rec = self.rec()
        if rec is None:
            return
        rec["instruction_text"] = self.text.toPlainText()
        I.parse_record(rec)
        rec["status"] = "unresolved" if rec["unresolved"] else "draft"
        I.resolve(self.p, rec)                       # show problems / choices before applying
        rec["status"] = I._status(rec)
        self.win.commit("Parse instruction")
        self.load()
        self._update_row()

    def apply_current(self, reviewed=False, label_text="Apply instruction"):
        rec = self.rec()
        if rec is None:
            return
        if self.text.toPlainText() != rec.get("instruction_text", ""):
            rec["instruction_text"] = self.text.toPlainText()
        ok, msg = I.apply(self.p, self.sid, self.win.log, reviewed=reviewed)
        self._sync_sheet()
        self.win.commit(label_text)
        self.win.refresh_all()
        if not ok:
            self.win.status_msg(f"{self.sid}: not applied - {msg[:160]}")
        self.refresh()

    def _sync_sheet(self):
        """A scene sheet is the plan: edits made here are written back into it, so the text and the timeline
        never disagree."""
        from . import timedplan
        p = self.p
        rec = I.records(p).get(self.sid) or {}
        if rec.get("from_plan") and p["settings"].get("scene_mode") == "timed" and timedplan.is_sheet(
                p.get("timed_plan") or ""):
            p["timed_plan"] = timedplan.export_sheet(p)
            self.win._load_plan_boxes()

    def _update_row(self):
        self.refresh()

    def _vis_changed(self, _i):
        v = self.vis.currentData()
        self._setting("primary_visual", {"asset_filename": v} if v else None, "Scene visual")

    def _anchor_changed(self, key, line, occ):
        t = line.text().strip()
        v = {"text": t, "occurrence": occ.value() or None} if t else None
        if self.rec()["settings"].get(key) == v:
            return
        if key == "narration_anchor" and v:
            self.rec()["settings"]["timing_mode"] = "follow_narration"
        self._setting(key, v, "Scene anchor")

    def _time_typed(self, which, v):
        rec = self.rec()
        s = rec["settings"]
        a, b, d = self.st.value(), self.en.value(), self.du.value()
        if which == "dur":
            b = a + d
        elif which == "start":
            b = a + d if d > 0 else b
        if b <= a:
            self.win.status_msg("The end must be after the start.")
            self.load()
            return
        s.update(timing_mode="absolute", start_seconds=round(a, 3), end_seconds=round(b, 3), duration_seconds=None)
        self._changed("Scene times")

    def _motion_changed(self, *_):
        m = self.mmode.currentData()
        v = {"mode": m}
        if m == "explicit":
            v.update(type=self.mtype.currentData(), strength=self.mstr.currentData(), ease=self.mease.currentData())
            old = self.rec()["settings"].get("motion") or {}
            if old.get("focus"):
                v["focus"] = old["focus"]
        self._setting("motion", v, "Scene motion")

    def _framing_changed(self, *_):
        m = self.fmode.currentData()
        v = {"mode": m}
        if m == "explicit":
            v.update(fit=self.fit.currentData(), focus=[round(self.fx.value() / 100, 3), round(self.fy.value() / 100, 3)])
        self._setting("framing", v, "Scene framing")

    def _transition_changed(self, *_):
        m = self.tr_mode.currentData()
        v = {"mode": m}
        if m == "explicit":
            v.update(type=self.tr_type.currentData(), duration_seconds=self.tr_dur.value() or None)
        elif m == "none":
            v.update(type="cut", duration_seconds=0.0)
        self._setting("transition_out", v, "Transition out")

    def _captions_changed(self, *_):
        m = self.cap_mode.currentData()
        v = {"mode": m}
        if m == "explicit" and self.cap_pos.currentData():
            v["position"] = self.cap_pos.currentData()
        self._setting("captions", v, "Scene captions")

    def _lock_field(self, field, on):
        rec = self.rec()
        lf = set(rec["settings"].get("locked_fields") or [])
        if (field in lf) == on:
            return
        k, sc = self.scene()
        if sc is not None:
            I.lock_field(self.p, self.sid, field, on)
            self._sync_sheet()
            self.win.commit("Lock " + I.FIELD_NAMES[field] if on else "Unlock " + I.FIELD_NAMES[field])
            self.win.refresh_all()
            self.refresh()
        else:
            (lf.add if on else lf.discard)(field)
            self._setting("locked_fields", sorted(lf), "Locks")

    def _notes_save(self):
        rec = self.rec()
        if rec is None or rec.get("notes", "") == self.notes.toPlainText():
            return
        rec["notes"] = self.notes.toPlainText()
        self.win.commit("Scene notes")

    def _list_mode(self, kind, mode):
        self._setting(kind + "_mode", mode, {"b_roll": "B-roll", "sfx": "Sound effects", "effects": "Effects"}[kind]
                      + " mode")

    def _list_add(self, kind):
        rec = self.rec()
        s = rec["settings"]
        if kind == "b_roll":
            vids = [m for m in self._media() if is_video(m)] or self._media()
            if not vids:
                return
            item = I._parse_broll(os.path.basename(vids[0]), "")
        elif kind == "sfx":
            files = I.sfx_files(self.p)
            if not files:
                QMessageBox.information(self, "AutoEditor", "No sound files found - choose an SFX folder (Audio tab) "
                                        "or use Browse… after adding.")
                item = I._parse_sfx("choose.wav", "")
            else:
                item = I._parse_sfx(os.path.basename(files[0]), "")
            item.pop("_why", None)
        else:
            item = {"type": "vignette", "params": {"amount": 0.5}, "start_offset": 0.0, "end_offset": None}
        s.setdefault(kind, []).append(item)
        s[kind + "_mode"] = "explicit"
        setattr(self, {"b_roll": "_b", "sfx": "_s", "effects": "_e"}[kind], len(s[kind]) - 1)
        self._changed("Add " + kind.replace("_", "-"))

    def _list_remove(self, kind):
        rec = self.rec()
        r = {"b_roll": self._b, "sfx": self._s, "effects": self._e}[kind]
        items = rec["settings"].get(kind) or []
        if 0 <= r < len(items):
            items.pop(r)
            self._changed("Remove " + kind.replace("_", "-"))

    def _item_set(self, kind, key, value):
        rec = self.rec()
        r = {"b_roll": self._b, "sfx": self._s, "effects": self._e}[kind]
        items = rec["settings"].get(kind) or []
        if not (0 <= r < len(items)) or items[r].get(key) == value:
            return
        items[r][key] = value
        self._changed({"b_roll": "B-roll", "sfx": "Sound effect", "effects": "Effect"}[kind] + " " +
                      key.replace("_", " "))

    def _sfx_anchor(self):
        t = self.s_anchor.text().strip()
        self._item_set("sfx", "anchor", {"text": t, "occurrence_in_scene": self.s_occ.value() or None} if t else None)

    def _effect_type(self, _i):
        typ = self.e_type.currentData()
        spec = I.EFFECTS[typ]
        params = {"text": ""} if spec.get("param") == "text" else {"amount": spec["default"]}
        rec = self.rec()
        items = rec["settings"].get("effects") or []
        if 0 <= self._e < len(items):
            items[self._e].update(type=typ, params=params)
            self._changed("Effect type")

    def _effect_param(self, key, v):
        rec = self.rec()
        items = rec["settings"].get("effects") or []
        if 0 <= self._e < len(items):
            pr = items[self._e].setdefault("params", {})
            if pr.get(key) == v:
                return
            pr[key] = v
            self._changed("Effect " + key)

    def _choose(self, key, n):
        if n is None:
            return
        rec = self.rec()
        rec.setdefault("choices", {})[key] = int(n)
        self._changed("Choose")

    def accept_warnings(self):
        rec = self.rec()
        acc = set(rec.get("accepted") or [])
        for i in rec.get("issues") or []:
            if i.get("accept"):
                acc.add(i["msg"])
        if rec.get("unresolved"):
            acc.add("unresolved")
        rec["accepted"] = sorted(acc)
        rec["status"] = I._status(rec)
        self.win.commit("Accept warnings")
        self.load()
        self._update_row()

    # ================================================================ bulk actions
    def parse_all(self):
        n = 0
        for sid, rec in I.records(self.p).items():
            if rec.get("instruction_text", "").strip():
                I.parse_record(rec)
                I.resolve(self.p, rec)
                rec["status"] = I._status(rec)
                n += 1
        self.win.commit("Parse all instructions")
        self.win.status_msg(f"{n} instruction(s) parsed - review them, then Apply valid instructions.")
        self.refresh()

    def apply_valid(self):
        done, skipped = I.apply_all(self.p, self.win.log)
        self.win.commit("Apply valid instructions")
        self.win.refresh_all()
        self.refresh()
        msg = f"{len(done)} instruction(s) applied."
        if skipped:
            msg += f" {len(skipped)} not applied (they need a decision): " + ", ".join(s for s, _ in skipped[:8])
        self.win.status_msg(msg)
        return done, skipped

    def validate(self):
        items = I.validate(self.p)
        if not items:
            QMessageBox.information(self, "Check before export", "No problems found in the editing instructions.")
            return items
        txt = "\n".join(("✖ " if i["level"] == "fatal" else "⚠ ") + f"{i.get('scene') or 'project'}: {i['msg']}"
                        for i in items[:40])
        QMessageBox.information(self, "Check before export", ("Fatal problems block the export:\n\n" if any(
            i["level"] == "fatal" for i in items) else "Warnings (export is possible once accepted):\n\n") + txt)
        return items

    def move(self, d):
        k, sc = self.scene()
        scenes = self.p["scenes"]
        if sc is None or not (0 <= k + d < len(scenes)):
            return
        o = scenes[k + d]
        if any("primary_visual" in (x.get("locked_fields") or []) or x.get("locked") for x in (sc, o)):
            self.win.status_msg("A locked visual can't be moved.")
            return
        for key in ("media", "motion", "motion_amount", "fit", "focus", "trim", "clip_in", "clip_out"):
            sc[key], o[key] = o.get(key), sc.get(key)
        for x in (sc, o):
            for key in [k_ for k_, v in x.items() if v is None]:
                x.pop(key)
        self._sync_sheet()
        self.win.commit("Move visual")
        self.win.refresh_all()
        self.refresh()

    def bulk(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        targets = [self.rows[r][2] for r in rows if self.rows[r][2] != self.sid]
        if not targets:
            QMessageBox.information(self, "AutoEditor", "Select the scenes to copy to (Ctrl/Shift + click), with the "
                                    "source scene shown on the right.")
            return
        d = QDialog(self)
        d.setWindowTitle("Copy settings")
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel(f"Copy from {self.sid} to {len(targets)} scene(s):"))
        groups = {"Motion": ["motion"], "Framing": ["framing"], "Transition out": ["transition_out"],
                  "Captions": ["captions"], "Sound effects": ["sfx_mode", "sfx"], "B-roll": ["b_roll_mode", "b_roll"],
                  "Effects": ["effects_mode", "effects"], "Field locks": ["locked_fields"]}
        cbs = {}
        for g in groups:
            cbs[g] = QCheckBox(g)
            lay.addWidget(cbs[g])
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if d.exec() != QDialog.Accepted:
            return
        src = self.rec()["settings"]
        keys = [k for g, cb in cbs.items() if cb.isChecked() for k in groups[g]]
        for sid in targets:
            r = I.record(self.p, sid)
            for k in keys:
                r["settings"][k] = copy.deepcopy(src.get(k))
            r["applied"] = False
            if self.auto.isChecked():
                I.apply(self.p, sid, self.win.log)
        self.win.commit(f"Copy settings to {len(targets)} scenes")
        self.win.refresh_all()
        self.refresh()

    # ================================================================ import / export
    def do_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import editing instructions", "",
                                              "Instructions (*.json *.csv);;All files (*)")
        if path:
            self.import_file(path)

    def import_file(self, path, confirm=True):
        inc, errs, preview = I.load_import(self.p, path)
        if errs and not (inc.get("scenes") if inc else None):
            QMessageBox.warning(self, "Import", "Nothing was imported:\n\n" + "\n".join(errs[:30]))
            return False
        text = "Proposed changes:\n\n" + "\n".join(preview[:40])
        if errs:
            text += "\n\nRows/fields that will be SKIPPED:\n" + "\n".join(errs[:20])
        if confirm:
            box = QMessageBox(self)
            box.setWindowTitle("Import editing instructions")
            box.setText(text[:3500])
            ok = box.addButton("Import (then review)", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not ok:
                return False
        ids = I.commit_import(self.p, inc)
        self.win.commit("Import editing instructions")
        self.win.status_msg(f"{len(ids)} scene instruction(s) imported - press 'Apply valid instructions'.")
        self.refresh()
        return True

    def export_json(self):
        import json
        path, _ = QFileDialog.getSaveFileName(self, "Export instructions", "instructions.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(I.export_json(self.p), f, indent=1, ensure_ascii=False)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "CSV template", "instructions.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(I.template_csv(self.p))

    # ================================================================ preview
    def preview(self):
        k, sc = self.scene()
        if sc is None:
            return
        pl = self.win.player
        pl.pause()
        pl.seek(sc["start"])
        self._stop_at = sc["end"]
        pl.play()
        self._timer.start()

    def _watch(self):
        pl = self.win.player
        if self._stop_at is None or not pl.playing:
            self._timer.stop()
            return
        if pl.t >= self._stop_at - 0.02:
            pl.pause()
            self._stop_at = None
            self._timer.stop()

    def show_frames(self):
        k, sc = self.scene()
        if sc is None:
            return
        from .engine import Compositor
        st = self.p["settings"]
        from .exportcfg import dims
        W, H = dims(st.get("aspect", "16:9"), "Draft")
        comp = Compositor(self.p, W, H, int(st.get("fps", 30)), preview=True, captions=False)
        try:
            for t, lab in ((sc["start"] + 1 / 30, self.f_start), (sc["end"] - 2 / 30, self.f_end)):
                fr = comp.frame_at(t, captions=False)
                img = QImage(fr.data, fr.shape[1], fr.shape[0], fr.shape[1] * 3, QImage.Format_RGB888).copy()
                lab.setPixmap(QPixmap.fromImage(img).scaled(lab.width(), lab.height(), Qt.KeepAspectRatio,
                                                            Qt.SmoothTransformation))
        finally:
            comp.close()

    def browse_sfx(self):
        path, _ = QFileDialog.getOpenFileName(self, "Sound file", self.p.get("sfx_dir") or "",
                                              "Audio (*.wav *.mp3 *.ogg *.flac *.m4a *.aac)")
        if not path:
            return
        ex = self.p.setdefault("extra_sfx", [])
        if path not in ex:
            ex.append(path)
        self._item_set("sfx", "asset_filename", os.path.basename(path))
        self.load()

    def audition(self):
        name = self.s_asset.currentData()
        hits = [f for f in I.sfx_files(self.p) if os.path.basename(f) == name]
        if not hits:
            self.win.status_msg(f"{name} is missing. Select a replacement.")
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if self.audition_player is None:
                self.audition_player = QMediaPlayer(self)
                self._aout = QAudioOutput(self)
                self.audition_player.setAudioOutput(self._aout)
            self.audition_player.setSource(QUrl.fromLocalFile(hits[0]))
            self.audition_player.play()
        except Exception as e:
            self.win.status_msg(f"Can't play audio here: {e}")
