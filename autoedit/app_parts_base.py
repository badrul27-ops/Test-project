"""Shared building blocks for the AutoEditor window (workers, undo, path fields, dialogs)."""
import os
import time
import traceback

from PySide6.QtCore import QObject, QSettings, QSignalBlocker, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from .render import Cancelled
from .util import IMAGE_EXT, VIDEO_EXT, clean_path

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")


def icon(name):
    return QIcon(os.path.join(ICON_DIR, name + ".svg"))


class Worker(QObject):
    log = Signal(str)
    progress = Signal(float, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.stop = False

    def run(self):
        try:
            self.done.emit(self.fn(self))
        except Cancelled:
            self.failed.emit("Cancelled.")
        except Exception as e:
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(e))


class UndoStack:
    """Snapshot undo. Consecutive edits with the same label within 1.5 s are merged
    (so dragging a slider is one undo step)."""

    def __init__(self, limit=80):
        self.undo, self.redo, self.limit = [], [], limit
        self._last_label, self._last_time = None, 0.0

    def push(self, label, before):
        now = time.monotonic()
        if self.undo and label == self._last_label and now - self._last_time < 1.5:
            self._last_time = now
        else:
            self.undo.append((label, before))
            del self.undo[:-self.limit]
            self._last_label, self._last_time = label, now
        self.redo.clear()

    def break_merge(self):
        self._last_label = None

    def clear(self):
        self.undo.clear()
        self.redo.clear()
        self._last_label = None


def dropped_paths(event):
    md = event.mimeData()
    if md.hasUrls():
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile()]
    return []


class DropLineEdit(QLineEdit):
    """Line edit that accepts files/folders dragged from File Explorer."""

    def __init__(self, on_drop):
        super().__init__()
        self.on_drop = on_drop
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        paths = dropped_paths(e)
        if paths:
            self.on_drop(paths)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class PathRow(QWidget):
    """Path field + Browse + clear (x), with an info line showing what is selected.

    Emits `changed` only on a deliberate change (Browse, drag & drop, finishing typing).
    Emits `clear_requested` when x is pressed - the window decides what else depends on the
    input and asks before clearing. Clearing never touches the file on disk."""
    changed = Signal()
    clear_requested = Signal()

    def __init__(self, kind, filt="", placeholder=""):
        super().__init__()
        self.kind, self.filt = kind, filt
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.edit = DropLineEdit(self.set_from_drop)
        self.edit.setPlaceholderText(placeholder or "Drag & drop here, or Browse")
        self.edit.editingFinished.connect(self._typed)
        self.btn = QPushButton("Browse")
        self.btn.setFixedWidth(68)
        self.btn.clicked.connect(self.browse)
        self.clear_btn = QToolButton()
        self.clear_btn.setIcon(icon("clear"))
        self.clear_btn.setObjectName("ClearBtn")
        self.clear_btn.setToolTip("Clear this selection (the file on disk is not touched)")
        self.clear_btn.clicked.connect(self.clear_requested.emit)
        self.clear_btn.setVisible(False)
        row.addWidget(self.edit, 1)
        row.addWidget(self.clear_btn)
        row.addWidget(self.btn)
        outer.addLayout(row)
        self.info = QLabel("")
        self.info.setObjectName("Help")
        self.info.setWordWrap(True)
        self.info.setVisible(False)
        outer.addWidget(self.info)
        self._last = ""

    def _typed(self):
        if self.edit.text() != self._last:
            self._last = self.edit.text()
            self.clear_btn.setVisible(bool(self.edit.text().strip()))
            self.changed.emit()

    def set_from_drop(self, paths):
        p = paths[0]
        if self.kind in ("media", "dir") and os.path.isfile(p):
            p = os.path.dirname(p)
        self.edit.setText(os.path.normpath(p))
        self._typed()

    def browse(self):
        start = self.text() or QSettings().value("last_dir", "")
        if start and os.path.isfile(start):
            start = os.path.dirname(start)
        if start and not os.path.isdir(start):
            start = ""
        p = ""
        if self.kind == "media":
            # Qt's own dialog always lists the images (the Windows folder picker hides files).
            exts = " ".join("*" + e + " *" + e.upper() for e in sorted(IMAGE_EXT | VIDEO_EXT))
            dlg = QFileDialog(self, "Open your images folder, click any image, then Open", start)
            dlg.setOption(QFileDialog.DontUseNativeDialog, True)
            dlg.setFileMode(QFileDialog.ExistingFiles)
            dlg.setNameFilters([f"Images and clips ({exts})", "All files (*)"])
            dlg.setViewMode(QFileDialog.Detail)
            dlg.resize(900, 560)
            if dlg.exec() and dlg.selectedFiles():
                p = dlg.selectedFiles()[0]
                p = os.path.dirname(p) if os.path.isfile(p) else p
        elif self.kind == "dir":
            p = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose file", start, self.filt)
        if p:
            self.edit.setText(os.path.normpath(p))
            QSettings().setValue("last_dir", p if os.path.isdir(p) else os.path.dirname(p))
            self._typed()

    def text(self):
        t = self.edit.text().strip().strip('"').strip("'").strip()
        if t.lower().startswith("file:"):
            t = QUrl(t).toLocalFile() or t
        return clean_path(t) if self.kind == "media" else t

    def load(self, t):
        with QSignalBlocker(self.edit):
            self.edit.setText(t or "")
        self._last = self.edit.text()
        self.edit.setToolTip(t or "")
        self.clear_btn.setVisible(bool((t or "").strip()))

    def set_info(self, text, state="Help"):
        self.info.setText(text)
        self.info.setVisible(bool(text))
        if self.info.objectName() != state:
            self.info.setObjectName(state)
            self.info.style().unpolish(self.info)
            self.info.style().polish(self.info)


class ToolTabBar(QWidget):
    """Horizontal row of tool tabs (icon above a short label). Scrolls sideways when the
    panel is too narrow - it never wraps into a tall sidebar."""
    currentChanged = Signal(int)

    def __init__(self, tabs):
        super().__init__()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        inner = QWidget()
        inner.setObjectName("ToolTabs")
        lay = QHBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(2)
        self.buttons = []
        for i, (name, ico, tip) in enumerate(tabs):
            b = QToolButton()
            b.setObjectName("ToolTab")
            b.setText(name)
            b.setIcon(icon(ico))
            b.setIconSize(QSize(20, 20))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setToolTip(tip)
            fm = b.fontMetrics()
            b.setMinimumWidth(max(46, fm.horizontalAdvance(name) + 12))
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self.group.addButton(b, i)
            lay.addWidget(b)
            self.buttons.append(b)
        lay.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidget(inner)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setObjectName("ToolTabScroll")
        h = inner.sizeHint().height() + 12
        self.scroll.setFixedHeight(h)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll)
        self.group.idToggled.connect(lambda i, on: on and self.currentChanged.emit(i))
        self.buttons[0].setChecked(True)

    def setCurrentIndex(self, i):
        if 0 <= i < len(self.buttons):
            self.buttons[i].setChecked(True)
            self.scroll.ensureWidgetVisible(self.buttons[i])

    def currentIndex(self):
        return self.group.checkedId()

    def count(self):
        return len(self.buttons)


def ask_unsaved(parent, what="leave this project"):
    """Save / Discard changes / Keep editing. Returns 'save', 'discard' or 'keep'."""
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Unsaved changes")
    box.setText(f"This project has unsaved changes. Save them before you {what}?")
    box.setInformativeText("Discard changes only drops the edits since your last save. Your voiceover, "
                           "images, rendered videos and any saved project file are never deleted.")
    save = box.addButton("Save", QMessageBox.AcceptRole)
    discard = box.addButton("Discard changes", QMessageBox.DestructiveRole)
    box.addButton("Keep editing", QMessageBox.RejectRole)
    box.setDefaultButton(save)
    box.exec()
    c = box.clickedButton()
    return "save" if c is save else "discard" if c is discard else "keep"


def label(text, obj=None, wrap=True):
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def page(title, subtitle=""):
    """Scrollable tool page with a title. Returns (scroll_area, layout)."""
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(8)
    lay.addWidget(label(title, "PanelTitle"))
    if subtitle:
        lay.addWidget(label(subtitle, "Help"))
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sc.setWidget(inner)
    return sc, lay


def form():
    f = QFormLayout()
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    f.setRowWrapPolicy(QFormLayout.WrapAllRows)   # label above field: fits narrow panels
    f.setHorizontalSpacing(10)
    f.setVerticalSpacing(8)
    return f


class ProjectSettingsDialog(QDialog):
    def __init__(self, parent, st, categories=None):
        super().__init__(parent)
        self.setWindowTitle("Project settings")
        self.categories = categories or {}
        f = form()
        self.cat = QComboBox()
        self.cat.addItem("(not chosen)", "")
        for k in self.categories:
            self.cat.addItem(k, k)
        i = self.cat.findData(st.get("category", ""))
        self.cat.setCurrentIndex(max(0, i))
        self.cat_about = label("", "Help")
        self.apply_defaults = QCheckBox("Apply this category's starting defaults now (preset, motion, captions, "
                                        "look, sound) - you can still change each one afterwards")
        self.cat.currentIndexChanged.connect(self._cat_changed)
        self._cat0 = st.get("category", "")
        if self.categories:
            f.addRow("Video category / editing style", self.cat)
            f.addRow("", self.cat_about)
            f.addRow("", self.apply_defaults)
        self.model = QComboBox()
        self.model.addItems(WHISPER_MODELS)
        self.model.setCurrentText(st.get("whisper_model", "small"))
        self.lang = QLineEdit(st.get("language", ""))
        self.lang.setPlaceholderText("auto-detect  (or en, bn, es…)")
        f.addRow("Speech model", self.model)
        f.addRow("", label("tiny/base = fastest, small = recommended, medium/large = most accurate but slow "
                           "on a laptop without an NVIDIA GPU. A new model re-transcribes on the next Analyze.",
                           "Help"))
        f.addRow("Language", self.lang)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(bb)
        self._cat_changed()
        self.resize(560, 360)

    def _cat_changed(self, *_):
        c = self.cat.currentData() or ""
        self.cat_about.setText(self.categories.get(c, {}).get("about", ""))
        self.apply_defaults.setEnabled(bool(c))
        self.apply_defaults.setChecked(bool(c) and c != self._cat0)

    def values(self):
        return {"whisper_model": self.model.currentText(), "language": self.lang.text().strip(),
                "category": self.cat.currentData() or "", "apply_defaults": self.apply_defaults.isChecked()}
