"""Timed edit plan (v7.2): the user says which image runs from which second to which second; AutoEditor
builds exactly that and refuses to export until the plan covers the whole narration.
Checked on a REAL export (decoded frames + audio) and in the window.

    python -m tests.test_timed_plan
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

from autoedit import instructions as I  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit import timedplan as TP  # noqa: E402
from autoedit.render import render_all  # noqa: E402
from tests.test_edit_instructions import FPS, build, frame, onset, read_audio, which_tint  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="ae_tp_")
    p = build(tmp)
    p["settings"]["scene_mode"] = "timed"
    assert prj.new_project()["settings"]["scene_mode"] == "timed"
    dur = p["duration"]
    media = prj.project_media(p)

    # ---- template from the narration: exact times, no gaps, images left for the user
    tpl = TP.template(p["words"], dur)
    rows, probs = TP.parse_plan(tpl, media, dur)
    lines = [ln for ln in tpl.splitlines() if ln and not ln.startswith("#")]
    assert len(lines) >= 5 and all("??? |" in ln for ln in lines)
    assert sum(1 for x in probs if "choose an image" in x["msg"]) == len(lines)
    print(f"  template: {len(lines)} lines with times from the recording (0:00 → {dur:.2f} s), each asks for an image")

    # ---- empty plan: draft for preview, export blocked
    prj.replan(p, log=lambda m: None)
    assert all(s["status"] == "fallback" for s in p["scenes"])
    v = I.validate(p)
    assert any(i["level"] == "fatal" and "timed edit plan is required" in i["msg"] for i in v), v
    try:
        render_all(dict(p, name="blocked"), log=lambda m: None)
        raise AssertionError("export must be blocked without a plan")
    except RuntimeError as e:
        assert "timed edit plan is required" in str(e)
    print("  no plan: an automatic DRAFT is shown for preview, export is blocked with a clear message")

    # ---- broken plans are rejected line by line (nothing built)
    bad = ("0:00 - 0:05 | 001.png\n0:05 - 0:09 | 002.png\n0:08 - 0:12 | 004.png\n0:13 - 0:20 | 099.png\n"
           "0:20 - 0:24 | ???\nhello there\n0:24 - 0:22 | 005.png\n")
    rows, probs = TP.parse_plan("0:00 - 0:05 | 003.png\n0:05 - 0:09 | 3\n", media, dur)
    m2 = " | ".join(x["msg"] for x in probs)
    assert "2 files are named 003.png" in m2 and "write the folder too" in m2 and "2 files have this number" in m2, m2
    rows, probs = TP.parse_plan(bad, media, dur)
    msgs = " | ".join(x["msg"] for x in probs)
    for want in ("line 3 starts at 0:08.00 but line 2 runs until 0:09.00", "099.png isn't in the images folder",
                 "line 5 (0:20.00-0:24.00): choose an image", "line 6: not a scene row or a note - NOT used",
                 "line 7: ends (0:22.00) before", "the narration continues until"):
        assert want in msgs, (want, msgs)
    p["timed_plan"] = bad
    before = [dict(s) for s in p["scenes"]]
    try:
        TP.build(p, log=lambda m: None)
        raise AssertionError("bad plan built")
    except ValueError as e:
        assert "nothing was built" in str(e)
    assert p["scenes"] == before
    print("  broken plan: overlap, gap, missing image, ???, unreadable line, end-before-start and short coverage "
          "are each reported with their line - nothing is built")

    # ---- a real plan, followed exactly
    t1, t2, t3, t4 = 3.2, 6.5, 9.9, 15.0
    plan = (f"# my plan\n"
            f"0:00 - {t1} | 001.png | slow zoom in, cut\n"
            f"{t1} - {t2} | 2 | pan right, sfx paper.wav, crossfade 0.4\n"
            f"{t2} - {t3} | image 4 | still, b&w, cut\n"
            f"{t3} - {t4} | 005.png | make it fly, cut\n"
            f"{t4} - {dur:.2f} | 006.png | zoom out, vignette\n")
    p["timed_plan"] = plan
    prj.replan(p, log=lambda m: None)                  # what Analyze does
    sc = p["scenes"]
    want = [(0.0, t1, "001.png"), (t1, t2, "002.png"), (t2, t3, "004.png"), (t3, t4, "005.png"), (t4, dur, "006.png")]
    got = [(s["start"], s["end"], os.path.basename(s["media"])) for s in sc]
    assert len(got) == 5 and all(abs(a - x) < 1 / FPS and abs(b - y) < 1 / FPS and n == m
                                 for (a, b, n), (x, y, m) in zip(got, want)), got
    assert [s["id"] for s in sc] == ["T001", "T002", "T003", "T004", "T005"]
    assert sc[0]["motion"] == "zoom_in" and sc[1]["motion"] == "pan_right" and sc[2]["motion"] == "none"
    assert sc[1]["transition"] == "crossfade" and sc[1]["transition_s"] == 0.4 and sc[0]["transition"] == "cut"
    assert sc[2]["effects"][0]["type"] == "black_white" and sc[4]["effects"][0]["type"] == "vignette"
    assert any("make it fly" in r for r in p["plan_report"])
    assert not [i for i in I.validate(p) if i["level"] == "fatal"]
    # a manual change afterwards is noticed
    sc[0]["media"] = sc[3]["media"]
    assert any(i["key"] == "timed_plan" for i in I.validate(p))
    TP.build(p, log=lambda m: None)
    out = render_all(dict(p, name="plan"), log=lambda m: None)[0]
    tints = {"001.png": 1, "002.png": 2, "004.png": 4, "005.png": 5}
    for a, b, n in want[:4]:
        if n == "004.png":
            continue                       # shown in black & white - checked below
        for t in (a + 0.25, (a + b) / 2, b - 0.25):
            k, c = which_tint(frame(out, t))
            assert k == tints[n], (n, t, k, c)
    f_bw = frame(out, (t2 + t3) / 2).astype(int)
    assert np.abs(f_bw[..., 0] - f_bw[..., 1]).mean() < 6, "b&w should remove colour"
    x = read_audio(out)
    t_on, _ = onset(x, 3000, after=t1 - 1.0)
    assert abs(t_on - t1) <= 1 / FPS, (t_on, t1)
    print(f"  plan followed exactly on the export: 5 shots at the written seconds (checked on frames), image 4 in "
          f"black & white, crossfade 0.4 s only where asked, paper.wav at {t_on:.3f} s for the shot at {t1} s; "
          "'make it fly' reported as not understood; a later manual change is flagged")

    # ---- the window: template button, typing, check, build, start page box in sync
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.setOrganizationName("AETPTest")
    QApplication.setApplicationName("AETPTest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    import app as A
    A.ask_unsaved = lambda *a, **k: "discard"
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    w = A.MainWindow()
    w.resize(1400, 900)
    w.show()
    q = build(tmp + "_w")
    q["settings"]["scene_mode"] = "timed"
    w.project = q
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    app.processEvents()
    assert w.mode.currentData() == "timed" and w.home_mode.currentData() == "timed"
    box = w.instr_panel.timed
    box.fill_template()
    assert "??? |" in q["timed_plan"] and "choose an image" in box.problems.item(0).text()
    assert not box.build()                                   # ??? left: refused
    n = sum(1 for ln in q["timed_plan"].splitlines() if "| ??? |" in ln)
    txt = q["timed_plan"]
    imgs = ["001.png", "002.png", "alt/003.png", "004.png", "005.png", "006.png"]
    out_lines = []
    k = 0
    for ln in txt.splitlines():
        if "| ??? |" in ln:
            ln = ln.replace("???", imgs[k % 6], 1)
            k += 1
        out_lines.append(ln)
    txt = "\n".join(out_lines) + "\n"
    box.edit.setPlainText(txt)
    box.save()
    assert not hasattr(w, "home_plan") and not hasattr(w.instr_panel, "scene")   # ONE plan box, no duplicates
    assert box.build()
    got = [os.path.basename(s["media"]) for s in w.project["scenes"]]
    assert got == [os.path.basename(imgs[k % 6]) for k in range(n)], got
    assert os.sep + "alt" + os.sep in w.project["scenes"][2]["media"]
    assert "✓ Ready to apply" in box.status.text()
    w.do_undo()
    assert w.project["scenes"][0]["id"] != "T001"
    w.do_redo()
    assert w.project["scenes"][0]["id"] == "T001"
    w.a_plan.trigger()                                       # F9: big editor shows the same plan
    app.processEvents()
    assert w.plan_win.box.edit.toPlainText() == w.project["timed_plan"]
    w.plan_win.box.edit.setPlainText(w.project["timed_plan"] + "# note\n")
    w.plan_win.box.save()
    assert box.edit.toPlainText().endswith("# note\n")
    if os.environ.get("AE_SHOTS"):
        w.plan_win.grab().save(os.path.join(os.environ["AE_SHOTS"], "timed_plan_big.png"))
        os.makedirs(os.environ["AE_SHOTS"], exist_ok=True)
        w.tools.setCurrentIndex([b.text() for b in w.tools.buttons].index("Editing Plan"))
        app.processEvents()
        w.grab().save(os.path.join(os.environ["AE_SHOTS"], "timed_plan.png"))
    print(f"  window: 'Fill template' → {n} lines with ???; Build refused until every image is chosen; then built "
          "exactly (undo/redo work); one plan box (F9 window shows the same plan)")
    print("TIMED PLAN OK")


if __name__ == "__main__":
    main()
