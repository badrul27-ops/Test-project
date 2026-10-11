"""Regression test: clicking around must never change the project.

    python -m tests.test_gui_state      (from the AutoEditor folder; runs headless)

Covers project load, tab/panel changes, scene selection, timeline clicks (including a
click with tiny jitter on a cut edge), mouse-wheel over unfocused controls, UI refreshes;
then checks deliberate edits DO apply, are undoable, and survive save/reopen.
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QMessageBox, QSlider  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit.theme import WheelGuard, apply_theme  # noqa: E402


def make_project(tmp):
    imgs = []
    for i in range(4):
        p = os.path.join(tmp, f"scene_{i + 1:02d}.jpg")
        Image.new("RGB", (640, 360), (40 + 50 * i, 80, 120)).save(p)
        imgs.append(p)
    words, t = [], 0.2
    for i in range(40):
        words.append({"w": f"word{i}" + ("." if i % 8 == 7 else ""), "s": round(t, 3), "e": round(t + 0.3, 3)})
        t += 0.45 + (0.6 if i % 8 == 7 else 0)
    p = prj.new_project()
    p.update(name="guitest", voiceover="", media_dir=tmp, output_dir=tmp, words=words, duration=round(t, 3))
    p["settings"]["atmosphere"] = "Old Film"
    prj.replan(p, log=lambda m: None)
    path = os.path.join(tmp, "guitest.autoedit.json")
    prj.save(p, path)
    return path


def mouse(w, typ, x, y):
    ev = QMouseEvent(typ, QPointF(x, y), QPointF(x, y), Qt.LeftButton,
                     Qt.LeftButton if typ != QEvent.MouseButtonRelease else Qt.NoButton, Qt.NoModifier)
    getattr(w, {QEvent.MouseButtonPress: "mousePressEvent", QEvent.MouseMove: "mouseMoveEvent",
                QEvent.MouseButtonRelease: "mouseReleaseEvent"}[typ])(ev)


def main():
    QApplication.setOrganizationName("AutoEditorGuiTest")
    QApplication.setApplicationName("AutoEditorGuiTest")
    QSettings().clear()
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    guard = WheelGuard(app)
    if os.environ.get("AE_NO_GUARD") != "1":
        app.installEventFilter(guard)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Discard)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    A.ask_unsaved = lambda *a, **k: "discard"
    tmp = tempfile.mkdtemp()
    path = make_project(tmp)

    w = A.MainWindow()
    w.resize(1366, 740)
    w.show()
    app.processEvents()
    w.open_project(path)
    app.processEvents()
    s0 = w.snapshot()
    assert not w.is_dirty(), "project marked dirty right after opening"
    assert not w.undo.undo

    # --- navigation / selection / refresh
    for i in range(w.tools.count()):
        w.tools.setCurrentIndex(i)
    for i in range(w.bottom_tabs.count()):
        w.bottom_tabs.setCurrentIndex(i)
    for i in range(w.right_tabs.count()):
        w.right_tabs.setCurrentIndex(i)
    for k in range(len(w.project["scenes"])):
        w.select_scene(k)
        w.table.selectRow(k)
    w.player.aspect.setCurrentIndex(1)
    w.player.aspect.setCurrentIndex(0)
    w.refresh_all()
    w.load_controls()
    app.processEvents()

    # --- timeline: click on scenes, click exactly on a cut, click with 2 px jitter on a cut
    tl = w.timeline
    y = tl.track_y["scene"][0] + 20
    for s in w.project["scenes"]:
        mouse(tl, QEvent.MouseButtonPress, tl.x_of((s["start"] + s["end"]) / 2), y)
        mouse(tl, QEvent.MouseButtonRelease, tl.x_of((s["start"] + s["end"]) / 2), y)
    # every other track + the gutter labels: select only
    for key in ("broll", "text", "caption", "voice", "music", "sfx"):
        ty = tl.track_y[key][0] + 5
        for x in (tl.x_of(1.0), tl.x_of(5.0), 20):
            mouse(tl, QEvent.MouseButtonPress, x, ty)
            mouse(tl, QEvent.MouseButtonRelease, x, ty)
    mouse(tl, QEvent.MouseButtonPress, tl.x_of(3.0), y)
    mouse(tl, QEvent.MouseButtonRelease, tl.x_of(3.0), y)
    assert w.sel_item[0] == "scene" and w.props.stack.currentWidget() is w.props.pages["visual"]
    b = tl.x_of(w.project["scenes"][1]["start"])
    for jitter in (0, 2, -3):
        mouse(tl, QEvent.MouseButtonPress, b, y)
        mouse(tl, QEvent.MouseMove, b + jitter, y)
        mouse(tl, QEvent.MouseButtonRelease, b + jitter, y)

    # --- mouse wheel over every unfocused combo / spin box / slider
    seen = set()
    for page_i in range(w.tools.count()):
        w.tools.setCurrentIndex(page_i)
        for tab_i in range(w.right_tabs.count()):
            w.right_tabs.setCurrentIndex(tab_i)
            app.processEvents()
            for cls in (QComboBox, QAbstractSpinBox, QSlider):
                for c in w.findChildren(cls):
                    if not c.isVisible() or not c.isEnabled() or id(c) in seen:
                        continue
                    seen.add(id(c))
                    c.clearFocus()
                    for d in (120, -120, 240, 240):
                        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, d), Qt.NoButton,
                                         Qt.NoModifier, Qt.NoScrollPhase, False)
                        app.sendEvent(c, ev)
    n = len(seen)
    app.processEvents()
    assert w.snapshot() == s0, "clicking/scrolling around changed the project"
    assert not w.is_dirty() and not w.undo.undo
    print(f"  no accidental changes (navigation, selection, timeline clicks, wheel over {n} controls)")

    # --- deliberate edits apply, only to their own key / scene, and are undoable
    i = w.atmo.findData("Dreamy Glow")
    w.atmo.setCurrentIndex(i)
    w.atmo.activated.emit(i)
    assert w.project["settings"]["atmosphere"] == "Dreamy Glow" and w.is_dirty() and len(w.undo.undo) == 1
    diff = {k for k in s0["settings"] if s0["settings"][k] != w.project["settings"][k]}
    assert diff == {"atmosphere"}, diff
    w.select_scene(2)
    before = [dict(s) for s in w.project["scenes"]]
    w.props.tf_w["zoom"][0].setValue(140)
    changed = [k for k, (a, b2) in enumerate(zip(before, w.project["scenes"])) if a != b2]
    assert changed == [2] and w.project["scenes"][2]["zoom"] == 1.4, changed
    old = w.project["scenes"][1]["start"]
    mouse(tl, QEvent.MouseButtonPress, tl.x_of(old), y)
    mouse(tl, QEvent.MouseMove, tl.x_of(old) + 30, y)
    mouse(tl, QEvent.MouseButtonRelease, tl.x_of(old) + 30, y)
    assert w.project["scenes"][1]["start"] != old
    assert len(w.undo.undo) == 3, [u[0] for u in w.undo.undo]
    for _ in range(3):
        w.do_undo()
    assert w.snapshot() == s0, "undo did not restore the original project"
    w.do_redo()
    assert w.project["settings"]["atmosphere"] == "Dreamy Glow"
    print("  deliberate edits apply to their own setting/scene only; undo/redo restore exactly")
    s1 = w.snapshot()

    # --- v6 timeline editing: V2 clip add / drag / trim, keyframes, text card, caption edit, delete
    img = w.project["scenes"][0]["media"]
    w.player.seek(2.0)
    w.add_broll(img, 2.0)
    assert len(w.project["broll"]) == 1 and w.sel_item == ("broll", 0)
    c = w.project["broll"][0]
    yb = tl.track_y["broll"][0] + 10
    x0 = tl.x_of((c["start"] + c["end"]) / 2)
    st0 = c["start"]
    mouse(tl, QEvent.MouseButtonPress, x0, yb)
    mouse(tl, QEvent.MouseButtonRelease, x0 + 2, yb)           # jitter: no move
    assert w.project["broll"][0]["start"] == st0
    mouse(tl, QEvent.MouseButtonPress, x0, yb)
    mouse(tl, QEvent.MouseMove, x0 + 40, yb)
    mouse(tl, QEvent.MouseButtonRelease, x0 + 40, yb)
    assert w.project["broll"][0]["start"] > st0, "dragging the V2 clip did not move it"
    xe = tl.x_of(w.project["broll"][0]["end"])
    e0 = w.project["broll"][0]["end"]
    mouse(tl, QEvent.MouseButtonPress, xe, yb)
    mouse(tl, QEvent.MouseMove, xe - 25, yb)
    mouse(tl, QEvent.MouseButtonRelease, xe - 25, yb)
    assert w.project["broll"][0]["end"] < e0, "trimming the V2 clip did not change its end"
    # keyframes on scene 2: add at two times with different scale
    w.select_scene(1)
    sc = w.project["scenes"][1]
    w.player.seek(sc["start"] + 0.1)
    w.props._kf_add()
    w.player.seek(sc["end"] - 0.1)
    w.props.sync_playhead()
    w.props.tf_w["zoom"][0].setValue(160)
    kfs = w.project["scenes"][1]["keyframes"]
    assert len(kfs) == 2 and kfs[1]["zoom"] == 1.6 and kfs[0]["zoom"] == sc.get("zoom", 1.0), kfs
    # opacity via properties on the V2 clip only
    w.select_item("broll", 0)
    w.props.tf_w["opacity"][0].setValue(60)
    assert w.project["broll"][0]["opacity"] == 0.6 and "opacity" not in w.project["scenes"][0]
    # text card + caption edit
    w.new_card_type.setCurrentIndex(0)
    w.add_text_card(1.0)
    assert w.sel_item[0] == "text" and w.project["overlays"]
    w.props.t_text.setText("Hello")
    w.props.t_text.editingFinished.emit()
    assert any(o["text"] == "Hello" for o in w.project["overlays"])
    w.select_item("caption", 0)
    old_w = [x["w"] for x in w.project["words"]]
    n_first = len(w.timeline.captions[0]["text"].split())
    w.props.c_text.setPlainText(" ".join(["fixed"] * n_first))
    w.props._caption_apply()
    assert w.project["words"][0]["w"] == "fixed" and len(w.project["words"]) == len(old_w)
    # delete V2 clip via the Delete key on the timeline
    w.select_item("broll", 0)
    from PySide6.QtGui import QKeyEvent
    tl.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
    assert not w.project["broll"]
    n_undo = len(w.undo.undo)
    for _ in range(n_undo):
        w.do_undo()
    assert w.snapshot()["broll"] == [] and w.project["words"] == s0["words"]
    print(f"  V2 add/drag/trim, keyframes, per-clip opacity, text card, caption edit, delete: OK "
          f"({n_undo} undo steps back)")
    while w.undo.redo:
        w.undo.redo.pop()
    w.restore(s1)

    # --- save / reopen keeps the change
    assert w.save_project() and not w.is_dirty()
    w2 = A.MainWindow()
    w2.open_project(path)
    assert w2.project["settings"]["atmosphere"] == "Dreamy Glow" and not w2.is_dirty()
    print("  save / reopen preserves edits")

    # --- clearing inputs: dependent state explained + cleared, files on disk untouched, undoable
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    vo = os.path.join(tmp, "voice.wav")
    open(vo, "wb").write(b"RIFF0000WAVE")          # stand-in file; must survive every clear
    w.project["voiceover"] = vo
    w.commit("set voice")
    n_scenes = len(w.project["scenes"])
    w.clear_input("voiceover")
    assert w.project["voiceover"] == "" and not w.project["scenes"] and not w.project["words"]
    assert os.path.isfile(vo), "clearing the voiceover deleted the file!"
    w.do_undo()
    assert w.project["voiceover"] == vo and len(w.project["scenes"]) == n_scenes
    w.clear_input("media_dir")
    assert w.project["media_dir"] == "" and all(not s["media"] for s in w.project["scenes"])
    assert all(os.path.isfile(os.path.join(tmp, f"scene_{i + 1:02d}.jpg")) for i in range(4))
    w.do_undo()
    assert all(s["media"] for s in w.project["scenes"])
    w.select_scene(1)
    img = w.project["scenes"][1]["media"]
    w.remove_visual()
    assert w.project["scenes"][1]["media"] == "" and os.path.isfile(img)
    w.do_undo()
    print("  clear voiceover / images folder / remove visual: dependents handled, files kept, undoable")

    # --- back to setup / start over never touch the saved project or source files
    saved_bytes = open(path, "rb").read()
    w.show_editor()
    w.project["settings"]["atmosphere"] = "Dark Noir"
    w.commit("edit")
    A.ask_unsaved = lambda *a, **k: "keep"
    assert w.back_to_setup() is False and w.in_editor() and w.project["settings"]["atmosphere"] == "Dark Noir"
    A.ask_unsaved = lambda *a, **k: "discard"
    assert w.back_to_setup() is True and not w.in_editor()
    assert not w.project["scenes"] and w.project_path is None and not w.is_dirty()
    assert open(path, "rb").read() == saved_bytes, "Back to setup changed the saved project file"
    w.open_project(path)
    w.project["settings"]["atmosphere"] = "Old Film"
    w.commit("edit")
    w.start_over()
    assert w.project_path is None and not w.project["voiceover"] and not w.project["scenes"]
    assert w.project["settings"]["atmosphere"] == "Old Film", "Start over should keep style settings"
    assert open(path, "rb").read() == saved_bytes and os.path.isfile(vo)
    print("  back to setup (keep / discard) and start over: saved project + files untouched")

    # --- cancel render is separate from leaving a project
    assert not w.a_cancel.isEnabled()
    w.busy_flag = True
    w.update_actions()
    assert w.a_cancel.isEnabled()
    assert w.back_to_setup() is False, "must not leave a project while a render runs"
    w.busy_flag = False
    print("  Cancel render is a separate action; can't leave while a job runs")

    # --- media bin import (folder + files), remove keeps files; autosave + delete project file
    w.open_project(path)
    extra = os.path.join(tmp, "more")
    os.makedirs(extra, exist_ok=True)
    for i in range(3):
        Image.new("RGB", (320, 180), (200, 30 * i, 60)).save(os.path.join(extra, f"extra_{i}.png"))
    open(os.path.join(extra, "notes.xyz"), "w").write("x")
    got = w.import_media([extra, os.path.join(extra, "notes.xyz"), os.path.join(tmp, "missing.png")])
    assert len(got) == 3 and w.bin.list.count() == 7, w.bin.list.count()
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    w.bin_remove([os.path.join(extra, "extra_0.png")])
    assert w.bin.list.count() == 6 and os.path.isfile(os.path.join(extra, "extra_0.png"))
    w.project["settings"]["autosave"] = True
    w.autosave()
    assert os.path.isfile(path + ".autosave"), "autosave did not write a recovery copy"
    assert w.save_project() and not os.path.isfile(path + ".autosave")
    w.delete_project_file()
    assert not os.path.isfile(path) and os.path.isfile(img) and os.path.isfile(os.path.join(extra, "extra_1.png"))
    assert w.project["scenes"], "deleting the project file must not close/clear the open project"
    print("  media bin import/remove (files kept), autosave recovery copy, delete project file (JSON only)")

    # --- closing from the setup screen must not save the editor panels as hidden (v6.0.1 fix)
    w.show_home()
    app.processEvents()
    w._save_layout()
    w4 = A.MainWindow()
    w4.resize(1366, 740)
    w4.show()
    w4.show_editor()
    app.processEvents()
    assert w4.left.isVisible() and w4.right.isVisible() and w4.bottom.isVisible(), \
        "editor panels came back hidden after closing from the setup screen"
    print("  panels stay visible after closing from the setup screen")

    # --- v6.1: square preview viewport, volume, lower panel, scrollbar, categories, motion levels
    p61 = prj.load(make_project(tempfile.mkdtemp()))
    w4.project = p61
    w4.reset_state_tracking(saved=False)
    w4.refresh_all()
    w4.show_editor()
    w4.reset_layout()
    app.processEvents()
    scr = w4.player.screen
    fmts0 = list(w4.project["settings"]["formats"])
    exp0 = w4.snapshot()

    def frame_ok(ratio):
        w4.player.seek(1.0)
        app.processEvents()
        r = scr.frame_rect()
        inside = r.left() >= -0.5 and r.top() >= -0.5 and r.right() <= scr.width() + 0.5 and \
            r.bottom() <= scr.height() + 0.5
        touches = abs(r.width() - (scr.width() - 2)) < 2 or abs(r.height() - (scr.height() - 2)) < 2
        img = scr._img
        return (inside and touches and abs(r.width() / r.height() - ratio) < 0.02 and
                abs(img.width() / img.height() - ratio) < 0.02), r
    sizes = {}
    for name, ratio in (("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0)):
        w4.player.aspect.setCurrentText(name)
        ok, r = frame_ok(ratio)
        assert ok, (name, r, scr.width(), scr.height())
        small = r.width() * r.height()
        w4.resize(1700, 1000)          # enlarge the window -> preview panel grows -> canvas grows
        w4.reset_layout()
        app.processEvents()
        ok, r2 = frame_ok(ratio)
        assert ok and r2.width() * r2.height() > small * 1.3, (name, r, r2)
        sizes[name] = (int(r.width()), int(r.height()), int(r2.width()), int(r2.height()))
        w4.resize(1366, 740)
        w4.reset_layout()
        app.processEvents()
    assert w4.project["settings"]["formats"] == fmts0 and w4.snapshot() == exp0, \
        "preview aspect / resizing changed the project"
    w4.player.aspect.setCurrentText("16:9")
    # Fill covers the canvas, zoom grows the picture, reset returns to Fit
    w4.player.fit.setCurrentText("Fill")
    w4.player.fit.activated.emit(1)
    r = scr.frame_rect()
    assert r.width() >= scr.width() - 3 and r.height() >= scr.height() - 3
    w4.player.reset_view()
    r_fit = scr.frame_rect()
    w4.player.zoom_in_btn.click()
    assert scr.frame_rect().width() > r_fit.width() * 1.2
    w4.player.reset_view()
    assert abs(scr.frame_rect().width() - r_fit.width()) < 1 and scr.mode == "Fit"
    # theater mode: panels hidden, picture bigger, everything restored afterwards
    before_vis = [x.isVisible() for x in (w4.left, w4.right, w4.bottom)]
    a_fit = r_fit.width() * r_fit.height()
    w4.player.theater_btn.click()
    app.processEvents()
    assert not w4.left.isVisible() and not w4.right.isVisible() and not w4.bottom.isVisible()
    r_th = scr.frame_rect()
    assert r_th.width() * r_th.height() > a_fit * 1.5, (r_fit, r_th)
    w4.a_theater.trigger()
    app.processEvents()
    assert [x.isVisible() for x in (w4.left, w4.right, w4.bottom)] == before_vis
    assert abs(scr.frame_rect().width() - r_fit.width()) < 3
    print("  preview 16:9 / 9:16 / 1:1: whole frame visible, right shape, grows with the window "
          + str(sizes) + "; Fill / zoom / reset; theater mode in and out; export settings untouched")
    snap = w4.snapshot()
    w4.player.set_volume(35)
    w4.player.toggle_mute()
    assert w4.player.muted and "Muted" in w4.player.vol_btn.text()
    if w4.player.audio_out is not None:
        assert w4.player.audio_out.volume() == 0.0
    w4.player.toggle_mute()
    w4.a_vol_down.trigger()
    assert w4.player.volume == 25 and not w4.player.muted
    assert w4.snapshot() == snap, "preview volume must not change the project"
    w4.set_lower_panel(False)
    assert not w4.bottom_tabs.isVisible() and w4.tabs_show_btn.isVisible()
    w4.set_lower_panel(True)
    assert w4.bottom_tabs.isVisible() and w4.tl_scroll.isVisible()
    # scrollbar follows zoom and drives the timeline
    tl4, bar = w4.timeline, w4.tl_scroll.bar
    tl4.fit()
    assert not bar.isEnabled()
    tl4.zoom(4.0)
    app.processEvents()
    assert bar.isEnabled() and bar.maximum() > 0
    bar.setValue(bar.maximum())
    assert abs(tl4.offset - tl4.max_offset()) < 0.02, "dragging the scrollbar didn't move the timeline"
    tl4.set_offset(0.0)
    assert bar.value() == 0, "scrollbar didn't follow the timeline"
    tl4.fit()
    # categories: starting defaults, still editable, saved in the project
    w4.choose_category("Horror")
    st = w4.project["settings"]
    assert st["category"] == "Horror" and st["preset"] == "Horror" and st["atmosphere"] == "Dark Noir"
    i = w4.atmo.findData("Old Film")
    w4.atmo.setCurrentIndex(i)
    w4.atmo.activated.emit(i)
    assert st["atmosphere"] == "Old Film" and st["category"] == "Horror", "category must not lock settings"
    w4.choose_category("Stickman")
    assert st["preset"] == "Stickman" and st["motion_level"] == "Subtle"
    out = os.path.join(tempfile.mkdtemp(), "cat.autoedit.json")
    prj.save(w4.project, out)
    assert prj.load(out)["settings"]["category"] == "Stickman"
    # motion levels: None = still; Dynamic = varied amounts; video clips untouched
    w4.project["scenes"][0]["media"] = w4.project["scenes"][0]["media"].replace(".jpg", ".mp4")
    w4.project["scenes"][0]["motion"] = "none"
    for lvl in ("None", "Dynamic"):
        i = w4.motion_level.findData(lvl)
        w4.motion_level.setCurrentIndex(i)
        w4.motion_level.activated.emit(i)
        w4.apply_motion_level_all()
        imgs = [s for s in w4.project["scenes"][1:]]
        if lvl == "None":
            assert all(s["motion"] == "none" for s in imgs)
        else:
            amts = {s["motion_amount"] for s in imgs}
            assert len(amts) > 1 and all(s["motion"] not in ("none", "static", "subtle") for s in imgs), amts
        assert w4.project["scenes"][0]["motion"] == "none", "video clip was given image motion"
    # locked scenes and motion from your own editing instructions are never re-chosen
    s1, s2 = w4.project["scenes"][1], w4.project["scenes"][2]
    s1.update(locked=True, motion="pan_left", motion_amount=0.2)
    s2.update(motion="subtle", motion_amount=0.03, provenance={"motion": "instruction S002"})
    for _ in range(5):
        w4.apply_motion_level_all()
    assert (s1["motion"], s1["motion_amount"]) == ("pan_left", 0.2), s1
    assert (s2["motion"], s2["motion_amount"]) == ("subtle", 0.03), s2
    print("  volume/mute (preview only); lower panel toggle; scrollbar <-> zoom; "
          "categories set editable defaults + saved; motion levels None/Dynamic, clips untouched")
    check_review_fixes(app, w4)
    print("GUI STATE OK")


def check_review_fixes(app, w):
    """Regressions: reorder/split keep clip settings + ids, locks respected, selection follows the clip,
    caption track follows caption size, remembered defaults skip per-video text."""
    from autoedit.captions import project_caption_spans
    from autoedit.util import run_ffmpeg
    tmp = tempfile.mkdtemp()
    w.project = prj.load(make_project(tmp))
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    app.processEvents()
    sc = w.project["scenes"]
    # reorder: clip range and framing travel with the picture; a locked scene blocks the move
    sc[0].update(clip_in=1.5, fit="fit_blur", focus=[0.2, 0.3])
    m0 = sc[0]["media"]
    assert w.timeline.reorder_visuals(0, 1)
    assert sc[1]["media"] == m0 and sc[1].get("clip_in") == 1.5 and sc[1].get("focus") == [0.2, 0.3]
    assert "clip_in" not in sc[0] and "focus" not in sc[0]
    sc[2]["locked"] = True
    before = [s["media"] for s in sc]
    assert not w.timeline.reorder_visuals(1, 3) and [s["media"] for s in sc] == before
    sc[2]["locked"] = False
    # split a scene: the second half gets its own id
    w.select_item("scene", 1)
    w.player.seek((sc[1]["start"] + sc[1]["end"]) / 2)
    n = len(sc)
    w.split_at_playhead()
    sc = w.project["scenes"]
    assert len(sc) == n + 1 and len({s["id"] for s in sc}) == len(sc), [s["id"] for s in sc]
    # split a video B-roll clip: the second part continues the footage, it doesn't replay it
    clip = os.path.join(tmp, "b.mp4")
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=s=160x90:r=30:d=6", "-pix_fmt", "yuv420p", clip])
    w.project["broll"] = [{"media": clip, "start": 1.0, "end": 5.0, "mode": "insert", "clip_in": 0.5},
                          {"media": clip, "start": 6.0, "end": 7.0, "mode": "insert"}]
    w.refresh_all()
    w.select_item("broll", 0)
    w.player.seek(3.0)
    w.split_at_playhead()
    br = w.project["broll"]
    assert (br[0]["clip_in"], br[1]["start"], br[1]["clip_in"]) == (0.5, 3.0, 2.5), br[:2]
    # move a clip past another: the SAME clip stays selected after the list is re-sorted
    first = br[0]
    w.select_item("broll", 0)
    first.update(start=7.5, end=8.5)
    w.on_timeline_edited("Move clip")
    kind, idx = w.sel_item
    assert kind == "broll" and w.project["broll"][idx] is first, (kind, idx)
    # caption size changes: the CC track is rebuilt to match the captions on screen
    w.project["settings"]["caption_size"] = 170
    w.commit("Caption size")
    assert len(w.timeline.captions) == len(project_caption_spans(w.project))
    # remembered defaults: style yes, this video's instructions / continuity notes no
    QSettings().setValue("defaults/settings", {"caption_size": 130, "project_instructions": "cuts only",
                                               "continuity": "same jacket"})
    p2 = prj.new_project()
    w._apply_new_project_defaults(p2)
    assert p2["settings"]["caption_size"] == 130
    assert p2["settings"]["project_instructions"] == "" and p2["settings"]["continuity"] == ""
    QSettings().remove("defaults/settings")
    # choosing another images folder replaces the pictures: no old images left in the bin or on the timeline
    from PIL import Image as _Im
    old_q = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    new_dir, other = os.path.join(tempfile.mkdtemp(), "new_imgs"), os.path.join(tempfile.mkdtemp(), "other_imgs")
    for d in (new_dir, other):
        os.makedirs(d)
        _Im.new("RGB", (64, 36), (0, 90, 0)).save(os.path.join(d, os.path.basename(d) + "_1.png"))
    w.import_media([other])                                    # added separately (not from the folder)
    old_pic = w.project["scenes"][0]["media"]
    w.media.load(new_dir)
    w.media.changed.emit()
    bin_now = [w.bin.list.item(i).text() for i in range(w.bin.list.count())]
    assert bin_now == ["new_imgs_1.png"], bin_now
    assert not w.project["extra_media"] and all(not s.get("media") for s in w.project["scenes"])
    w.do_undo()                                                # one undo step brings everything back
    assert w.project["scenes"][0]["media"] == old_pic and len(w.project["extra_media"]) == 1
    w.media.clear_requested.emit()                             # x: the whole picture set goes
    assert not w.bin.list.count() or all(w.bin.list.item(i).text() == "" for i in range(w.bin.list.count()))
    assert all(not s.get("media") for s in w.project["scenes"]) and os.path.isfile(old_pic)
    QMessageBox.question = old_q
    print("  reorder/split keep clip settings and ids, locks respected, selection follows the clip, "
          "CC track follows caption size, defaults skip per-video text")


if __name__ == "__main__":
    main()
