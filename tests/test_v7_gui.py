"""v7 GUI: Script / Instructions tabs, Review scenes, Properties -> Framing, single-aspect Export tab,
old-project migration.

    python -m tests.test_v7_gui            (AE_SHOTS=folder saves screenshots)
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from autoedit import project as prj  # noqa: E402
from tests.test_planner import PROMPTS, SCRIPT, make_media, words_for  # noqa: E402


def shot(w, name):
    d = os.environ.get("AE_SHOTS")
    if d:
        os.makedirs(d, exist_ok=True)
        w.grab().save(os.path.join(d, name + ".png"))


def main():
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.setOrganizationName("AEv7Test")
    QApplication.setApplicationName("AEv7Test")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    import app as A
    from autoedit.theme import apply_theme
    apply_theme(app)
    A.ask_unsaved = lambda *a, **k: "discard"
    tmp = tempfile.mkdtemp()
    media = os.path.join(tmp, "media")
    make_media(media, size=(1920, 1080))
    # a picture with baked-in black bars, for the border tool
    rng = np.random.default_rng(3)
    a = (rng.random((20, 30, 3)) * 180 + 40).astype(np.uint8)
    im = np.asarray(Image.fromarray(a).resize((1200, 800), Image.BICUBIC)).copy()
    im[:80] = 0
    im[-80:] = 0
    Image.fromarray(im).save(os.path.join(media, "04.png"))
    words, dur = words_for(SCRIPT.replace("\n", " "))
    w = A.MainWindow()
    w.resize(1500, 900)
    w.show()
    p = prj.new_project()
    p.update(name="gui7", media_dir=media, words=words, duration=dur, output_dir=os.path.join(tmp, "out"))
    w.project = p
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    app.processEvents()

    # ---------------- defaults for a NEW project
    st = w.project["settings"]
    assert st["scene_mode"] == "timed" and st["aspect"] == "16:9" and st["resolution"] == "Full HD"
    assert st["fps"] == 30 and st["enc_quality"] == "Standard" and not st["batch_export"] and st["fit"] == "crop"
    assert w.export_summary.text() == "Will export: 1 video · 16:9 · 1920 × 1080 · 30 fps · Standard", \
        w.export_summary.text()
    assert w.resolution.itemText(w.resolution.currentIndex()) == "Full HD - 1920 × 1080"
    assert not w.adv.isVisible() or not w.adv.isVisibleTo(w)
    print("  new project: timed edit plan required, ONE 16:9 1920×1080 30 fps Standard video, Fill framing, advanced "
          "options collapsed")

    # ---------------- Script tab
    names = [b.text() for b in w.tools.buttons]
    assert names[:3] == ["Media", "Script", "Instructions"], names
    w.tools.buttons[1].click()
    app.processEvents()
    w.script_panel.edit.setPlainText(SCRIPT)
    w.script_panel.save()
    assert w.project["script_text"] == SCRIPT
    w.do_undo()
    assert w.project["script_text"] == ""
    w.do_redo()
    assert w.project["script_text"] == SCRIPT
    # script-vs-recording list (as analyze stores it)
    from autoedit.planner import script_check
    w.project["script_check"] = script_check(SCRIPT.replace("heavy ", "").replace("silver ", ""), words)
    w.project["alignment"] = {"status": "script+audio"}
    w.load_controls()
    assert w.script_panel.list.count() >= 2 and "differ" in w.script_panel.list.item(0).text()
    shot(w, "v7_script_tab")
    print("  Script tab: exact script saved with the project (undoable); differences listed with times")

    # ---------------- Instructions: prompts + plan
    w.tools.buttons[2].click()
    app.processEvents()
    csvp = os.path.join(tmp, "prompts.csv")
    with open(csvp, "w", encoding="utf-8") as f:
        f.write("asset_filename,prompt\n" + "\n".join(f'{r["asset_filename"]},"{r["prompt"]}"' for r in PROMPTS))
    w.instr_panel.import_prompts_file(csvp)
    assert len(w.project["asset_meta"]) == 3
    w.instr_panel.proj.setPlainText("no camera motion\nmake it pop")
    w.instr_panel.save_project_instr()
    assert "Not understood" in w.instr_panel.proj_fb.text() and "motion_level=None" in w.instr_panel.proj_fb.text()
    w.instr_panel.plan()
    app.processEvents()
    sc = w.project["scenes"]
    by_media = {os.path.basename(s["media"]): s for s in sc if s["media"]}
    assert by_media["03.png"]["status"] == "matched" and by_media["01.png"]["status"] == "matched"
    assert any(s["status"] in ("missing", "fallback") for s in sc)
    assert w.bottom_tabs.currentWidget() is w.review
    shot(w, "v7_instructions_review")
    w.open_review_window()
    app.processEvents()
    assert w.review_win.panel.table.rowCount() == len(sc)
    shot(w.review_win, "v7_review_window")
    print(f"  Instructions: prompts imported, project instructions parsed (unknown line flagged), Plan -> "
          f"{len(sc)} scenes; Review opens")

    # ---------------- Review scenes
    rv = w.review
    assert rv.table.rowCount() == len(sc)
    statuses = [rv.table.item(r, 4).text() for r in range(rv.table.rowCount())]
    assert "Matched" in statuses and ("No visual" in statuses or "Fallback" in statuses), statuses
    rv.filter.setCurrentIndex(1)
    n_att = rv.table.rowCount()
    assert 1 <= n_att < len(sc)
    rv.filter.setCurrentIndex(0)
    k = next(i for i, s in enumerate(sc) if s["media"].endswith("02.png"))
    rv.table.selectRow(k)
    app.processEvents()
    assert w.sel == k
    rv.toggle_lock()
    assert w.project["scenes"][k]["locked"] and w.project["scenes"][k]["status"] == "locked"
    lk = dict(w.project["scenes"][k])
    rv.b_reanalyze.click()
    app.processEvents()
    L = [s for s in w.project["scenes"] if s.get("locked")]
    assert len(L) == 1 and L[0]["media"] == lk["media"] and L[0]["start"] == lk["start"] and L[0]["id"] == lk["id"]
    # play scene with narration stops at the scene end
    rv.select(w.project["scenes"].index(L[0]))
    rv.play_scene()
    assert w.player.playing and abs(w.player.t - L[0]["start"]) < 0.05
    w.player.pause()
    rv._watch()
    print(f"  Review: {len(sc)} rows with status/why/warnings, 'Needs attention' filter shows {n_att}; Lock survives "
          "'Reanalyze unlocked scenes'; Play scene starts at the scene")

    # ---------------- Properties -> Framing on the barred picture
    k4 = next(i for i, s in enumerate(w.project["scenes"]) if not s.get("locked"))
    w.select_scene(k4)
    w.replace_media(os.path.join(media, "04.png"))
    assert w.project["scenes"][k4]["status"] == "manual"
    w.select_scene(k4)
    pr = w.props
    pr.vtabs.setCurrentIndex(4)
    assert pr.vtabs.tabText(4) == "Framing"
    pr._detect_borders()
    assert "Uniform border detected" in pr.trim_msg.text() and pr.b_trim_apply.isEnabled(), pr.trim_msg.text()
    pr._apply_trim()
    t = w.project["scenes"][k4]["trim"]
    assert abs(t[1] - 0.1) < 0.01 and abs(t[3] - 0.1) < 0.01
    pr.fit_combo.setCurrentIndex(pr.fit_combo.findData("fit_color"))
    pr.fit_combo.activated.emit(pr.fit_combo.currentIndex())
    assert w.project["scenes"][k4]["fit"] == "fit_color" and pr.bg_btn.isEnabled()
    pr.focus_x.setValue(20)
    shot(w, "v7_framing")
    w.do_undo()       # focal point
    w.do_undo()       # fit
    w.do_undo()       # trim
    assert not w.project["scenes"][k4].get("trim")
    print("  Properties → Framing: borders detected (top/bottom 10%) and trimmed reversibly; per-scene framing "
          "mode + colour; undo restores")

    # ---------------- Export tab
    w.right_tabs.setCurrentIndex(1)
    w.aspect.setCurrentIndex(w.aspect.findData("9:16"))
    w.aspect.activated.emit(w.aspect.currentIndex())
    app.processEvents()
    assert w.project["settings"]["aspect"] == "9:16" and w.player.aspect_name() == "9:16"
    assert w.resolution.itemText(w.resolution.currentIndex()) == "Full HD - 1080 × 1920"
    w.resolution.setCurrentIndex(w.resolution.findData("HD"))
    w.resolution.activated.emit(w.resolution.currentIndex())
    w.enc_quality.setCurrentIndex(w.enc_quality.findData("High"))
    w.enc_quality.activated.emit(w.enc_quality.currentIndex())
    assert w.export_summary.text() == "Will export: 1 video · 9:16 · 720 × 1280 · 30 fps · High", \
        w.export_summary.text()
    w.batch_cb.setChecked(True)
    w.batch_w["16:9"].setChecked(True)
    assert w.export_summary.text().startswith("Will export: 2 videos · 9:16 + 16:9"), w.export_summary.text()
    assert not w.batch_w["9:16"].isEnabled()
    w.batch_cb.setChecked(False)
    assert w.export_summary.text().startswith("Will export: 1 video · 9:16"), w.export_summary.text()
    assert w._ask_overwrite() is False      # nothing exported yet -> no question
    shot(w, "v7_export_tab")
    print("  Export: aspect is one choice (preview follows it), resolutions show exact pixels, summary reads "
          "'1 video · 9:16 · 720 × 1280 · 30 fps · High'; batch only when switched on")

    # ---------------- migration of an old project (v6: two formats ticked, 720p, blur)
    old = {"name": "old", "settings": {"formats": ["16:9", "9:16"], "quality": "720p", "fit": "blur",
                                       "scene_mode": "auto", "fps": 30},
           "scenes": [], "words": [], "duration": 0}
    op = os.path.join(tmp, "old.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(old, f)
    q = prj.load(op)["settings"]
    assert q["aspect"] == "16:9" and q["batch_export"] and q["batch_formats"] == ["9:16"]
    assert q["resolution"] == "HD" and q["fit"] == "blur" and q["scene_mode"] == "auto" and q["short_clip"] == "auto"
    old2 = {"name": "old2", "settings": {"formats": ["16:9"], "quality": "1080p"}, "scenes": [], "words": []}
    with open(op, "w", encoding="utf-8") as f:
        json.dump(old2, f)
    q2 = prj.load(op)["settings"]
    assert q2["scene_mode"] == "auto" and not q2["batch_export"] and q2["fit"] == "crop"
    print("  old projects: saved choices kept (2 formats -> batch export on, 720p -> HD, blur framing, pause-based "
          "planning)")
    print("V7 GUI OK")


if __name__ == "__main__":
    main()
