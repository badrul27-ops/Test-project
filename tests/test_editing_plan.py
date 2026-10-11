"""Editing plan (v7.4): ONE panel for the whole plan. Every format (compact lines, Markdown tables, CSV, scene
blocks, note lines) becomes the same scene-plan schema; applying it changes the real timeline and the exported
frames; a plan that fails is never replaced by pictures in file order.

    python -m tests.test_editing_plan
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoedit import instructions as I  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit import timedplan as TP  # noqa: E402
from autoedit.render import render_all  # noqa: E402
from tests.test_edit_instructions import FPS, build, frame, which_tint  # noqa: E402

# the row from the user's screenshot that used to fail with "needs a time range first"
SCREENSHOT_ROW = "| 23 | 023.jpg | 01:56.84 | 02:06.76 | 9.92 |"


def check_parser():
    media = [os.path.join("m", f"{i:03d}.jpg") for i in range(1, 40)]
    ents, rows, probs = TP.parse_entries(SCREENSHOT_ROW, media, None, 30)
    e = ents[0]
    assert (os.path.basename(e["media"]), e["start"], e["end"], e["duration"], e["scene"]) == \
        ("023.jpg", 116.84, 126.76, 9.92, 23), e
    assert not e["errors"] and not [x for x in probs if "needs a time range" in x["msg"] or "duration" in x["msg"]]
    assert abs((e["end"] - e["start"]) - 9.92) < 1e-9
    r = rows[0]
    assert (r["start_raw"], r["end_raw"]) == (116.84, 126.76)
    assert abs(r["start"] - I.snap(116.84, 30)) < 1e-9          # on the timeline the cut lands on a whole frame
    print("  screenshot row → Image 023.jpg, Start 116.84 s, End 126.76 s, Duration 9.92 s (no error)")

    # the same scene written in every supported format -> the same schema
    forms = {
        "compact line": "01:56.84 - 02:06.76 | 023.jpg | slow zoom in",
        "Markdown table": "| Scene | Image | Start | End | Duration | Motion |\n|---|---|---|---|---|---|\n"
                          "| 23 | 023.jpg | 01:56.84 | 02:06.76 | 9.92 | slow zoom in |",
        "CSV": "scene,image,start,end,duration,motion\n23,023.jpg,01:56.84,02:06.76,9.92,slow zoom in\n",
        "scene block": "SCENE 023\nImage: 023.jpg\nStart: 01:56.840\nEnd: 02:06.760\nDuration: 9.920 seconds\n"
                       "Motion: slow zoom in\n",
    }
    for name, txt in forms.items():
        ents, rows, probs = TP.parse_entries(txt, media, None, 30)
        assert len(rows) == 1 and len(ents) == 1, (name, probs)
        r = rows[0]
        assert os.path.basename(r["media"]) == "023.jpg", name
        assert abs(r["start"] - I.snap(116.84, 30)) < 1e-9 and abs(r["end"] - I.snap(126.76, 30)) < 1e-9, (name, r)
        assert not [x for x in probs if x["level"] == "error" and "nothing is assigned from 0:00" not in x["msg"]], \
            (name, probs)
    # Markdown header + separator rows are recognised; extra columns become editing phrases or notes
    md = ("| Scene | Image | Start | End | Duration | Motion | Transition | SFX | Notes |\n"
          "| :--- | --- | --- | --- | ---: | --- | --- | --- | --- |\n"
          "| 1 | 001.jpg | 00:00.00 | 00:04.50 | 4.50 | slow zoom in | crossfade | whoosh.wav | opening |\n"
          "| 2 | 002.jpg | 00:04.50 | 00:09.00 | 4.00 | pan right | | | |\n"
          "| 3 | 003.jpg | 00:09.00 | 00:12.00 | 3.00 | make it fly | | | |\n"
          "\n"
          "- **002.jpg:** Close-up of the plaque; keep it readable.\n"
          "Scene 3: label SHIFT 1 - FARMING\n"
          "- **099.jpg:** an image that isn't in the plan\n"
          "Some text that is neither a scene nor a note\n")
    ents, rows, probs = TP.parse_entries(md, media, 12.0, 30)
    msgs = " | ".join(x["msg"] for x in probs)
    assert len(rows) == 3 and not [x for x in probs if x["level"] == "error"], msgs
    assert rows[0]["extras"] == "slow zoom in, dissolve into the next scene, sfx whoosh.wav", rows[0]["extras"]
    assert rows[0]["notes"] == "opening"
    assert "Close-up of the plaque" in rows[1]["notes"] and "SHIFT 1" in rows[2]["notes"]
    assert "duration 4.00 s doesn't match start/end (4.50 s) - start/end are used" in msgs
    assert "“make it fly” not understood - NOT applied" in msgs
    assert "099.jpg isn't in the plan, so the note is NOT attached" in msgs
    assert "not a scene row or a note - NOT used: “Some text that is neither" in msgs
    assert "NOT applied as editing instructions" in msgs
    # the brief's examples: key=value lines, CSV values (zoom_in / none, S001 ids), JSON, plan guidelines
    kv = ("00:00.000 - 00:04.200 | 001.jpg | motion=zoom_in | transition=cut\n"
          "00:04.200 - 00:08.800 | 002.jpg | motion=none | transition=cut\n")
    csv_txt = ("scene_id,image,start,end,motion,transition\nS001,001.jpg,00:00.000,00:04.200,zoom_in,cut\n"
               "S002,002.jpg,00:04.200,00:08.800,none,cut\n")
    js = ('{"version": 1, "guidelines": ["Use subtle motion", "Keep the narration unchanged", "Make it pop"], '
          '"scenes": [{"scene_id": "S001", "image": "001.jpg", "start": "00:00.000", "end": "00:04.200", '
          '"motion": "zoom_in", "transition": "cut"}, {"scene_id": "S002", "image": "002.jpg", "start": 4.2, '
          '"end": 8.8, "motion": "none", "sfx": "none", "b_roll": "none", "mood": "calm"}]}')
    for name, txt in (("key=value", kv), ("CSV", csv_txt), ("JSON", js)):
        ents, rows, probs = TP.parse_entries(txt, media, 8.8, 30)
        assert [os.path.basename(r["media"]) for r in rows] == ["001.jpg", "002.jpg"], (name, probs)
        assert not [x for x in probs if "not understood - NOT applied" in x["msg"] and "Make it pop" not in x["msg"]
                    and "mood" not in x["msg"]], (name, probs)
        s1, i1, _u, _n = I.parse(rows[0]["text"])
        s2, i2, _u, _n = I.parse(rows[1]["text"])
        assert s1["motion"]["type"] == "zoom_in" and s1["transition_out"]["type"] == "cut", (name, rows[0]["text"])
        assert s2["motion"]["mode"] == "none", (name, rows[1]["text"])     # None switches it OFF - no fallback
        if name != "key=value":
            assert [e["scene"] for e in ents] == [1, 2], name
        if name == "JSON":
            assert s2["sfx_mode"] == "none" and s2["b_roll_mode"] == "none"
            assert rows[0]["guide"] == {"motion_level": "Subtle"}
            msgs = " | ".join(x["msg"] for x in probs)
            assert "“Make it pop” not understood - NOT applied" in msgs and "mood not understood" in msgs, msgs
    ents, rows, probs = TP.parse_entries('{"scenes": [ {"image": "001.jpg", }', media, 8.8, 30)
    assert not rows and "can't be read" in probs[0]["msg"] and "column" in probs[0]["msg"]
    # every downloadable template (More → Save a plan template, examples/) parses cleanly
    for ext, txt in TP.TEMPLATES.items():
        ents, rows, probs = TP.parse_entries(txt, [os.path.join("m", f"{i:03d}.png") for i in (1, 2, 3)], 12.0, 30)
        assert len(rows) == 3 and not [x for x in probs if x["level"] == "error" or "not understood" in x["msg"]], \
            (ext, probs)
    # times: seconds, mm:ss.fraction and hh:mm:ss.fraction all mean the same thing
    for a, b in (("116.84", "126.76"), ("01:56.84", "02:06.76"), ("00:01:56.84", "00:02:06.76")):
        ents, rows, probs = TP.parse_entries(f"{a} - {b} | 023.jpg", media, None, 30)
        assert (ents[0]["start"], ents[0]["end"]) == (116.84, 126.76), (a, b, ents[0])
    # a note for an image used by two scenes is attached to both and flagged
    amb = "0 - 4 | 001.jpg\n4 - 8 | 001.jpg\n- **001.jpg:** same picture twice\n"
    ents, rows, probs = TP.parse_entries(amb, media, 8.0, 30)
    assert all("same picture twice" in r["notes"] for r in rows)
    assert any("used by 2 scenes" in x["msg"] for x in probs)
    print("  compact / Markdown / CSV / scene block → same scene; header + separator rows recognised; duration "
          "checked (start/end win); notes attached to their image or scene; unknown phrases, stray text, a note for "
          "a missing image and an ambiguous note are all listed")


def check_project_and_export(tmp):
    p = build(tmp)
    st = p["settings"]
    st.update(scene_mode="timed", captions=False, atmosphere="None")
    dur = p["duration"]
    t1, t2, t3 = round(dur * 0.25, 2), round(dur * 0.5, 2), round(dur * 0.75, 2)

    def tc(t):
        m, s = divmod(t, 60)
        return f"{int(m):02d}:{s:05.2f}"
    rows = [("1", "001.png", 0.0, t1), ("2", "002.png", t1, t2), ("3", "004.png", t2, t3), ("4", "005.png", t3, dur)]
    plan = ("| Scene | Image | Start | End | Duration | Notes |\n|---|---|---|---|---|---|\n" +
            "".join(f"| {n} | {im} | {tc(a)} | {tc(b)} | {b - a:.2f} | |\n" for n, im, a, b in rows) +
            "\n- **004.png:** keep the colour strong\n")
    # ---- strict: a broken plan never turns into pictures in file order; the timeline stays as it was
    prj.replan(p, log=lambda m: None)                  # no plan yet -> automatic draft (preview only)
    before = [(s["start"], s["end"], s.get("media")) for s in p["scenes"]]
    p["timed_plan"] = plan.replace("004.png", "099.png")
    prj.replan(p, log=lambda m: None)
    assert [(s["start"], s["end"], s.get("media")) for s in p["scenes"]] == before
    assert p["plan_report"][0].startswith("Editing plan NOT applied") and "099.png is missing" in \
        " ".join(p["plan_report"])
    assert any(i["level"] == "fatal" for i in I.validate(p)), "export must stay blocked"
    # ---- a valid plan: exact images and times on the timeline, notes kept with the scene
    p["timed_plan"] = plan
    prj.replan(p, log=lambda m: None)
    sc = p["scenes"]
    got = [(os.path.basename(s["media"]), s["start"], s["end"]) for s in sc]
    assert got == [(im, I.snap(a, FPS), I.snap(b, FPS) if b != dur else dur) for _n, im, a, b in rows], got
    assert "keep the colour strong" in I.record(p, sc[2]["id"])["notes"]
    assert not [i for i in I.validate(p) if i["level"] == "fatal"]
    out = render_all(dict(p, name="editing_plan"), log=lambda m: None)[0]
    tint = {"001.png": 1, "002.png": 2, "004.png": 4, "005.png": 5}
    for _n, im, a, b in rows:
        for t in (a + 0.2, (a + b) / 2, b - 0.2):
            k, c = which_tint(frame(out, t))
            assert k == tint[im], (im, t, k, c)
    print(f"  broken plan (missing 099.png): timeline left unchanged, no file-order fallback, export blocked; fixed "
          f"plan: 4 scenes with the exact images and times; the exported frames show 001/002/004/005 at "
          f"0-{t1}/{t1}-{t2}/{t2}-{t3}/{t3}-{dur:.2f} s; the note stays with its scene")
    return p, rows, dur


def check_three_scenes(tmp):
    """Acceptance: a strict 3-scene plan (out of file order, decimal times) shows the right picture on the last
    frame before and the first frame after every cut - in the PREVIEW and in the decoded EXPORT - and survives
    save / reopen / re-plan unchanged."""
    from autoedit.engine import Compositor
    from tests.test_edit_instructions import H, W
    p = build(tmp)
    p["settings"].update(scene_mode="timed", captions=False, atmosphere="None")
    dur = p["duration"]
    b1, b2 = 7.37, 15.43                       # cuts NOT on whole frames at 30 fps (7.3667 / 15.4333 after snapping)
    p["timed_plan"] = (f"00:00.000 - 00:{b1:06.3f} | 005.png | motion=zoom_in | transition=cut\n"
                       f"00:{b1:06.3f} - 00:{b2:06.3f} | 001.png | motion=none | transition=cut\n"
                       f"00:{b2:06.3f} - {dur:.3f} | 004.png | pan right, cut\n")
    prj.replan(p, log=lambda m: None)
    sc = p["scenes"]
    assert [os.path.basename(s["media"]) for s in sc] == ["005.png", "001.png", "004.png"], p.get("plan_report")
    cuts = [I.snap(b1, FPS), I.snap(b2, FPS)]
    assert [round(s["start"], 6) for s in sc[1:]] == [round(c, 6) for c in cuts]
    want = [(5, 1), (1, 4)]                    # picture before / after each cut
    comp = Compositor(p, W, H, FPS, preview=True, captions=False)
    checks = []
    for c, (before, after) in zip(cuts, want):
        k = int(round(c * FPS))                # first frame of the next scene
        for idx, tint in ((k - 1, before), (k, after)):
            got, _ = which_tint(comp.frame_at(idx / FPS))
            assert got == tint, ("preview", idx, got, tint)
            checks.append((idx, tint))
    comp.close()
    logs = []
    out = render_all(dict(p, name="three_scenes"), log=logs.append)[0]
    for idx, tint in checks:
        got, c = which_tint(frame(out, idx / FPS))
        assert got == tint, ("export", idx, got, tint, c)
    rev = next(m for m in logs if m.startswith("Exporting timeline revision"))
    assert TP.timeline_revision(p) in rev
    import json
    with open(os.path.join(p["output_dir"], "_autoedit_work", "last_export_plan.json"), encoding="utf-8") as f:
        diag = json.load(f)
    assert [r["asset"] for r in diag["scenes"]] == ["005.png", "001.png", "004.png"]
    assert (diag["scenes"][1]["requested_start"], diag["scenes"][1]["requested_end"]) == (b1, b2)
    assert abs(diag["scenes"][1]["start"] - cuts[0]) < 1e-6
    # save, reopen, re-plan: nothing moves
    path = os.path.join(tmp, "three.autoedit.json")
    prj.save(p, path)
    q = prj.load(path)
    assert [(s["start"], s["end"], s["media"]) for s in q["scenes"]] == [(s["start"], s["end"], s["media"]) for s in sc]
    prj.replan(q, log=lambda m: None)
    assert [(s["start"], s["end"], s["media"]) for s in q["scenes"]] == [(s["start"], s["end"], s["media"]) for s in sc]
    print(f"  strict 3-scene plan (005 → 001 → 004, cuts at {b1} and {b2} s): last frame before / first frame after "
          f"each cut correct in the preview AND the decoded export (frames {', '.join(str(i) for i, _ in checks)}); "
          f"export logged {rev.split()[3]}; diagnostic plan lists requested vs frame-snapped times; save → reopen → "
          f"re-plan changes nothing")


def check_window(tmp, p, rows, dur):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.setOrganizationName("AEEPTest")
    QApplication.setApplicationName("AEEPTest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    import app as A
    A.ask_unsaved = lambda *a, **k: "discard"
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    w = A.MainWindow()
    w.resize(1500, 950)
    w.show()
    p["timed_plan"] = ""
    w.project = p
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    w.tools.setCurrentIndex([b.text() for b in w.tools.buttons].index("Editing Plan"))
    app.processEvents()
    panel = w.instr_panel.plan_panel
    assert not hasattr(w, "home_plan") and not hasattr(w.instr_panel, "scene") and not hasattr(w.instr_panel, "b_plan")
    assert [panel.table.horizontalHeaderItem(c).text() for c in range(panel.table.columnCount())] == \
        ["Image", "Script excerpt", "Start", "End", "Duration", "Motion", "Status"]
    # Import Plan: a Markdown file with notes
    path = os.path.join(tmp, "plan.md")

    def tc(t):
        m, s = divmod(t, 60)
        return f"{int(m):02d}:{s:05.2f}"
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Plan for the test video\n| Scene | Image | Start | End | Duration |\n|---|---|---|---|---|\n" +
                "".join(f"| {n} | {im} | {tc(a)} | {tc(b)} | {b - a:.2f} |\n" for n, im, a, b in rows) +
                "\n- **002.png:** Close-up note\n")
    panel.import_file(path)
    assert panel.table.rowCount() == 4 and "✓ Ready to apply" in panel.status.text(), panel.status.text()
    assert panel.entries[1]["notes"] == "Close-up note" and "Close-up note" in panel.table.item(1, 6).toolTip()
    assert panel.table.item(1, 1).text(), "script excerpt: what is said during the scene"
    assert panel.table.item(0, 6).text().startswith("✓")
    # edit in the table: scene 2 now starts 1 s later (scene 1 ends there too) -> plan text updated
    new_cut = round(rows[1][2] + 1.0, 2)
    panel.table.item(0, 3).setText(f"{new_cut}")             # End of scene 1
    panel.table.item(1, 2).setText(f"{new_cut}")             # Start of scene 2
    txt = w.project["timed_plan"]
    assert txt.splitlines()[:2] == ["# Plan for the test video", TP.TABLE_HEAD] and "Close-up note" in txt, txt
    assert "✓ Ready to apply" in panel.status.text(), panel.status.text()
    old = [(s["start"], s.get("media")) for s in w.project["scenes"]]
    assert old[1][0] == I.snap(rows[1][2], FPS), "the timeline must not change before Apply"
    # scene details for the selected row
    panel.b_det.setChecked(True)
    panel.table.setCurrentCell(1, 0)
    assert "Notes (kept, not applied): Close-up note" in panel.det_text.text()
    assert panel.det_notes.text() == "Close-up note"
    panel.det_notes.setText("Close-up note - keep it readable")
    panel.det_notes.editingFinished.emit()
    assert "keep it readable" in w.project["timed_plan"] and panel.entries[1]["notes"].endswith("readable")
    # Apply -> the real timeline changes; the table says so
    w.open_instructions = lambda *a, **k: None
    assert panel.apply()
    sc = w.project["scenes"]
    assert abs(sc[1]["start"] - I.snap(new_cut, FPS)) < 1e-9 and os.path.basename(sc[1]["media"]) == "002.png"
    assert all(panel.table.item(i, 6).text() == "✓ On the timeline" for i in range(4))
    w.do_undo()
    assert abs(w.project["scenes"][1]["start"] - I.snap(rows[1][2], FPS)) < 1e-9
    w.do_redo()
    # a broken edit is refused - nothing applied
    panel.table.item(2, 0).setText("nope.png")
    assert "✖" in panel.table.item(2, 6).text() and not panel.apply()
    assert abs(w.project["scenes"][1]["start"] - I.snap(new_cut, FPS)) < 1e-9
    w.do_undo()
    panel.load()
    if os.environ.get("AE_SHOTS"):
        os.makedirs(os.environ["AE_SHOTS"], exist_ok=True)
        panel.validate(quiet=True)
        panel.table.setCurrentCell(1, 0)
        app.processEvents()
        w.grab().save(os.path.join(os.environ["AE_SHOTS"], "editing_plan.png"))
        w.a_plan.trigger()
        app.processEvents()
        w.plan_win.grab().save(os.path.join(os.environ["AE_SHOTS"], "editing_plan_big.png"))
    # the edited plan is what gets exported
    out = render_all(dict(w.project, name="editing_plan_edit"), log=lambda m: None)[0]
    k, _ = which_tint(frame(out, new_cut - 0.3))
    assert k == 1, "001.png must now run until the edited cut"
    k, _ = which_tint(frame(out, new_cut + 0.3))
    assert k == 2
    print(f"  window: one Editing plan panel (no duplicate boxes); Import Plan → 4 scenes in the table, note in the "
          f"scene drawer; a table edit rewrites the plan text but leaves the timeline alone until Apply; Apply moves the "
          f"cut to {new_cut} s on the timeline AND in the exported frames; a bad edit is refused (nothing applied)")


def main():
    check_parser()
    tmp = tempfile.mkdtemp(prefix="ae_ep_")
    p, rows, dur = check_project_and_export(tmp)
    check_three_scenes(tempfile.mkdtemp(prefix="ae_ep3_"))
    check_window(tmp, p, rows, dur)
    print("EDITING PLAN OK")


if __name__ == "__main__":
    main()
