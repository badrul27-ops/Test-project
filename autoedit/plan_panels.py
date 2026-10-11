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
    """Instructions tab: ONE editing plan (what is shown when), then the whole-video guidelines and the
    optional helpers for 'Smart' mode. The narration script lives in the Script tab."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plan_panel = EditingPlanPanel(win)
        self.timed = self.plan_panel                 # older name
        lay.addWidget(self.plan_panel, 3)
        row = QVBoxLayout()                    # stacked: the side panel can be narrow
        b_ins = QPushButton("Editing instructions per scene… (F8)")
        b_ins.setToolTip("Scene-by-scene visual, timing, motion, B-roll, sound, effects, transitions and locks")
        b_ins.clicked.connect(lambda: self.win.open_instructions())
        b_rev = QPushButton("Review scenes…")
        b_rev.setToolTip("What each scene shows, why it was chosen, and what needs a decision (Ctrl+Shift+R)")
        b_rev.clicked.connect(lambda: self.win.open_review_window())
        row.addWidget(b_ins)
        row.addWidget(b_rev)
        lay.addLayout(row)

        def section(title, open_=False):
            btn = QToolButton()
            btn.setCheckable(True)
            body = QWidget()
            bl = QVBoxLayout(body)
            bl.setContentsMargins(0, 0, 0, 0)

            def toggled(c):
                body.setVisible(c)
                btn.setText(title + (" ▾" if c else " ▸"))
            btn.toggled.connect(toggled)
            btn.setChecked(open_)
            toggled(open_)
            lay.addWidget(btn)
            lay.addWidget(body)
            return bl

        g = section("Project guidelines (whole video)")
        g.addWidget(label("Written guidance for the whole video (rule-based - lines it doesn't understand are "
                          "listed), then the structured settings below. Scenes follow these unless the editing plan "
                          "says otherwise for that scene.", "Help"))
        self.proj = QPlainTextEdit()
        self.proj.setPlaceholderText("e.g.\nno camera motion\nscenes of 3-8 seconds")
        self.proj.setMinimumHeight(70)
        self.proj.setTabChangesFocus(True)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(700)
        self._t.timeout.connect(self.save_project_instr)
        self.proj.textChanged.connect(self._t.start)
        g.addWidget(self.proj)
        self.proj_fb = label("", "Help")
        g.addWidget(self.proj_fb)
        more = QToolButton()
        more.setText("Supported phrases ▸")
        more.setCheckable(True)
        self.help = label(PROJECT_HELP, "Help")
        self.help.setVisible(False)
        more.toggled.connect(lambda c: (self.help.setVisible(c), more.setText("Supported phrases ▾" if c else
                                                                               "Supported phrases ▸")))
        g.addWidget(more)
        g.addWidget(self.help)
        self.guide = GuidelinesBox(win)
        g.addWidget(self.guide)
        h = section("Smart mode helpers")
        h.addWidget(label("Not used by the editing plan. Only for scene mode 'Smart': it matches images to the script by meaning, using "
                          "descriptions you import. Image content is not analysed by an AI model. Nothing is "
                          "uploaded anywhere. Re-plan from the Scenes page.", "Help"))
        b1 = QPushButton("Import scene map…")
        b1.setToolTip("CSV or JSON with columns: scene_id, asset_filename, script_excerpt, start_seconds, "
                      "end_seconds, fit_mode, motion, locked")
        b1.clicked.connect(self.import_map)
        b2 = QPushButton("Import image prompts…")
        b2.setToolTip("The prompts / descriptions your images were made from (CSV/JSON with asset_filename + "
                      "prompt, or a .txt with one prompt per paragraph in image order).")
        b2.clicked.connect(self.import_prompts)
        h.addWidget(b1)
        h.addWidget(b2)
        self.map_info = label("", "Help")
        h.addWidget(self.map_info)
        self.b_clear_map = QPushButton("Remove imported scene map")
        self.b_clear_map.clicked.connect(self.clear_map)
        h.addWidget(self.b_clear_map)
        self.issues = QListWidget()
        self.issues.setMaximumHeight(110)
        h.addWidget(self.issues)
        lay.addStretch(0)

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
        """Smart-mode planning (Scenes page → Re-plan uses the same). Not used for the editing plan."""
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


# ============================================================================ Editing plan (required)
PLAN_HELP = """Paste your complete editing plan - any of these work, and they can be mixed with notes:

  01:56.84 - 02:06.76 | 023.jpg | slow zoom in, crossfade

  | Scene | Image   | Start    | End      | Duration | Motion       | Notes            |
  |-------|---------|----------|----------|----------|--------------|------------------|
  | 23    | 023.jpg | 01:56.84 | 02:06.76 | 9.92     | slow zoom in | plaque close-up  |

  image,start,end,motion,notes        (CSV with a header row)

  SCENE 023 / Image: 023.jpg / Start: 01:56.840 / End: 02:06.760 …   (scene blocks)

  - **023.jpg:** a note about this image     (notes go to that image's scene - not applied as edits)

Start and End decide the timing; a Duration column is only checked. Shots must cover the whole narration
with no gaps or overlaps. Anything not understood is listed - never guessed."""
DETAIL_GROUPS = (("Transition", ("transition",)), ("Sound effects", ("sound",)), ("B-roll", ("b-roll",)),
                 ("Effects and text", ("effect",)), ("Framing and captions", ("framing", "captions", "focus")),
                 ("Lock", ("lock",)))


class EditingPlanPanel(QWidget):
    """THE editing plan: paste or import it, validate it, review every scene in a table, apply it to the
    timeline. Strict: the timeline gets exactly these images and times - never a file-order fallback."""
    COLS = ("Image", "Script excerpt", "Start", "End", "Duration", "Motion", "Status")
    FIELDS = ("image", None, "start", "end", None, "motion", None)
    STATUS_COL = 6

    def __init__(self, win, big=False):
        super().__init__()
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QLineEdit, QMenu, QSplitter
        self.win, self.entries, self._filling, self._probs = win, [], False, []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if big:
            lay.addWidget(label("Editing plan", "Section"))
        hl = label("Paste or Import Plan → Validate → Apply to Timeline. Examples: click in the empty box, or More → "
                   "Save a plan template. (The narration script stays separate.)", "Help")
        hl.setToolTip(PLAN_HELP)
        lay.addWidget(hl)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(PLAN_HELP)
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.edit.setMinimumHeight(120 if not big else 260)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(800)
        self._t.timeout.connect(self.save)
        self.edit.textChanged.connect(self._t.start)
        r = QHBoxLayout()
        b_imp = QPushButton("Import Plan…")
        b_imp.setToolTip("A .txt / .md / .csv file with your plan (replaces the text above)")
        b_imp.clicked.connect(self.import_plan)
        b_val = QPushButton("Validate")
        b_val.setToolTip("Check every scene - file names, times, gaps and overlaps. Nothing is changed.")
        b_val.clicked.connect(self.validate)
        self.b_apply = QPushButton("Apply to Timeline")
        self.b_apply.setProperty("primary", True)
        self.b_apply.setToolTip("Validate, then put exactly these images and times on the timeline (Ctrl+Z undoes). "
                                "If anything is wrong, nothing is applied.")
        self.b_apply.clicked.connect(self.apply)
        more = QToolButton()
        more.setText("More ▾")
        more.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(more)
        m.addAction("Fill template from narration", self.fill_template)
        m.addAction("Fill scene-sheet template", lambda: self.fill_template(sheet=True))
        m.addAction("Update plan from timeline", self.from_timeline)
        m.addSeparator()
        m.addAction("Save a plan template…", self.save_template)
        m.addAction("Save diagnostic plan (what the timeline uses)…", self.save_diagnostic)
        if not big:
            m.addAction("Open in a large window (F9)", lambda: self.win.open_plan_window())
        more.setMenu(m)
        if big:
            for b in (b_imp, b_val, self.b_apply, more):
                r.addWidget(b)
        else:                                  # the side panel is narrow: two rows, nothing cut off
            r1 = QHBoxLayout()
            r1.addWidget(b_imp)
            r1.addWidget(b_val)
            r.addWidget(self.b_apply, 1)
            r.addWidget(more)
            r = (r1, r)
        self.status = label("", "Help")
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        hh = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setIconSize(QSize(48, 27))
        self.table.setMinimumHeight(220 if not big else 160)
        from .media_bin import loader
        loader().updated.connect(self._refresh_icons)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.currentCellChanged.connect(lambda *_: (self._show_details(), self._update_fix_buttons()))
        self.table.cellDoubleClicked.connect(self._row_double_clicked)
        self.b_det = QToolButton()
        self.b_det.setText("Scene details ▸")
        self.b_det.setToolTip("Sound effects, B-roll, effects, text and transition for the selected scene")
        self.b_det.setCheckable(True)
        self.det = QWidget()
        dl = QVBoxLayout(self.det)
        dl.setContentsMargins(0, 0, 0, 0)
        self.det_text = label("", "Help")
        self.det_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.det_look = QLineEdit()
        self.det_look.setPlaceholderText("Look for this scene, e.g.  crossfade, sfx whoosh.wav, show 006.mp4 as "
                                         "b-roll, sepia, text \"Morocco, 1910\"")
        self.det_look.editingFinished.connect(self._look_edited)
        self.det_notes = QLineEdit()
        self.det_notes.setPlaceholderText("Notes for this scene (kept with it - not applied as edits)")
        self.det_notes.editingFinished.connect(self._notes_edited)
        dl.addWidget(self.det_text)
        dl.addWidget(label("Look (sound, B-roll, effects, text, transition):", "Help"))
        dl.addWidget(self.det_look)
        dl.addWidget(label("Notes:", "Help"))
        dl.addWidget(self.det_notes)
        self.det.setVisible(False)
        self.b_det.toggled.connect(lambda c: (self.det.setVisible(c), self.b_det.setText(
            "Scene details " + ("▾" if c else "▸"))))
        self.b_map = QPushButton("Map folder…")
        self.b_map.setToolTip("Your plan names a folder (e.g. Numbered_Images/) that isn't inside the images folder: "
                              "choose the folder those pictures are in - once, for every row of this project")
        self.b_map.clicked.connect(self.map_folder)
        self.b_choose = QPushButton("Choose image…")
        self.b_choose.setToolTip("Pick the picture for the selected scene (writes its file name into the plan)")
        self.b_choose.clicked.connect(self.choose_image)
        self.fix_row = QHBoxLayout()
        self.fix_row.addWidget(self.b_map)
        self.fix_row.addWidget(self.b_choose)
        self.fix_row.addStretch(1)
        self.b_map.setVisible(False)
        self.b_choose.setVisible(False)
        self.problems = QListWidget()
        self.problems.setMaximumHeight(120)
        self.problems.setWordWrap(True)
        self.problems.itemDoubleClicked.connect(self._goto)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        for row_ in (r if isinstance(r, tuple) else (r,)):   # the actions first: visible even on small screens
            tl.addLayout(row_)
        tl.addWidget(self.edit, 1)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.status)
        bl.addLayout(self.fix_row)
        bl.addWidget(self.problems)            # problems first: they decide whether anything can be applied
        bl.addWidget(self.table, 1)
        bl.addWidget(self.b_det)
        bl.addWidget(self.det)
        sp = QSplitter(Qt.Vertical)
        sp.setChildrenCollapsible(False)
        sp.addWidget(top)
        sp.addWidget(bottom)
        lay.addWidget(sp, 1)
        win._bindings.append(self.load)

    # ------------------------------------------------------------------ text <-> project
    def load(self):
        txt = self.win.project.get("timed_plan") or ""
        if self.edit.toPlainText() != txt and not self.edit.hasFocus():
            with QSignalBlocker(self.edit):
                self.edit.setPlainText(txt)
            self.validate(quiet=True)

    def save(self):
        txt = self.edit.toPlainText()
        p = self.win.project
        if (p.get("timed_plan") or "") == txt:
            return
        p["timed_plan"] = txt
        self.win.commit("Edit editing plan")
        self.win._load_plan_boxes()

    def _set_text(self, txt, label_text):
        with QSignalBlocker(self.edit):
            self.edit.setPlainText(txt)
        self.win.project["timed_plan"] = txt
        self.win.commit(label_text)
        self.win._load_plan_boxes()

    def import_plan(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import editing plan", "",
                                              "Plan (*.txt *.md *.csv *.tsv);;All files (*)")
        if path:
            self.import_file(path)

    def import_file(self, path):
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            txt = f.read()
        if path.lower().endswith(".tsv"):
            txt = txt.replace("\t", ",")
        if self.edit.toPlainText().strip() and self.edit.toPlainText() != txt:
            if QMessageBox.question(self, "Import plan", f"Replace the current plan with {os.path.basename(path)}?"
                                    ) != QMessageBox.Yes:
                return
        self._set_text(txt, "Import editing plan")
        self.validate()

    def fill_template(self, sheet=False):
        from . import timedplan
        p = self.win.project
        if not p.get("words"):
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first - the template takes its times "
                                    "from your recording.")
            return
        if self.edit.toPlainText().strip():
            if QMessageBox.question(self, "Editing plan", "Replace the current plan with a fresh template?"
                                    ) != QMessageBox.Yes:
                return
        self._set_text(timedplan.sheet_template(p["words"], p["duration"]) if sheet else
                       timedplan.template(p["words"], p["duration"]), "Fill plan template")
        self.validate()

    def from_timeline(self):
        from . import timedplan
        p = self.win.project
        if not p.get("scenes"):
            return
        if self.edit.toPlainText().strip():
            if QMessageBox.question(self, "Editing plan", "Replace the plan text with the current timeline "
                                    "(as a scene sheet)?") != QMessageBox.Yes:
                return
        self._set_text(timedplan.export_sheet(p), "Update plan from timeline")
        self.validate()

    def save_template(self):
        from . import timedplan
        path, flt = QFileDialog.getSaveFileName(self, "Save a plan template", "editing_plan_template.md",
                                                "Markdown table (*.md);;CSV (*.csv);;JSON (*.json);;Plain lines (*.txt)")
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in timedplan.TEMPLATES:
            ext = {"CSV": ".csv", "JSON": ".json", "Plain": ".txt"}.get(flt.split(" ")[0], ".md")
            path += ext
        with open(path, "w", encoding="utf-8") as f:
            f.write(timedplan.TEMPLATES[ext])
        self.win.status_msg(f"Template saved: {path} - fill it in, then Import Plan.")

    def save_diagnostic(self):
        from . import timedplan
        p = self.win.project
        if not p.get("scenes"):
            QMessageBox.information(self, "AutoEditor", "The timeline is empty - apply a plan first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostic plan", "diagnostic_plan.json", "JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        d = timedplan.save_diagnostic_plan(p, path)
        self.win.status_msg(f"Diagnostic plan saved ({len(d['scenes'])} scenes, timeline revision "
                            f"{d['timeline_revision']}): {path} and a .csv next to it.")

    # ------------------------------------------------------------------ validate / review table
    def _parse(self):
        from . import timedplan
        from .project import project_media
        p = self.win.project
        return timedplan.parse_entries(p.get("timed_plan") or "", project_media(p), p.get("duration") or None,
                                       int(p["settings"].get("fps", 30)), p.get("words"),
                                       ctx=timedplan.plan_context(p))

    def validate(self, quiet=False):
        """Parse the plan and show every scene + every problem. Changes nothing. -> (rows, problems)"""
        if not quiet:
            self.save()
        self.entries, rows, probs = self._parse()
        self._probs = probs
        self._fill_table()
        self.problems.clear()
        for x in probs:
            it = QListWidgetItem(("✖ " if x["level"] == "error" else "⚠ ") + x["msg"])
            it.setForeground(QColor("#FF5252" if x["level"] == "error" else "#FFB300"))
            it.setData(Qt.UserRole, x.get("line") or 0)
            self.problems.addItem(it)
        p = self.win.project
        errs = [x for x in probs if x["level"] == "error"]
        self.problems.setVisible(bool(probs))
        if errs and not quiet:
            self.show_first_problem()
        cover = sum(r["end"] - r["start"] for r in rows)
        mode = "" if p["settings"].get("scene_mode") == "timed" else \
            "  (Applying switches scene mode to 'My timed edit plan'.)"
        if hasattr(self.win, "update_plan_banner"):
            self.win.update_plan_banner()
        if not (p.get("timed_plan") or "").strip():
            self.status.setText("No plan yet - paste it above, or Import Plan." + mode)
        else:
            self.status.setText(self.summary(rows, probs, cover) + mode)
        self._update_fix_buttons(probs)
        return rows, probs

    KIND_TEXT = (("timing", "row(s) with times that can't be read"), ("no_asset", "row(s) without an image"),
                 ("unmapped", "image path(s) that can't be found in your images folder - use Map folder…"),
                 ("not_found", "image(s) not in the images folder"),
                 ("ambiguous", "name(s) that fit several files - choose one"),
                 ("gap", "gap(s) in the timing"), ("overlap", "overlap(s) in the timing"))

    def summary(self, rows, probs, cover=None):
        """One compact line: rows, images assigned, coverage, and problems grouped by what is wrong."""
        from collections import Counter
        p = self.win.project
        n = len(self.entries)
        assigned = sum(1 for e in self.entries if e.get("media") and not e.get("errors"))
        cover = sum(r["end"] - r["start"] for r in rows) if cover is None else cover
        kinds = Counter(e.get("kind", "timing") for e in self.entries if e.get("errors"))
        for x in probs:                        # timing gaps/overlaps belong to no single row
            if x["level"] == "error" and x.get("kind") in ("gap", "overlap"):
                kinds[x["kind"]] += 1
        head = (f"{n} timed row(s); {assigned} image(s) assigned; {cover:.2f} s of "
                f"{float(p.get('duration') or 0):.2f} s covered. ")
        if not any(x["level"] == "error" for x in probs):
            return head + "✓ Ready to apply."
        parts = [f"{kinds[k]} {t}" for k, t in self.KIND_TEXT if kinds.get(k)]
        other = sum(1 for x in probs if x["level"] == "error") - sum(kinds.values())
        if other > 0:
            parts.append(f"{other} other problem(s)")
        return (head + "✖ " + "; ".join(parts) + ". Nothing was applied - the timeline still shows the PREVIOUS "
                "version. Details are in the list below (double-click one to jump to its line).")

    def _plan_folder(self):
        """The folder name most rows of the plan use (e.g. 'Numbered_Images'), or ''."""
        from collections import Counter
        c = Counter(os.path.dirname(e["ref"].replace("\\", "/")) for e in self.entries
                    if e.get("ref") and ("/" in e["ref"] or "\\" in e["ref"]))
        return c.most_common(1)[0][0] if c else ""

    def _update_fix_buttons(self, probs=None):
        probs = self._probs if probs is None else probs
        folder = self._plan_folder()
        roots = self.win.project["settings"].get("plan_roots") or {}
        need = folder and (folder in roots or any(e.get("kind") in ("unmapped", "not_found", "ambiguous")
                                                  for e in self.entries if e.get("errors"))
                           or any(x.get("kind") == "mapped" for x in probs))
        self.b_map.setVisible(bool(need))
        self.b_map.setText(f"Map folder “{folder}”…" + (f" (now: {os.path.basename(roots[folder])})"
                                                         if folder in roots else ""))
        r = self.table.currentRow()
        bad = 0 <= r < len(self.entries) and self.entries[r].get("kind") in ("no_asset", "not_found", "ambiguous",
                                                                            "unmapped")
        self.b_choose.setVisible(bool(bad))

    def map_folder(self):
        """One folder for every row that names the plan's folder - stored in this project (undoable)."""
        folder = self._plan_folder()
        if not folder:
            return
        st = self.win.project["settings"]
        start = (st.get("plan_roots") or {}).get(folder) or self.win.project.get("media_dir") or ""
        path = QFileDialog.getExistingDirectory(self, f"Which folder holds the “{folder}” images?", start)
        if not path:
            return
        self.set_folder_mapping(folder, path)

    def set_folder_mapping(self, folder, path):
        st = self.win.project["settings"]
        st["plan_roots"] = dict(st.get("plan_roots") or {}, **{folder: os.path.normpath(path)})
        self.win.commit(f"Map plan folder {folder}")
        self.validate(quiet=True)

    def candidates(self, r):
        """Pictures that could be meant for row r: same file name first, else every scene picture."""
        from .project import project_media
        from .timedplan import _is_reference
        p = self.win.project
        media = project_media(p)
        e = self.entries[r]
        base = os.path.basename((e.get("ref") or "").replace("\\", "/")).lower()
        same = [m for m in media if os.path.basename(m).lower() == base]
        return same or [m for m in media if not _is_reference(m, p.get("media_dir") or "")]

    def choose_image(self):
        """Compact picker (thumbnail + path) for the selected scene; the choice is written into the plan."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from .media_bin import loader
        r = self.table.currentRow()
        if not (0 <= r < len(self.entries)):
            return
        root = self.win.project.get("media_dir") or ""
        d = QDialog(self)
        d.setWindowTitle(f"Choose the picture for {self.entries[r].get('label') or 'this scene'}")
        lay = QVBoxLayout(d)
        lay.addWidget(label(f"The plan says “{self.entries[r].get('ref') or '(nothing)'}”.", "Help"))
        lw = QListWidget()
        lw.setIconSize(QSize(96, 54))
        for m in self.candidates(r):
            rel = os.path.relpath(m, root) if root and os.path.commonpath([os.path.abspath(m), os.path.abspath(root)]) \
                == os.path.abspath(root) else m
            it = QListWidgetItem(rel)
            it.setData(Qt.UserRole, rel)
            pm = loader().thumb(m, 96, 54)
            if pm is not None:
                from PySide6.QtGui import QIcon
                it.setIcon(QIcon(pm))
            lw.addItem(it)
        lw.itemDoubleClicked.connect(lambda _it: d.accept())
        lay.addWidget(lw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.resize(520, 420)
        self._picker = d
        if lw.count():
            lw.setCurrentRow(0)
        if d.exec() and lw.currentItem() is not None:
            self._edit(r, "image", lw.currentItem().data(Qt.UserRole).replace("\\", "/"))

    def show_first_problem(self):
        """Select the first scene that can't be applied (or the first error in the list) so it's easy to find."""
        bad = next((i for i, e in enumerate(self.entries) if e.get("errors")), None)
        if bad is not None:
            self.table.setCurrentCell(bad, 0)
            self.table.scrollToItem(self.table.item(bad, 0))
        for i in range(self.problems.count()):
            if self.problems.item(i).text().startswith("✖"):
                self.problems.setCurrentRow(i)
                self.problems.scrollToItem(self.problems.item(i))
                break

    def check(self):                       # older name (tests, other windows)
        return self.validate()

    def _applied(self, e):
        """Is this scene on the timeline exactly as planned (same picture, same frame-snapped times)?"""
        from .instructions import snap
        fps = int(self.win.project["settings"].get("fps", 30))
        if not e.get("media") or e.get("start") is None:
            return False
        a, b = snap(e["start"], fps), snap(e["end"], fps)
        return any(abs(s["start"] - a) < 1e-3 and abs(s["end"] - b) < 0.02 and
                   os.path.normcase(s.get("media") or "") == os.path.normcase(e["media"])
                   for s in self.win.project.get("scenes") or [])

    def _fill_table(self):
        from .timedplan import _f
        self._filling = True
        cur = self.table.currentRow()
        self.table.setRowCount(len(self.entries))
        from .scenes import scene_text
        words = self.win.project.get("words") or []
        for i, e in enumerate(self.entries):
            a, b = e.get("start"), e.get("end")
            said = scene_text(words, a, b) if words and a is not None and b is not None else ""
            vals = (e.get("ref") or "", said[:90] + ("…" if len(said) > 90 else ""),
                    _f(a) if a is not None else "", _f(b) if b is not None else "",
                    f"{b - a:.2f}" if a is not None and b is not None else "", e.get("motion") or "")
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if self.FIELDS[c] is None:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if c == 2 and a is not None:
                    it.setToolTip(f"{a:.3f} s exactly")
                if c == 3 and b is not None:
                    it.setToolTip(f"{b:.3f} s exactly")
                if c == 1:
                    it.setToolTip(said or "Analyze the voiceover to see what is said here")
                if c == 0 and e.get("media"):
                    it.setToolTip(e["media"])
                self.table.setItem(i, c, it)
            if e.get("errors"):
                st, col, tip = "✖ " + e["errors"][0].split(": ", 1)[-1], "#FF5252", "\n".join(e["errors"])
            elif e.get("warnings"):
                st, col, tip = f"⚠ {len(e['warnings'])} warning(s)", "#FFB300", "\n".join(e["warnings"])
            elif self._applied(e):
                st, col, tip = "✓ On the timeline", "#4CAF50", "This scene is on the timeline exactly as planned."
            else:
                st, col, tip = "✓ Valid - not applied yet", "#4FA3FF", "Press Apply to Timeline."
            if e.get("notes"):
                tip += "\n\nNotes: " + e["notes"]
            it = QTableWidgetItem(st)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            it.setForeground(QColor(col))
            it.setToolTip(tip)
            self.table.setItem(i, self.STATUS_COL, it)
            self.table.setVerticalHeaderItem(i, QTableWidgetItem(str(e["scene"] if e.get("scene") is not None
                                                                     else i + 1)))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(1, min(320, max(160, self.table.columnWidth(1))))
        self._refresh_icons()
        if 0 <= cur < len(self.entries):
            self.table.setCurrentCell(cur, 0)
        self._filling = False
        self._show_details()

    def _cell_changed(self, item):
        if self._filling:
            return
        r, c = item.row(), item.column()
        if not (0 <= r < len(self.entries)) or self.FIELDS[c] is None:
            return
        self._edit(r, self.FIELDS[c], item.text())

    def _edit(self, r, field, value):
        from . import timedplan
        self.save()
        old = self.win.project.get("timed_plan") or ""
        new, msg = timedplan.edit_entry(old, self.entries, r, field, value)
        if new != old:
            self._set_text(new, f"Edit plan scene {r + 1}")
        self.validate(quiet=True)
        if msg:
            self.status.setText(msg + "  " + self.status.text())
        if 0 <= r < self.table.rowCount():
            self.table.setCurrentCell(r, 0)

    def _refresh_icons(self):
        """Thumbnails arrive from the background loader - the table never waits for them."""
        from .media_bin import loader
        for i, e in enumerate(self.entries):
            it = self.table.item(i, 0)
            if it is None or not e.get("media"):
                continue
            pm = loader().thumb(e["media"], 64, 36)
            if pm is not None:
                from PySide6.QtGui import QIcon
                was, self._filling = self._filling, True
                it.setIcon(QIcon(pm))
                self._filling = was

    def _notes_edited(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.entries) and self.det_notes.text().strip() != (self.entries[r].get("notes") or ""):
            self._edit(r, "notes", self.det_notes.text())

    def _look_edited(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.entries) and self.det_look.text().strip() != (self.entries[r].get("look") or ""):
            self._edit(r, "look", self.det_look.text())

    def _show_details(self):
        r = self.table.currentRow()
        if not (0 <= r < len(self.entries)):
            self.det_text.setText("Select a scene in the table to see its details.")
            self.det_look.setEnabled(False)
            self.det_notes.setEnabled(False)
            return
        self.det_notes.setEnabled(True)
        e = self.entries[r]
        interp = [m for _c, m in (e.get("interp") or [])]
        lines = [f"Plan line {e.get('src_line') or e['line']} ({ {'sheet': 'scene sheet', 'table': 'table row', 'csv': 'CSV row', 'lines': 'plan line'}.get(e.get('fmt'), e.get('fmt')) })"]
        if e.get("start") is not None and e.get("end") is not None:
            from .instructions import snap
            fps = int(self.win.project["settings"].get("fps", 30))
            lines.append(f"Time: {e['start']:.3f} - {e['end']:.3f} s ({e['end'] - e['start']:.3f} s); on the timeline "
                         f"cuts land on whole frames: {snap(e['start'], fps):.3f} - {snap(e['end'], fps):.3f} s")
        mo = [m for m in interp if m.startswith("motion")]
        lines.append("Motion: " + ("; ".join(mo) if mo else (e.get("motion") or "project guidelines")))
        for name, keys in DETAIL_GROUPS:
            got = [m for m in interp if m.lower().startswith(keys)]
            lines.append(f"{name}: " + ("; ".join(got) if got else "none"))
        bad = [w for w in e.get("warnings") or [] if "not understood" in w]
        if bad:
            lines.append("NOT applied (not understood): " + "; ".join(w.split(": ", 1)[-1] for w in bad))
        if e.get("notes"):
            lines.append("Notes (kept, not applied): " + e["notes"])
        for x in e.get("errors") or []:
            lines.append("✖ " + x)
        self.det_text.setText("\n".join(lines))
        self.det_look.setEnabled(e.get("fmt") != "sheet")
        self.det_look.setToolTip("" if e.get("fmt") != "sheet" else
                                 "Scene sheets keep these in their own fields - edit them in the plan text")
        with QSignalBlocker(self.det_look):
            self.det_look.setText(e.get("look") or "")
        with QSignalBlocker(self.det_notes):
            self.det_notes.setText(e.get("notes") or "")

    def _row_double_clicked(self, r, c):
        if self.FIELDS[c] is None and 0 <= r < len(self.entries):
            self._goto_line(self.entries[r].get("src_line") or self.entries[r]["line"])

    def _goto(self, it):
        self._goto_line(int(it.data(Qt.UserRole) or 0))

    def _goto_line(self, n):
        if n <= 0:
            return
        from PySide6.QtGui import QTextCursor
        blk = self.edit.document().findBlockByNumber(n - 1)
        c = QTextCursor(blk)
        c.select(QTextCursor.LineUnderCursor)
        self.edit.setTextCursor(c)
        self.edit.setFocus()

    # ------------------------------------------------------------------ apply
    def apply(self):
        """Validate; if anything is wrong nothing changes. Otherwise build the timeline exactly from the plan."""
        from . import timedplan
        rows, probs = self.validate()
        p = self.win.project
        if not p.get("words"):
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first.")
            return False
        if any(x["level"] == "error" for x in probs):
            first = next(x["msg"] for x in probs if x["level"] == "error")
            QMessageBox.warning(self, "Editing plan", self.summary(rows, probs) + "\n\nFirst problem: " + first
                                + "\n\nEvery problem is listed under the plan; the first broken row is selected.")
            self.show_first_problem()
            return False
        p["settings"]["scene_mode"] = "timed"
        try:
            timedplan.build(p, self.win.log)
        except ValueError as e:
            QMessageBox.warning(self, "Editing plan", str(e)[:2500])
            self.win.restore(self.win._last_state)
            return False
        self.win.sel = 0
        self.win.commit("Apply editing plan")
        self.win.refresh_all()
        self.validate(quiet=True)
        rep = p.get("plan_report") or []
        self.win.status_msg(rep[0] if rep else "Applied.")
        if hasattr(self.win, "open_instructions"):      # every scene: image, resolved times, settings - editable
            self.win.open_instructions()
        return True

    def build(self):                       # older name (tests, other windows)
        return self.apply()


TimedPlanBox = EditingPlanPanel            # the old name, used by older code
