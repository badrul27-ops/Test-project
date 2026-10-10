"""v7 panels: Script (exact narration + script-vs-recording check), Instructions (project + scene
instructions, scene-map and prompt import) and Review scenes (what was matched, why, and fixes).

They work on the window's project through a few window methods (commit, replan, select_scene, ...),
so every change is one undo step, like the rest of the app."""
import os

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QMessageBox,
                               QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from . import planner as P
from .app_parts_base import label
from .util import fmt_time

STATUS_TEXT = {"matched": "Matched", "assigned": "Your instruction", "locked": "Locked", "order": "File order",
               "fallback": "Fallback", "missing": "No visual", "manual": "Chosen by you", "auto": "Automatic",
               "accepted": "Fallback (accepted)"}
STATUS_COLOR = {"matched": "#4CAF50", "assigned": "#4FA3FF", "locked": "#B39DDB", "manual": "#4FA3FF",
                "order": "#C9A227", "fallback": "#FF8C42", "missing": "#FF5252", "accepted": "#9E9E9E"}
PROJECT_HELP = """Project instructions apply to the whole video (one per line). Understood phrases:
  • no camera motion  /  subtle motion  /  dynamic motion
  • cuts only  /  use crossfades
  • fill the frame (no bars)  /  show the whole picture (blurred background)  /  black bars
  • keep the images in chronological order  /  match by meaning (any order)
  • don't repeat images  /  allow repeats
  • scenes of 3-8 seconds
Lines that aren't understood are listed - they are never silently guessed."""
SCENE_HELP = """Scene instructions - one per line, e.g.
  Use image 003 while the narrator discusses the letter; keep it until the next topic.
  Use 12.png from 1:20 to 1:31
  Show clip 7 when the narrator says "the river burst its banks" for 5 seconds (lock)
Or paste your script split into ONE PARAGRAPH PER IMAGE (paragraph 1 = image 1 …), or use [1] [2] markers."""


def _status(s):
    st = s.get("status") or ("locked" if s.get("locked") else "auto")
    if st == "fallback" and s.get("accepted"):
        st = "accepted"
    return st


# ============================================================================ Script
class ScriptPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label("Paste the EXACT narration (what the voiceover reads). It's used for caption "
                            "spelling, for finding topic changes, and to check the recording.", "Help"))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Paste your script here. Blank lines between paragraphs help AutoEditor "
                                     "find where topics change.")
        self.edit.setMinimumHeight(160)
        self.edit.setTabChangesFocus(True)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(700)
        self._t.timeout.connect(self.save)
        self.edit.textChanged.connect(self._t.start)
        lay.addWidget(self.edit, 2)
        r = QHBoxLayout()
        b = QPushButton("Load .txt…")
        b.clicked.connect(self.load_file)
        self.b_check = QPushButton("Compare")
        self.b_check.setToolTip("Lists passages that are in the script but not spoken, spoken but not in the "
                                "script, or worded differently")
        self.b_check.clicked.connect(self.check)
        r.addWidget(b)
        r.addWidget(self.b_check)
        lay.addLayout(r)
        self.align = label("", "Help")
        lay.addWidget(self.align)
        lay.addWidget(label("Script vs recording", "Section"))
        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        self.list.itemDoubleClicked.connect(lambda it: self.win.player.seek(float(it.data(Qt.UserRole) or 0)))
        lay.addWidget(self.list, 1)
        lay.addWidget(label("Double-click a difference to hear that spot. AutoEditor never stretches or speeds up "
                            "the narration to fit the script.", "Help"))

    def load(self):
        p = self.win.project
        txt = p.get("script_text") or ""
        if not txt and p.get("script") and os.path.isfile(p["script"]):
            with open(p["script"], encoding="utf-8", errors="replace") as f:
                txt = f.read()
        if self.edit.toPlainText() != txt and not self.edit.hasFocus():
            with QSignalBlocker(self.edit):
                self.edit.setPlainText(txt)
        al = p.get("alignment") or {}
        stt = al.get("status")
        self.align.setText({"estimated": "⚠ Timing is ESTIMATED from the script (speech recognition failed) - "
                                         "check the scenes.",
                            "script+audio": "✓ Timing comes from the recording; caption spelling from the script.",
                            "audio only": "Timing comes from the recording (no script given)."}.get(stt, ""))
        self.show_issues(p.get("script_check") or [])

    def save(self):
        txt = self.edit.toPlainText()
        p = self.win.project
        if (p.get("script_text") or "") == txt:
            return
        p["script_text"] = txt
        self.win.commit("Edit script")

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Script", "", "Text (*.txt *.md);;All files (*)")
        if not path:
            return
        with open(path, encoding="utf-8", errors="replace") as f:
            self.edit.setPlainText(f.read())
        self.save()

    def raw_words(self):
        """The transcript as recognised (before script spelling was applied), when cached."""
        import json
        from .project import _cache_path
        p = self.win.project
        try:
            with open(_cache_path(p), encoding="utf-8") as f:
                return json.load(f)["words"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def check(self):
        self.save()
        p = self.win.project
        script = P.script_text(p)
        if not script or not p.get("words"):
            self.win.status_msg("Paste the script and analyze the voiceover first.")
            return
        words = self.raw_words()
        if words is None:
            self.win.status_msg("The recognised transcript isn't cached - re-run Analyze to compare.")
            return
        p["script_check"] = P.script_check(script, words)
        self.win.commit("Compare script")
        self.show_issues(p["script_check"])

    def show_issues(self, issues):
        self.list.clear()
        if not issues:
            self.list.addItem("No differences found (or not checked yet)." if not self.win.project.get("words")
                              else "✓ The recording matches the script.")
            return
        self.list.addItem(P.check_summary(issues))
        names = {"missing": "In script, not spoken", "extra": "Spoken, not in script", "changed": "Worded differently",
                 "word": "One word differs"}
        for i in issues[:300]:
            txt = f"{fmt_time(i['t'])}  {names[i['kind']]}: "
            if i["kind"] == "missing":
                txt += f"“{i['script'][:90]}”"
            elif i["kind"] == "extra":
                txt += f"“{i['spoken'][:90]}”"
            else:
                txt += f"script “{i['script'][:60]}” → spoken “{i['spoken'][:60]}”"
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, i["t"])
            if i["kind"] in ("missing", "extra", "changed"):
                it.setForeground(QColor("#FF8C42"))
            self.list.addItem(it)


# ============================================================================ Instructions
class InstructionsPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.timed = TimedPlanBox(win)
        lay.addWidget(self.timed)
        lay.addWidget(label("Project guidelines", "Section"))
        lay.addWidget(label("Written guidance for the whole video (rule-based - lines it doesn't understand are "
                            "listed), then the structured settings below. Scenes inherit these unless their own "
                            "instruction says otherwise.", "Help"))
        self.proj = QPlainTextEdit()
        self.proj.setPlaceholderText("e.g.\nno camera motion\nkeep the images in chronological order\n"
                                     "scenes of 3-8 seconds")
        self.proj.setMinimumHeight(80)
        self.proj.setTabChangesFocus(True)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(700)
        self._t.timeout.connect(self.save_project_instr)
        self.proj.textChanged.connect(self._t.start)
        lay.addWidget(self.proj)
        self.proj_fb = label("", "Help")
        lay.addWidget(self.proj_fb)
        more = QToolButton()
        more.setText("Supported phrases ▸")
        more.setCheckable(True)
        self.help = label(PROJECT_HELP, "Help")
        self.help.setVisible(False)
        more.toggled.connect(lambda c: (self.help.setVisible(c), more.setText("Supported phrases ▾" if c else
                                                                               "Supported phrases ▸")))
        lay.addWidget(more)
        lay.addWidget(self.help)
        self.guide = GuidelinesBox(win)
        lay.addWidget(self.guide)
        lay.addWidget(label("Scene instructions", "Section"))
        self.scene = win._directions_box(130)
        self.scene.setPlaceholderText(SCENE_HELP)
        self.scene.setToolTip(SCENE_HELP)
        lay.addWidget(self.scene)
        lay.addWidget(label("Order of authority: locked scenes › your scene instructions / scene map › project "
                            "instructions › automatic choices.", "Help"))
        r = QVBoxLayout()
        b1 = QPushButton("Import scene map…")
        b1.setToolTip("CSV or JSON with columns: scene_id, asset_filename, script_excerpt, start_seconds, "
                      "end_seconds, fit_mode, motion, locked")
        b1.clicked.connect(self.import_map)
        b2 = QPushButton("Import image prompts…")
        b2.setToolTip("The prompts / descriptions your images were made from (CSV/JSON with asset_filename + "
                      "prompt, or a .txt with one prompt per paragraph in image order). Used to match "
                      "images to the script by meaning.")
        b2.clicked.connect(self.import_prompts)
        r.addWidget(b1)
        r.addWidget(b2)
        lay.addLayout(r)
        self.map_info = label("", "Help")
        lay.addWidget(self.map_info)
        self.b_clear_map = QPushButton("Remove imported scene map")
        self.b_clear_map.clicked.connect(self.clear_map)
        lay.addWidget(self.b_clear_map)
        b_ins = QPushButton("Editing instructions per scene… (F8)")
        b_ins.setToolTip("Scene-by-scene visual, timing, motion, B-roll, sound, effects, transitions and locks")
        b_ins.clicked.connect(lambda: self.win.open_instructions())
        lay.addWidget(b_ins)
        self.b_plan = QPushButton("Plan scenes (keeps locked scenes)")
        self.b_plan.setProperty("primary", True)
        self.b_plan.setToolTip("Re-plans with 'Smart' mode: topic changes in the script decide WHEN, image "
                               "descriptions decide WHAT. Locked scenes are kept. Ctrl+Z undoes.")
        self.b_plan.clicked.connect(self.plan)
        lay.addWidget(self.b_plan)
        b_rev = QPushButton("Review scenes…")
        b_rev.setToolTip("What each scene shows, why it was chosen, and what needs a decision (Ctrl+Shift+R)")
        b_rev.clicked.connect(lambda: self.win.open_review_window())
        lay.addWidget(b_rev)
        self.issues = QListWidget()
        self.issues.setMinimumHeight(90)
        lay.addWidget(self.issues, 1)
        lay.addWidget(label("Image content is not analysed by an AI model in this version. Matching uses the "
                            "prompts/descriptions you import, a .txt next to an image (same name), or text that "
                            "image generators embed in PNG/JPEG files. Nothing is uploaded anywhere.", "Help"))

    def load(self):
        st = self.win.project["settings"]
        txt = st.get("project_instructions") or ""
        if self.proj.toPlainText() != txt and not self.proj.hasFocus():
            with QSignalBlocker(self.proj):
                self.proj.setPlainText(txt)
        self._feedback()
        p = self.win.project
        n_map, n_meta = len(p.get("scene_map") or []), len(p.get("asset_meta") or {})
        self.map_info.setText(f"Scene map: {n_map} row(s) · image descriptions: {n_meta}")
        self.b_clear_map.setVisible(bool(n_map))
        self.show_report(p.get("plan_report") or [])

    def _feedback(self):
        ov, lines = P.parse_project_instructions(self.win.project["settings"].get("project_instructions") or "")
        if not lines:
            self.proj_fb.setText("")
            return
        bad = [ln for ln, ok in lines if not ok]
        good = ", ".join(f"{k}={v}" for k, v in ov.items())
        self.proj_fb.setText((f"Understood: {good}." if good else "") +
                             (f"  Not understood (ignored): {'; '.join(b[:40] for b in bad)}" if bad else ""))

    def save_project_instr(self):
        txt = self.proj.toPlainText()
        st = self.win.project["settings"]
        if (st.get("project_instructions") or "") == txt:
            return
        st["project_instructions"] = txt
        self.win.commit("Project instructions")
        self._feedback()

    def import_map(self):
        path, _ = QFileDialog.getOpenFileName(self, "Scene map", "", "Scene map (*.csv *.json);;All files (*)")
        if path:
            self.import_map_file(path)

    def import_map_file(self, path):
        from .project import project_media
        p = self.win.project
        try:
            rows, probs = P.load_scene_map(path, project_media(p), p.get("duration") or None)
        except Exception as e:
            self.show_report([f"Couldn't read the scene map: {e}"])
            return
        p["scene_map"] = rows
        if rows and p["settings"].get("scene_mode") != "smart":
            p["settings"]["scene_mode"] = "smart"
            self.win.load_controls()
        self.win.commit("Import scene map")
        self.show_report([f"Scene map: {len(rows)} row(s) imported from {os.path.basename(path)}."] + probs)
        self.map_info.setText(f"Scene map: {len(rows)} row(s) · image descriptions: {len(p.get('asset_meta') or {})}")
        self.b_clear_map.setVisible(bool(rows))

    def clear_map(self):
        self.win.project["scene_map"] = []
        self.win.commit("Remove scene map")
        self.load()

    def import_prompts(self):
        path, _ = QFileDialog.getOpenFileName(self, "Image prompts / descriptions", "",
                                              "Prompts (*.csv *.json *.txt);;All files (*)")
        if path:
            self.import_prompts_file(path)

    def import_prompts_file(self, path):
        from .asset_info import import_prompts
        from .project import project_media
        p = self.win.project
        try:
            n, probs = import_prompts(p, path, project_media(p))
        except Exception as e:
            self.show_report([f"Couldn't read the prompts file: {e}"])
            return
        self.win.commit("Import image prompts")
        self.show_report([f"{n} image description(s) attached (they follow the image even if it's renamed)."]
                         + probs)
        self.map_info.setText(f"Scene map: {len(p.get('scene_map') or [])} row(s) · image descriptions: "
                              f"{len(p.get('asset_meta') or {})}")

    def plan(self):
        self.win._save_directions(self.scene)
        self.save_project_instr()
        st = self.win.project["settings"]
        if st.get("scene_mode") != "smart":
            st["scene_mode"] = "smart"
            self.win.load_controls()
        self.win.replan(label_text="Plan scenes")
        self.show_report(self.win.project.get("plan_report") or [])

    def show_report(self, lines):
        self.issues.clear()
        for ln in lines:
            it = QListWidgetItem(ln)
            low = ln.lower()
            if any(w in low for w in ("not understood", "couldn't", "skipped", "no media", "isn't in", "duplicate",
                                      "overlap", "missing", "partly")):
                it.setForeground(QColor("#FF8C42"))
            self.issues.addItem(it)


# ============================================================================ Review scenes
class ReviewPanel(QWidget):
    COLS = ("", "ID", "Time", "Visual", "Status", "Why / warnings", "Script")

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._rows = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["All scenes", "Needs attention", "Locked"])
        self.filter.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.filter)
        self.summary = QLabel("")
        top.addWidget(self.summary, 1)
        lay.addLayout(top)
        btns = QHBoxLayout()
        self.b_play = QPushButton("▶ Play scene")
        self.b_play.setToolTip("Plays this scene with its narration")
        self.b_play.clicked.connect(self.play_scene)
        self.b_replace = QPushButton("Replace visual…")
        self.b_replace.clicked.connect(lambda: self._act("replace"))
        self.b_timing = QPushButton("Edit timing")
        self.b_timing.clicked.connect(lambda: self._act("timing"))
        self.b_framing = QPushButton("Adjust framing")
        self.b_framing.clicked.connect(lambda: self._act("framing"))
        self.b_lock = QPushButton("Lock")
        self.b_lock.clicked.connect(self.toggle_lock)
        self.b_accept = QPushButton("Accept fallback")
        self.b_accept.setToolTip("Keep the fallback visual on purpose (removes the warning)")
        self.b_accept.clicked.connect(self.accept_fallback)
        self.b_reanalyze = QPushButton("Reanalyze unlocked scenes")
        self.b_reanalyze.setToolTip("Re-plans every scene that isn't locked (Ctrl+Z undoes)")
        self.b_reanalyze.clicked.connect(lambda: self.win.replan(label_text="Reanalyze unlocked scenes"))
        for b in (self.b_play, self.b_replace, self.b_timing, self.b_framing, self.b_lock, self.b_accept,
                  self.b_reanalyze):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(80, 45))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        hh = self.table.horizontalHeader()
        for c, wdt in enumerate((88, 50, 156, 130, 110, 280)):
            self.table.setColumnWidth(c, wdt)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.itemDoubleClicked.connect(lambda _it: self.play_scene())
        lay.addWidget(self.table, 1)
        self.only_problems = QCheckBox()      # kept for API symmetry; filter combo is the visible control
        self._stop_at = None
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._watch)
        from .media_bin import loader
        loader().updated.connect(self._icons)

    # ---------------------------------------------------------------- data
    def scene_rows(self):
        sc = self.win.project.get("scenes") or []
        f = self.filter.currentIndex()
        out = []
        for k, s in enumerate(sc):
            st = _status(s)
            if f == 1 and not (st in ("fallback", "missing", "order") or s.get("warnings")):
                continue
            if f == 2 and not s.get("locked"):
                continue
            out.append(k)
        return out

    def refresh(self, *_):
        sc = self.win.project.get("scenes") or []
        from .planner import scene_warnings
        for s in sc:
            if "warnings" not in s:
                s["warnings"] = scene_warnings(s, self.win.project)
        self._rows = self.scene_rows()
        sel = self.current()
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(self._rows))
            for r, k in enumerate(self._rows):
                s = sc[k]
                st = _status(s)
                m = s.get("media") or ""
                why = s.get("why") or ""
                warn = [w for w in s.get("warnings") or [] if not (s.get("accepted") and "fallback" in w)]
                cells = ["", s.get("id") or f"#{k + 1}", f"{fmt_time(s['start'])}–{fmt_time(s['end'])}",
                         os.path.basename(m) if m else "—", STATUS_TEXT.get(st, st),
                         "; ".join(([why] if why else []) + (["⚠ " + w for w in warn])),
                         (s.get("text") or "")[:240]]
                for c, txt in enumerate(cells):
                    it = QTableWidgetItem(txt)
                    it.setToolTip(txt if c in (5, 6) else "")
                    if c == 4:
                        it.setForeground(QColor(STATUS_COLOR.get(st, "#D5D9E4")))
                    self.table.setItem(r, c, it)
            if sel in self._rows:
                self.table.selectRow(self._rows.index(sel))
        self._icons()
        n = {}
        for s in sc:
            n[_status(s)] = n.get(_status(s), 0) + 1
        need = sum(n.get(k, 0) for k in ("fallback", "missing"))
        self.summary.setText(f"{len(sc)} scenes · " + ", ".join(f"{v} {STATUS_TEXT.get(k, k).lower()}"
                                                                for k, v in sorted(n.items())) +
                             (f" · ⚠ {need} need a decision" if need else ""))
        self._buttons()

    def _icons(self):
        from .media_bin import loader
        sc = self.win.project.get("scenes") or []
        for r, k in enumerate(self._rows):
            if k >= len(sc) or self.table.item(r, 0) is None:
                continue
            pm = loader().thumb(sc[k].get("media"), 80, 45)
            if pm is not None:
                self.table.item(r, 0).setIcon(pm)

    def current(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows and rows[0].row() < len(self._rows):
            return self._rows[rows[0].row()]
        return None

    def select(self, k):
        if k in self._rows:
            with QSignalBlocker(self.table):
                self.table.selectRow(self._rows.index(k))
            self._buttons()

    def _selected(self):
        k = self.current()
        if k is not None:
            self.win.select_scene(k)
        self._buttons()

    def _buttons(self):
        k = self.current()
        sc = self.win.project.get("scenes") or []
        ok = k is not None and k < len(sc)
        for b in (self.b_play, self.b_replace, self.b_timing, self.b_framing, self.b_lock):
            b.setEnabled(ok)
        self.b_lock.setText("Unlock" if ok and sc[k].get("locked") else "Lock")
        self.b_accept.setEnabled(ok and sc[k].get("status") == "fallback" and not sc[k].get("accepted"))
        self.b_reanalyze.setEnabled(bool(self.win.project.get("words")))

    # ---------------------------------------------------------------- actions
    def _act(self, what):
        k = self.current()
        if k is None:
            return
        self.win.select_scene(k)
        if what == "replace":
            self.win.replace_visual()
            return
        self.win.right_tabs.setCurrentWidget(self.win.props)
        tab = {"timing": 0, "framing": 4}[what]
        self.win.props.vtabs.setCurrentIndex(tab)
        self.win.status_msg("Edit timing with 'Starts at / Ends at' in Properties → Video." if what == "timing"
                            else "Framing mode, focal point and border trim: Properties → Framing.")

    def toggle_lock(self):
        k = self.current()
        if k is None:
            return
        s = self.win.project["scenes"][k]
        s["locked"] = not s.get("locked")
        if s["locked"]:
            s["status_before_lock"] = s.get("status")
            s["status"] = "locked"
        else:
            s["status"] = s.pop("status_before_lock", None) or "manual"
        self.win.commit("Lock scene" if s["locked"] else "Unlock scene")
        self.win.refresh_all()

    def accept_fallback(self):
        k = self.current()
        if k is None:
            return
        s = self.win.project["scenes"][k]
        s["accepted"] = True
        s["warnings"] = [w for w in s.get("warnings") or [] if "fallback" not in w]
        self.win.commit("Accept fallback visual")
        self.refresh()

    def play_scene(self):
        k = self.current()
        if k is None:
            return
        s = self.win.project["scenes"][k]
        pl = self.win.player
        pl.pause()
        pl.seek(s["start"])
        self._stop_at = s["end"]
        pl.play()
        self._timer.start()

    def _watch(self):
        pl = self.win.player
        if self._stop_at is None or not pl.playing:
            self._timer.stop()
            self._stop_at = None
            return
        if pl.t >= self._stop_at - 0.02:
            pl.pause()
            pl.seek(self._stop_at - 0.02)
            self._stop_at = None
            self._timer.stop()


# ============================================================================ Project guidelines (structured)
class GuidelinesBox(QWidget):
    """Structured full-video guidelines. They write the same project settings the rest of the app uses,
    so there is one source of truth."""

    def __init__(self, win):
        super().__init__()
        from .app_parts_base import form
        from .framing import FIT_MODES
        from .guidelines import STYLES
        from .presets import CAPTION_STYLES, MOTION_LEVELS
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolButton()
        tb.setText("Structured guidelines ▸")
        tb.setCheckable(True)
        body = QWidget()
        body.setVisible(False)
        tb.toggled.connect(lambda c: (body.setVisible(c), tb.setText("Structured guidelines ▾" if c else
                                                                     "Structured guidelines ▸")))
        lay.addWidget(tb)
        lay.addWidget(body)
        f = form()
        body.setLayout(f)
        self.style = QComboBox()
        for k, (name, _) in STYLES.items():
            self.style.addItem(name, k)
        self.style.currentIndexChanged.connect(self._style_preview)
        self.style_note = label("", "Help")
        self.b_style = QPushButton("Apply this style")
        self.b_style.clicked.connect(self._apply_style)
        f.addRow("Style (starting preset)", self.style)
        f.addRow("", self.style_note)
        f.addRow("", self.b_style)
        self.pacing = QComboBox()
        win.bind_combo(self.pacing, "pacing", "Pacing", [("Relaxed (about 4-12 s shots)", "relaxed"),
                                                        ("Balanced (preset)", "balanced"),
                                                        ("Brisk (about 1.5-5 s shots)", "brisk")], effects=())
        f.addRow("Pacing", self.pacing)
        self.smin, self.smax = self._opt_spin("shot_min", "Shortest shot"), self._opt_spin("shot_max", "Longest shot")
        r = QHBoxLayout()
        r.addWidget(self.smin)
        r.addWidget(QLabel("to"))
        r.addWidget(self.smax)
        f.addRow("Preferred shot length", r)
        f.addRow("", label("Shot lengths are preferences: narration alignment, your explicit times and locks "
                           "always win; the narration is never cut or stretched to fit.", "Help"))
        self.fit = QComboBox()
        win.bind_combo(self.fit, "fit", "Default framing", [(FIT_MODES[k], k) for k in
                                                           ("crop", "fit_blur", "fit_color", "fit_bars", "blur")],
                       effects=("preview", "controls"))
        f.addRow("Default framing", self.fit)
        self.motion = QComboBox()
        win.bind_combo(self.motion, "motion_level", "Motion", [
            ("None - still pictures" if k == "None" else "Subtle automatic motion" if k == "Subtle" else
             f"{k} (custom per scene in Editing instructions)", k) for k in MOTION_LEVELS], effects=())
        f.addRow("Motion", self.motion)
        self.tr = QComboBox()
        win.bind_combo(self.tr, "transition_default", "Default transition", [
            ("Automatic (editing preset mix)", "auto"), ("Cut", "cut"), ("Dissolve (crossfade)", "crossfade"),
            ("Fade through black", "dip_black"), ("Slide", "slide"), ("Whip pan", "whip"), ("Zoom through", "zoom")],
            effects=("preview",))
        self.tr_freq = QComboBox()
        win.bind_combo(self.tr_freq, "transition_frequency", "Transition frequency", [
            ("At every cut", "every_cut"), ("Only at topic changes (cuts elsewhere)", "topic_changes")], effects=())
        self.tr_dur = self._opt_spin("transition_seconds", "Transition length", hi=3.0, special="preset default")
        f.addRow("Transitions", self.tr)
        f.addRow("How often", self.tr_freq)
        f.addRow("Transition length", self.tr_dur)
        self.sfx = QComboBox()
        win.bind_combo(self.sfx, "sfx_mode", "Sound effects guideline", [
            ("Automatic suggestions (review them)", "auto"), ("Only the ones I assign", "explicit_only"),
            ("None", "none")], effects=("sfx",))
        f.addRow("Sound effects", self.sfx)
        self.mdb = QDoubleSpinBox()
        self.mdb.setRange(-40, 0)
        self.mdb.setSuffix(" dB")
        win.bind_spin(self.mdb, "music_db", "Music level", effects=("levels",))
        self.duck = QCheckBox("Lower the music under the narration")
        win.bind_check(self.duck, "music_duck", "Ducking", effects=())
        self.duck_r = QComboBox()
        win.bind_combo(self.duck_r, "duck_ratio", "Ducking strength", [("Light", 3.0), ("Medium", 6.0),
                                                                      ("Strong", 10.0)], effects=())
        self.duck_rel = QComboBox()
        win.bind_combo(self.duck_rel, "duck_release", "Ducking recovery", [("Quick (250 ms)", 250.0),
                                                                          ("Normal (350 ms)", 350.0),
                                                                          ("Slow, no pumping (800 ms)", 800.0)],
                       effects=())
        f.addRow("Music level", self.mdb)
        f.addRow("", self.duck)
        f.addRow("Ducking strength", self.duck_r)
        f.addRow("Ducking recovery", self.duck_rel)
        f.addRow("", label("Choose the music file and fades in the Audio tab. Levels are in dB.", "Help"))
        self.cap = QCheckBox("Captions on")
        win.bind_check(self.cap, "captions", "Captions")
        self.cap_style = QComboBox()
        win.bind_combo(self.cap_style, "caption_style", "Caption style", [(c, c) for c in CAPTION_STYLES])
        self.cap_pos = QComboBox()
        win.bind_combo(self.cap_pos, "caption_position", "Caption position", [(c, c) for c in
                                                                             ("Bottom", "Middle", "Top")])
        self.cap_m = self._opt_spin("caption_margin", "Caption safe margin", lo=0, hi=30, suffix=" %",
                                    special="automatic safe zone", step=1, dec=0, effects=("preview",))
        f.addRow("", self.cap)
        f.addRow("Caption style", self.cap_style)
        f.addRow("Caption position", self.cap_pos)
        f.addRow("Safe margin", self.cap_m)
        self.cont = QPlainTextEdit()
        self.cont.setPlaceholderText("Recurring characters, objects, vehicles, locations, chronology, visual style… "
                                     "(kept as notes - AutoEditor can't check pictures for continuity)")
        self.cont.setMaximumHeight(70)
        self._ct = QTimer(self)
        self._ct.setSingleShot(True)
        self._ct.setInterval(700)
        self._ct.timeout.connect(lambda: win._set("continuity", self.cont.toPlainText(), "Continuity notes"))
        self.cont.textChanged.connect(self._ct.start)
        f.addRow("Continuity", self.cont)
        self.no_shake = QCheckBox("No shake / hand-held drift")
        win.bind_check(self.no_shake, "restrict_no_shake", "No shake", effects=())
        self.no_flash = QCheckBox("No flashing transitions (slide / whip / zoom become dissolves)")
        win.bind_check(self.no_flash, "restrict_no_flash", "No flashing", effects=())
        f.addRow("Restrictions", self.no_shake)
        f.addRow("", self.no_flash)
        f.addRow("", label("Always true in AutoEditor: no automatic B-roll, the voiceover's speed is never changed.",
                           "Help"))
        f.addRow("", label("Guidelines take effect when you plan / re-plan scenes; scene instructions and locks "
                           "override them.", "Help"))
        win._bindings.append(self._load)

    def _opt_spin(self, key, lab, lo=0.0, hi=60.0, suffix=" s", special="automatic", step=0.5, dec=1,
                  effects=()):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(dec)
        sp.setSuffix(suffix)
        sp.setSpecialValueText(special)
        sp.setKeyboardTracking(False)

        def load():
            v = self.win.project["settings"].get(key)
            with QSignalBlocker(sp):
                sp.setValue(float(v) if v not in (None, "") else lo)
        sp.valueChanged.connect(lambda v: self.win._set(key, None if v <= lo else round(v, 2), lab, effects))
        self.win._bindings.append(load)
        return sp

    def _load(self):
        st = self.win.project["settings"]
        with QSignalBlocker(self.style):
            i = self.style.findData(st.get("style", "custom"))
            self.style.setCurrentIndex(max(0, i))
        if self.cont.toPlainText() != (st.get("continuity") or "") and not self.cont.hasFocus():
            with QSignalBlocker(self.cont):
                self.cont.setPlainText(st.get("continuity") or "")
        self._style_preview()

    def _style_preview(self, *_):
        from .guidelines import style_changes
        ch = style_changes(self.style.currentData(), self.win.project["settings"])
        self.style_note.setText(("Applying changes: " + "; ".join(f"{a}: {b} → {c}" for a, b, c in ch)) if ch else
                                "Nothing to change.")
        self.b_style.setEnabled(bool(ch))

    def _apply_style(self):
        from .guidelines import apply_style
        apply_style(self.style.currentData(), self.win.project["settings"])
        self.win._apply_effects(("preview", "controls", "sfx"))
        self.win.commit("Apply style guidelines")
        self.win.status_msg("Style applied to the guidelines - re-plan scenes to use them (your scene "
                            "instructions and locks stay).")


# ============================================================================ Timed edit plan (required)
PLAN_HELP = """One line per shot:   start - end | image | how it looks (optional)
0:00 - 0:04.5 | 001.png | slow zoom in, crossfade
0:04.5 - 0:09 | 002 | pan right, sfx whoosh.wav, text "Morocco, 15,000 years ago"
0:09 - 0:12 | 003.png | still, b&w, cut
Press 'Fill template from narration' after Analyze: every sentence gets a line with its exact times -
you only replace ??? with the image."""


class TimedPlanBox(QWidget):
    """The required timed edit plan: which image from which second to which second, and how it looks."""

    def __init__(self, win, big=False):
        super().__init__()
        from PySide6.QtGui import QFontDatabase
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label("Timed edit plan (required)", "Section"))
        lay.addWidget(label("You decide which image is on screen from which second to which second. AutoEditor "
                            "builds exactly that - it never moves a cut or swaps a picture. Shots must cover the whole "
                            "narration without gaps or overlaps; the narration is never cut or stretched.", "Help"))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(PLAN_HELP)
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.edit.setMinimumHeight(220)
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(800)
        self._t.timeout.connect(self.save)
        self.edit.textChanged.connect(self._t.start)
        lay.addWidget(self.edit, 1)
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        b_tpl = QPushButton("Fill template from narration")
        b_tpl.setToolTip("One line per sentence with the exact times from your recording - replace ??? with images")
        b_tpl.clicked.connect(self.fill_template)
        b_load = QPushButton("Load plan file…")
        b_load.setToolTip(".txt in the format above, or .csv with columns start, end, image, look")
        b_load.clicked.connect(self.load_file)
        b_chk = QPushButton("Check plan")
        b_chk.clicked.connect(self.check)
        self.b_build = QPushButton("Build timeline from plan")
        self.b_build.setProperty("primary", True)
        self.b_build.clicked.connect(self.build)
        b_sheet = QPushButton("Fill scene-sheet template")
        b_sheet.setToolTip("SCENE 001 / Image: / Start: / End: / Motion: … blocks with each sentence and its exact "
                           "times filled in")
        b_sheet.clicked.connect(lambda: self.fill_template(sheet=True))
        b_sync = QPushButton("Update plan from timeline")
        b_sync.setToolTip("Write the current timeline (with your edits) back into the plan as a scene sheet")
        b_sync.clicked.connect(self.from_timeline)
        r1.addWidget(b_tpl)
        r1.addWidget(b_sheet)
        r1.addWidget(b_load)
        r2.addWidget(b_sync)
        if not big:
            b_big = QPushButton("Open big editor…")
            b_big.setToolTip("Edit the plan in a large window (same plan)")
            b_big.clicked.connect(lambda: self.win.open_plan_window())
            r2.addWidget(b_big)
        r2.addWidget(b_chk)
        r2.addWidget(self.b_build)
        lay.addLayout(r1)
        lay.addLayout(r2)
        self.status = label("", "Help")
        lay.addWidget(self.status)
        self.problems = QListWidget()
        self.problems.setMaximumHeight(130)
        self.problems.itemDoubleClicked.connect(self._goto)
        lay.addWidget(self.problems)
        more = QToolButton()
        more.setText("Words you can use after the image ▸")
        more.setCheckable(True)
        words = label("Motion: slow zoom in, zoom out, pan left/right/up/down, zoom in + pan right, still · "
                      "Transition to the next shot: cut, crossfade (0.8), fade, slide, whip · Sound: sfx paper.wav "
                      "(at “opened”, -14 dB, fade in 0.2) · Look: b&w, sepia, warm, cold, blur, vignette, darker, "
                      "brighter, more contrast · Text: text \"Morocco, 1910\" · Framing: fill, fit, focus on the top "
                      "left · Captions: no captions, captions at the top · lock. Separate them with commas. Anything "
                      "it doesn't understand is listed - never guessed.", "Help")
        words.setVisible(False)
        more.toggled.connect(words.setVisible)
        lay.addWidget(more)
        lay.addWidget(words)
        win._bindings.append(self.load)

    def load(self):
        txt = self.win.project.get("timed_plan") or ""
        if self.edit.toPlainText() != txt and not self.edit.hasFocus():
            with QSignalBlocker(self.edit):
                self.edit.setPlainText(txt)
        if self.win.project["settings"].get("scene_mode") != "timed":
            self.status.setText("Scene mode isn't 'My timed edit plan' - building the plan switches to it.")
        elif self.status.text().startswith("Scene mode isn't"):
            self.status.setText("")

    def save(self):
        txt = self.edit.toPlainText()
        p = self.win.project
        if (p.get("timed_plan") or "") == txt:
            return
        p["timed_plan"] = txt
        self.win.commit("Edit timed plan")
        self.win._load_plan_boxes()

    def from_timeline(self):
        from . import timedplan
        p = self.win.project
        if not p.get("scenes"):
            return
        if self.edit.toPlainText().strip():
            r = QMessageBox.question(self, "Timed edit plan", "Replace the plan text with the current timeline "
                                     "(as a scene sheet)?")
            if r != QMessageBox.Yes:
                return
        self.edit.setPlainText(timedplan.export_sheet(p))
        self.save()
        self.check()

    def fill_template(self, sheet=False):
        from . import timedplan
        p = self.win.project
        if not p.get("words"):
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first - the template takes its times "
                                    "from your recording.")
            return
        if self.edit.toPlainText().strip():
            r = QMessageBox.question(self, "Timed edit plan", "Replace the current plan with a fresh template?")
            if r != QMessageBox.Yes:
                return
        self.edit.setPlainText(timedplan.sheet_template(p["words"], p["duration"]) if sheet else
                               timedplan.template(p["words"], p["duration"]))
        self.save()
        self.check()

    def load_file(self):
        from . import timedplan
        path, _ = QFileDialog.getOpenFileName(self, "Timed edit plan", "", "Plan (*.txt *.csv);;All files (*)")
        if not path:
            return
        if path.lower().endswith(".csv"):
            txt = timedplan.from_csv(path)
        else:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                txt = f.read()
        self.edit.setPlainText(txt)
        self.save()
        self.check()

    def check(self):
        from . import timedplan
        from .project import project_media
        self.save()
        p = self.win.project
        rows, probs = timedplan.parse_plan(p.get("timed_plan") or "", project_media(p), p.get("duration") or None,
                                           int(p["settings"].get("fps", 30)), p.get("words"))
        self.problems.clear()
        errs = [x for x in probs if x["level"] == "error"]
        for x in probs:
            it = QListWidgetItem(("✖ " if x["level"] == "error" else "⚠ ") + x["msg"])
            it.setForeground(QColor("#FF5252" if x["level"] == "error" else "#FFB300"))
            it.setData(Qt.UserRole, x.get("line") or 0)
            self.problems.addItem(it)
        cover = sum(r["end"] - r["start"] for r in rows)
        self.status.setText(f"{len(rows)} {'scene' if timedplan.is_sheet(p.get('timed_plan') or '') else 'shot'}(s), {cover:.1f} s of {float(p.get('duration') or 0):.1f} s covered. " +
                            ("✓ Ready to build." if not errs else f"{len(errs)} problem(s) to fix first "
                                                                  "(double-click one to jump to its line)."))
        return rows, probs

    def _goto(self, it):
        n = int(it.data(Qt.UserRole) or 0)
        if n <= 0:
            return
        from PySide6.QtGui import QTextCursor
        blk = self.edit.document().findBlockByNumber(n - 1)
        c = QTextCursor(blk)
        c.select(QTextCursor.LineUnderCursor)
        self.edit.setTextCursor(c)
        self.edit.setFocus()

    def build(self):
        from . import timedplan
        rows, probs = self.check()
        p = self.win.project
        if not p.get("words"):
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first.")
            return False
        if any(x["level"] == "error" for x in probs):
            QMessageBox.warning(self, "Timed edit plan", "Fix these first - nothing was built:\n\n" +
                                "\n".join(x["msg"] for x in probs if x["level"] == "error")[:2500])
            return False
        p["settings"]["scene_mode"] = "timed"
        try:
            timedplan.build(p, self.win.log)
        except ValueError as e:
            QMessageBox.warning(self, "Timed edit plan", str(e)[:2500])
            self.win.restore(self.win._last_state)
            return False
        self.win.sel = 0
        self.win.commit("Build timeline from plan")
        self.win.refresh_all()
        rep = p.get("plan_report") or []
        self.win.status_msg(rep[0] if rep else "Built.")
        if hasattr(self.win, "open_instructions"):      # every scene: image, resolved times, settings - editable
            self.win.open_instructions()
        return True
