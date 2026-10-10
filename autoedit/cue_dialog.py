"""'Match images to narration' - type which image starts at which sentence."""
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import cues as C
from .media_bin import loader
from .project import project_media
from .util import fmt_time


class CueDialog(QDialog):
    seek = Signal(float)

    def __init__(self, parent, project):
        super().__init__(parent)
        self.setWindowTitle("Match images to narration")
        self.project = project
        self.words = project.get("words") or []
        self.media = project_media(project)
        self.index = C.media_index(self.media)
        self.segs = C.segments(self.words)
        self.result = None
        lay = QVBoxLayout(self)
        help_ = QLabel("Type an image number (or file name) next to the sentence where that image should START. "
                       "It stays on screen until the next number. Leave a row empty to keep the previous image. "
                       "Several images on one row (e.g. <b>4, 5</b>) share it, split at a pause. Double-click a "
                       "time to hear it in the preview.")
        help_.setWordWrap(True)
        lay.addWidget(help_)
        self.table = QTableWidget(len(self.segs), 4)
        self.table.setHorizontalHeaderLabels(["Time", "Narration", "Image", "Shows"])
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(80, 45))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 76), (2, 90), (3, 210)):
            self.table.setColumnWidth(c, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._filling = True
        for r, (a, b, text) in enumerate(self.segs):
            it = QTableWidgetItem(fmt_time(self.words[a]["s"])[:-3])
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, it)
            it = QTableWidgetItem(text)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            it.setToolTip(text)
            self.table.setItem(r, 1, it)
            self.table.setItem(r, 2, QTableWidgetItem(""))
            it = QTableWidgetItem("")
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 3, it)
        self._filling = False
        self.table.itemChanged.connect(self._changed)
        self.table.cellDoubleClicked.connect(self._dbl)
        lay.addWidget(self.table, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        for text, fn, tip in (
                ("From current scenes", self.fill_from_scenes, "Show the current scene plan as numbers"),
                ("Import [1] markers…", self.import_file, "Read a script .txt with [1], [2]… markers, or a .csv "
                                                         "(image,text or image,start)"),
                ("Number every row 1, 2, 3…", self.number_all, "One image per sentence, in file-number order"),
                ("Clear", self.clear, "Empty every Image cell")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.apply_btn = QPushButton("Apply to timeline")
        self.apply_btn.setProperty("primary", True)
        self.apply_btn.clicked.connect(self.apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(self.apply_btn)
        row.addWidget(cancel)
        lay.addLayout(row)
        self.resize(980, 640)
        self.fill_from_scenes()
        loader().updated.connect(self._thumbs)

    def _thumbs(self):
        if self.isVisible():
            self.refresh()

    # ---------------------------------------------------------------- fill
    def _ref_for(self, path):
        n = C.media_number(path)
        if n is not None and self.index[0].get(n, [None])[0] == path:
            return str(n)
        return os.path.basename(path)

    def _seg_of_time(self, t):
        for r, (a, b, _) in enumerate(self.segs):
            if self.words[b]["e"] >= t - 0.05:
                return r
        return len(self.segs) - 1

    def set_refs(self, refs):
        self._filling = True
        for r in range(len(self.segs)):
            self.table.item(r, 2).setText(refs.get(r, ""))
        self._filling = False
        self.refresh()

    def fill_from_scenes(self):
        refs = {}
        for s in self.project.get("scenes") or []:
            if not s.get("media"):
                continue
            r = self._seg_of_time(s["start"] + 0.05)
            refs[r] = (refs[r] + ", " if r in refs else "") + self._ref_for(s["media"])
        self.set_refs(refs)

    def number_all(self):
        nums = sorted(self.index[0])
        if not nums:
            self.status.setText("⚠ No numbered images found in the media folder.")
            return
        self.set_refs({r: str(nums[r]) for r in range(min(len(self.segs), len(nums)))})

    def clear(self):
        self.set_refs({})

    def import_file(self):
        start = os.path.dirname(self.project.get("script") or self.project.get("voiceover") or "")
        path, _ = QFileDialog.getOpenFileName(self, "Script with image markers", start,
                                              "Script or CSV (*.txt *.csv);;All files (*)")
        if not path:
            return
        cl = C.load_cues(path)
        if not cl:
            QMessageBox.information(self, "Match images", "No image markers found. Put [1], [2], ... before the "
                                    "part of the narration each image covers (or use a CSV: image,text).")
            return
        C.align(self.words, cl)
        refs = {}
        for c in cl:
            r = next((i for i, (a, b, _) in enumerate(self.segs) if a <= c["wi"] <= b), len(self.segs) - 1)
            refs[r] = (refs[r] + ", " if r in refs else "") + c["ref"]
        self.set_refs(refs)
        weak = [c for c in cl if c.get("score", 1) < 0.5 and c.get("text")]
        if weak:
            self.status.setText(self.status.text() + f"  ⚠ {len(weak)} marker(s) only partly matched the recording "
                                "- check those rows.")

    # ---------------------------------------------------------------- validate
    def _changed(self, item):
        if not self._filling and item.column() == 2:
            self.refresh()

    def refresh(self):
        """Show which image each row shows; flag unknown numbers."""
        cur, n_bad, n_warn, n_cues = None, 0, 0, 0
        self._filling = True
        for r in range(len(self.segs)):
            ref = self.table.item(r, 2).text().strip()
            show = self.table.item(r, 3)
            if ref:
                paths, bad = C.resolve(ref, self.media, self.index)
                n_cues += 1
                self.table.item(r, 2).setBackground(QColor(110, 40, 44) if (bad and not paths) else
                                                    (QColor(110, 80, 20) if bad else QColor(0, 0, 0, 0)))
                self.table.item(r, 2).setToolTip("\n".join(bad))
                missing = any("no file" in b or "no such" in b for b in bad)
                n_bad += int(missing)
                n_warn += int(bool(bad) and not missing)
                cur = paths or None
                show.setText(", ".join(os.path.basename(p) for p in paths) if paths else "⚠ not found")
                show.setForeground(QColor("#ECEEF4"))
                pm = loader().thumb(paths[0], 80, 45) if paths else None
                show.setIcon(QIcon(pm) if pm is not None else QIcon())
            else:
                show.setText(("↳ " + os.path.basename(cur[-1])) if cur else "(no image yet)")
                show.setForeground(QColor("#7C8396"))
                show.setIcon(QIcon())
                self.table.item(r, 2).setBackground(QColor(0, 0, 0, 0))
        self._filling = False
        first_ok = bool(self.segs) and bool(self.table.item(0, 2).text().strip())
        msg = f"{n_cues} image cue(s) for {len(self.segs)} sentences."
        if n_bad:
            msg += f"  ⚠ {n_bad} row(s) name images that weren't found - hover for details."
        if n_warn:
            msg += f"  {n_warn} row(s) (orange) match more than one file with that number - the first is used."
        if n_cues and not first_ok:
            msg += "  The first image will start at 0:00."
        self.status.setText(msg)
        self.apply_btn.setEnabled(n_cues > 0)

    def _dbl(self, r, c):
        if c == 0:
            self.seek.emit(max(0.0, self.words[self.segs[r][0]]["s"] - 0.2))

    # ---------------------------------------------------------------- apply
    def cues(self):
        out = []
        for r, (a, b, text) in enumerate(self.segs):
            ref = self.table.item(r, 2).text().strip()
            if ref:
                paths, _ = C.resolve(ref, self.media, self.index)
                if paths:
                    out.append({"ref": ref, "wi": a, "media": paths, "text": text, "score": 1.0})
        if out:
            out[0]["wi"] = 0
        return out

    def apply(self):
        cl = self.cues()
        if not cl:
            return
        try:
            scenes, report = C.plan_from_cues(self.project, cl)
        except ValueError as e:
            QMessageBox.warning(self, "Match images", str(e))
            return
        self.result = (scenes, report)
        self.accept()
