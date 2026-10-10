"""Extra GUI panels: captions & fonts, text cards & chapters, quality check."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .captions import CAPTION_ANIMS, CAPTION_POSITIONS, available_fonts
from .editorial import auto_chapters, chapter_cards, description_text, quality_report, yt_time
from .overlays import DEFAULT_DUR, OVERLAY_TYPES
from .presets import CAPTION_STYLES
from .util import fmt_time


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color="#FFFFFF"):
        super().__init__()
        self.setFixedWidth(70)
        self.clicked.connect(self.pick)
        self.set(color)

    def set(self, c):
        self.color = c
        self.setStyleSheet(f"background:{c}; border:1px solid #666; min-height:20px;")
        self.setText("")

    def pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Choose colour")
        if c.isValid():
            self.set(c.name().upper())
            self.changed.emit(self.color)


class CaptionPanel(QGroupBox):
    changed = Signal()

    def __init__(self):
        super().__init__("Captions && fonts")
        self.style = QComboBox()
        self.style.addItems(CAPTION_STYLES)
        self.font = QComboBox()
        self.font.addItems(available_fonts())
        self.title_font = QComboBox()
        self.title_font.addItems(["Same as captions"] + available_fonts()[1:])
        self.text_col = ColorButton("#FFFFFF")
        self.hi_col = ColorButton("#FFD600")
        cols = QHBoxLayout()
        cols.addWidget(QLabel("Text"))
        cols.addWidget(self.text_col)
        cols.addSpacing(10)
        cols.addWidget(QLabel("Highlight / accent"))
        cols.addWidget(self.hi_col)
        cols.addStretch(1)
        self.pos = QComboBox()
        self.pos.addItems(CAPTION_POSITIONS)
        self.size = QSpinBox()
        self.size.setRange(50, 180)
        self.size.setSuffix(" %")
        self.size.setSingleStep(5)
        self.anim = QComboBox()
        self.anim.addItems(CAPTION_ANIMS)
        f = QFormLayout(self)
        f.addRow("Caption style", self.style)
        f.addRow("Font", self.font)
        f.addRow("Colours", cols)
        f.addRow("Position", self.pos)
        f.addRow("Size", self.size)
        f.addRow("Animation", self.anim)
        f.addRow("Text-card font", self.title_font)
        note = QLabel("<small>9:16 captions stay inside the Shorts safe zone automatically.</small>")
        note.setWordWrap(True)
        f.addRow("", note)
        for w in (self.style, self.font, self.pos, self.anim, self.title_font):
            w.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.size.editingFinished.connect(self.changed.emit)
        self.text_col.changed.connect(lambda *_: self.changed.emit())
        self.hi_col.changed.connect(lambda *_: self.changed.emit())

    def load(self, st):
        for w in (self.style, self.font, self.pos, self.anim, self.title_font):
            w.blockSignals(True)
        self.style.setCurrentText(st.get("caption_style") if st.get("captions", True) else "None")
        if self.font.findText(st.get("caption_font", "")) >= 0:
            self.font.setCurrentText(st.get("caption_font"))
        else:
            self.font.setCurrentIndex(0)
        tf = st.get("title_font") or "Same as captions"
        self.title_font.setCurrentText(tf if self.title_font.findText(tf) >= 0 else "Same as captions")
        self.text_col.set(st.get("caption_color", "#FFFFFF"))
        self.hi_col.set(st.get("highlight_color", "#FFD600"))
        self.pos.setCurrentText(st.get("caption_position", "Bottom"))
        self.size.setValue(int(st.get("caption_size", 100)))
        self.anim.setCurrentText(st.get("caption_anim", "Pop-in"))
        for w in (self.style, self.font, self.pos, self.anim, self.title_font):
            w.blockSignals(False)

    def save(self, st):
        st["caption_style"] = self.style.currentText()
        st["captions"] = self.style.currentText() != "None"
        st["caption_font"] = self.font.currentText()
        tf = self.title_font.currentText()
        st["title_font"] = "" if tf == "Same as captions" else tf
        st["caption_color"] = self.text_col.color
        st["highlight_color"] = self.hi_col.color
        st["caption_position"] = self.pos.currentText()
        st["caption_size"] = self.size.value()
        st["caption_anim"] = self.anim.currentText()


# ====================================================================== text & chapters

class TextChaptersPanel(QWidget):
    changed = Signal()           # overlays/chapters edited -> refresh preview
    seek = Signal(float)

    def __init__(self, get_project, get_playhead):
        super().__init__()
        self.get_project = get_project
        self.get_playhead = get_playhead
        self._filling = False

        # ---- text cards
        self.type = QComboBox()
        for k, v in OVERLAY_TYPES.items():
            self.type.addItem(v, k)
        self.type.setCurrentIndex(3)
        self.txt = QLineEdit()
        self.txt.setPlaceholderText("Text  (e.g. 1917 · BERLIN)")
        self.sub = QLineEdit()
        self.sub.setPlaceholderText("Second line (optional)")
        b_add = QPushButton("+ Add at playhead")
        b_add.clicked.connect(self.add_overlay)
        b_del = QPushButton("Delete")
        b_del.clicked.connect(self.del_overlay)
        row = QHBoxLayout()
        row.addWidget(self.type, 1)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row2 = QHBoxLayout()
        row2.addWidget(self.txt, 3)
        row2.addWidget(self.sub, 2)
        self.otable = QTableWidget(0, 5)
        self.otable.setHorizontalHeaderLabels(["Type", "Start (s)", "End (s)", "Text", "Second line"])
        self.otable.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.otable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.otable.verticalHeader().setVisible(False)
        self.otable.itemChanged.connect(self.overlay_edited)
        self.otable.cellDoubleClicked.connect(self._jump_overlay)
        box1 = QGroupBox("Text cards  (double-click a row to jump there; edit cells directly)")
        l1 = QVBoxLayout(box1)
        l1.addLayout(row)
        l1.addLayout(row2)
        l1.addWidget(self.otable)

        # ---- chapters
        b_auto = QPushButton("Auto chapters")
        b_auto.clicked.connect(self.auto)
        b_addc = QPushButton("+ Chapter at playhead")
        b_addc.clicked.connect(self.add_chapter)
        b_delc = QPushButton("Delete")
        b_delc.clicked.connect(self.del_chapter)
        self.cards = QCheckBox("Show chapter cards in the video")
        self.cards.toggled.connect(self.sync_cards)
        crow = QHBoxLayout()
        for w in (b_auto, b_addc, b_delc, self.cards):
            crow.addWidget(w)
        crow.addStretch(1)
        self.ctable = QTableWidget(0, 2)
        self.ctable.setHorizontalHeaderLabels(["Start", "Chapter title"])
        self.ctable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.ctable.verticalHeader().setVisible(False)
        self.ctable.itemChanged.connect(self.chapter_edited)
        self.desc = QPlainTextEdit()
        self.desc.setReadOnly(True)
        self.desc.setMaximumHeight(120)
        b_copy = QPushButton("Copy YouTube description text")
        b_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.desc.toPlainText()))
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color:#c0392b")
        box2 = QGroupBox("Chapters  (YouTube needs 0:00 first, at least 3 chapters, each 10 s or longer)")
        l2 = QVBoxLayout(box2)
        l2.addLayout(crow)
        l2.addWidget(self.ctable)
        l2.addWidget(self.warn)
        l2.addWidget(self.desc)
        l2.addWidget(b_copy)

        lay = QHBoxLayout(self)
        lay.addWidget(box1, 3)
        lay.addWidget(box2, 2)

    # ---------------- overlays
    def refresh(self):
        p = self.get_project()
        self._filling = True
        ovs = p.setdefault("overlays", [])
        self.otable.setRowCount(len(ovs))
        for r, o in enumerate(ovs):
            it = QTableWidgetItem(o.get("type", ""))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.otable.setItem(r, 0, it)
            self.otable.setItem(r, 1, QTableWidgetItem(f"{o['start']:.2f}"))
            self.otable.setItem(r, 2, QTableWidgetItem(f"{o['end']:.2f}"))
            self.otable.setItem(r, 3, QTableWidgetItem(o.get("text", "")))
            self.otable.setItem(r, 4, QTableWidgetItem(o.get("sub", "")))
        chs = p.setdefault("chapters", [])
        self.ctable.setRowCount(len(chs))
        for r, c in enumerate(chs):
            it = QTableWidgetItem(yt_time(c["start"]))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.ctable.setItem(r, 0, it)
            self.ctable.setItem(r, 1, QTableWidgetItem(c["title"]))
        self.cards.blockSignals(True)
        self.cards.setChecked(any(o.get("type") == "chapter" for o in ovs))
        self.cards.blockSignals(False)
        self._filling = False
        self.update_desc()

    def add_overlay(self):
        p = self.get_project()
        typ = self.type.currentData()
        t = float(self.get_playhead())
        text = self.txt.text().strip() or {"label": "1917 · BERLIN", "lower_third": "Name",
                                           "title": "Title", "chapter": "Chapter", "hook": "Hook"}[typ]
        if typ == "hook":
            t = 0.1
        p.setdefault("overlays", []).append({"type": typ, "start": round(t, 2),
                                             "end": round(t + DEFAULT_DUR[typ], 2),
                                             "text": text, "sub": self.sub.text().strip()})
        p["overlays"].sort(key=lambda o: o["start"])
        self.txt.clear()
        self.sub.clear()
        self.refresh()
        self.changed.emit()

    def del_overlay(self):
        rows = sorted({i.row() for i in self.otable.selectedIndexes()}, reverse=True)
        p = self.get_project()
        for r in rows:
            if 0 <= r < len(p["overlays"]):
                del p["overlays"][r]
        self.refresh()
        self.changed.emit()

    def overlay_edited(self, item):
        if self._filling:
            return
        p = self.get_project()
        r, c = item.row(), item.column()
        if not (0 <= r < len(p["overlays"])):
            return
        o = p["overlays"][r]
        try:
            if c == 1:
                d = o["end"] - o["start"]
                o["start"] = max(0.0, float(item.text()))
                o["end"] = o["start"] + d
            elif c == 2:
                o["end"] = max(o["start"] + 0.3, float(item.text()))
            elif c == 3:
                o["text"] = item.text()
            elif c == 4:
                o["sub"] = item.text()
        except ValueError:
            pass
        self.changed.emit()

    def _jump_overlay(self, r, c):
        p = self.get_project()
        if 0 <= r < len(p["overlays"]) and c == 0:
            self.seek.emit(p["overlays"][r]["start"] + 0.5)

    # ---------------- chapters
    def auto(self):
        p = self.get_project()
        p["chapters"] = auto_chapters(p)
        if not p["chapters"]:
            self.warn.setText("Video too short for chapters (needs 40 s+), or analyze first.")
        self.sync_cards()
        self.refresh()

    def add_chapter(self):
        p = self.get_project()
        t = round(float(self.get_playhead()), 2)
        chs = p.setdefault("chapters", [])
        if not chs:
            chs.append({"start": 0.0, "title": "Intro"})
        if all(abs(c["start"] - t) > 1 for c in chs):
            chs.append({"start": t, "title": "New chapter"})
        chs.sort(key=lambda c: c["start"])
        self.sync_cards()
        self.refresh()

    def del_chapter(self):
        rows = sorted({i.row() for i in self.ctable.selectedIndexes()}, reverse=True)
        p = self.get_project()
        for r in rows:
            if 0 <= r < len(p["chapters"]):
                del p["chapters"][r]
        self.sync_cards()
        self.refresh()

    def chapter_edited(self, item):
        if self._filling or item.column() != 1:
            return
        p = self.get_project()
        r = item.row()
        if 0 <= r < len(p["chapters"]):
            p["chapters"][r]["title"] = item.text()
            self.sync_cards(refresh=False)
            self.update_desc()

    def sync_cards(self, *_, refresh=True):
        p = self.get_project()
        ovs = [o for o in p.setdefault("overlays", []) if o.get("type") != "chapter"]
        if self.cards.isChecked():
            ovs += chapter_cards(p.get("chapters", []))
        p["overlays"] = sorted(ovs, key=lambda o: o["start"])
        if refresh:
            self.refresh()
        self.changed.emit()

    def update_desc(self):
        p = self.get_project()
        txt, warn = description_text(p, p.get("chapters", []))
        self.desc.setPlainText(txt)
        self.warn.setText(" ".join(warn))


# ====================================================================== quality

COLORS = {"fix": "#c0392b", "warn": "#d35400", "tip": "#2471a3", "good": "#1e8449"}
ICONS = {"fix": "✖", "warn": "⚠", "tip": "💡", "good": "✔"}


class QualityPanel(QWidget):
    select_scene = Signal(int)

    def __init__(self, get_project):
        super().__init__()
        self.get_project = get_project
        self.score = QLabel("Run the check to see your score.")
        self.score.setStyleSheet("font-size:15px; font-weight:bold;")
        b = QPushButton("Run quality check")
        b.clicked.connect(self.run)
        top = QHBoxLayout()
        top.addWidget(b)
        top.addWidget(self.score, 1)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._clicked)
        info = QLabel("<small>Checks for things that make videos feel templated or slow (long static shots, "
                      "repeated visuals, same camera move, slow opening, missing captions/text cards). "
                      "It cannot predict YouTube's decisions - it helps you make a better, more original edit."
                      " Double-click an item to jump to the scene.</small>")
        info.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(info)
        lay.addWidget(self.list)

    def run(self):
        items, score = quality_report(self.get_project())
        self.show_items(items, score)
        return items, score

    def show_items(self, items, score):
        col = "#1e8449" if score >= 85 else "#d35400" if score >= 65 else "#c0392b"
        self.score.setText(f"<span style='color:{col}'>Quality score: {score}/100</span>")
        self.list.clear()
        for level, msg, k in items:
            it = QListWidgetItem(f"{ICONS[level]}  {msg}")
            it.setForeground(QColor(COLORS[level]))
            it.setData(Qt.UserRole, k)
            self.list.addItem(it)

    def _clicked(self, it):
        k = it.data(Qt.UserRole)
        if k is not None:
            self.select_scene.emit(int(k))


def fmt_items(items, limit=8):
    lines = [f"{ICONS[l]} {m}" for l, m, _ in items if l in ("fix", "warn")][:limit]
    return "\n".join(lines)


__all__ = ["CaptionPanel", "TextChaptersPanel", "QualityPanel", "fmt_items", "fmt_time"]


# ====================================================================== sound effects

class SfxPanel(QWidget):
    """Planned sound effects: see why each one was chosen, preview it, change level,
    time or on/off, add or remove. Edits are saved with the project and used at render."""
    edited = Signal(str)          # label for undo
    plan_requested = Signal()
    seek = Signal(float)

    def __init__(self, get_project, get_playhead, get_categories):
        super().__init__()
        self.get_project = get_project
        self.get_playhead = get_playhead
        self.get_categories = get_categories
        self._filling = False
        self.status = QLabel("")
        self.status.setObjectName("Help")
        self.status.setWordWrap(True)
        b_plan = QPushButton("Plan sound effects")
        b_plan.setToolTip("Choose effects from transitions, text cards and narration keywords")
        b_plan.clicked.connect(self.plan_requested.emit)
        self.add_cat = QComboBox()
        b_add = QPushButton("+ Add at playhead")
        b_add.clicked.connect(self.add_at_playhead)
        b_del = QPushButton("Remove")
        b_del.clicked.connect(self.remove)
        self.b_prev = QPushButton("▶ Preview")
        self.b_prev.clicked.connect(self.preview)
        top = QHBoxLayout()
        top.addWidget(b_plan)
        top.addSpacing(12)
        top.addWidget(self.add_cat)
        top.addWidget(b_add)
        top.addWidget(b_del)
        top.addWidget(self.b_prev)
        top.addStretch(1)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["On", "Time (s)", "Sound", "Level (dB)", "File", "Why"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        for c, w in ((0, 40), (1, 80), (2, 110), (3, 84), (4, 170)):
            self.table.setColumnWidth(c, w)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._jump)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addWidget(self.table)
        self.player = None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.player = QMediaPlayer(self)
            self._out = QAudioOutput(self)
            self.player.setAudioOutput(self._out)
        except Exception:
            self.b_prev.setEnabled(False)
            self.b_prev.setToolTip("Audio preview is not available on this PC")

    def refresh(self):
        from .presets import SFX_LEVELS
        p = self.get_project()
        st = p.get("settings", {})
        self._filling = True
        cats = self.get_categories()
        cur = self.add_cat.currentText()
        self.add_cat.blockSignals(True)
        self.add_cat.clear()
        self.add_cat.addItems(cats)
        if cur in cats:
            self.add_cat.setCurrentText(cur)
        self.add_cat.blockSignals(False)
        ev = p.get("sfx_events") or []
        self.table.setRowCount(len(ev))
        for r, e in enumerate(ev):
            on = QTableWidgetItem("")
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            on.setCheckState(Qt.Checked if e.get("enabled", True) else Qt.Unchecked)
            self.table.setItem(r, 0, on)
            self.table.setItem(r, 1, QTableWidgetItem(f"{e['t']:.2f}"))
            it = QTableWidgetItem(e.get("cat", ""))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 2, it)
            if e.get("source"):                 # placed by an editing instruction: its level lives there
                it = QTableWidgetItem(f"{float(e.get('rel_db') or 0):+.1f}")
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setToolTip(f"Level relative to the narration, set by the editing instruction for scene "
                              f"{e['source']}. Change it there (Editing instructions, F8).")
                self.table.setItem(r, 3, it)
            else:
                self.table.setItem(r, 3, QTableWidgetItem(f"{float(e.get('gain', 0)):+.1f}"))
            import os as _os
            it = QTableWidgetItem(_os.path.basename(e.get("file", "")))
            it.setToolTip(e.get("file", ""))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 4, it)
            it = QTableWidgetItem(e.get("reason", ""))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 5, it)
        self._filling = False
        level = st.get("sfx_level", "Normal")
        if not SFX_LEVELS.get(level):
            msg = "SFX level is None - no sound effects will be added."
        elif not p.get("sfx_planned"):
            msg = ("Effects are planned automatically when you render. Choose 'Plan sound effects' to see "
                   "and edit them first." if not ev else
                   "SFX settings changed - effects will be re-planned at render (your edits to this list "
                   "will be replaced). Choose 'Plan sound effects' to review them now.")
        else:
            on = sum(1 for e in ev if e.get("enabled", True))
            msg = (f"{on} of {len(ev)} effects on. Level column = your adjustment on top of the automatic "
                   f"level (effects sit under the voice). Double-click a row to jump there.")
        self.status.setText(msg)

    def _events(self):
        return self.get_project().setdefault("sfx_events", [])

    def _item_changed(self, item):
        if self._filling:
            return
        ev = self._events()
        r, c = item.row(), item.column()
        if not (0 <= r < len(ev)):
            return
        e = ev[r]
        try:
            if c == 0:
                e["enabled"] = item.checkState() == Qt.Checked
            elif c == 1:
                e["t"] = max(0.0, round(float(item.text()), 3))
            elif c == 3 and not e.get("source"):
                e["gain"] = max(-30.0, min(20.0, float(item.text())))
            else:
                return
        except ValueError:
            self.refresh()
            return
        self.get_project()["sfx_planned"] = True
        self.edited.emit("Edit sound effect")

    def _jump(self, r, c):
        ev = self._events()
        if 0 <= r < len(ev) and c in (2, 4, 5):
            self.seek.emit(max(0.0, ev[r]["t"] - 0.5))

    def add_at_playhead(self):
        from .audio import scan_sfx
        p = self.get_project()
        cat = self.add_cat.currentText()
        lib = scan_sfx(p.get("sfx_dir"), p.get("settings", {}).get("builtin_sfx", True))
        if cat not in lib:
            return
        ev = self._events()
        ev.append({"t": round(float(self.get_playhead()), 3), "file": lib[cat][0], "cat": cat, "gain": 0.0,
                   "rule_gain": 0.0, "dur": None, "enabled": True, "reason": "added by you"})
        ev.sort(key=lambda e: e["t"])
        p["sfx_planned"] = True
        self.refresh()
        self.edited.emit("Add sound effect")

    def remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        ev = self._events()
        if not rows:
            return
        for r in rows:
            if 0 <= r < len(ev):
                del ev[r]
        self.get_project()["sfx_planned"] = True
        self.refresh()
        self.edited.emit("Remove sound effect")

    def preview(self):
        from PySide6.QtCore import QUrl
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        ev = self._events()
        path = None
        if rows and rows[0] < len(ev):
            path = ev[rows[0]]["file"]
        elif self.add_cat.currentText():
            from .audio import scan_sfx
            p = self.get_project()
            lib = scan_sfx(p.get("sfx_dir"), p.get("settings", {}).get("builtin_sfx", True))
            files = lib.get(self.add_cat.currentText())
            path = files[0] if files else None
        if path and self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
