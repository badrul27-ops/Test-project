"""AutoEditor - desktop app (v6 editing workspace).

Layout: menu bar + header / left tool tabs (Media, Audio, Text, Effects, Transitions, Captions)
with the media bin / centre preview / right Properties + Export / bottom multi-track timeline
(V2, V1, Text, Captions, Voice, Music, SFX) + Scenes, Text, SFX, Quality and Log panels.

State model: every settings control is *bound* to exactly one project key. A control
writes only its own key and only from its own user-edit signal. Loading a project,
undo/redo and UI refreshes set controls under QSignalBlocker, so nothing is written
back by accident. Every real edit goes through commit(), which powers undo/redo and
the unsaved-changes indicator.
"""
import copy
import time
import os
import sys

from PySide6.QtCore import QSettings, QSignalBlocker, QSize, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSizePolicy, QSlider, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from autoedit import __version__
from autoedit import project as prj
from autoedit.app_parts_base import (PathRow, ProjectSettingsDialog, ToolTabBar, UndoStack, Worker, ask_unsaved,
                                     dropped_paths, form, icon, label, page)
from autoedit.audio import library_report, scan_sfx
from autoedit.editorial import description_text
from autoedit.engine import Compositor
from autoedit.fx import ATMOSPHERES
from autoedit.gui_panels import CaptionPanel, ColorButton, QualityPanel, SfxPanel, TextChaptersPanel, fmt_items
from autoedit.captions import project_caption_spans
from autoedit.gui_widgets import PlayerWidget, TimelineScrollBar, thumb
from autoedit.media_bin import MEDIA_FILTER, MediaBin, loader
from autoedit.project import project_media
from autoedit.properties import PropertiesPanel
from autoedit.timeline import VISUAL_KEYS, TimelineWidget, visual_locked
from autoedit import exportcfg as XC
from autoedit.framing import FIT_HELP, FIT_MODES as FRAMING
from autoedit.plan_panels import InstructionsPanel, ReviewPanel, ScriptPanel
from autoedit.presets import MOTION_LEVELS, PRESETS, SFX_LEVELS, TRANSITIONS
from autoedit.profiles import CATEGORIES, apply_category, category_notes, notes_text
from autoedit.render import export_package, plan_project_sfx, render_all, write_srt
from autoedit.scenes import reassign_transitions, scene_text
from autoedit.theme import WheelGuard, apply_theme
from autoedit.util import APP_DIR, AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, fmt_time, folder_report, is_video, list_media

SCENE_MODES = [("Strict plan - your editing plan's exact images and times", "timed"),
               ("Assisted plan - matches images by their descriptions (review it)", "smart"),
               ("Auto - split at pauses", "auto"), ("One scene per media file", "match_media"),
               ("Follow my directions (scene list / [1] markers)", "script_cues"),
               ("Timestamp file names (0-03.png)", "timestamps")]
FIT_MODES = [(FRAMING[k], k) for k in ("crop", "fit_blur", "fit_color", "fit_bars", "blur")]
FPS_CHOICES = [24, 25, 30, 60]
PREVIEW_SIZES = {"16:9": (640, 360), "9:16": (360, 640), "1:1": (480, 480)}
PREVIEW_TIERS = (640, 960, 1280)      # preview render size (long side) - chosen from the canvas size


def preview_size(fmt, canvas_w, canvas_h, dpr=1.0):
    """Preview render size for a canvas: sharp enough for the size it's shown at, capped for speed.
    Only the preview - the export uses the Export tab's resolution."""
    r = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}.get(fmt, 16 / 9)
    w = min(canvas_w, canvas_h * r) * dpr          # displayed frame width in Fit
    long_px = max(w, w / r)
    tier = next((t for t in PREVIEW_TIERS if t >= long_px * 0.9), PREVIEW_TIERS[-1])
    if r >= 1:
        W, H = tier, int(round(tier / r))
    else:
        W, H = int(round(tier * r)), tier
    return W - W % 2, H - H % 2
PRESET_HELP = {
    "Documentary": "3-8 s shots, slow zooms, crossfades, dip to black between sections.",
    "Dark Psychology": "Slower moves, strong vignette, desaturated look.",
    "Energetic (Cars)": "Short shots, push-ins, whip / slide / zoom transitions.",
    "Shorts / Reels (fast)": "Very quick cuts and punchy motion.",
    "Vox Style": "2-5 s shots, mostly still or gentle moves, clean cuts and slides.",
    "Long Story History": "5-11 s shots, slow zooms and pans, long crossfades, dip to black between sections.",
    "Horror": "Slow creeping push-ins, dark desaturated look, dips to black.",
    "2D Animation": "Lively pans and zooms for illustrated scenes, slides and cuts.",
    "Stickman": "Mostly still with gentle pushes, clean cuts - keeps drawings readable.",
}
DIRECTIONS_HELP = ("Paste your script split into scenes - ONE PARAGRAPH PER IMAGE (blank line between), in the "
                   "same order as your numbered images. Paragraph 1 = image 1, paragraph 2 = image 2...\n\n"
                   "Or use markers to be exact:  [1] first lines...  [2] next lines...  [3, 4] split this part...\n\n"
                   "AutoEditor finds each paragraph in the recording and starts that image there. "
                   "Leave empty to let AutoEditor split at pauses.")
MOTION_LEVEL_HELP = {"None": "Still images don't move (use keyframes for exact moves).",
                     "Subtle": "Small, slow moves.", "Medium": "The preset's normal movement.",
                     "Dynamic": "Bigger, livelier moves; no still shots."}
def _split_parts(a, b, cut_t):
    """After cutting one clip into a (before) and b (after) at cut_t seconds into it: the second part
    continues the footage instead of replaying it, and timed effects stay where they were on screen."""
    from autoedit.visuals import probe_duration
    m = b.get("media") or ""
    if is_video(m):
        cin = float(b.get("clip_in") or 0)
        full = probe_duration(m) if os.path.isfile(m) else None
        avail = (float(b["clip_out"]) if b.get("clip_out") else (full or 0)) - cin
        if avail >= cut_t + 0.05:              # plays at normal speed here (short-clip loop/slow keep their rule)
            b["clip_in"] = round(cin + cut_t, 3)
    if a.get("effects") or b.get("effects"):
        effs = a.get("effects") or []
        a["effects"] = [dict(e, end=min(e["end"], cut_t)) for e in effs if e["start"] < cut_t - 1e-3]
        b["effects"] = [dict(e, start=round(max(0.0, e["start"] - cut_t), 4), end=round(e["end"] - cut_t, 4))
                        for e in effs if e["end"] > cut_t + 1e-3]


CAPTION_INPUTS = ("captions", "caption_style", "caption_size", "aspect", "resolution")


def _caption_inputs(state):
    st = (state or {}).get("settings") or {}
    return (state or {}).get("words"), tuple(st.get(k) for k in CAPTION_INPUTS)


STATE_KEYS = ("name", "voiceover", "script", "media_dir", "music", "sfx_dir", "output_dir", "settings",
              "words", "duration", "scenes", "overlays", "chapters", "sfx_events", "sfx_planned",
              "broll", "extra_media", "excluded_media", "stock_assets", "directions",
              "script_text", "scene_map", "asset_meta", "next_scene_id", "script_check", "alignment",
              "plan_report", "edit_instructions", "extra_sfx", "timed_plan")
TOOL_TABS = [("Media", "media", "Voiceover, script, media bin and scene planning"),
             ("Script", "script", "The exact narration text and the script-vs-recording check"),
             ("Editing Plan", "instructions", "Your editing plan: which image from which second, how it looks"),
             ("Audio", "audio", "Voice and music levels, fades, ducking, sound effects"),
             ("Text", "text", "Hook title, text cards and chapters"),
             ("Effects", "effects", "Whole-video atmosphere and overlay"),
             ("Transitions", "transitions", "Change and preview transitions"),
             ("Captions", "captions", "Caption style, font, colours, position"),
             ("Stock", "stock", "Search Pexels / Pixabay stock B-roll with your own API keys"),
             ("Export", "export", "Check and export ONE video in the shape and size you chose")]
MAIN_TABS = ("Media", "Editing Plan", "Export")       # the workflow; the rest sit behind 'More' 
AUTOSAVE_MS = 30000


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoEditor")
        self.setAcceptDrops(True)
        self.project = prj.new_project()
        self._apply_new_project_defaults(self.project)
        self.project_path = None
        self.thread = self.worker = None
        self._on_done = None
        self.busy_flag = False
        self.comp = None
        self.comp_key = None
        self.peaks = None
        self._peaks_for = None
        self.sel = -1                 # selected scene (V1) - scene actions use it
        self.sel_item = ("", -1)      # what the timeline / properties panel has selected
        self.undo = UndoStack()
        self._bindings = []          # (load_fn) for every bound control
        self._last_state = None
        self._saved_state = None

        self._build_actions()
        self._build_menus()
        self._build_header()
        self._build_workspace()
        self._build_statusbar()
        WheelGuard.prepare(self)

        self._restore_layout()
        self.reset_state_tracking(saved=False)
        self.refresh_all()
        self.show_home()
        from PySide6.QtCore import QTimer
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(AUTOSAVE_MS)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()
        self._preview_stop = QTimer(self)
        self._preview_stop.setSingleShot(True)
        self._preview_stop.timeout.connect(self.player.pause)

    # ======================================================================== state
    def snapshot(self):
        return copy.deepcopy({k: self.project.get(k) for k in STATE_KEYS})

    def reset_state_tracking(self, saved):
        self._last_state = self.snapshot()
        self._saved_state = copy.deepcopy(self._last_state) if saved else None
        self.undo.clear()
        self.update_status()

    def is_dirty(self):
        if self._saved_state is None:
            return bool(self.project.get("words") or self.project.get("voiceover"))
        return self.snapshot() != self._saved_state

    def commit(self, label_text):
        """Record a user edit (for undo) if the project really changed."""
        now = self.snapshot()
        if now == self._last_state:
            return False
        self.undo.push(label_text, self._last_state)
        before, self._last_state = self._last_state, now
        if _caption_inputs(before) != _caption_inputs(now) and hasattr(self, "timeline"):
            self.refresh_timeline()            # caption blocks depend on these - keep the CC track in step
        self.update_status()
        return True

    def restore(self, state):
        for k, v in copy.deepcopy(state).items():
            self.project[k] = v
        self._last_state = self.snapshot()
        self.refresh_all()

    def do_undo(self):
        if not self.undo.undo:
            return
        label_text, state = self.undo.undo.pop()
        self.undo.redo.append((label_text, self.snapshot()))
        self.undo.break_merge()
        self.restore(state)
        self.status_msg(f"Undid: {label_text}")

    def do_redo(self):
        if not self.undo.redo:
            return
        label_text, state = self.undo.redo.pop()
        self.undo.undo.append((label_text, self.snapshot()))
        self.undo.break_merge()
        self.restore(state)
        self.status_msg(f"Redid: {label_text}")

    # ======================================================================== bindings
    def _set(self, key, value, label_text, effects=(), top=False):
        target = self.project if top else self.project["settings"]
        if target.get(key) == value:
            return
        target[key] = value
        self._apply_effects(effects)
        self.commit(label_text)

    def _apply_effects(self, effects):
        if "sfx" in effects and self.project.get("sfx_planned"):
            self.project["sfx_planned"] = False
            self.log("SFX settings changed - effects will be re-planned at render.")
        if "media" in effects:
            self.update_media_count()
        if "voice" in effects:
            self.load_peaks()
        if "sfxlib" in effects or "sfx" in effects:
            self.update_sfx_status()
            self.sfx_panel.refresh()
        if "preview" in effects:
            self.invalidate_preview()
        if "music" in effects:
            self.player.set_music(self.project.get("music"), self.project["settings"].get("music_db", -20))
            self.timeline.update()
        if "props" in effects:
            self.props.reload()
        if "controls" in effects or "export" in effects:
            self.load_controls()
        if "export" in effects:
            self.update_actions()
        if "levels" in effects or "sfx" in effects:
            self.update_player_audio()

    def bind_combo(self, w, key, label_text, items, effects=("preview",)):
        """items: list of (text, value)."""
        w.clear()
        for t, v in items:
            w.addItem(t, v)

        def load():
            st = self.project["settings"]
            i = w.findData(st.get(key))
            with QSignalBlocker(w):
                w.setCurrentIndex(i if i >= 0 else 0)
        w.activated.connect(lambda _i: self._set(key, w.currentData(), label_text, effects))
        self._bindings.append(load)

    def bind_check(self, w, key, label_text, effects=("preview",), on=True, off=False):
        def load():
            with QSignalBlocker(w):
                w.setChecked(self.project["settings"].get(key) == on or
                             (on is True and bool(self.project["settings"].get(key)) and off is False))
        w.toggled.connect(lambda c: self._set(key, on if c else off, label_text, effects))
        self._bindings.append(load)

    def bind_slider(self, w, key, label_text, scale=100.0, effects=("preview",), readout=None):
        def show(v):
            if readout is not None:
                readout.setText(f"{v}%")

        def load():
            v = int(round(float(self.project["settings"].get(key, 0) or 0) * scale))
            with QSignalBlocker(w):
                w.setValue(v)
            show(v)
        w.valueChanged.connect(show)
        w.valueChanged.connect(lambda v: self._set(key, round(v / scale, 3), label_text, effects))
        self._bindings.append(load)

    def bind_spin(self, w, key, label_text, effects=()):
        def load():
            with QSignalBlocker(w):
                w.setValue(float(self.project["settings"].get(key, 0) or 0))
        w.valueChanged.connect(lambda v: self._set(key, float(v), label_text, effects))
        self._bindings.append(load)

    def bind_path(self, row, key, label_text, effects=(), top=True):
        def load():
            row.load(self.project.get(key, "") if top else self.project["settings"].get(key, ""))

        def changed():
            self._set(key, row.text(), label_text, effects, top=top)
            self.load_controls()          # keep the twin field (setup screen / editor) in step
            self.update_input_info()
        row.changed.connect(changed)
        row.clear_requested.connect(lambda: self.clear_input(key))
        self._bindings.append(load)

    def bind_line(self, w, key, label_text, effects=(), top=False):
        def load():
            v = self.project.get(key, "") if top else self.project["settings"].get(key, "")
            with QSignalBlocker(w):
                w.setText(v or "")
        w.editingFinished.connect(lambda: self._set(key, w.text().strip() if not top else
                                                    (w.text().strip() or "video"), label_text, effects, top=top))
        self._bindings.append(load)

    def load_controls(self):
        for fn in self._bindings:
            fn()
        self.cap_panel.load(self.project["settings"])
        st = self.project["settings"]
        self._load_export_controls()
        if hasattr(self, "script_panel"):
            self.script_panel.load()
            self.instr_panel.load()
        self.preset_help.setText(PRESET_HELP.get(st.get("preset"), ""))
        if hasattr(self, "home_cat"):
            self.update_home_category()
        self._load_directions()
        if hasattr(self, "cat_label"):
            self.update_category_info()

    # ======================================================================== actions / menus
    def _act(self, text, fn, shortcut=None, tip=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(fn)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("New project", self.new_project, "Ctrl+N", "Start a new project on the setup screen")
        self.a_open = A("Open project…", self.open_project, "Ctrl+O", "Open a saved .autoedit.json project")
        self.a_save = A("Save", self.save_project, "Ctrl+S", "Save the project")
        self.a_save_as = A("Save as…", self.save_project_as, "Ctrl+Shift+S", "Save the project under a new name")
        self.a_exit = A("Exit", self.close, "Alt+F4")
        self.a_undo = A("Undo", self.do_undo, "Ctrl+Z", "Undo the last edit")
        self.a_redo = A("Redo", self.do_redo, ["Ctrl+Shift+Z", "Ctrl+Y"], "Redo")
        self.a_split = A("Split at playhead", self.split_at_playhead, "Ctrl+B",
                         "Cut the scene (or selected V2 clip / text card) under the playhead in two")
        self.a_duplicate = A("Duplicate", self.duplicate_selected, "Ctrl+D",
                             "Duplicate the selected clip (a scene's visual is copied to the next scene)")
        self.a_delete = A("Delete selected", self.delete_selected, None,
                          "Delete the selected clip (a scene is merged into the previous one). Delete key "
                          "on the timeline.")
        self.a_add_media = A("Import media files…", self.bin_add_files, "Ctrl+I",
                             "Add images or clips to the media bin (several at once)")
        self.a_merge = A("Merge with next scene", self.merge_next, "Ctrl+M")
        self.a_swap = A("Swap visual with next", self.swap_next, None)
        self.a_replace = A("Replace visual…", self.replace_media, "Ctrl+R", "Choose another image/clip for the scene")
        self.a_settings = A("Project settings…", self.project_settings, "Ctrl+,", "Speech model and language")
        self.a_toggle_left = A("Tool panel", lambda c: self.left.setVisible(c), None, checkable=True)
        self.a_toggle_right = A("Inspector / export panel", lambda c: self.right.setVisible(c), None, checkable=True)
        self.a_toggle_bottom = A("Timeline panel", lambda c: self.bottom.setVisible(c), None, checkable=True)
        self.a_toggle_centre = A("Preview panel", lambda c: self.centre.setVisible(c), None, checkable=True)
        self.a_toggle_tabs = A("Lower panel (Scenes, Review, Text, SFX, Quality, Log)", self.set_lower_panel, "Ctrl+J",
                               "Show or hide the tabs under the timeline", checkable=True)
        for a in (self.a_toggle_left, self.a_toggle_right, self.a_toggle_bottom, self.a_toggle_centre,
                  self.a_toggle_tabs):
            a.setChecked(True)
        self.a_theater = A("Theater mode (big preview)", self.set_theater, "F11",
                           "Give most of the window to the preview; F11 again returns to the editing layout",
                           checkable=True)
        self.a_vol_up = A("Preview volume up", lambda: self.player.step_volume(10), "Ctrl+Up")
        self.a_vol_down = A("Preview volume down", lambda: self.player.step_volume(-10), "Ctrl+Down")
        self.a_mute = A("Mute / unmute preview", lambda: self.player.toggle_mute(), "Ctrl+Shift+M")
        self.a_reset_layout = A("Reset layout", self.reset_layout)
        self.a_zoom_in = A("Zoom timeline in", lambda: self.timeline.zoom(1.4), "Ctrl+=")
        self.a_zoom_out = A("Zoom timeline out", lambda: self.timeline.zoom(1 / 1.4), "Ctrl+-")
        self.a_zoom_fit = A("Fit timeline", lambda: self.timeline.fit(), "Ctrl+0")
        self.a_analyze = A("Analyze voiceover", self.analyze, "F5",
                           "Transcribe the voiceover and build the scene plan")
        self.a_replan = A("Re-plan scenes", self.replan, "Shift+F5",
                          "Rebuild scenes from the transcript with the current settings (keeps transcript)")
        self.a_plan = A("Editing plan…", self.open_plan_window, "F9",
                        "Which image from which second to which second - required before export")
        self.a_instr = A("Editing instructions…", self.open_instructions, "F8",
                         "Scene-by-scene: visual, timing, motion, B-roll, sound effects, effects, transitions, locks")
        self.a_review = A("Review scenes…", self.open_review_window, "Ctrl+Shift+R",
                          "What each scene shows, why it was chosen, and what needs a decision")
        self.a_cues = A("Match images to narration…", self.open_cue_dialog, "Ctrl+L",
                        "Choose which image starts at which sentence (or import a script with [1], [2]… markers)")
        self.a_plan_sfx = A("Plan sound effects", self.plan_sfx, None, "Choose effects now so you can review them")
        self.a_quality = A("Quality check", self.run_quality, "F7")
        self.a_info = A("Project information", self.project_info)
        self.a_play = A("Play / pause", lambda: self.player.toggle(), "Space")
        self.a_stop = A("Stop", self.stop_playback, None)
        self.a_prev = A("Previous scene", lambda: self.step_scene(-1), "PgUp")
        self.a_next = A("Next scene", lambda: self.step_scene(1), "PgDown")
        self.a_render = A("Render final video", self.render, "Ctrl+E",
                          "Full-quality render of the ticked formats with the export settings")
        self.a_draft = A("Draft preview render", self.render_draft, "Ctrl+Shift+D",
                         "Quick 480p check of the format shown in the preview (fast CPU encode)")
        self.a_package = A("Export editor package (CapCut / Premiere)…", self.export_pkg, "Ctrl+Shift+E",
                           "One clip per scene + audio + captions for finishing elsewhere")
        self.a_srt = A("Export captions (.srt)…", self.export_srt)
        self.a_desc = A("Copy YouTube description (chapters)", self.copy_description)
        self.a_open_out = A("Open output folder", self.open_output)
        self.a_cancel = A("Cancel render / analysis", self.cancel, "Esc",
                          "Stop the running render or analysis. Your project stays open.")
        self.a_back = A("Cancel project (back to setup)", self.back_to_setup, "Ctrl+W",
                        "Close this project and return to the setup screen (asks about unsaved changes). "
                        "The saved project file and your media are kept.")
        self.a_start_over = A("Clear project…", self.start_over, None,
                              "Clear all inputs, scenes and clips from this working project (asks first). "
                              "Files on your PC and the saved project file are not deleted.")
        self.a_delete_file = A("Delete project file…", self.delete_project_file, None,
                               "Delete this project's .autoedit.json file (asks first). Media files are never "
                               "deleted.")
        self.a_remove_visual = A("Remove visual from scene", self.remove_visual, None,
                                 "Leave the selected scene without an image/clip (the file is not deleted)")
        self.a_shortcuts = A("Keyboard shortcuts", self.show_shortcuts, "F1")
        self.a_readme = A("Read me (local documentation)", self.open_readme)
        self.a_about = A("About AutoEditor", self.about)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open):
            m.addAction(a)
        self.recent_menu = m.addMenu("Open recent")
        m.addSeparator()
        for a in (self.a_save, self.a_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_add_media)
        m.addSeparator()
        m.addAction(self.a_back)
        m.addAction(self.a_start_over)
        m.addAction(self.a_delete_file)
        m.addSeparator()
        m.addAction(self.a_exit)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_split, self.a_duplicate, self.a_delete, self.a_merge, self.a_swap, self.a_replace,
                  self.a_remove_visual):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_settings)
        m = mb.addMenu("&View")
        m.addAction(self.a_theater)
        m.addSeparator()
        for a in (self.a_toggle_left, self.a_toggle_centre, self.a_toggle_right, self.a_toggle_bottom,
                  self.a_toggle_tabs):
            m.addAction(a)
        m.addAction(self.a_reset_layout)
        m.addSeparator()
        for a in (self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit):
            m.addAction(a)
        m = mb.addMenu("&Project")
        for a in (self.a_analyze, self.a_replan, self.a_plan, self.a_cues, self.a_instr, self.a_review, self.a_plan_sfx,
                  self.a_quality):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_info)
        m.addAction(self.a_settings)
        m = mb.addMenu("P&layback")
        for a in (self.a_play, self.a_stop, self.a_prev, self.a_next):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_vol_up, self.a_vol_down, self.a_mute):
            m.addAction(a)
        m = mb.addMenu("E&xport")
        for a in (self.a_draft, self.a_render, self.a_package, self.a_srt, self.a_desc):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_open_out)
        m.addAction(self.a_cancel)
        m = mb.addMenu("&Help")
        for a in (self.a_shortcuts, self.a_readme, self.a_about):
            m.addAction(a)
        self._rebuild_recent()

    def _build_header(self):
        tb = QToolBar("Header")
        tb.setObjectName("Header")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        brand = QLabel("◆ AutoEditor")
        brand.setObjectName("Brand")
        tb.addWidget(brand)
        self.proj_name = QLabel("Untitled")
        self.proj_name.setObjectName("ProjectName")
        self.proj_name.setToolTip("Project file")
        tb.addWidget(self.proj_name)
        self.h_back = QPushButton("Back to setup")
        self.h_back.setIcon(icon("back"))
        self.h_back.setToolTip("Cancel project: close it and return to the setup screen (Ctrl+W). "
                               "Your media and saved project file are kept. Doesn't stop a render - use "
                               "Cancel render for that.")
        self.h_back.clicked.connect(self.back_to_setup)
        self.h_back_act = tb.addWidget(self.h_back)      # toolbar widgets are shown/hidden via their action
        self.h_back_sep = tb.addSeparator()
        # short labels in the header so everything fits on small / scaled screens (menus keep full names)
        self.a_new.setIconText("New")
        self.a_open.setIconText("Open")
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.pill = QLabel("Not saved yet")
        self.pill.setObjectName("StatusPill")
        self.pill.setMinimumWidth(96)
        self.pill.setAlignment(Qt.AlignCenter)
        tb.addWidget(self.pill)
        tb.addSeparator()
        self.h_analyze = QPushButton("Analyze")
        self.h_analyze.setToolTip("Transcribe the voiceover and build the scene plan (F5)")
        self.h_analyze.clicked.connect(self.analyze)
        self.h_draft = QPushButton("Draft preview")
        self.h_draft.setToolTip(self.a_draft.toolTip() + " (Ctrl+Shift+D)")
        self.h_draft.clicked.connect(self.render_draft)
        self.h_render = QPushButton("Export")
        self.h_render.setProperty("primary", True)
        self.h_render.setToolTip("Final render with the Export settings (Ctrl+E)")
        self.h_render.clicked.connect(self.render)
        tb.addWidget(self.h_analyze)
        tb.addWidget(self.h_draft)
        tb.addWidget(self.h_render)
        self.addToolBar(Qt.TopToolBarArea, tb)

    # ======================================================================== workspace
    def _build_workspace(self):
        audio_f = "Audio (" + " ".join("*" + e for e in sorted(AUDIO_EXT)) + ")"
        video_f = "Video (" + " ".join("*" + e for e in sorted(VIDEO_EXT)) + ")"

        # ---------------- left: horizontal tool tabs + pages
        self.tools = ToolTabBar(TOOL_TABS, primary=MAIN_TABS)
        self.pages = QStackedWidget()
        self.tools.currentChanged.connect(self.pages.setCurrentIndex)

        # Media -----------------------------------------------------------------
        pg, lay = page("Media", "Your voiceover drives the timing. Drag files onto the window, into the bin, "
                                "or use Browse.")
        f = form()
        self.voice = PathRow("file", audio_f, "Voiceover MP3/WAV")
        self.bind_path(self.voice, "voiceover", "Choose voiceover", ("voice",))
        self.script = PathRow("file", "Text (*.txt)", "Optional: script .txt for exact spelling")
        self.bind_path(self.script, "script", "Choose script")
        self.media = PathRow("media", placeholder="Images / clips folder")
        self.bind_path(self.media, "media_dir", "Choose images folder", ("media",))
        f.addRow("Voiceover", self.voice)
        f.addRow("Script", self.script)
        f.addRow("Images / clips folder", self.media)
        lay.addLayout(f)
        lay.addWidget(label("Media bin", "Section"))
        self.bin = MediaBin(lambda: project_media(self.project), self.used_media)
        self.bin.use_for_scene.connect(self.bin_use_for_scene)
        self.bin.add_to_v2.connect(lambda path: self.add_broll(path, self.player.t))
        self.bin.remove_from_project.connect(self.bin_remove)
        self.bin.add_files.connect(self.bin_add_files)
        self.bin.add_folder.connect(self.media.browse)
        self.bin.setMinimumHeight(260)
        lay.addWidget(self.bin, 1)
        lay.addWidget(label("Double-click = use for the selected scene · drag onto V1 to replace a scene's "
                            "visual, or onto V2 to add a B-roll clip.", "Help"))
        lay.addWidget(label("Scene planning", "Section"))
        f = form()
        self.mode = QComboBox()
        self.bind_combo(self.mode, "scene_mode", "Scene mode", SCENE_MODES, effects=("controls",))
        self.snap_ts = QCheckBox("Snap timestamp cuts to the nearest pause")
        self.bind_check(self.snap_ts, "snap_timestamps", "Snap timestamps", effects=())
        self.preset = QComboBox()
        self.bind_combo(self.preset, "preset", "Editing preset", [(k, k) for k in PRESETS])
        self.preset.activated.connect(lambda _i: self.preset_help.setText(
            PRESET_HELP.get(self.preset.currentData(), "")))
        self.preset_help = label("", "Help")
        self.vary = QCheckBox("Own style mix per video")
        self.bind_check(self.vary, "auto_vary", "Style variety", effects=())
        self.btn_shuffle = QPushButton("Shuffle style")
        self.btn_shuffle.setToolTip("Re-plan with a different motion/transition mix (resets manual scene edits)")
        self.btn_shuffle.clicked.connect(self.shuffle_style)
        self.hook_fast = QCheckBox("Faster cuts in first 10 s")
        self.bind_check(self.hook_fast, "hook_fast_cuts", "Hook cuts", effects=())
        self.fit = QComboBox()
        self.bind_combo(self.fit, "fit", "Odd-shaped images", FIT_MODES)
        self.cat_label = label("", None)
        self.cat_btn = QPushButton("Change…")
        self.cat_btn.setToolTip("Choose a video category - it sets starting defaults you can still change")
        self.cat_btn.clicked.connect(self.project_settings)
        crow = QHBoxLayout()
        crow.addWidget(self.cat_label, 1)
        crow.addWidget(self.cat_btn)
        self.cat_notes = label("", "Help")
        self.motion_level = QComboBox()
        self.bind_combo(self.motion_level, "motion_level", "Motion level", [(k, k) for k in MOTION_LEVELS],
                        effects=())
        self.motion_level.activated.connect(lambda _i: self.motion_help.setText(
            MOTION_LEVEL_HELP.get(self.motion_level.currentData(), "")))
        self.motion_help = label("", "Help")
        b_ml = QPushButton("Apply motion level to all scenes")
        b_ml.setToolTip("Re-choose automatic motion for every still image now (keeps timing, visuals and "
                        "keyframes; video clips are left alone)")
        b_ml.clicked.connect(self.apply_motion_level_all)
        f.addRow("Video category", crow)
        f.addRow("", self.cat_notes)
        self.dir_box = self._directions_box(120)
        b_dir = QPushButton("Apply directions (re-plan scenes)")
        b_dir.setToolTip("Rebuild the scenes from the directions above (keeps the transcript; Ctrl+Z undoes)")
        b_dir.clicked.connect(self.apply_directions)
        self.repeat_cb = QCheckBox("Allow images to repeat")
        self.repeat_cb.setToolTip("Off (recommended): each image is shown once, in order - scenes get longer when "
                                  "there are fewer images than pauses. On: images cycle again.")
        self.bind_check(self.repeat_cb, "repeat_media", "Allow repeating images", effects=())
        f.addRow("Scene mode", self.mode)
        f.addRow("", self.snap_ts)
        f.addRow("", self.repeat_cb)
        f.addRow("Directions - your scene list", self.dir_box)
        f.addRow("", b_dir)

        def directions_rows(f=f, b_dir=b_dir):     # only for 'Follow my directions' - never next to a plan
            on = self.project["settings"].get("scene_mode") == "script_cues"
            f.setRowVisible(self.dir_box, on)
            f.setRowVisible(b_dir, on)
        self._bindings.append(directions_rows)
        f.addRow("Automatic motion", self.motion_level)
        f.addRow("", self.motion_help)
        f.addRow("", b_ml)
        f.addRow("Editing preset (motion + transitions mix)", self.preset)
        f.addRow("", self.preset_help)
        f.addRow("Variety", self.vary)
        f.addRow("", self.btn_shuffle)
        f.addRow("Opening", self.hook_fast)
        f.addRow("Framing (pictures of another shape)", self.fit)
        lay.addLayout(f)
        lay.addWidget(label("Scene mode, preset, variety and opening cuts take effect when you analyze or "
                            "re-plan. Per-clip motion and keyframes: select a clip → Properties → Animation.",
                            "Help"))
        b = QPushButton("Analyze voiceover")
        b.setProperty("primary", True)
        b.clicked.connect(self.analyze)
        b2 = QPushButton("Re-plan scenes")
        b2.setToolTip("Keeps the transcript; rebuilds scenes with the current settings")
        b2.clicked.connect(self.replan)
        self.btn_analyze, self.btn_replan = b, b2
        lay.addWidget(b)
        lay.addWidget(b2)
        b3 = QPushButton("Match images to narration…")
        b3.setToolTip(self.a_cues.toolTip() + " (Ctrl+L)")
        b3.clicked.connect(self.open_cue_dialog)
        lay.addWidget(b3)
        self.pages.addWidget(pg)

        # Script + Instructions (v7) -----------------------------------------------
        pg, lay = page("Script", "The exact narration. Timing always comes from the recording.")
        self.script_panel = ScriptPanel(self)
        lay.addWidget(self.script_panel, 1)
        self.pages.addWidget(pg)
        pg, lay = page("Editing Plan")
        self.instr_panel = InstructionsPanel(self)
        lay.addWidget(self.instr_panel, 1)
        self.pages.addWidget(pg)

        # Audio -----------------------------------------------------------------
        pg, lay = page("Audio", "Voice, music and sound effects. Click the Voice (A1) or Music (A2) track label "
                                "on the timeline for the same settings in Properties.")
        f = form()
        self.voice_db = QDoubleSpinBox()
        self.voice_db.setRange(-20, 12)
        self.voice_db.setSingleStep(0.5)
        self.voice_db.setSuffix(" dB")
        self.bind_spin(self.voice_db, "voice_db", "Voice level", effects=("props", "levels"))
        self.music = PathRow("file", audio_f, "Optional background music")
        self.bind_path(self.music, "music", "Choose music", ("music",))
        self.music_db = QDoubleSpinBox()
        self.music_db.setRange(-40, 0)
        self.music_db.setSuffix(" dB")
        self.bind_spin(self.music_db, "music_db", "Music volume", effects=("music", "props", "levels"))
        self.fade_in = QDoubleSpinBox()
        self.fade_in.setRange(0, 10)
        self.fade_in.setSingleStep(0.5)
        self.fade_in.setSuffix(" s")
        self.bind_spin(self.fade_in, "music_fade_in", "Music fade in", effects=("props",))
        self.fade_out = QDoubleSpinBox()
        self.fade_out.setRange(0, 15)
        self.fade_out.setSingleStep(0.5)
        self.fade_out.setSuffix(" s")
        self.bind_spin(self.fade_out, "music_fade_out", "Music fade out", effects=("props",))
        self.duck = QCheckBox("Duck music under the voice")
        self.bind_check(self.duck, "music_duck", "Music ducking", effects=("props",))
        self.loud = QCheckBox("Normalize to -14 LUFS")
        self.bind_check(self.loud, "loudness", "Loudness", effects=(), on=-14.0, off=None)
        f.addRow("Voice level", self.voice_db)
        f.addRow("Music", self.music)
        f.addRow("Music volume", self.music_db)
        fr = QHBoxLayout()
        fr.addWidget(QLabel("in"))
        fr.addWidget(self.fade_in)
        fr.addWidget(QLabel("out"))
        fr.addWidget(self.fade_out)
        f.addRow("Music fades", fr)
        f.addRow("", self.duck)
        f.addRow("Loudness", self.loud)
        lay.addLayout(f)
        b = QPushButton("▶ Preview music")
        b.setToolTip("Play the music from the playhead with the voice (no ducking in preview)")
        b.clicked.connect(self.preview_music)
        lay.addWidget(b)
        lay.addWidget(label("Sound effects", "Section"))
        f = form()
        self.sfx_level = QComboBox()
        self.bind_combo(self.sfx_level, "sfx_level", "SFX level", [(k, k) for k in SFX_LEVELS],
                        effects=("sfx",))
        self.sfx = PathRow("dir", placeholder="Optional: your SFX library folder (category sub-folders)")
        self.bind_path(self.sfx, "sfx_dir", "Choose SFX folder", ("sfx",))
        self.builtin_sfx = QCheckBox("Use the built-in library too")
        self.bind_check(self.builtin_sfx, "builtin_sfx", "Built-in SFX", effects=("sfx",))
        self.sfx_db = QDoubleSpinBox()
        self.sfx_db.setRange(-20, 12)
        self.sfx_db.setSingleStep(0.5)
        self.sfx_db.setSuffix(" dB")
        self.sfx_db.setToolTip("Volume of all sound effects in the export (on top of the automatic voice-matched level)")
        self.bind_spin(self.sfx_db, "sfx_db", "SFX volume", effects=("props", "levels"))
        f.addRow("Amount", self.sfx_level)
        f.addRow("SFX volume", self.sfx_db)
        f.addRow("Your SFX library", self.sfx)
        f.addRow("", self.builtin_sfx)
        lay.addLayout(f)
        self.sfx_status = label("", "Help")
        lay.addWidget(self.sfx_status)
        lay.addWidget(label("None = no effects · Minimal = transitions and text cards only · Normal / Cinematic "
                            "= also narration keywords (more, and slightly louder). Effects are level-matched "
                            "to your voice so they stay audible. Organise your own licensed sounds as "
                            "SFX/whoosh/*.wav, SFX/impact/*.wav… (see sfx_library/README.txt).", "Help"))
        r = QHBoxLayout()
        b = QPushButton("Plan sound effects")
        b.setToolTip("Choose effects now so you can see them on the A3 track, preview and edit them")
        b.clicked.connect(self.plan_sfx)
        b2 = QPushButton("Sound-effects list")
        b2.clicked.connect(lambda: self.bottom_tabs.setCurrentWidget(self.sfx_panel))
        r.addWidget(b)
        r.addWidget(b2)
        lay.addLayout(r)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # Text ------------------------------------------------------------------
        pg, lay = page("Text", "Hook title, labels, lower thirds, title and chapter cards.")
        f = form()
        self.hook_title = QLineEdit()
        self.hook_title.setPlaceholderText("e.g. Why did America really join WWI?")
        self.bind_line(self.hook_title, "hook_title", "Hook title")
        f.addRow("Hook title", self.hook_title)
        lay.addLayout(f)
        lay.addWidget(label("Shown big over the first seconds with a bass-drop sound. Leave empty for none.",
                            "Help"))
        lay.addWidget(label("Add a text card", "Section"))
        f = form()
        self.new_card_type = QComboBox()
        from autoedit.overlays import OVERLAY_TYPES
        for k, v in OVERLAY_TYPES.items():
            if k != "hook":
                self.new_card_type.addItem(v, k)
        b = QPushButton("+ Add at playhead")
        b.clicked.connect(lambda: self.add_text_card(self.player.t))
        r = QHBoxLayout()
        r.addWidget(self.new_card_type, 1)
        r.addWidget(b)
        f.addRow("Type", r)
        lay.addLayout(f)
        lay.addWidget(label("New cards appear on the T track - select one to edit its text and timing in "
                            "Properties, drag it to move, drag its edges to trim.", "Help"))
        b = QPushButton("Text cards && chapters list")
        b.clicked.connect(lambda: self.bottom_tabs.setCurrentWidget(self.text_panel))
        lay.addWidget(b)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # Effects ---------------------------------------------------------------
        pg, lay = page("Effects", "Whole-video look and atmosphere, generated by the app (no stock files needed). "
                                  "Per-clip filters and adjustments: select a clip → Properties → Effects.")
        f = form()
        self.atmo = QComboBox()
        self.bind_combo(self.atmo, "atmosphere", "Atmosphere", [(k, k) for k in ATMOSPHERES])
        self.atmo_strength = QSlider(Qt.Horizontal)
        self.atmo_strength.setRange(0, 150)
        self.atmo_lbl = QLabel("100%")
        self.atmo_lbl.setFixedWidth(40)
        self.bind_slider(self.atmo_strength, "atmo_strength", "Atmosphere strength", readout=self.atmo_lbl)
        srow = QHBoxLayout()
        srow.addWidget(self.atmo_strength, 1)
        srow.addWidget(self.atmo_lbl)
        self.overlay = PathRow("file", video_f, "Optional overlay video (dust, light leaks…)")
        self.bind_path(self.overlay, "overlay", "Overlay video", ("preview",), top=False)
        self.overlay_op = QSlider(Qt.Horizontal)
        self.overlay_op.setRange(0, 100)
        self.op_lbl = QLabel("50%")
        self.op_lbl.setFixedWidth(40)
        self.bind_slider(self.overlay_op, "overlay_opacity", "Overlay opacity", readout=self.op_lbl)
        orow = QHBoxLayout()
        orow.addWidget(self.overlay_op, 1)
        orow.addWidget(self.op_lbl)
        f.addRow("Atmosphere", self.atmo)
        f.addRow("Strength", srow)
        f.addRow("Overlay video", self.overlay)
        f.addRow("Overlay opacity", orow)
        lay.addLayout(f)
        b = QPushButton("Edit the selected clip's effects")
        b.clicked.connect(lambda: self.open_props_tab(2))
        lay.addWidget(b)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # Transitions -------------------------------------------------------------
        pg, lay = page("Transitions", "Each scene's transition is in Properties → Transition (select the scene). "
                                      "Use these tools to change many at once.")
        f = form()
        self.all_trans = QComboBox()
        self.all_trans.addItems(TRANSITIONS)
        b = QPushButton("Apply to every scene")
        b.clicked.connect(self.set_all_transitions)
        row = QHBoxLayout()
        row.addWidget(self.all_trans, 1)
        row.addWidget(b)
        f.addRow("Set all to", row)
        b2 = QPushButton("Re-roll from the preset mix")
        b2.setToolTip("Pick new transitions using the editing preset (section breaks keep their transition)")
        b2.clicked.connect(self.reroll_transitions)
        f.addRow("", b2)
        lay.addLayout(f)
        b3 = QPushButton("▶ Preview the selected scene's transition")
        b3.clicked.connect(self.preview_transition)
        lay.addWidget(b3)
        lay.addWidget(label("Transitions show as ◆ markers between scenes on V1. Slide, whip and zoom "
                            "transitions get a whoosh-style sound; dip to black gets an impact (when sound "
                            "effects are on).", "Help"))
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # Captions --------------------------------------------------------------
        pg, lay = page("Captions", "Burned-in captions with the spoken word highlighted. To fix a word, click its "
                                   "caption on the CC track and edit it in Properties.")
        self.cap_panel = CaptionPanel()
        self.cap_panel.setTitle("")
        self.cap_panel.changed.connect(self.captions_changed)
        lay.addWidget(self.cap_panel)
        b = QPushButton("Edit the caption at the playhead")
        b.clicked.connect(self.edit_caption_at_playhead)
        lay.addWidget(b)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # Stock B-roll ------------------------------------------------------------
        pg, lay = page("Stock B-roll")
        from autoedit.stock_panel import StockPanel
        self.stock = StockPanel(lambda: self.project, self._stock_scene_text,
                                lambda: self.project["settings"].get("category") or "",
                                lambda: self.player.aspect_name())
        self.stock.downloaded.connect(self.on_stock_downloaded)
        self.stock.log.connect(self.log)
        lay.addWidget(self.stock, 1)
        self.pages.addWidget(pg)

        # Export (workflow step 3: the settings live in the right panel's Export tab - one place) ------------
        pg, lay = page("Export", "One export = one video in the shape and size you chose.")
        self.exp_summary = label("", "Help")
        lay.addWidget(self.exp_summary)
        for text, act, primary_ in (("Export video (Ctrl+E)", self.a_render, True),
                                    ("Quality check (F7)", self.a_quality, False),
                                    ("Draft preview - quick low-resolution check", self.a_draft, False),
                                    ("Export settings (shape, size, quality)…", None, False)):
            b = QPushButton(text)
            if primary_:
                b.setProperty("primary", True)
            b.clicked.connect(act.trigger if act is not None else
                              (lambda: self.right_tabs.setCurrentIndex(1)))
            lay.addWidget(b)
        lay.addWidget(label("Before exporting, the quality check lists anything unfinished - for example an editing "
                            "plan that was changed but not applied yet.", "Help"))
        lay.addStretch(1)
        self._bindings.append(lambda: self.exp_summary.setText(
            XC.summary(self.project["settings"]) + ("\n" + ("Editing plan: applied." if not
                                                         self._plan_unapplied() else
                                                         "Editing plan: changed since it was applied - press "
                                                         "Apply to Timeline (or the export uses the old timeline).")
                                                   if self.project["settings"].get("scene_mode") == "timed" and
                                                   (self.project.get("timed_plan") or "").strip() else "")))
        self.pages.addWidget(pg)

        left = QWidget()
        left.setObjectName("Panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addWidget(self.tools)
        ll.addWidget(self.pages, 1)
        left.setMinimumWidth(280)
        self.left = left
        self.tools.setCurrentIndex(0)
        self.tools.currentChanged.connect(self._tool_tab_changed)

        # ---------------- centre: preview
        self.player = PlayerWidget()
        self.player.frame_fn = self.preview_frame
        self.player.time_changed.connect(self.on_player_time)
        self.player.aspect.currentIndexChanged.connect(self.invalidate_preview)
        self.player.aspect.setMinimumContentsLength(5)
        self.player.caps.toggled.connect(lambda _: self.player.show_frame())
        self.player.fx.toggled.connect(self.invalidate_preview)
        self.player.theater_btn.toggled.connect(self.set_theater)
        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.addWidget(self.player, 1)       # no title row: every pixel of height goes to the picture
        centre.setMinimumWidth(300)
        self.centre = centre

        # ---------------- right: properties + export
        self.props = PropertiesPanel(lambda: self.project, lambda: self.player.t)
        self.props.changed.connect(self.on_props_changed)
        self.props.request.connect(self.on_props_request)
        self.props.seek.connect(self.player.seek)
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self.props, "Properties")
        self.right_tabs.addTab(self._scroll(self._build_export_tab()), "Export")
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 8, 8, 8)
        rl.addWidget(self.right_tabs)
        right.setMinimumWidth(260)
        self.right = right

        # ---------------- bottom: timeline + panels
        self.timeline = TimelineWidget()
        self.timeline.seek.connect(self.player.seek)
        self.timeline.selected.connect(self.on_timeline_selected)
        self.timeline.cut_released.connect(self.on_cut_released)
        self.timeline.edited.connect(self.on_timeline_edited)
        self.timeline.request.connect(self.on_timeline_request)
        self.timeline.media_dropped.connect(self.on_media_dropped)
        tbar = QHBoxLayout()
        tbar.setSpacing(6)
        tbar.addWidget(label("Timeline", "PanelTitle", wrap=False))
        self.tl_hint = label("Click to select · drag clips to move / trim · Ctrl+wheel zooms", "Help", wrap=False)
        self.tl_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # shrinks first, never clips buttons
        tbar.addWidget(self.tl_hint)
        tbar.addStretch(1)
        for text, fn, tip in (("−", lambda: self.timeline.zoom(1 / 1.4), "Zoom out (Ctrl+-)"),
                              ("+", lambda: self.timeline.zoom(1.4), "Zoom in (Ctrl+=)"),
                              ("Fit", self.timeline.fit, "Fit whole video (Ctrl+0)")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(44 if text != "Fit" else 50)
            b.clicked.connect(fn)
            tbar.addWidget(b)
        self.snap_tl = QCheckBox("Snap")
        self.snap_tl.setToolTip("Snap cuts and clips to pauses in the narration, scene edges and the playhead")
        self.snap_tl.setChecked(True)
        self.snap_tl.toggled.connect(lambda v: setattr(self.timeline, "snap_on", v))
        tbar.addWidget(self.snap_tl)
        bm = QPushButton("Match images…")
        bm.setToolTip(self.a_cues.toolTip() + " (Ctrl+L)")
        bm.clicked.connect(self.open_cue_dialog)
        tbar.addWidget(bm)
        for a, text in ((self.a_split, "Split"), (self.a_duplicate, "Duplicate"), (self.a_delete, "Delete"),
                        (self.a_merge, "Merge →"), (self.a_swap, "Swap →")):
            b = QPushButton(text)
            b.setToolTip(a.toolTip() or a.text())
            b.clicked.connect(a.trigger)
            tbar.addWidget(b)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "Time", "Length", "Narration", "Visual", "Motion", "Transition →"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(80, 45))
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for c, w in [(0, 36), (1, 100), (2, 60), (4, 210), (5, 90), (6, 100)]:
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(lambda r, c: self.replace_media() if c == 4 else None)
        self.table.itemSelectionChanged.connect(self.table_selection)
        self.text_panel = TextChaptersPanel(lambda: self.project, lambda: self.player.t)
        self.text_panel.changed.connect(lambda: self.edited("Edit text cards / chapters", preview=True))
        self.text_panel.seek.connect(self.player.seek)
        self.sfx_panel = SfxPanel(lambda: self.project, lambda: self.player.t, self.sfx_categories)
        self.sfx_panel.edited.connect(lambda lbl: self.edited(lbl))
        self.sfx_panel.plan_requested.connect(self.plan_sfx)
        self.sfx_panel.seek.connect(self.player.seek)
        self.quality_panel = QualityPanel(lambda: self.project)
        self.quality_panel.select_scene.connect(self.select_scene)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.table, "Scenes")
        self.review = ReviewPanel(self)
        self.bottom_tabs.addTab(self.review, "Review scenes")
        self.bottom_tabs.addTab(self.text_panel, "Text && chapters")
        self.bottom_tabs.addTab(self.sfx_panel, "Sound effects")
        self.bottom_tabs.addTab(self.quality_panel, "Quality check")
        self.bottom_tabs.addTab(self.logbox, "Log")
        self.bottom_tabs.setMinimumHeight(56)
        self.tabs_btn = QPushButton("▾ Hide")
        self.tabs_btn.setToolTip("Hide / show this lower panel (Ctrl+J). Drag the divider above the tabs to resize it.")
        self.tabs_btn.clicked.connect(lambda: self.set_lower_panel(not self.bottom_tabs.isVisible()))
        self.tabs_show_btn = QPushButton("▴ Panels")
        self.tabs_show_btn.setToolTip("Show the Scenes / Review scenes / Text & chapters / Sound effects / Quality check / Log "
                                      "panel under the timeline (Ctrl+J)")
        self.tabs_show_btn.clicked.connect(lambda: self.set_lower_panel(True))
        self.tabs_show_btn.setVisible(False)
        self.bottom_tabs.setCornerWidget(self.tabs_btn, Qt.TopRightCorner)
        tbar.insertWidget(2, self.tabs_show_btn)        # in the timeline toolbar when the panel is hidden

        tl_box = QWidget()
        tll = QVBoxLayout(tl_box)
        tll.setContentsMargins(8, 4, 8, 0)
        tll.setSpacing(4)
        tll.addLayout(tbar)
        self.tl_area = self._scroll(self.timeline)
        self.tl_area.setFrameShape(QFrame.NoFrame)
        self.tl_area.setMinimumHeight(120)
        tll.addWidget(self.tl_area, 1)
        self.tl_scroll = TimelineScrollBar(self.timeline)
        tll.addWidget(self.tl_scroll)
        self.split_b = QSplitter(Qt.Vertical)
        self.split_b.setHandleWidth(7)
        self.split_b.addWidget(tl_box)
        self.split_b.addWidget(self.bottom_tabs)
        self.split_b.setStretchFactor(0, 3)
        self.split_b.setStretchFactor(1, 1)
        self.split_b.setChildrenCollapsible(False)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 4)
        bl.addWidget(self.split_b)
        self.bottom = bottom

        self.split_h = QSplitter(Qt.Horizontal)
        self.split_h.setHandleWidth(6)
        self.split_h.addWidget(self.left)
        self.split_h.addWidget(centre)
        self.split_h.addWidget(self.right)
        self.split_h.setStretchFactor(1, 1)
        self.split_h.setChildrenCollapsible(False)
        self.split_v = QSplitter(Qt.Vertical)
        self.split_v.setHandleWidth(7)
        self.split_v.addWidget(self.split_h)
        self.split_v.addWidget(self.bottom)
        self.split_v.setStretchFactor(0, 3)
        self.split_v.setStretchFactor(1, 2)
        self.split_v.setChildrenCollapsible(False)
        self.stack = QStackedWidget()
        self.home = self._build_home()
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.split_v)
        self.setCentralWidget(self.stack)

        for host in (self.player, self.timeline, self.table):
            for key, fn in ((Qt.Key_Left, lambda: self.player.seek(self.player.t - 1)),
                            (Qt.Key_Right, lambda: self.player.seek(self.player.t + 1))):
                sc = QShortcut(QKeySequence(key), host, activated=fn)
                sc.setContext(Qt.WidgetWithChildrenShortcut)
        loader().updated.connect(self._thumbs_ready)

    def _build_home(self):
        """Setup screen: start a new project or open one."""
        audio_f = "Audio (" + " ".join("*" + e for e in sorted(AUDIO_EXT)) + ")"
        home = QWidget()
        home.setObjectName("Home")
        outer = QVBoxLayout(home)
        outer.setContentsMargins(40, 30, 40, 30)
        t = QLabel("Start a video")
        t.setObjectName("HomeTitle")
        sub = QLabel("Add your voiceover and images - AutoEditor times every scene to the narration. "
                     "Everything stays on this PC.")
        sub.setObjectName("HomeSub")
        sub.setWordWrap(True)
        outer.addWidget(t)
        outer.addWidget(sub)
        outer.addSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(18)
        from PySide6.QtWidgets import QFrame
        c1 = QFrame()
        c1.setObjectName("Card")
        l1 = QVBoxLayout(c1)
        l1.setContentsMargins(18, 16, 18, 18)
        l1.setSpacing(10)
        l1.addWidget(label("New project", "PanelTitle"))
        f = form()
        self.home_voice = PathRow("file", audio_f, "Voiceover MP3/WAV - drag it here")
        self.bind_path(self.home_voice, "voiceover", "Choose voiceover", ("voice",))
        self.home_media = PathRow("media", placeholder="Images / clips folder - drag it here")
        self.bind_path(self.home_media, "media_dir", "Choose images folder", ("media",))
        self.home_script = PathRow("file", "Text (*.txt)", "Optional: script .txt")
        self.bind_path(self.home_script, "script", "Choose script")
        f.addRow("1. Voiceover", self.home_voice)
        f.addRow("2. Images / clips", self.home_media)
        f.addRow("3. Script (exact narration, optional)", self.home_script)
        self.home_aspect = QComboBox()
        self.bind_combo(self.home_aspect, "aspect", "Aspect ratio", [(v, k) for k, v in XC.ASPECTS.items()],
                        effects=("preview", "export"))
        f.addRow("Video shape", self.home_aspect)
        self.home_cat = QComboBox()
        self.home_cat.addItem("Choose a category…", "")
        for k, v in CATEGORIES.items():
            self.home_cat.addItem(k, k)
            self.home_cat.setItemData(self.home_cat.count() - 1, v["about"], Qt.ToolTipRole)
        self.home_cat.activated.connect(self.home_category_chosen)
        self.home_cat_info = label("Sets starting defaults for pacing, motion, captions, look and sound - "
                                   "everything stays editable.", "Help")
        more = QToolButton()
        more.setCheckable(True)
        more.setText("More options (category, how images are placed) ▸")
        f.addRow("", more)
        f.addRow("Video category / editing style", self.home_cat)
        f.addRow("", self.home_cat_info)
        self.home_mode = QComboBox()
        self.bind_combo(self.home_mode, "scene_mode", "Scene mode", SCENE_MODES, effects=("controls",))
        f.addRow("How images follow the narration", self.home_mode)

        def more_rows(on, f=f, more=more):
            for wdg in (self.home_cat, self.home_cat_info, self.home_mode):
                f.setRowVisible(wdg, on)
            more.setText("More options (category, how images are placed) " + ("▾" if on else "▸"))
        more.toggled.connect(more_rows)
        more_rows(False)
        f.addRow("Editing plan", label("Paste or import it in the editor's Editing Plan tab (one place for the "
                                       "whole plan).", "Help"))
        self._bindings.append(self._load_plan_boxes)
        l1.addLayout(f)
        self.home_analyze = QCheckBox("Analyze the voiceover as soon as the editor opens")
        self.home_analyze.setChecked(True)
        l1.addWidget(self.home_analyze)
        self.home_go = QPushButton("Create project")
        self.home_go.setToolTip("Opens the editor with these files (nothing is copied or changed)")
        self.home_go.setProperty("primary", True)
        self.home_go.setMinimumHeight(36)
        self.home_go.clicked.connect(self.home_continue)
        l1.addWidget(self.home_go)
        l1.addWidget(label("You can change every input later in the editor's Media tab.", "Help"))
        l1.addStretch(1)
        c2 = QFrame()
        c2.setObjectName("Card")
        l2 = QVBoxLayout(c2)
        l2.setContentsMargins(18, 16, 18, 18)
        l2.setSpacing(10)
        l2.addWidget(label("Open a project", "PanelTitle"))
        b = QPushButton("Open project…")
        b.clicked.connect(lambda: self.open_project())
        l2.addWidget(b)
        self.home_recent_lbl = label("Recent - double-click to open", "Section")
        l2.addWidget(self.home_recent_lbl)
        self.home_recent = QListWidget()
        self.home_recent.setMaximumHeight(200)
        self.home_recent.itemActivated.connect(lambda it: self.open_project(it.data(Qt.UserRole)))
        l2.addWidget(self.home_recent)
        self.home_recover = QPushButton("Recover unsaved work…")
        self.home_recover.setToolTip("Open the recovery copy of a project that was never saved")
        self.home_recover.clicked.connect(self.recover_untitled)
        l2.addWidget(self.home_recover)
        l2.addStretch(1)
        row.addWidget(c1, 3)
        row.addWidget(c2, 2)
        outer.addLayout(row, 1)
        sc = self._scroll(home)
        sc.setObjectName("HomeScroll")
        sc.setFrameShape(QFrame.NoFrame)
        return sc

    # ---------------------------------------------------------------- directions box
    def _directions_box(self, h):
        ed = QPlainTextEdit()
        ed.setPlaceholderText(DIRECTIONS_HELP)
        ed.setToolTip(DIRECTIONS_HELP)
        ed.setMinimumHeight(h)
        ed.setTabChangesFocus(True)
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(700)
        t.timeout.connect(lambda ed=ed: self._save_directions(ed))
        ed.textChanged.connect(t.start)
        ed._save_timer = t
        return ed

    def _save_directions(self, ed):
        txt = ed.toPlainText()
        p = self.project
        if (p.get("directions") or "") == txt:
            return
        p["directions"] = txt
        st = p["settings"]
        if txt.strip() and st.get("scene_mode") not in ("script_cues", "smart", "timed"):
            st["scene_mode"] = "script_cues"
            self.status_msg("Scene mode set to 'Follow my directions' - images will start where your paragraphs "
                            "are spoken.")
        self.commit("Directions")
        self.load_controls()
        self._directions_info()

    def _load_plan_boxes(self):
        """The editing plan is shown in the Instructions tab (and the F9 window) - keep both in step."""
        if hasattr(self, "instr_panel"):
            self.instr_panel.timed.load()
        if getattr(self, "plan_win", None) is not None:
            self.plan_win.box.load()

    def open_plan_window(self):
        from PySide6.QtWidgets import QDialog
        from autoedit.plan_panels import TimedPlanBox
        if getattr(self, "plan_win", None) is None:
            d = QDialog(self)
            d.setWindowTitle("Editing plan - AutoEditor")
            d.setModal(False)
            lay = QVBoxLayout(d)
            d.box = TimedPlanBox(self, big=True)
            lay.addWidget(d.box)
            d.resize(min(1300, max(900, self.width() - 100)), 800)
            d.finished.connect(lambda _r: d.box.save())
            self.plan_win = d
        self.plan_win.box.load()
        self.plan_win.show()
        self.plan_win.raise_()

    def _directions_info(self):
        from autoedit.cues import parse_directions
        txt = self.project.get("directions") or ""
        if not txt.strip():
            return
        cl, note = parse_directions(txt, project_media(self.project))
        self.status_msg(f"Directions: {note or str(len(cl)) + ' markers'}.")

    def _load_directions(self):
        txt = self.project.get("directions") or ""
        for ed in (getattr(self, "dir_box", None),):
            if ed is not None and ed.toPlainText() != txt and not ed.hasFocus():
                with QSignalBlocker(ed):
                    ed.setPlainText(txt)

    def apply_directions(self):
        if self.project["settings"].get("scene_mode") == "timed":
            self.status_msg("Your editing plan decides the scenes (scene mode 'My editing plan'), so directions "
                            "would replace it - NOT applied. Change the scene mode first if you want directions.")
            return
        self._save_directions(self.dir_box)
        if not (self.project.get("directions") or "").strip():
            self.status_msg("Type or paste your scene list in the Directions box first.")
            return
        if self.project["settings"].get("scene_mode") not in ("script_cues", "smart"):
            self.project["settings"]["scene_mode"] = "script_cues"
            self.load_controls()
        self.replan(label_text="Apply directions")

    def home_category_chosen(self, _i=None):
        name = self.home_cat.currentData()
        if not name:
            return
        self.choose_category(name, True)
        self.update_home_category()

    def update_home_category(self):
        name = self.project["settings"].get("category") or ""
        with QSignalBlocker(self.home_cat):
            i = self.home_cat.findData(name)
            self.home_cat.setCurrentIndex(max(0, i))
        if name:
            self.home_cat_info.setText(CATEGORIES[name]["about"] + "\n" + notes_text(category_notes(self.project)))
        else:
            self.home_cat_info.setText("Sets starting defaults for pacing, motion, captions, look and sound - "
                                       "everything stays editable.")

    def show_home(self):
        self.set_theater(False)
        self.player.pause()
        self.stack.setCurrentWidget(self.home)
        self._fill_home_recent()
        self.update_actions()

    def match_preview_aspect(self):
        """The preview shows the export's aspect ratio (framing and captions as they'll be exported)."""
        fm = XC.export_formats(self.project["settings"])
        i = self.player.aspect.findText(fm[0])
        if i >= 0 and i != self.player.aspect.currentIndex():
            self.player.aspect.setCurrentIndex(i)

    def show_editor(self):
        self.match_preview_aspect()
        self.stack.setCurrentWidget(self.split_v)
        self.update_actions()

    def in_editor(self):
        return self.stack.currentWidget() is self.split_v

    def home_continue(self):
        self.show_editor()
        if self.project["settings"].get("scene_mode") == "timed":
            self.tools.setCurrentIndex([b.text() for b in self.tools.buttons].index("Editing Plan"))
        vo = self.project.get("voiceover")
        if self.home_analyze.isChecked() and vo and os.path.isfile(vo) and not self.project.get("words"):
            self.analyze()
        else:
            self.tools.setCurrentIndex(0)

    def _fill_home_recent(self):
        self.home_recover.setVisible(os.path.isfile(os.path.join(APP_DIR, "cache", "autosave",
                                                                 "untitled.autoedit.json.autosave")))
        self.home_recent.clear()
        rec = QSettings().value("recent") or []
        if isinstance(rec, str):
            rec = [rec]
        for r in [r for r in rec if os.path.isfile(r)]:
            it = QListWidgetItem(f"{os.path.basename(r)}\n{os.path.dirname(r)}")
            it.setData(Qt.UserRole, r)
            it.setToolTip(r)
            self.home_recent.addItem(it)
        on = bool(self.home_recent.count())     # hidden when there's nothing to show
        self.home_recent.setVisible(on)
        self.home_recent_lbl.setVisible(on)

    # ---------------------------------------------------------------- leaving / resetting
    def _confirm_leave(self, what):
        """True to continue. Asks Save / Discard changes / Keep editing if there are unsaved edits."""
        if self.busy_flag:
            QMessageBox.information(self, "AutoEditor", "A render or analysis is running. Use "
                                    "'Cancel render / analysis' first (or wait for it to finish).")
            return False
        if not self.is_dirty():
            return True
        ans = ask_unsaved(self, what)
        if ans == "save":
            return self.save_project()
        if ans == "discard":
            self._drop_autosave()
        return ans == "discard"

    def _blank_project(self, keep_style=False):
        st = copy.deepcopy(self.project["settings"]) if keep_style else None
        p = prj.new_project()
        if keep_style:
            p["settings"].update(st)
            p["settings"]["hook_title"] = ""
            p["settings"]["shuffle"] = 0
        else:
            self._apply_new_project_defaults(p)
        return p

    def back_to_setup(self):
        """Close the project (the saved file, if any, is left exactly as last saved)."""
        if not self._confirm_leave("go back to setup"):
            return False
        loader().cancel_pending()
        self.project = self._blank_project()
        self.project_path = None
        self.sel = -1
        self.sel_item = ("", -1)
        self.reset_state_tracking(saved=False)
        self.refresh_all()
        self.show_home()
        self.status_msg("Project closed - your media and the saved project file were not changed.")
        return True

    def start_over(self):
        p = self.project
        n = len(p.get("scenes", []))
        parts = [x for x, v in (("voiceover", p.get("voiceover")), ("script", p.get("script")),
                                ("images folder", p.get("media_dir")), ("music", p.get("music")),
                                ("SFX folder", p.get("sfx_dir"))) if v]
        if n:
            parts.append(f"transcript and {n} scenes")
        if p.get("overlays") or p.get("chapters"):
            parts.append("text cards and chapters")
        if p.get("sfx_events"):
            parts.append("planned sound effects")
        if p.get("broll") or p.get("extra_media"):
            parts.append("V2 clips and imported media")
        if not parts:
            self.show_home()
            return
        if QMessageBox.question(self, "Clear project", "Clear this project and start empty?\n\nThis clears: "
                                + ", ".join(parts) + ".\n\nYour style settings are kept. Files on your PC "
                                "and any saved project file are NOT deleted.") != QMessageBox.Yes:
            return
        if not self._confirm_leave("start over"):
            return
        loader().cancel_pending()
        self.project = self._blank_project(keep_style=True)
        self.project_path = None       # detach, so the saved copy can't be overwritten by the empty one
        self.sel = -1
        self.sel_item = ("", -1)
        self.reset_state_tracking(saved=False)
        self.refresh_all()
        self.show_home()
        self.status_msg("Project cleared - no files on your PC were deleted.")

    def clear_input(self, key):
        """x button next to an input. Explains what depends on it, never deletes files."""
        p = self.project
        top = key != "overlay"
        cur = p.get(key) if top else p["settings"].get(key)
        if not cur:
            return
        name = os.path.basename(str(cur).rstrip("\\/")) or str(cur)
        if key == "voiceover" and (p.get("words") or p.get("scenes")):
            if QMessageBox.question(
                    self, "Remove voiceover",
                    f"Remove '{name}' from this project?\n\nThe transcript and the scene plan "
                    f"({len(p.get('scenes', []))} scenes) come from this voiceover, so they are removed too. "
                    "Text cards and chapters are kept.\n\nThe audio file stays on your PC. You can undo "
                    "this (Ctrl+Z).") != QMessageBox.Yes:
                return
            p.update(words=[], duration=0.0, scenes=[], sfx_events=[], sfx_planned=False)
            self.sel = -1
        if key == "media_dir":
            folder = os.path.normcase(os.path.abspath(cur))
            using = [s for s in p.get("scenes", []) if s.get("media") and
                     os.path.normcase(os.path.abspath(s["media"])).startswith(folder.rstrip(os.sep) + os.sep)]
            if using:
                if QMessageBox.question(
                        self, "Remove images folder",
                        f"{len(using)} scenes show images from '{name}'. They'll be left without a visual "
                        "(their timing is kept) until you choose a new folder and re-plan, or replace "
                        "visuals one by one.\n\nNo files are deleted. You can undo this.") != QMessageBox.Yes:
                    return
                for s in using:
                    s["media"] = ""
        if top:
            p[key] = ""
        else:
            p["settings"][key] = ""
        if key == "sfx_dir":
            p["sfx_planned"] = False
        if key == "script" and p.get("words"):
            self.status_msg("Script removed. Captions keep the corrected spelling until you analyze again.")
        self.commit(f"Clear {key.replace('_dir', ' folder').replace('_', ' ')}")
        self.refresh_all()

    def remove_visual(self):
        k = self.sel if self.sel_item[0] in ("scene", "") else -1
        sc = self.project["scenes"]
        if not (0 <= k < len(sc)) or not sc[k].get("media"):
            return
        sc[k]["media"] = ""
        self.commit("Remove visual")
        self.refresh_all()
        self.status_msg(f"Scene {k + 1} has no visual now - Replace visual to add one (file not deleted).")

    def update_input_info(self):
        """Show what each input points at, so a wrong pick is easy to spot."""
        p = self.project

        def fsize(path):
            try:
                n = os.path.getsize(path)
                return f"{n / 1e6:.1f} MB" if n > 1e5 else f"{n / 1e3:.0f} KB"
            except OSError:
                return ""

        def file_info(rows, path, extra=""):
            for r in rows:
                if not path:
                    r.set_info("")
                elif os.path.isfile(path):
                    r.set_info(f"✓ {os.path.basename(path)} · {fsize(path)}{extra}", "Ok")
                else:
                    r.set_info(f"⚠ File not found: {path}", "Err")
        dur = f" · {fmt_time(p['duration'])[:-3]} transcribed" if p.get("words") else ""
        file_info((self.voice, self.home_voice), p.get("voiceover"), dur)
        file_info((self.script, self.home_script), p.get("script"))
        file_info((self.music,), p.get("music"))
        file_info((self.overlay,), p["settings"].get("overlay"))
        out = p.get("output_dir")
        self.outdir.set_info("" if not out else ("✓ Folder exists" if os.path.isdir(out) else
                                                  "Will be created when you render"),
                             "Ok" if out and os.path.isdir(out) else "Help")
        sfx = p.get("sfx_dir")
        self.sfx.set_info("" if not sfx else ("⚠ Folder not found" if not os.path.isdir(sfx) else
                                              f"✓ {os.path.basename(sfx)}"), "Err" if sfx and not os.path.isdir(sfx) else "Ok")

    @staticmethod
    def _scroll(w):
        from PySide6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setWidget(w)
        return sc

    def _build_export_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        f = form()
        self.aspect = QComboBox()
        self.bind_combo(self.aspect, "aspect", "Aspect ratio", [(v, k) for k, v in XC.ASPECTS.items()],
                        effects=("preview", "export"))
        self.aspect.activated.connect(lambda _i: self.match_preview_aspect())
        self.aspect.activated.connect(lambda _i: self._aspect_changed())
        self.resolution = QComboBox()
        for r in XC.RESOLUTIONS:
            self.resolution.addItem(r, r)
        self.resolution.activated.connect(lambda _i: self._set("resolution", self.resolution.currentData(),
                                                               "Resolution", ("export",)))
        self.enc_quality = QComboBox()
        for q in XC.ENC_QUALITY:
            self.enc_quality.addItem(f"{q} - {XC.ENC_QUALITY_HELP[q]}", q)
        self.enc_quality.activated.connect(lambda _i: self._set("enc_quality", self.enc_quality.currentData(),
                                                                "Encoding quality", ("export",)))
        self.fps = QComboBox()
        self.bind_combo(self.fps, "fps", "Frame rate", [(f"{v} fps", v) for v in FPS_CHOICES],
                        effects=("preview", "export"))
        self.encoder = QComboBox()
        self.bind_combo(self.encoder, "encoder", "Encoder", [(k, k) for k in XC.ENCODERS], effects=("export",))
        self.export_fit = QComboBox()
        self.bind_combo(self.export_fit, "fit", "Framing", FIT_MODES, effects=("preview", "controls"))
        self.fit_help = label("", "Help")
        self.bg_color = ColorButton("#000000")
        self.bg_color.changed.connect(lambda c: self._set("bg_color", c, "Background colour", ("preview",)))
        self.outdir = PathRow("dir", placeholder="Folder for finished videos")
        self.bind_path(self.outdir, "output_dir", "Output folder")
        self.name = QLineEdit()
        self.name.setPlaceholderText("video")
        self.bind_line(self.name, "name", "File name", top=True)
        f.addRow("Aspect ratio", self.aspect)
        f.addRow("Resolution", self.resolution)
        f.addRow("Frame rate", self.fps)
        f.addRow("Quality", self.enc_quality)
        f.addRow("Framing", self.export_fit)
        f.addRow("", self.fit_help)
        f.addRow("Background colour", self.bg_color)
        self._export_form = f
        f.addRow("Output folder", self.outdir)
        f.addRow("File name", self.name)
        lay.addLayout(f)
        self.export_summary = QLabel("")
        self.export_summary.setObjectName("Section")
        self.export_summary.setWordWrap(True)
        lay.addWidget(self.export_summary)
        # advanced (collapsed)
        adv_btn = QToolButton()
        adv_btn.setText("Advanced ▸")
        adv_btn.setCheckable(True)
        self.adv = QWidget()
        af = form()
        af.setContentsMargins(0, 0, 0, 0)
        self.adv.setLayout(af)
        self.adv.setVisible(False)
        adv_btn.toggled.connect(lambda c: (self.adv.setVisible(c), adv_btn.setText("Advanced ▾" if c else
                                                                                   "Advanced ▸")))
        self.batch_cb = QCheckBox("Batch export: also render other aspect ratios")
        self.batch_cb.setToolTip("Off (default): one export = one video. On: the ticked extra formats are rendered "
                                 "too, one after another (each takes the full render time).")
        self.bind_check(self.batch_cb, "batch_export", "Batch export", effects=("export",))
        self.batch_w = {}
        bl = QVBoxLayout()
        for k, v in XC.ASPECTS.items():
            cb = QCheckBox(v)
            cb.toggled.connect(self._batch_changed)
            self.batch_w[k] = cb
            bl.addWidget(cb)
        self.overwrite_cb = QCheckBox("Replace an existing video with the same name without asking")
        self.bind_check(self.overwrite_cb, "overwrite", "Overwrite exports", effects=())
        af.addRow("", self.batch_cb)
        af.addRow("Extra formats", bl)
        af.addRow("Encoder", self.encoder)
        af.addRow("", self.overwrite_cb)
        af.addRow("", label("GPU encoders are tested with a real short encode before use; if one fails the export "
                            "falls back to the CPU (x264) automatically and says so in the Log.", "Help"))
        lay.addWidget(adv_btn)
        lay.addWidget(self.adv)
        self.export_note = label("", "Help")
        lay.addWidget(self.export_note)
        self.btn_draft = QPushButton("Draft preview render (fast, 480p, marked DRAFT)")
        self.btn_draft.setToolTip(self.a_draft.toolTip())
        self.btn_draft.clicked.connect(self.render_draft)
        self.btn_render = QPushButton("Render final video")
        self.btn_render.setProperty("primary", True)
        self.btn_render.setToolTip(self.a_render.toolTip())
        self.btn_render.clicked.connect(self.render)
        self.btn_pkg = QPushButton("Export editor package…")
        self.btn_pkg.setToolTip(self.a_package.toolTip())
        self.btn_pkg.clicked.connect(self.export_pkg)
        self.btn_srt = QPushButton("Export captions (.srt)…")
        self.btn_srt.clicked.connect(self.export_srt)
        self.btn_open = QPushButton("Open output folder")
        self.btn_open.clicked.connect(self.open_output)
        for b in (self.btn_draft, self.btn_render, self.btn_pkg, self.btn_srt, self.btn_open):
            lay.addWidget(b)
        self.autosave_cb = QCheckBox("Autosave a recovery copy every 30 s")
        self.bind_check(self.autosave_cb, "autosave", "Autosave", effects=())
        lay.addWidget(self.autosave_cb)
        lay.addStretch(1)
        return w

    def _build_statusbar(self):
        sb = self.statusBar()
        self.status = QLabel("Ready")
        self.status.setMinimumWidth(200)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self.cancel)
        sb.addWidget(self.status, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.btn_cancel)

    # ======================================================================== layout persistence
    def _restore_layout(self):
        s = QSettings()
        geo = s.value("ui/geometry")
        ok = False
        if geo is not None:
            ok = self.restoreGeometry(geo)
        if not ok:
            scr = QGuiApplication.primaryScreen()
            av = scr.availableGeometry() if scr else None
            if av is not None:
                self.resize(min(1560, av.width() - 40), min(960, av.height() - 60))
            else:
                self.resize(1440, 900)
        # v6.3: new keys, so layouts saved by older versions (tiny preview) start from the new defaults
        hs, vs = s.value("ui/split_h7"), s.value("ui/split_v7")
        restored = bool(hs is not None and self.split_h.restoreState(hs)) and \
            bool(vs is not None and self.split_v.restoreState(vs))
        if not restored:
            self._default_splits()
        elif s.value("ui/split_b7") is not None:
            self.split_b.restoreState(s.value("ui/split_b7"))
        # v6.0.1: new keys - older versions could save "hidden" by mistake (see _save_layout)
        for w, key, act in ((self.left, "ui/left6", self.a_toggle_left), (self.right, "ui/right6", self.a_toggle_right),
                            (self.bottom, "ui/bottom6", self.a_toggle_bottom),
                            (self.centre, "ui/centre6", self.a_toggle_centre)):
            vis = str(s.value(key, "true")).lower() != "false"
            w.setVisible(vis)
            with QSignalBlocker(act):
                act.setChecked(vis)
        if restored:
            self.timeline.set_compact(str(s.value("ui/compact7", "false")).lower() == "true")
            if s.value("ui/tabs6") is not None:
                self.set_lower_panel(str(s.value("ui/tabs6")).lower() != "false")

    def _default_splits(self):
        W = max(self.width(), 1000)
        # height really available to the editor (window minus menu, header and status bar)
        H = self.split_v.height() if self.split_v.isVisible() and self.split_v.height() > 200 else \
            max(self.height() - 110, 480)
        self.timeline.set_compact(False)
        tl_full = self.timeline.minimumHeight() + 70       # toolbar + all tracks + scrollbar
        # the preview gets at least ~55 % of the height (like CapCut); the timeline takes the rest
        top = max(int(H * 0.55), H - (tl_full + 130))
        bottom = H - top
        if bottom < tl_full:                               # not enough room: thinner tracks
            self.timeline.set_compact(True)
        tl_h = self.timeline.minimumHeight() + 70
        tabs = bottom - tl_h >= 110                        # lower tabs only when they fit readably
        self.set_lower_panel(tabs)
        if not tabs and bottom > tl_h:                     # spare timeline height goes to the preview
            top, bottom = H - (tl_h + 4), tl_h + 4
        lw = max(300, int(W * 0.24))
        rw = max(270, int(W * 0.21))
        cw = max(430, W - lw - rw)
        self.split_h.setSizes([lw, cw, rw])
        self.split_v.setSizes([top, bottom])
        self.split_b.setSizes([min(tl_h, bottom), max(80, bottom - tl_h)])

    def set_theater(self, on):
        """Theater mode: hide the tool, properties and timeline panels so the preview gets most of the
        window. Everything comes back exactly as it was when you leave it."""
        on = bool(on)
        if on and not getattr(self, "_theater", None):
            if not self.in_editor():
                with QSignalBlocker(self.a_theater), QSignalBlocker(self.player.theater_btn):
                    self.a_theater.setChecked(False)
                    self.player.theater_btn.setChecked(False)
                return
            self._theater = {"vis": [w.isVisible() for w in (self.left, self.right, self.bottom, self.centre)],
                             "h": self.split_h.saveState(), "v": self.split_v.saveState()}
            self.centre.setVisible(True)
            for w in (self.left, self.right, self.bottom):
                w.setVisible(False)
            self.status_msg("Theater mode - F11 or the Exit theater button returns to the editing layout.")
        elif not on and getattr(self, "_theater", None):
            st = self._theater
            self._theater = None
            for w, v in zip((self.left, self.right, self.bottom, self.centre), st["vis"]):
                w.setVisible(v)
            self.split_h.restoreState(st["h"])
            self.split_v.restoreState(st["v"])
            self.status_msg("Back to the editing layout.")
        for w in (self.a_theater, self.player.theater_btn):
            with QSignalBlocker(w):
                w.setChecked(on)
        self.player.theater_btn.setText("✕ Exit theater" if on else "⛶ Theater")
        QTimer.singleShot(0, self.player.show_frame)

    def show_review(self):
        """After planning: the Review scenes tab if there's room to read it, else the Review window
        (the preview keeps its size)."""
        self.set_lower_panel(True)
        self.bottom_tabs.setCurrentWidget(self.review)
        sz = self.split_b.sizes()
        if len(sz) == 2 and sz[1] < 230:
            if sum(sz) > 480:
                want = min(300, sum(sz) - 230)
                self.split_b.setSizes([sum(sz) - want, want])
            else:
                self.open_review_window()

    def review_panels(self):
        out = [self.review] if hasattr(self, "review") else []
        if getattr(self, "review_win", None) is not None and self.review_win.isVisible():
            out.append(self.review_win.panel)
        return out

    _ins_refreshing = False

    def open_instructions(self):
        if not self.project.get("scenes"):
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first - instructions attach to the "
                                    "scenes of the narration.")
            return
        from autoedit.instructions_panel import InstructionsWindow
        if getattr(self, "ins_win", None) is None:
            self.ins_win = InstructionsWindow(self)
        self.ins_win.show()
        self.ins_win.raise_()
        self.ins_win.refresh()

    def open_review_window(self):
        from PySide6.QtWidgets import QDialog
        if getattr(self, "review_win", None) is None:
            d = QDialog(self)
            d.setWindowTitle("Review scenes - AutoEditor")
            d.setModal(False)
            lay = QVBoxLayout(d)
            lay.setContentsMargins(8, 8, 8, 8)
            d.panel = ReviewPanel(self)
            lay.addWidget(d.panel)
            d.resize(min(1200, max(900, self.width() - 120)), 620)
            self.review_win = d
        self.review_win.show()
        self.review_win.raise_()
        self.review_win.panel.refresh()
        if 0 <= self.sel < len(self.project["scenes"]):
            self.review_win.panel.select(self.sel)

    def set_lower_panel(self, show):
        """Show / hide the Scenes / Text / SFX / Quality / Log tabs under the timeline."""
        self.bottom_tabs.setVisible(bool(show))
        self.tabs_show_btn.setVisible(not show)
        with QSignalBlocker(self.a_toggle_tabs):
            self.a_toggle_tabs.setChecked(bool(show))

    def reset_layout(self):
        for w, act in ((self.left, self.a_toggle_left), (self.right, self.a_toggle_right),
                       (self.bottom, self.a_toggle_bottom), (self.centre, self.a_toggle_centre)):
            w.setVisible(True)
            act.setChecked(True)
        self.set_lower_panel(True)
        self._default_splits()
        self.timeline.fit()

    def _save_layout(self):
        s = QSettings()
        s.setValue("ui/geometry", self.saveGeometry())
        s.setValue("ui/split_h7", self.split_h.saveState())
        s.setValue("ui/split_v7", self.split_v.saveState())
        s.setValue("ui/split_b7", self.split_b.saveState())
        s.setValue("ui/compact7", self.timeline.compact)
        # Save what the user chose in View, not isVisible(): when the app is closed from the setup
        # screen the editor isn't on screen, so isVisible() is False and every panel used to be
        # saved as hidden - the editor then reopened showing only the preview.
        s.setValue("ui/left6", self.a_toggle_left.isChecked())
        s.setValue("ui/right6", self.a_toggle_right.isChecked())
        s.setValue("ui/bottom6", self.a_toggle_bottom.isChecked())
        s.setValue("ui/centre6", self.a_toggle_centre.isChecked())
        s.setValue("ui/tabs6", self.a_toggle_tabs.isChecked())

    # ======================================================================== refresh (never writes)
    def refresh_all(self):
        """Reload every control from the project. Controls are signal-blocked, so this
        can't change settings."""
        self.load_controls()
        self.update_media_count()
        self.update_input_info()
        self.update_sfx_status()
        if self._peaks_for != self.project.get("voiceover"):
            self.load_peaks()
        p = self.project
        self.refresh_timeline()
        self.player.set_duration(p["duration"])
        self.update_player_audio()
        self.update_category_info()
        self.fill_table()
        self.bin.refresh()
        self.text_panel.refresh()
        self.sfx_panel.refresh()
        self.invalidate_preview()
        n = len(p["scenes"])
        kind, idx = self.sel_item
        if kind not in ("", "scene") and self._item_exists(kind, idx):
            if n:
                self.sel = min(max(self.sel, 0), n - 1)
            self.select_item(kind, idx, seek=False)
        elif n:
            self.select_scene(min(max(self.sel, 0), n - 1), seek=False)
        else:
            self.sel = -1
            self.select_item("", -1, seek=False)
        self.update_status()

    def refresh_timeline(self):
        p = self.project
        caps = project_caption_spans(p) if p["settings"].get("captions", True) else []
        self.timeline.set_project(p, self.peaks, caps)

    def edited(self, label_text, preview=False):
        """Called after a panel changed the project directly."""
        if preview:
            self.invalidate_preview()
        self.commit(label_text)
        self.update_player_audio()
        self.timeline.update()

    def update_status(self):
        busy = self.busy_flag
        dirty = self.is_dirty()
        name = os.path.basename(self.project_path) if self.project_path else "Untitled"
        if busy:
            txt, state = "Working…", "busy"
        elif dirty:
            txt, state = "● Unsaved", "dirty"
        elif self.project_path:
            txt, state = "Saved", "saved"
        else:
            txt, state = "Not saved yet", ""
        self.pill.setText(txt)
        self.pill.setProperty("state", state)
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)
        self.setWindowTitle(f"{'• ' if dirty else ''}{name} - AutoEditor {__version__}")
        self.proj_name.setText(("• " if dirty else "") + name.replace(".autoedit.json", ""))
        self.proj_name.setToolTip(self.project_path or "Not saved yet")
        self.update_actions()

    def update_actions(self):
        p = self.project
        has_words = bool(p.get("words"))
        has_scenes = bool(p.get("scenes"))
        busy = self.busy_flag
        sel_ok = 0 <= self.sel < len(p.get("scenes", []))
        for a in (self.a_analyze,):
            a.setEnabled(not busy)
        for a in (self.a_replan, self.a_plan_sfx, self.a_cues, self.a_review, self.a_instr):
            a.setEnabled(has_words and not busy)
        for a in (self.a_render, self.a_package):
            a.setEnabled(has_scenes and not busy)
        self.a_srt.setEnabled(has_words)
        self.a_desc.setEnabled(bool(p.get("chapters")))
        for a in (self.a_split, self.a_replace):
            a.setEnabled(has_scenes)
        kind, idx = self.sel_item
        self.a_duplicate.setEnabled(kind in ("scene", "broll", "text", "sfx") and idx >= 0 and
                                    not (kind == "scene" and idx >= len(p["scenes"]) - 1))
        self.a_delete.setEnabled(kind in ("scene", "broll", "text", "sfx") and idx >= 0 and
                                 not (kind == "scene" and len(p["scenes"]) < 2))
        self.a_delete_file.setEnabled(bool(self.project_path) and os.path.isfile(self.project_path or ""))
        self.a_draft.setEnabled(has_scenes and not busy)
        for a in (self.a_merge, self.a_swap):
            a.setEnabled(sel_ok and self.sel < len(p["scenes"]) - 1)
        self.a_prev.setEnabled(has_scenes)
        self.a_next.setEnabled(has_scenes)
        self.a_undo.setEnabled(bool(self.undo.undo) and not busy)
        self.a_redo.setEnabled(bool(self.undo.redo) and not busy)
        self.a_undo.setToolTip(f"Undo: {self.undo.undo[-1][0]} (Ctrl+Z)" if self.undo.undo else "Nothing to undo")
        self.a_redo.setToolTip(f"Redo: {self.undo.redo[-1][0]} (Ctrl+Shift+Z)" if self.undo.redo else "Nothing to redo")
        self.a_cancel.setEnabled(busy)
        editor = self.in_editor() if hasattr(self, "stack") else True
        self.a_back.setEnabled(editor)
        if hasattr(self, "h_back_act"):
            self.h_back_act.setVisible(editor)
            self.h_back_sep.setVisible(editor)
        self.a_remove_visual.setEnabled(sel_ok and bool(p["scenes"][self.sel].get("media")) if sel_ok else False)
        out = p.get("output_dir")
        self.a_open_out.setEnabled(bool(out) and os.path.isdir(out))
        self.btn_open.setEnabled(self.a_open_out.isEnabled())
        for w in (self.h_analyze, self.btn_analyze):
            w.setEnabled(not busy)
        self.btn_replan.setEnabled(has_words and not busy)
        for w in (self.h_render, self.btn_render, self.btn_pkg, self.h_draft, self.btn_draft):
            w.setEnabled(has_scenes and not busy)
        self.btn_srt.setEnabled(has_words)
        if not has_scenes:
            self.export_note.setText("Analyze a voiceover first - then render here.")
        else:
            self.export_note.setText("Exports exactly what the summary says. Older videos with the same name are "
                                     "kept (a new name is used) unless you allow replacing them in Advanced.")
        self.export_summary.setText("Will export: " + XC.summary(p["settings"]))

    def status_msg(self, m):
        self.status.setText(m)

    def log(self, msg):
        self.logbox.appendPlainText(msg)

    def update_media_count(self):
        folder = self.project.get("media_dir", "")
        m = list_media(folder)
        if hasattr(self, "bin"):
            self.bin.refresh()
        if not folder:
            txt, st = "", "Help"
        elif not m:
            txt, st = "⚠ " + folder_report(folder), "Err"
        else:
            nv = sum(1 for x in m if is_video(x))
            from autoedit.scenes import parse_timestamp
            nts = sum(1 for x in m if parse_timestamp(x) is not None)
            sub = len({os.path.dirname(x) for x in m})
            extra = (f" · {nts} timestamp-named" if nts else "") + (f" · in {sub} folders" if sub > 1 else "")
            txt, st = f"✓ {os.path.basename(folder.rstrip(chr(92) + '/'))}: {len(m) - nv} images, {nv} clips{extra}", "Ok"
        for r in (self.media, self.home_media):
            r.set_info(txt, st)

    def sfx_categories(self):
        return sorted(scan_sfx(self.project.get("sfx_dir"), self.project["settings"].get("builtin_sfx", True)))

    def update_sfx_status(self):
        st = self.project["settings"]
        if not SFX_LEVELS.get(st.get("sfx_level")):
            self.sfx_status.setObjectName("Help")
            self.sfx_status.setText("Sound effects are off (level None).")
        else:
            ok, lines, lib = library_report(self.project.get("sfx_dir"), st.get("builtin_sfx", True))
            self.sfx_status.setObjectName("Ok" if ok else "Err")
            self.sfx_status.setText(("✓ " if ok else "⚠ ") + " · ".join(lines))
        self.sfx_status.style().unpolish(self.sfx_status)
        self.sfx_status.style().polish(self.sfx_status)

    def load_peaks(self):
        """Voice waveform - computed in the background; the timeline repaints when ready."""
        vo = self.project.get("voiceover")
        self.peaks = loader().peaks(vo) if vo and os.path.isfile(vo) else None
        self._peaks_for = vo
        self.player.set_audio(vo)
        self.refresh_timeline()

    def _thumbs_ready(self):
        vo = self.project.get("voiceover")
        if self.peaks is None and vo and self._peaks_for == vo:
            pk = loader().peaks(vo)
            if pk is not None:
                self.peaks = pk
                self.timeline.peaks = pk
                self.timeline.update()
        sc = self.project["scenes"]
        with QSignalBlocker(self.table):
            for r in range(min(self.table.rowCount(), len(sc))):
                it = self.table.item(r, 4)
                if it is not None and it.icon().isNull():
                    m = sc[r].get("media")
                    pm = thumb(m, 80, 45) if m else None
                    if pm is not None:
                        it.setIcon(QIcon(pm))

    # ======================================================================== preview
    def preview_frame(self, t):
        if not self.project["scenes"]:
            return None
        fmt = self.player.aspect_name()
        cv = self.player.screen
        W, H = preview_size(fmt, cv.width(), cv.height(), cv.devicePixelRatioF())
        key = (fmt, self.player.fx.isChecked(), W, H)
        if self.comp is None or self.comp_key != key:
            if self.comp is not None:
                self.comp.close()
            self.comp = Compositor(self.project, W, H, 30, total=self.project["duration"] + 0.4,
                                   preview=True, atmosphere=self.player.fx.isChecked(), log=self.log)
            self.comp_key = key
        return self.comp.frame_at(t, captions=self.player.caps.isChecked())

    def invalidate_preview(self, *_):
        if self.comp is not None:
            self.comp.close()
        self.comp = None
        self.player.show_frame()

    def scene_at(self, t):
        sc = self.project["scenes"]
        for k, s in enumerate(sc):
            if s["start"] <= t < s["end"]:
                return k
        return len(sc) - 1 if sc else -1

    def on_player_time(self, t):
        self.timeline.set_playhead(t, follow=self.player.playing)
        if self.player.playing and self.project["scenes"] and self.sel_item[0] in ("scene", ""):
            k = self.scene_at(t)
            if k != self.sel:
                self.select_scene(k, seek=False)
        elif not self.player.playing:
            self.props.sync_playhead()

    def stop_playback(self):
        self.player.pause()
        self.player.seek(0.0)

    def step_scene(self, d):
        sc = self.project["scenes"]
        if not sc:
            return
        k = min(max(0, (self.sel if self.sel >= 0 else 0) + d), len(sc) - 1)
        self.select_scene(k)

    # ======================================================================== selection (never writes)
    def select_scene(self, k, seek=True):
        sc = self.project["scenes"]
        if not (0 <= k < len(sc)):
            return
        self.select_item("scene", k, seek=seek)

    def update_player_audio(self):
        """Preview follows the project's track levels and planned effects (preview volume is separate)."""
        p = self.project
        st = p["settings"]
        self.player.set_levels(st.get("voice_db", 0.0), st.get("music_db", -20), st.get("sfx_db", 0.0))
        self.player.set_music(p.get("music"))
        level_on = bool(SFX_LEVELS.get(st.get("sfx_level"))) and st.get("sfx_mode", "auto") == "auto"
        ev = [e for e in p.get("sfx_events") or [] if not e.get("source")] if (p.get("sfx_planned") and level_on) \
            else []
        explicit = []
        if p.get("voiceover") and os.path.isfile(p["voiceover"]):
            from autoedit.audio import sfx_ref_db, voice_ref_db
            for e in p.get("sfx_events") or []:
                if e.get("source") and e.get("file") and os.path.isfile(e["file"]):
                    try:      # same level rule as the export (relative to the narration)
                        v = voice_ref_db(p["voiceover"]) + float(e.get("rel_db", -10)) - sfx_ref_db(e["file"])
                    except Exception:
                        v = -12.0
                    explicit.append(dict(e, vol_db=max(-60.0, min(12.0, v))))
        self.player.set_sfx(ev + explicit)
        self.player.sfx_on.setToolTip(
            "Play planned sound effects in the preview (levels approximate - the render matches them to your "
            "voice)" if p.get("sfx_planned") else
            "No effects planned yet - choose 'Plan sound effects' (Audio tab) to hear them in the preview")

    def update_category_info(self):
        st = self.project["settings"]
        name = st.get("category") or ""
        self.cat_label.setText(f"<b>{name}</b>" if name else "<i>not chosen</i>")
        self.cat_label.setToolTip(CATEGORIES.get(name, {}).get("about", "Choose a category for starting defaults"))
        self.cat_notes.setText(notes_text(category_notes(self.project)) if name else
                               "A category sets starting defaults for pacing, motion, captions, look and sound.")
        self.motion_help.setText(MOTION_LEVEL_HELP.get(st.get("motion_level", "Medium"), ""))

    def _plan_unapplied(self):
        from autoedit.timedplan import problems_for_export
        try:
            return bool(problems_for_export(self.project))
        except Exception:
            return False

    def _tool_tab_changed(self, i):
        """The Stock tab needs room for its results grid: widen the left panel a little if it's narrow."""
        if self.tools.buttons[i].text() == "Export":
            self.right_tabs.setCurrentIndex(1)
            self.load_controls()
        if self.pages.widget(i) is not None and self.pages.widget(i).findChild(type(self.stock)) is self.stock:
            sizes = self.split_h.sizes()
            if len(sizes) == 3 and sizes[0] < 420 and sizes[1] > 700:
                d = min(420 - sizes[0], sizes[1] - 700)
                self.split_h.setSizes([sizes[0] + d, sizes[1] - d, sizes[2]])

    # ---------------------------------------------------------------- image cues
    def open_cue_dialog(self):
        if not self.project.get("words"):
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first - images are matched to the "
                                    "words in the recording.")
            return
        from autoedit.cue_dialog import CueDialog
        self.player.pause()
        dlg = CueDialog(self, self.project)
        dlg.seek.connect(self.player.seek)
        if dlg.exec() and dlg.result:
            scenes, report = dlg.result
            self.project["scenes"] = scenes
            self.project["sfx_planned"] = False
            self.sel, self.sel_item = 0, ("scene", 0)
            self.commit("Match images to narration")
            self.refresh_all()
            self.log("---- Match images to narration")
            for line in report:
                self.log("  " + line)
            self.status_msg(report[0] + " Undo with Ctrl+Z.")

    # ---------------------------------------------------------------- stock B-roll
    def _stock_scene_text(self):
        sc = self.project["scenes"]
        return sc[self.sel]["text"] if 0 <= self.sel < len(sc) else ""

    def _ask_replace(self, scene, path):
        """-> 'replace' | 'v2' | 'bin'"""
        box = QMessageBox(self)
        box.setWindowTitle("Replace scene visual?")
        box.setText(f"Scene {self.sel + 1} already shows {os.path.basename(scene['media'])}.\n\n"
                    f"Replace it with the stock clip {os.path.basename(path)}?")
        rb = box.addButton("Replace", QMessageBox.AcceptRole)
        vb = box.addButton("Add on V2 instead", QMessageBox.ActionRole)
        box.addButton("Keep in media bin only", QMessageBox.RejectRole)
        box.exec()
        return "replace" if box.clickedButton() == rb else ("v2" if box.clickedButton() == vb else "bin")

    def on_stock_downloaded(self, path, info, action):
        """A clip the user chose was downloaded: record its source/license, put it in the bin, then
        do what they asked - never silently replacing a scene's existing visual."""
        p = self.project
        p.setdefault("stock_assets", {})[os.path.normpath(path)] = info
        self._register_media(path)
        sc = p["scenes"]
        if action == "scene":
            if not (0 <= self.sel < len(sc)):
                self.commit("Download stock clip")
                self.refresh_all()
                self.status_msg("Clip saved to the media bin - select a scene, then use it from the bin.")
                return
            s = sc[self.sel]
            if s.get("media"):
                choice = self._ask_replace(s, path)
                if choice == "v2":
                    action = "v2"
                elif choice != "replace":
                    self.commit("Download stock clip")
                    self.refresh_all()
                    self.status_msg("Clip saved to the media bin - the scene was not changed.")
                    return
            if action == "scene":
                self._set_visual(s, path)
                self.sel_item = ("scene", self.sel)
                self.commit("Stock clip for scene")
                self.refresh_all()
                self.status_msg(f"Scene {self.sel + 1} now uses the stock clip (undo with Ctrl+Z).")
                return
        if action == "v2":
            t = self.player.t
            if 0 <= self.sel < len(sc) and not (sc[self.sel]["start"] <= t < sc[self.sel]["end"]):
                t = sc[self.sel]["start"]
            self.add_broll(path, t, length=min(6.0, float(info.get("duration") or 4.0)))
            return
        self.commit("Download stock clip")
        self.refresh_all()
        self.status_msg("Clip saved to the media bin.")

    def apply_motion_level_all(self):
        from autoedit.scenes import apply_motion_level
        p = self.project
        if not p["scenes"]:
            self.status_msg("Analyze the voiceover first.")
            return
        st = p["settings"]
        preset = PRESETS.get(st.get("preset"), PRESETS["Documentary"])
        # locked scenes and motion set by your own editing instructions win over the automatic level
        free = [s for s in p["scenes"] if not s.get("locked") and "motion" not in (s.get("locked_fields") or [])
                and not str((s.get("provenance") or {}).get("motion", "")).startswith("instruction")]
        apply_motion_level(free, preset, st.get("motion_level", "Medium"), seed=int(time.time()) % 100000)
        if st.get("restrict_no_shake"):
            for s in free:
                if s.get("motion") == "subtle":
                    s["motion"] = "zoom_in"
        kept = len(p["scenes"]) - len(free)
        self.invalidate_preview()
        if self.commit(f"Motion level: {st.get('motion_level', 'Medium')}"):
            self.fill_table()
            self.props.reload()
            self.status_msg(f"Automatic motion re-chosen for the still images ({st.get('motion_level')})"
                            + (f"; {kept} locked or instructed scene(s) kept as they were" if kept else "")
                            + ". Ctrl+Z undoes it.")

    def choose_category(self, name, apply_defaults=True):
        st = self.project["settings"]
        if apply_defaults:
            changed = apply_category(st, name)
        else:
            st["category"], changed = name, []
        self.load_controls()
        self.invalidate_preview()
        self.update_category_info()
        if self.commit(f"Video category: {name}"):
            extra = (" Re-plan scenes (Shift+F5) to use its pacing and motion." if self.project["scenes"] and
                     any(k in changed for k in ("preset", "motion_level", "hook_fast_cuts")) else "")
            self.status_msg(f"Category {name}: starting defaults applied ({len(changed)} settings) - all still "
                            f"editable.{extra}")
        return changed

    def _item_exists(self, kind, idx):
        p = self.project
        if kind in ("voice", "music") or (kind == "sfx" and idx == -1):
            return True
        if kind == "caption":
            return idx >= 0 and bool(p.get("words"))
        lst = {"scene": p["scenes"], "broll": p.get("broll") or [], "text": p.get("overlays") or [],
               "sfx": p.get("sfx_events") or []}.get(kind)
        return lst is not None and 0 <= idx < len(lst)

    def select_item(self, kind, idx, seek=True):
        """Select something (never writes). Timeline, properties, table and preview follow."""
        if kind and not self._item_exists(kind, idx):
            kind, idx = "", -1
        self.sel_item = (kind, idx)
        sc = self.project["scenes"]
        if kind == "scene":
            self.sel = idx
            if self.table.currentRow() != idx:
                with QSignalBlocker(self.table):
                    self.table.selectRow(idx)
            for rp in self.review_panels():
                rp.select(idx)
            if seek and not self.player.playing:
                s = sc[idx]
                if not (s["start"] <= self.player.t < s["end"]):
                    self.player.seek(s["start"] + min(0.5, (s["end"] - s["start"]) / 2))
        elif kind in ("broll", "text") and seek and not self.player.playing:
            it = (self.project["broll"] if kind == "broll" else self.project["overlays"])[idx]
            if not (it["start"] <= self.player.t < it["end"]):
                self.player.seek(it["start"] + 0.05)
        self.timeline.set_selected(kind, idx)
        self.props.show_item(kind, idx)
        self.update_actions()

    def on_timeline_selected(self, kind, idx):
        # the timeline already moved the playhead to the click position
        self.select_item(kind, idx, seek=False)

    def table_selection(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.select_scene(rows[0].row())

    # ======================================================================== edits
    def _aspect_changed(self):
        from autoedit.instructions import flag_framing_review
        n = flag_framing_review(self.project, self.project["settings"].get("aspect", "16:9"))
        if n:
            self.status_msg(f"{n} scene(s) with manual framing are marked for review (Editing instructions).")
            self.commit("Aspect ratio")
            self.fill_table()

    def _batch_changed(self, *_):
        a = self.project["settings"].get("aspect", "16:9")
        fm = [k for k, cb in self.batch_w.items() if cb.isChecked() and k != a]
        self._set("batch_formats", fm, "Batch formats", ("export",))

    def _load_export_controls(self):
        st = self.project["settings"]
        a = st.get("aspect", "16:9")
        with QSignalBlocker(self.resolution):
            for i in range(self.resolution.count()):
                r = self.resolution.itemData(i)
                W, H = XC.dims(a, r)
                self.resolution.setItemText(i, f"{r} - {W} × {H}")
            i = self.resolution.findData(st.get("resolution", "Full HD"))
            self.resolution.setCurrentIndex(i if i >= 0 else 2)
        with QSignalBlocker(self.enc_quality):
            i = self.enc_quality.findData(st.get("enc_quality", "Standard"))
            self.enc_quality.setCurrentIndex(i if i >= 0 else 1)
        for k, cb in self.batch_w.items():
            with QSignalBlocker(cb):
                cb.setChecked(k in (st.get("batch_formats") or []) and k != a)
                cb.setEnabled(bool(st.get("batch_export")) and k != a)
        self.fit_help.setText(FIT_HELP.get(st.get("fit", "crop"), ""))
        self.bg_color.set(st.get("bg_color", "#000000"))
        self._export_form.setRowVisible(self.bg_color, st.get("fit") == "fit_color")
        if hasattr(self, "export_summary"):
            self.export_summary.setText("Will export: " + XC.summary(st))

    def captions_changed(self):
        before = copy.deepcopy(self.project["settings"])
        self.cap_panel.save(self.project["settings"])
        if self.project["settings"] != before:
            self.invalidate_preview()
            self.commit("Caption style")

    # ---------------------------------------------------------------- properties panel
    def on_props_changed(self, label_text, kind):
        """The properties panel changed the project; refresh what depends on it + undo step."""
        if kind == "cut":
            words = self.project["words"]
            for s in self.project["scenes"]:
                s["text"] = scene_text(words, s["start"], s["end"])
            self.invalidate_preview()
            self.fill_table()
            self.timeline.update()
            self.commit(label_text)
            self.update_actions()
            return
        if kind in ("tf", "keys", "adjust"):
            self.player.show_frame()           # transforms are applied per frame - no rebuild needed
        elif kind == "motion" and self.comp is not None and self.sel_item[0] == "scene":
            self.comp.invalidate(self.sel_item[1])
            self.player.show_frame()
        elif kind in ("sfx", "audio"):
            if kind == "audio":
                if label_text == "SFX level":
                    self._apply_effects(("sfx",))
                self.load_controls()
            self.update_player_audio()
            self.sfx_panel.refresh()
        else:
            self.invalidate_preview()
        if kind in ("motion", "transition", "lock", "framing") and self.sel_item[0] == "scene":
            self.update_table_row(self.sel_item[1])
            if kind == "lock":
                for rp in self.review_panels():
                    rp.refresh()
        if kind == "caption":
            self.refresh_timeline()
            for s in self.project["scenes"]:
                s["text"] = scene_text(self.project["words"], s["start"], s["end"])
            self.fill_table()
        if kind == "text":
            self.text_panel.refresh()
        self.timeline.update()
        self.commit(label_text)
        self.update_actions()

    def on_props_request(self, what):
        kind, idx = self.sel_item
        if what == "replace":
            self.replace_media()
        elif what == "remove_visual":
            self.remove_visual()
        elif what == "delete":
            self.delete_selected()
        elif what == "motion_all":
            self.apply_motion_all()
        elif what == "effects_all":
            self.apply_effects_all()
        elif what == "preview_transition":
            self.preview_transition()
        elif what == "preview_sfx" and kind == "sfx":
            self.preview_sfx(idx)
        elif what == "plan_sfx":
            self.plan_sfx()
        elif what == "preview_music":
            self.preview_music()

    def open_props_tab(self, i):
        self.right_tabs.setCurrentWidget(self.props)
        if self.sel_item[0] in ("scene", "broll"):
            self.props.vtabs.setCurrentIndex(i)
        else:
            self.status_msg("Select a clip on the timeline first.")

    def _selected_visual(self):
        kind, idx = self.sel_item
        if kind == "broll" and self._item_exists(kind, idx):
            return self.project["broll"][idx]
        if 0 <= self.sel < len(self.project["scenes"]):
            return self.project["scenes"][self.sel]
        return None

    def apply_motion_all(self):
        src = self._selected_visual()
        if src is None:
            return
        n = 0
        for s in self.project["scenes"]:
            if s.get("media") and not is_video(s["media"]):
                s["motion"] = src.get("motion", "zoom_in") if src.get("motion") != "none" else "static"
                s["motion_amount"] = src.get("motion_amount")
                n += 1
        self.invalidate_preview()
        if self.commit("Apply motion to all"):
            self.fill_table()
            self.status_msg(f"Motion '{src.get('motion')}' applied to {n} still images.")

    def apply_effects_all(self):
        src = self._selected_visual()
        if src is None:
            return
        for s in self.project["scenes"]:
            if src.get("adjust"):
                s["adjust"] = copy.deepcopy(src["adjust"])
            else:
                s.pop("adjust", None)
        self.invalidate_preview()
        if self.commit("Apply clip effects to all"):
            self.timeline.update()
            self.status_msg("Clip effects copied to every scene.")

    # ---------------------------------------------------------------- previews
    def _play_range(self, t0, secs):
        self.player.pause()
        self.player.seek(max(0.0, t0))
        self.player.play()
        self._preview_stop.start(int(secs * 1000))

    def preview_transition(self):
        sc = self.project["scenes"]
        k = self.sel
        if not (0 <= k < len(sc) - 1):
            self.status_msg("Select a scene that has a next scene to preview its transition.")
            return
        self._play_range(sc[k]["end"] - 1.5, 3.0)
        self.status_msg(f"Previewing transition '{sc[k].get('transition', 'cut')}' after scene {k + 1}.")

    def preview_music(self):
        if not self.project.get("music"):
            self.status_msg("Choose a music file in the Audio tab first.")
            return
        self.player.music_on.setChecked(True)
        self._play_range(self.player.t, 8.0)

    def preview_sfx(self, idx):
        ev = self.project.get("sfx_events") or []
        if not (0 <= idx < len(ev)):
            return
        self.sfx_panel.table.selectRow(idx)
        self.sfx_panel.preview()

    # ---------------------------------------------------------------- media bin
    def used_media(self):
        p = self.project
        return {os.path.normcase(os.path.abspath(c["media"])) for c in (p["scenes"] + (p.get("broll") or []))
                if c.get("media")}

    def _register_media(self, path):
        """Make sure a file used on the timeline also shows in the bin (paths only - nothing is copied)."""
        p = self.project
        n = os.path.normcase(os.path.abspath(path))
        p["excluded_media"] = [x for x in p.get("excluded_media") or [] if os.path.normcase(os.path.abspath(x)) != n]
        if n not in {os.path.normcase(os.path.abspath(x)) for x in project_media(p)}:
            p.setdefault("extra_media", []).append(os.path.normpath(path))

    def import_media(self, paths):
        """Register image/clip paths quickly; thumbnails are made in the background."""
        good, bad = [], []
        for f in paths:
            ext = os.path.splitext(f)[1].lower()
            if os.path.isdir(f):
                found = list_media(f)
                if found:
                    good += found
                else:
                    bad.append(f"{os.path.basename(f)} (no images or clips inside)")
            elif not os.path.isfile(f):
                bad.append(f"{os.path.basename(f)} (not found)")
            elif ext in IMAGE_EXT or ext in VIDEO_EXT:
                good.append(f)
            else:
                bad.append(f"{os.path.basename(f)} (unsupported type {ext or '?'})")
        for f in good:
            self._register_media(f)
        if good:
            self.commit(f"Import {len(good)} media file(s)")
            self.bin.refresh()
            self.update_media_count()
        msg = f"Imported {len(good)} media file(s) - previews are generated in the background."
        if bad:
            msg += f" Skipped {len(bad)}: " + ", ".join(bad[:4]) + ("…" if len(bad) > 4 else "")
            self.log("Import skipped: " + "; ".join(bad))
        self.status_msg(msg)
        return good

    def bin_add_files(self):
        start = self.project.get("media_dir") or QSettings().value("last_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "Import images / clips", start, MEDIA_FILTER)
        if files:
            QSettings().setValue("last_dir", os.path.dirname(files[0]))
            self.import_media(files)

    def bin_use_for_scene(self, path):
        if not (0 <= self.sel < len(self.project["scenes"])):
            self.status_msg("Select a scene on the timeline first (or drag the file onto V1).")
            return
        self._set_visual(self.project["scenes"][self.sel], path)
        self.sel_item = ("scene", self.sel)
        self.commit("Use media for scene")
        self.refresh_all()

    def bin_remove(self, paths):
        if not paths:
            return
        used = self.used_media()
        n_used = [x for x in paths if os.path.normcase(os.path.abspath(x)) in used]
        msg = f"Remove {len(paths)} file(s) from this project's media bin?"
        if n_used:
            msg += (f"\n\n{len(n_used)} of them are on the timeline - those scenes will be left without a "
                    "visual and V2 clips using them are removed.")
        msg += "\n\nThe files stay on your PC. You can undo this (Ctrl+Z)."
        if QMessageBox.question(self, "Remove from project", msg) != QMessageBox.Yes:
            return
        rm = {os.path.normcase(os.path.abspath(x)) for x in paths}
        p = self.project
        p["extra_media"] = [x for x in p.get("extra_media") or [] if os.path.normcase(os.path.abspath(x)) not in rm]
        folder_media = {os.path.normcase(os.path.abspath(x)) for x in list_media(p.get("media_dir"))}
        ex = p.setdefault("excluded_media", [])
        for x in paths:
            if os.path.normcase(os.path.abspath(x)) in folder_media:
                ex.append(x)
        for s in p["scenes"]:
            if s.get("media") and os.path.normcase(os.path.abspath(s["media"])) in rm:
                s["media"] = ""
        p["broll"] = [c for c in p.get("broll") or [] if os.path.normcase(os.path.abspath(c["media"])) not in rm]
        self.commit(f"Remove {len(paths)} file(s) from project")
        self.refresh_all()

    # ---------------------------------------------------------------- V2 / text / sfx items
    def add_broll(self, path, t, length=None):
        if not self.project["scenes"]:
            self.status_msg("Analyze the voiceover first - then add clips to V2.")
            return
        dur = self.project["duration"]
        t = min(max(0.0, t), max(0.0, dur - 0.5))
        ln = length or 3.0
        if is_video(path):
            from autoedit.visuals import probe_duration
            try:
                ln = min(ln if length else 6.0, float(probe_duration(path) or ln))
            except Exception:
                pass
        clip = {"media": os.path.normpath(path), "start": round(t, 3), "end": round(min(dur + 0.4, t + ln), 3),
                "motion": "none" if is_video(path) else "zoom_in", "zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0,
                "opacity": 1.0, "fade": 0.25}
        self.project.setdefault("broll", []).append(clip)
        self.project["broll"].sort(key=lambda c: c["start"])
        self._register_media(path)
        self.sel_item = ("broll", self.project["broll"].index(clip))
        self.commit("Add V2 clip")
        self.refresh_all()
        self.status_msg(f"Added {os.path.basename(path)} on V2 at {fmt_time(t)} - drag it to move, drag its "
                        "edges to trim.")

    def add_text_card(self, t, kind=None):
        from autoedit.overlays import DEFAULT_DUR
        if not self.project["duration"]:
            self.status_msg("Analyze the voiceover first.")
            return
        kind = kind or self.new_card_type.currentData() or "label"
        t = min(max(0.0, t), max(0.0, self.project["duration"] - 0.5))
        o = {"type": kind, "start": round(t, 3), "end": round(min(self.project["duration"], t + DEFAULT_DUR[kind]), 3),
             "text": "New text", "sub": ""}
        self.project.setdefault("overlays", []).append(o)
        self.project["overlays"].sort(key=lambda x: x["start"])
        self.sel_item = ("text", self.project["overlays"].index(o))
        self.commit("Add text card")
        self.refresh_all()
        self.right_tabs.setCurrentWidget(self.props)
        self.props.t_text.setFocus()
        self.props.t_text.selectAll()

    def add_sfx_at(self, t):
        cats = self.sfx_categories()
        if not cats:
            self.status_msg("No sound-effect library found - check the Audio tab.")
            return
        cat = self.sfx_panel.add_cat.currentText() or cats[0]
        from autoedit.audio import scan_sfx
        lib = scan_sfx(self.project.get("sfx_dir"), self.project["settings"].get("builtin_sfx", True))
        e = {"t": round(max(0.0, t), 3), "file": lib[cat][0], "cat": cat, "gain": 0.0, "rule_gain": 0.0,
             "dur": None, "enabled": True, "reason": "added by you"}
        ev = self.project.setdefault("sfx_events", [])
        ev.append(e)
        ev.sort(key=lambda x: x["t"])
        self.project["sfx_planned"] = True
        self.sel_item = ("sfx", ev.index(e))
        self.commit("Add sound effect")
        self.refresh_all()

    def split_item(self, kind, idx):
        lst = self.project["broll"] if kind == "broll" else self.project["overlays"]
        it = lst[idx]
        t = self.player.t
        if not (it["start"] + 0.2 < t < it["end"] - 0.2):
            self.status_msg("Move the playhead inside the selected clip to split it.")
            return
        a, b = copy.deepcopy(it), copy.deepcopy(it)
        a["end"] = b["start"] = round(t, 3)
        _split_parts(a, b, a["end"] - it["start"])
        if a.get("keyframes"):
            cut_t = t - it["start"]
            a["keyframes"] = [k for k in a["keyframes"] if k["t"] <= cut_t] or None
            b["keyframes"] = [dict(k, t=round(k["t"] - cut_t, 3)) for k in b["keyframes"] if k["t"] > cut_t] or None
            for x in (a, b):
                if not x["keyframes"]:
                    x.pop("keyframes")
        lst[idx:idx + 1] = [a, b]
        self.sel_item = (kind, idx + 1)
        self.commit("Split clip")
        self.refresh_all()

    def duplicate_selected(self):
        kind, idx = self.sel_item
        p = self.project
        if kind == "scene" and 0 <= idx < len(p["scenes"]) - 1:
            src, dst = p["scenes"][idx], p["scenes"][idx + 1]
            if visual_locked(dst):
                self.status_msg("The next scene's visual is locked - unlock it first. Nothing was changed.")
                return
            for k in VISUAL_KEYS:
                if k in src:
                    dst[k] = copy.deepcopy(src[k])
                else:
                    dst.pop(k, None)
            self.sel_item = ("scene", idx + 1)
            self.sel = idx + 1
            self.commit("Duplicate visual to next scene")
        elif kind in ("broll", "text") and self._item_exists(kind, idx):
            lst = p["broll"] if kind == "broll" else p["overlays"]
            it = copy.deepcopy(lst[idx])
            ln = it["end"] - it["start"]
            it["start"] = round(min(it["end"], max(0.0, p["duration"] - ln)), 3)
            it["end"] = round(it["start"] + ln, 3)
            lst.append(it)
            lst.sort(key=lambda c: c["start"])
            self.sel_item = (kind, lst.index(it))
            self.commit("Duplicate clip")
        elif kind == "sfx" and self._item_exists(kind, idx):
            e = copy.deepcopy(p["sfx_events"][idx])
            e["t"] = round(e["t"] + 0.5, 3)
            e["reason"] = "copied by you"
            p["sfx_events"].append(e)
            p["sfx_events"].sort(key=lambda x: x["t"])
            p["sfx_planned"] = True
            self.sel_item = ("sfx", p["sfx_events"].index(e))
            self.commit("Duplicate sound effect")
        else:
            return
        self.refresh_all()

    def delete_selected(self):
        kind, idx = self.sel_item
        p = self.project
        if kind == "scene" and self._item_exists(kind, idx):
            sc = p["scenes"]
            if len(sc) < 2:
                self.status_msg("A project needs at least one scene.")
                return
            if idx == 0:
                sc[1]["start"] = sc[0]["start"]
                sc[1]["text"] = (sc[0]["text"] + " " + sc[1]["text"]).strip()
                del sc[0]
                new = 0
            else:
                sc[idx - 1]["end"] = sc[idx]["end"]
                sc[idx - 1]["text"] = (sc[idx - 1]["text"] + " " + sc[idx]["text"]).strip()
                sc[idx - 1]["transition"] = sc[idx].get("transition", "cut")
                del sc[idx]
                new = idx - 1
            self.sel, self.sel_item = new, ("scene", new)
            self.commit("Delete scene (merged)")
            self.status_msg("Scene deleted - its time was given to the neighbouring scene (narration is never cut).")
        elif kind in ("broll", "text", "sfx") and self._item_exists(kind, idx):
            key = {"broll": "broll", "text": "overlays", "sfx": "sfx_events"}[kind]
            del p[key][idx]
            if kind == "sfx":
                p["sfx_planned"] = True
            self.sel_item = ("", -1)
            self.commit({"broll": "Delete V2 clip", "text": "Delete text card", "sfx": "Delete sound effect"}[kind])
        else:
            return
        self.refresh_all()

    # ---------------------------------------------------------------- timeline signals
    def on_timeline_edited(self, label_text):
        if label_text == "Reorder visuals":
            self.sel = self.sel_item[1] if self.sel_item[0] == "scene" else self.sel
            self.fill_table()
        if label_text in ("Move sound effect",):
            self.sfx_panel.refresh()
            self.update_player_audio()
        if label_text in ("Move clip", "Trim clip"):
            kind, idx = self.sel_item
            lists = {"broll": self.project["broll"], "text": self.project["overlays"]}
            picked = lists[kind][idx] if kind in lists and 0 <= idx < len(lists[kind]) else None
            for key in ("broll", "overlays"):
                self.project[key].sort(key=lambda c: c["start"])
            if picked is not None:                 # keep the SAME clip selected after re-sorting
                idx = next(i for i, c in enumerate(lists[kind]) if c is picked)
                self.sel_item = (kind, idx)
                self.timeline.sel = (kind, idx)
            self.text_panel.refresh()
        self.invalidate_preview()
        self.commit(label_text)
        self.props.reload()
        self.update_actions()

    def on_timeline_request(self, action, kind, idx):
        if kind and idx >= 0:
            self.select_item(kind, idx, seek=False)
        if action == "split":
            self.split_at_playhead()
        elif action == "duplicate":
            self.duplicate_selected()
        elif action == "delete":
            self.delete_selected()
        elif action == "replace":
            self.replace_media()
        elif action == "remove_visual":
            self.remove_visual()
        elif action == "locked_visual":
            self.status_msg("A scene with a locked visual is in the way - unlock it first. Nothing was moved.")
        elif action == "edit" and kind == "caption":
            self.right_tabs.setCurrentWidget(self.props)
            self.props.c_text.setFocus()
        elif action == "preview" and kind == "sfx":
            self.preview_sfx(idx)
        elif action == "add_here":
            t = idx / 1000.0
            if kind == "broll":
                start = self.project.get("media_dir") or ""
                path, _ = QFileDialog.getOpenFileName(self, "Add image or clip to V2", start, MEDIA_FILTER)
                if path:
                    self.add_broll(path, t)
            elif kind == "text":
                self.add_text_card(t)
            elif kind == "sfx":
                self.add_sfx_at(t)

    def on_media_dropped(self, path, kind, idx, t):
        ext = os.path.splitext(path)[1].lower()
        if not (ext in IMAGE_EXT or ext in VIDEO_EXT) or not os.path.isfile(path):
            self.status_msg(f"Can't use {os.path.basename(path)} here - drop an image or video clip.")
            return
        if kind == "scene" and 0 <= idx < len(self.project["scenes"]):
            self._set_visual(self.project["scenes"][idx], path)
            self._register_media(path)
            self.sel, self.sel_item = idx, ("scene", idx)
            self.commit("Replace visual (drag & drop)")
            self.refresh_all()
        elif kind == "broll":
            self.add_broll(path, t)

    def edit_caption_at_playhead(self):
        caps = self.timeline.captions
        t = self.player.t
        i = next((j for j, c in enumerate(caps) if c["start"] <= t <= c["end"] + 0.05), -1)
        if i < 0:
            self.status_msg("No caption at the playhead - move the playhead onto spoken words.")
            return
        self.select_item("caption", i, seek=False)
        self.right_tabs.setCurrentWidget(self.props)
        self.props.c_text.setFocus()

    # ---------------------------------------------------------------- autosave / project file
    def autosave_path(self):
        if self.project_path:
            return self.project_path + ".autosave"
        return os.path.join(APP_DIR, "cache", "autosave", "untitled.autoedit.json.autosave")

    def autosave(self):
        if not self.project["settings"].get("autosave", True) or self.busy_flag or not self.is_dirty():
            return
        if not (self.project.get("voiceover") or self.project.get("scenes")):
            return
        path = self.autosave_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            prj.save(self.project, tmp)
            os.replace(tmp, path)
            self.status_msg(f"Recovery copy saved {time.strftime('%H:%M:%S')}")
        except OSError as e:
            self.log(f"Autosave failed: {e}")

    def _drop_autosave(self, path=None):
        a = path or self.autosave_path()
        try:
            if os.path.isfile(a):
                os.remove(a)          # our own recovery file - never a user file
        except OSError:
            pass

    def _offer_recovery(self, path):
        """On open: a newer recovery copy exists -> offer it."""
        a = path + ".autosave" if path else self.autosave_path()
        if not os.path.isfile(a) or (path and os.path.getmtime(a) <= os.path.getmtime(path)):
            return None
        ans = QMessageBox.question(
            self, "Recover unsaved changes",
            f"AutoEditor found a recovery copy from {time.strftime('%d %b %H:%M', time.localtime(os.path.getmtime(a)))} "
            "with changes that were not saved.\n\nOpen the recovered version? (Your saved project file is not "
            "changed until you save.)")
        if ans != QMessageBox.Yes:
            return None
        try:
            return prj.load(a)
        except Exception as e:
            QMessageBox.warning(self, "AutoEditor", f"The recovery copy could not be read:\n{e}")
            return None

    def recover_untitled(self):
        proj = self._offer_recovery(None)
        if proj is None:
            return
        self.project = proj
        self.project_path = None
        self.reset_state_tracking(saved=False)
        self.refresh_all()
        self.show_editor()

    def delete_project_file(self):
        path = self.project_path
        if not path or not os.path.isfile(path):
            return
        if QMessageBox.question(
                self, "Delete project file",
                f"Delete the project file?\n\n{path}\n\nOnly this .json project file (and its recovery copy) is "
                "deleted. Your voiceover, images, clips, music and rendered videos are NOT touched.\n\nThe "
                "project stays open here - use Save as to keep it again.") != QMessageBox.Yes:
            return
        try:
            os.remove(path)
        except OSError as e:
            QMessageBox.warning(self, "AutoEditor", f"Could not delete:\n{e}")
            return
        self._drop_autosave(path + ".autosave")
        self.project_path = None
        self._saved_state = None
        self._rebuild_recent()
        self.update_status()
        self.status_msg(f"Deleted {os.path.basename(path)} - media files untouched.")

    def on_cut_released(self):
        words = self.project["words"]
        for s in self.project["scenes"]:
            s["text"] = scene_text(words, s["start"], s["end"])
        self.fill_table()
        self.invalidate_preview()
        # the timeline emits edited("Move cut") right after this; that commits the undo step

    def replace_visual(self):
        self.sel_item = ("scene", self.sel)
        self.replace_media()

    def replace_media(self, path=None):
        """Replace the visual of the selected V2 clip, or else of the selected scene."""
        kind, idx = self.sel_item
        if kind == "broll" and self._item_exists("broll", idx):
            target, what = self.project["broll"][idx], "V2 clip"
        elif 0 <= self.sel < len(self.project["scenes"]):
            target, what = self.project["scenes"][self.sel], "scene"
        else:
            return
        if not path:
            start = os.path.dirname(target.get("media") or "") or self.project.get("media_dir") or ""
            path, _ = QFileDialog.getOpenFileName(self, "Choose image or clip", start, MEDIA_FILTER)
        if not path:
            return
        self._set_visual(target, path)
        self._register_media(path)
        self.commit(f"Replace {what} visual")
        self.refresh_all()

    @staticmethod
    def _set_visual(target, path):
        target["media"] = os.path.normpath(path)
        if "start" in target and "status" in target:     # a planned scene: now chosen by the user
            target["status"] = "manual" if not target.get("locked") else "locked"
            target["why"] = "chosen by you"
            target.pop("accepted", None)
            target.pop("warnings", None)
        if is_video(path):
            target["motion"] = "none"
        elif target.get("motion") in (None, "none"):
            target["motion"] = "zoom_in"

    def swap_next(self):
        k, sc = self.sel, self.project["scenes"]
        if 0 <= k < len(sc) - 1:
            if not self.timeline.reorder_visuals(k, k + 1):
                self.status_msg("One of these scenes has a locked visual - unlock it first. Nothing was swapped.")
                return
            self.sel = k + 1
            self.sel_item = ("scene", k + 1)
            self.commit("Swap visuals")
            self.refresh_all()

    def merge_next(self):
        k, sc = self.sel, self.project["scenes"]
        if 0 <= k < len(sc) - 1:
            sc[k]["end"] = sc[k + 1]["end"]
            sc[k]["text"] = (sc[k]["text"] + " " + sc[k + 1]["text"]).strip()
            sc[k]["transition"] = sc[k + 1]["transition"]
            del sc[k + 1]
            self.commit("Merge scenes")
            self.refresh_all()

    def split_at_playhead(self):
        kind, idx = self.sel_item
        if kind in ("broll", "text") and self._item_exists(kind, idx):
            return self.split_item(kind, idx)
        sc = self.project["scenes"]
        t = self.player.t
        k = self.scene_at(t)
        if k < 0:
            return
        s = sc[k]
        words = self.project["words"]
        gaps = [(words[i]["e"] + words[i + 1]["s"]) / 2 for i in range(len(words) - 1)
                if s["start"] + 0.3 < (words[i]["e"] + words[i + 1]["s"]) / 2 < s["end"] - 0.3]
        if gaps:
            near = min(gaps, key=lambda g: abs(g - t))
            if abs(near - t) < 0.5:
                t = near
        if not (s["start"] + 0.3 < t < s["end"] - 0.3):
            self.status_msg("Move the playhead inside a scene (at least 0.3 s from its edges) to split it.")
            return
        a, b = copy.deepcopy(s), copy.deepcopy(s)
        a["end"], a["transition"] = round(t, 3), "cut"
        b["start"] = round(t, 3)
        _split_parts(a, b, a["end"] - a["start"])
        if s.get("id"):                        # every scene needs its own id (instructions, review, replan)
            from autoedit.instructions import _fresh_id
            b["id"] = _fresh_id(self.project)
        a["text"] = scene_text(words, a["start"], a["end"])
        b["text"] = scene_text(words, b["start"], b["end"])
        if b.get("motion") == "zoom_in":
            b["motion"] = "zoom_out"
        if a.get("keyframes"):      # keyframes follow the part they fall in
            cut_t = a["end"] - a["start"]
            a["keyframes"] = [kf for kf in a["keyframes"] if kf["t"] <= cut_t]
            b["keyframes"] = [dict(kf, t=round(kf["t"] - cut_t, 3)) for kf in b["keyframes"] if kf["t"] > cut_t]
            for x in (a, b):
                if not x["keyframes"]:
                    x.pop("keyframes")
        sc[k:k + 1] = [a, b]
        self.sel = k + 1
        self.sel_item = ("scene", k + 1)
        self.commit("Split scene")
        self.refresh_all()

    def set_all_transitions(self):
        sc = self.project["scenes"]
        if not sc:
            return
        tr = self.all_trans.currentText()
        for s in sc[:-1]:
            s["transition"] = tr
        if self.commit(f"Set all transitions to {tr}"):
            self.refresh_all()

    def reroll_transitions(self):
        p = self.project
        if not p["scenes"]:
            return
        preset = PRESETS.get(p["settings"].get("preset"), PRESETS["Documentary"])
        p["settings"]["shuffle"] = int(p["settings"].get("shuffle", 0))
        reassign_transitions(p["scenes"], preset, p["words"], seed=int(time.time()) % 100000)
        if self.commit("Re-roll transitions"):
            self.refresh_all()

    def shuffle_style(self):
        if not self.project["words"]:
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first.")
            return
        if QMessageBox.question(self, "Shuffle style", "Re-plan the scenes with a new motion/transition mix? "
                                "Manual scene edits (swaps, zoom, cut positions) will be replaced. "
                                "You can undo this.") != QMessageBox.Yes:
            return
        st = self.project["settings"]
        st["shuffle"] = int(st.get("shuffle", 0)) + 1
        st["auto_vary"] = True
        self.replan(label_text="Shuffle style")

    # ======================================================================== table
    def fill_table(self):
        for rp in self.review_panels():
            rp.refresh()
        if getattr(self, "ins_win", None) is not None and self.ins_win.isVisible() and not self._ins_refreshing:
            self._ins_refreshing = True
            try:
                self.ins_win.refresh()
            finally:
                self._ins_refreshing = False
        sc = self.project["scenes"]
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(sc))
            for r in range(len(sc)):
                self.table.setRowHeight(r, 50)
                self.update_table_row(r)

    def update_table_row(self, r):
        sc = self.project["scenes"]
        if not (0 <= r < len(sc)):
            return
        s = sc[r]
        with QSignalBlocker(self.table):
            self.table.setItem(r, 0, QTableWidgetItem(str(r + 1)))
            self.table.setItem(r, 1, QTableWidgetItem(f"{fmt_time(s['start'])}\n{fmt_time(s['end'])}"))
            self.table.setItem(r, 2, QTableWidgetItem(f"{s['end'] - s['start']:.1f}s"))
            it = QTableWidgetItem(s["text"])
            it.setToolTip(s["text"])
            self.table.setItem(r, 3, it)
            m = s.get("media", "")
            it = QTableWidgetItem(("🎬 " if is_video(m) else "") + (os.path.basename(m) if m else "(none)"))
            it.setToolTip(m)
            pm = thumb(m, 80, 45) if m else None
            if pm:
                it.setIcon(QIcon(pm))
            self.table.setItem(r, 4, it)
            self.table.setItem(r, 5, QTableWidgetItem(s.get("motion", "")))
            self.table.setItem(r, 6, QTableWidgetItem(s.get("transition", "") if r < len(sc) - 1 else ""))

    # ======================================================================== drag & drop anywhere
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        """Drop files anywhere: audio -> voiceover (or music), .txt -> script, folders / images / clips ->
        images folder (first time) or the media bin."""
        paths = dropped_paths(e)
        done = []
        visuals = [p for p in paths if os.path.isdir(p) or os.path.splitext(p)[1].lower() in IMAGE_EXT | VIDEO_EXT]
        if visuals:
            if not self.project.get("media_dir") and len(visuals) == 1 and os.path.isdir(visuals[0]):
                self.media.set_from_drop(visuals)
                done.append("images folder")
            else:
                n = len(self.import_media(visuals))
                done.append(f"{n} media file(s) to the bin")
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if ext in AUDIO_EXT:
                target = self._audio_target(p)
                if target == "voice":
                    self.voice.set_from_drop([p])
                    done.append("voiceover")
                elif target == "music":
                    self.music.set_from_drop([p])
                    done.append("music")
            elif ext == ".txt":
                self.script.set_from_drop([p])
                done.append("script")
        if done:
            self.status_msg("Added: " + ", ".join(done) + " (Ctrl+Z to undo)")
            e.acceptProposedAction()

    def _audio_target(self, path):
        p = self.project
        if not p.get("voiceover"):
            return "voice"
        if not p.get("music"):
            box = QMessageBox(self)
            box.setWindowTitle("Audio file")
            box.setText(f"Use '{os.path.basename(path)}' as…")
            m = box.addButton("Background music", QMessageBox.AcceptRole)
            v = box.addButton("Replace the voiceover", QMessageBox.DestructiveRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            return "music" if box.clickedButton() == m else ("voice" if box.clickedButton() == v else None)
        box = QMessageBox(self)
        box.setWindowTitle("Audio file")
        box.setText(f"Use '{os.path.basename(path)}' to replace…")
        m = box.addButton("The music", QMessageBox.AcceptRole)
        v = box.addButton("The voiceover", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return "music" if box.clickedButton() == m else ("voice" if box.clickedButton() == v else None)

    # ======================================================================== jobs
    def on_progress(self, p, msg=""):
        self.progress.setValue(int(p * 1000))
        if msg:
            self.status_msg(msg)

    def set_busy(self, b):
        self.busy_flag = b
        self.progress.setVisible(b)
        self.btn_cancel.setVisible(b)
        if b:
            self.progress.setValue(0)
        self.update_status()

    def start(self, fn, on_done):
        self._on_done = on_done
        self.worker = Worker(fn)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.log)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self._worker_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.done.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._thread_finished)
        self.set_busy(True)
        self.thread.start()

    def _worker_done(self, res):
        cb, self._on_done = self._on_done, None
        if cb:
            cb(res)

    def _thread_finished(self):
        self.set_busy(False)

    def on_failed(self, msg):
        self.status_msg("Stopped" if msg == "Cancelled." else "Error - see Log")
        self.log("ERROR: " + msg if msg != "Cancelled." else msg)
        if msg != "Cancelled.":
            self.bottom_tabs.setCurrentWidget(self.logbox)
            QMessageBox.warning(self, "AutoEditor", msg[:1200])

    def cancel(self):
        if self.worker and self.busy_flag:
            self.worker.stop = True
            self.status_msg("Cancelling…")

    def analyze(self):
        p = self.project
        if not p["voiceover"] or not os.path.isfile(p["voiceover"]):
            QMessageBox.information(self, "AutoEditor", "Choose a voiceover file first (Media panel).")
            self.tools.setCurrentIndex(0)
            return
        self.player.pause()
        self.show_editor()
        work = copy.deepcopy(p)

        def job(w):
            w.progress.emit(0.0, "Transcribing…")
            prj.analyze(work, progress=lambda x: w.progress.emit(x, f"Transcribing… {x * 100:.0f}%"),
                        log=w.log.emit)
            return work

        def done(res):
            for k in STATE_KEYS:
                if k in ("words", "duration", "scenes", "script_check", "alignment", "plan_report",
                         "next_scene_id", "asset_meta", "edit_instructions", "broll", "sfx_events", "overlays"):
                    self.project[k] = res.get(k)
            self.project["sfx_planned"] = False
            self.sel = 0
            self.commit("Analyze voiceover")
            self.refresh_all()
            self.on_progress(1.0, "Scene plan ready - preview, adjust, then render")
            self.player.seek(0)
            self.timeline.show_seconds(60)

        self.log("---- Analyze")
        self.bottom_tabs.setCurrentWidget(self.logbox)
        self.start(job, done)

    def replan(self, *_, label_text="Re-plan scenes"):
        if not self.project["words"]:
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first.")
            return
        try:
            prj.replan(self.project, log=self.log)
        except Exception as e:
            QMessageBox.warning(self, "AutoEditor", str(e))
            self.restore(self._last_state)
            return
        self.project["sfx_planned"] = False
        self.sel = 0
        self.commit(label_text)
        self.refresh_all()
        rep = self.project.get("plan_report") or []
        if rep:
            self.status_msg(rep[0])
            if hasattr(self, "review"):
                self.show_review()

    def plan_sfx(self):
        if not self.project["scenes"]:
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first.")
            return
        self.log("---- Plan sound effects")
        ev = plan_project_sfx(self.project, log=self.log)
        self.commit("Plan sound effects")
        self.update_player_audio()
        self.timeline.update()
        self.sfx_panel.refresh()
        self.bottom_tabs.setCurrentWidget(self.sfx_panel)
        self.status_msg(f"{len(ev)} sound effects planned.")

    def run_quality(self):
        self.bottom_tabs.setCurrentWidget(self.quality_panel)
        return self.quality_panel.run()

    def quality_gate(self):
        items, score = self.run_quality()
        if not [i for i in items if i[0] in ("fix", "warn")]:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Quality check")
        box.setText(f"Quality score: {score}/100\n\n{fmt_items(items)}\n\nRender anyway?")
        go = box.addButton("Render anyway", QMessageBox.AcceptRole)
        box.addButton("Go back and fix", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() == go

    def _ready(self, need_formats=True):
        p = self.project
        if not p["scenes"]:
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first.")
            return False
        if need_formats and p["settings"].get("aspect") not in XC.ASPECTS:
            QMessageBox.information(self, "AutoEditor", "Choose an aspect ratio in Export.")
            self.right_tabs.setCurrentIndex(1)
            return False
        if not p.get("output_dir"):
            self.project["output_dir"] = os.path.dirname(p["voiceover"])
            self.outdir.load(self.project["output_dir"])
            self.commit("Output folder")
        os.makedirs(p["output_dir"], exist_ok=True)
        self.player.pause()
        return True

    def _take_sfx(self, work):
        """Keep the effects a render planned, so they can be reviewed/edited afterwards."""
        if work.get("sfx_planned") and not self.project.get("sfx_planned"):
            self.project["sfx_events"] = work.get("sfx_events", [])
            self.project["sfx_planned"] = True
            self.commit("Plan sound effects")
            self.sfx_panel.refresh()

    def _ask_overwrite(self):
        """-> True (replace), False (keep both: new name) or None (cancel)."""
        from autoedit.render import final_path
        st = self.project["settings"]
        if st.get("overwrite"):
            return True
        exist = []
        for fmt in XC.export_formats(st):
            W, H = XC.dims(fmt, st.get("resolution", "Full HD"))
            pth = final_path(self.project, fmt, W, H, overwrite=True)
            if os.path.exists(pth):
                exist.append(pth)
        if not exist:
            return False
        box = QMessageBox(self)
        box.setWindowTitle("A video with this name exists")
        box.setText("This export already exists:\n\n" + "\n".join(os.path.basename(x) for x in exist) +
                    "\n\nKeep the old video and save the new one with a new name, or replace it?")
        keep = box.addButton("Keep both (new name)", QMessageBox.AcceptRole)
        rep_b = box.addButton("Replace", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        c = box.clickedButton()
        return False if c == keep else True if c == rep_b else None

    def render(self):
        if not self._ready() or not self.quality_gate():
            return
        ow = self._ask_overwrite()
        if ow is None:
            return
        work = copy.deepcopy(self.project)

        def job(w):
            outs = render_all(work, progress=lambda x, m: w.progress.emit(x, m),
                              should_stop=lambda: w.stop, log=w.log.emit, overwrite=ow)
            return outs, work

        t0 = time.time()

        def done(res):
            outs, w = res
            self._take_sfx(w)
            self.log(f"Final render time: {time.time() - t0:.1f} s")
            self.on_progress(1.0, "Render finished")
            self.log("Saved:\n" + "\n".join(outs))
            self.finished_box("Render finished:\n\n" + "\n".join(outs), os.path.dirname(outs[0]))

        self.log("---- Render")
        self.bottom_tabs.setCurrentWidget(self.logbox)
        self.start(job, done)

    def render_draft(self):
        """Quick check: the format shown in the preview, 480p, fastest encode. No quality gate."""
        if not self._ready(need_formats=False):
            return
        work = copy.deepcopy(self.project)
        fmt = self.player.aspect_name()
        t0 = time.time()

        def job(w):
            outs = render_all(work, progress=lambda x, m: w.progress.emit(x, "Draft " + m),
                              should_stop=lambda: w.stop, log=w.log.emit, draft=True, formats=[fmt])
            return outs, work

        def done(res):
            outs, w = res
            self._take_sfx(w)
            el = time.time() - t0
            self.on_progress(1.0, f"Draft ready in {el:.0f} s")
            self.log(f"Draft saved ({el:.1f} s): {outs[0]}")
            box = QMessageBox(self)
            box.setWindowTitle("Draft preview ready")
            box.setText(f"Draft render finished in {el:.0f} s:\n\n{outs[0]}\n\nThis is a low-resolution check - "
                        "use Export for the final video.")
            play = box.addButton("Play draft", QMessageBox.AcceptRole)
            box.addButton("OK", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() == play:
                QDesktopServices.openUrl(QUrl.fromLocalFile(outs[0]))

        self.log(f"---- Draft preview render ({fmt})")
        self.bottom_tabs.setCurrentWidget(self.logbox)
        self.start(job, done)

    def export_pkg(self):
        if not self._ready():
            return
        work = copy.deepcopy(self.project)

        def job(w):
            return export_package(work, progress=lambda x, m: w.progress.emit(x, m),
                                  should_stop=lambda: w.stop, log=w.log.emit), work

        def done(res):
            pkg, w = res
            self._take_sfx(w)
            self.on_progress(1.0, "Package ready")
            self.finished_box("Editor package ready:\n\n" + pkg + "\n\nSee README.txt inside for CapCut steps.", pkg)

        self.log("---- Export editor package")
        self.start(job, done)

    def export_srt(self):
        if not self.project["words"]:
            QMessageBox.information(self, "AutoEditor", "Analyze the voiceover first.")
            return
        start = os.path.join(self.project.get("output_dir") or "", f"{self.project.get('name') or 'video'}.srt")
        path, _ = QFileDialog.getSaveFileName(self, "Save captions", start, "Subtitles (*.srt)")
        if path:
            write_srt(self.project["words"], path)
            self.status_msg(f"Captions saved: {path}")

    def copy_description(self):
        txt, warn = description_text(self.project, self.project.get("chapters", []))
        from autoedit.stock import credits_text
        cr = credits_text(self.project)
        if cr:
            txt = (txt + "\n\n" + cr).strip()
        QGuiApplication.clipboard().setText(txt)
        self.status_msg("YouTube description copied." + (" " + " ".join(warn) if warn else ""))

    def open_output(self):
        d = self.project.get("output_dir")
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def finished_box(self, text, folder):
        box = QMessageBox(self)
        box.setWindowTitle("AutoEditor")
        box.setText(text)
        open_btn = box.addButton("Open folder", QMessageBox.AcceptRole)
        box.addButton("OK", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ======================================================================== files
    def _apply_new_project_defaults(self, p):
        """Remembered defaults apply to NEW projects only - never to opened ones."""
        last = QSettings().value("defaults/settings")
        if not isinstance(last, dict):
            return
        st = p["settings"]
        for k, v in last.items():
            # per-video text (instructions, continuity notes, hook title) never silently carries over
            if k not in st or k in ("shuffle", "hook_title", "project_instructions", "continuity"):
                continue
            d = st[k]
            try:
                if k == "loudness":
                    st[k] = None if v in (None, "", "None", "off") else float(v)
                elif isinstance(d, bool):
                    st[k] = v if isinstance(v, bool) else str(v).lower() == "true"
                elif isinstance(d, (int, float)) and not isinstance(d, bool):
                    st[k] = type(d)(float(v))
                elif isinstance(d, list):
                    st[k] = [v] if isinstance(v, str) else list(v or [])
                else:
                    st[k] = v
            except (TypeError, ValueError):
                pass

    def maybe_save(self, what="continue"):
        """True if it's OK to continue (saved, discarded, or nothing to save)."""
        return self._confirm_leave(what)

    def new_project(self):
        self.back_to_setup()

    def open_project(self, path=None):
        if not self.maybe_save("open another project"):
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open project", QSettings().value("last_dir", ""),
                                                  "AutoEditor project (*.json)")
        if not path:
            return
        try:
            proj = prj.load(path)
        except Exception as e:
            QMessageBox.warning(self, "AutoEditor", f"Could not open project:\n{e}")
            return
        self.player.pause()
        loader().cancel_pending()
        rec = self._offer_recovery(path)
        self.project = rec or proj
        self.project_path = path
        self.sel = 0
        self.sel_item = ("", -1)
        self._add_recent(path)
        self.reset_state_tracking(saved=True)
        if rec is not None:
            self._saved_state = prj.load(path)
            self._saved_state = copy.deepcopy({k: self._saved_state.get(k) for k in STATE_KEYS})
            self.update_status()
        self.refresh_all()
        self.show_editor()
        self.player.seek(0)
        self.timeline.show_seconds(60)
        self.status_msg(f"Opened {os.path.basename(path)}")

    def save_project(self):
        if not self.project_path:
            return self.save_project_as()
        return self._write(self.project_path)

    def save_project_as(self):
        p = self.project
        start = os.path.join(p.get("output_dir") or (os.path.dirname(p["voiceover"]) if p.get("voiceover") else ""),
                             f"{p.get('name') or 'video'}.autoedit.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save project", start, "AutoEditor project (*.json)")
        if not path:
            return False
        return self._write(path)

    def _write(self, path):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            prj.save(self.project, path)
        except OSError as e:
            QMessageBox.warning(self, "AutoEditor", f"Could not save:\n{e}")
            return False
        old_auto = self.autosave_path()
        self.project_path = path
        self._saved_state = self.snapshot()
        self._drop_autosave(old_auto)
        self._drop_autosave()
        self._add_recent(path)
        QSettings().setValue("defaults/settings", dict(self.project["settings"]))
        self.update_status()
        self.status_msg(f"Saved {path}")
        return True

    def _add_recent(self, path):
        s = QSettings()
        rec = s.value("recent") or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [path] + [r for r in rec if r != path]
        s.setValue("recent", rec[:8])
        self._rebuild_recent()

    def _rebuild_recent(self):
        self.recent_menu.clear()
        rec = QSettings().value("recent") or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [r for r in rec if os.path.isfile(r)]
        for r in rec:
            a = self.recent_menu.addAction(os.path.basename(r))
            a.setToolTip(r)
            a.triggered.connect(lambda _=False, r=r: self.open_project(r))
        self.recent_menu.setEnabled(bool(rec))

    def project_settings(self):
        st = self.project["settings"]
        dlg = ProjectSettingsDialog(self, st, CATEGORIES)
        if dlg.exec():
            vals = dlg.values()
            cat = vals.pop("category", "")
            apply_defaults = vals.pop("apply_defaults", False)
            st.update(vals)
            self.commit("Project settings")
            if cat and (cat != st.get("category") or apply_defaults):
                self.choose_category(cat, apply_defaults)

    def project_info(self):
        p = self.project
        m = list_media(p.get("media_dir"))
        QMessageBox.information(self, "Project information", "\n".join([
            f"Project file: {self.project_path or '(not saved yet)'}",
            f"Voiceover: {p.get('voiceover') or '-'}",
            f"Duration: {fmt_time(p.get('duration', 0))}   Words: {len(p.get('words', []))}",
            f"Scenes: {len(p.get('scenes', []))}   Media files: {len(m)}",
            f"Text cards: {len(p.get('overlays', []))}   Chapters: {len(p.get('chapters', []))}",
            f"Sound effects planned: {len(p.get('sfx_events') or []) if p.get('sfx_planned') else 'at render'}",
            f"Speech model: {p['settings'].get('whisper_model')}   Language: {p['settings'].get('language') or 'auto'}",
        ]))

    def show_shortcuts(self):
        rows = []
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as, self.a_back, self.a_undo, self.a_redo, self.a_split,
                  self.a_merge, self.a_replace, self.a_analyze, self.a_replan, self.a_quality, self.a_play,
                  self.a_prev, self.a_next, self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit, self.a_render,
                  self.a_package, self.a_settings, self.a_cancel):
            if a.shortcut().toString():
                rows.append(f"{a.shortcut().toString(QKeySequence.NativeText):<16} {a.text()}")
        rows.append("←  /  →           Back / forward 1 s (preview or timeline focused)")
        rows.append("Ctrl + wheel      Zoom the timeline")
        QMessageBox.information(self, "Keyboard shortcuts", "\n".join(rows))

    def open_readme(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(APP_DIR, "README.md")))

    def about(self):
        QMessageBox.about(self, "About AutoEditor",
                          f"<b>AutoEditor {__version__}</b><br>Voiceover-driven automatic video editor.<br><br>"
                          "Everything runs locally on this PC - your voiceover, media and projects are never "
                          "uploaded.")

    def closeEvent(self, e):
        if self.thread and self.thread.isRunning():
            if QMessageBox.question(self, "AutoEditor", "A job is running. Quit anyway?") != QMessageBox.Yes:
                e.ignore()
                return
            self.cancel()
            self.thread.quit()
            self.thread.wait(3000)
        if self.is_dirty():
            ans = ask_unsaved(self, "exit")
            if ans == "keep" or (ans == "save" and not self.save_project()):
                e.ignore()
                return
            if ans == "discard":
                self._drop_autosave()
        loader().cancel_pending()
        self.player.pause()
        self.set_theater(False)
        self._save_layout()
        e.accept()


def main():
    QApplication.setOrganizationName("AutoEditor")
    QApplication.setApplicationName("AutoEditor")
    # 125 % / 150 % Windows display scaling: scale exactly instead of rounding to 100 % / 200 %
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    apply_theme(app)
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and sys.argv[1].endswith(".json") and os.path.isfile(sys.argv[1]):
        w.open_project(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
