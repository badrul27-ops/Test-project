"""Image cues: script markers / CSV / dialog -> scenes that start exactly where the narration says.

    python -m tests.test_cues
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from autoedit import cues as C  # noqa: E402
from autoedit import project as prj  # noqa: E402

SPOKEN = ("In nineteen ten seven men boarded a private train in New Jersey. They told the staff they were "
          "duck hunters. The island was silent and cold. For nine days they worked in secret. "
          "What they wrote would change American money forever. Nobody outside the room knew.")
SCRIPT = """Title: The secret meeting  [pause]
[1] In 1910, seven men boarded a private train in New Jersey.
[2] They told the staff they were duck hunters.
[3, 4] The island was silent and cold. For nine days they worked in secret.
[05.png] What they wrote would change American money forever.
[99] Nobody outside the room knew.
"""


def words_for(text, t0=0.4):
    out, t = [], t0
    for w in text.split():
        d = 0.18 + 0.03 * len(w)
        out.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
        t += d + (0.7 if w.endswith(".") else 0.08)
    return out, round(t + 0.3, 3)


def start_of(words, phrase):
    toks = phrase.lower().split()
    for i in range(len(words)):
        if [w["w"].lower().strip(".,") for w in words[i:i + len(toks)]] == toks:
            return i
    raise AssertionError(phrase)


def main():
    tmp = tempfile.mkdtemp()
    media = os.path.join(tmp, "Numbered_Images")
    os.makedirs(os.path.join(media, "more"))
    for n in range(1, 7):
        Image.new("RGB", (320, 180), (40 * n, 90, 120)).save(os.path.join(media, f"{n:02d}.png"))
    Image.new("RGB", (320, 180), (0, 0, 0)).save(os.path.join(media, "more", "image_3.png"))   # duplicate number
    words, dur = words_for(SPOKEN)
    p = prj.new_project()
    p.update(name="cues", media_dir=media, words=words, duration=dur)

    # ---- parsing
    cl = C.parse_script(SCRIPT)
    assert [c["ref"] for c in cl] == ["1", "2", "3, 4", "05.png", "99"], [c["ref"] for c in cl]   # [pause] ignored
    assert "[" not in C.strip_markers(SCRIPT).replace("[pause]", "")
    # ---- plan
    scenes, report = C.plan_from_cues(p, cl)
    names = [os.path.basename(s["media"]) for s in scenes]
    assert names == ["01.png", "02.png", "03.png", "04.png", "05.png"], names
    assert any("99" in r and "no file" in r for r in report), report
    assert any("image 3" in r and "2 files" in r for r in report), report
    i2 = start_of(words, "they told the staff")
    t2 = scenes[1]["start"]
    assert words[i2 - 1]["e"] <= t2 <= words[i2]["s"], (t2, words[i2 - 1], words[i2])
    i5 = start_of(words, "what they wrote")
    assert words[i5 - 1]["e"] <= scenes[4]["start"] <= words[i5]["s"]
    # shared cue [3, 4] split at a pause between its two sentences
    i_for = start_of(words, "for nine days")
    assert words[i_for - 1]["e"] <= scenes[3]["start"] <= words[i_for]["s"], scenes[3]["start"]
    assert scenes[0]["start"] == 0.0 and scenes[-1]["end"] == dur
    assert all(a["end"] == b["start"] for a, b in zip(scenes, scenes[1:]))
    assert C._tok("1910") != C._tok("nineteen ten")      # recording differs from script: still aligned above
    print("  script markers: [1]…[05.png] matched to the recording (even '1910' vs 'nineteen ten'); shared [3, 4] "
          "split at the pause; [pause] ignored; missing [99] and duplicate numbers reported")

    # ---- CSV with times and with text
    csvp = os.path.join(tmp, "cues.csv")
    with open(csvp, "w", encoding="utf-8") as f:
        f.write("image,start\n1,0:00\n2,0:03.5\n6,0:07\n")
    cl = C.load_cues(csvp)
    sc, _ = C.plan_from_cues(p, cl)
    assert [os.path.basename(s["media"]) for s in sc] == ["01.png", "02.png", "06.png"]
    assert abs(sc[1]["start"] - 3.5) < 0.8 and abs(sc[2]["start"] - 7.0) < 0.8, [s["start"] for s in sc]
    print("  CSV (image,start) works")

    # ---- replan with scene mode 'script_cues' + caption spelling ignores markers
    sp = os.path.join(tmp, "script.txt")
    open(sp, "w", encoding="utf-8").write(SCRIPT)
    p["script"] = sp
    p["settings"]["scene_mode"] = "script_cues"
    prj.replan(p, log=lambda m: None)
    assert [os.path.basename(s["media"]) for s in p["scenes"]] == names
    from autoedit.transcribe import apply_script
    fixed = apply_script([dict(w) for w in words], C.strip_markers(SCRIPT))
    assert not any("[" in w["w"] or w["w"] in ("1]", "[1") for w in fixed)
    print("  scene mode 'Image markers in script' re-plans from the script; markers never reach the captions")

    # ---- no repeats by default: fewer images than pauses -> each image once, in order
    few = os.path.join(tmp, "few")
    os.makedirs(few)
    for n in (1, 2, 10):                       # numeric order: 1, 2, 10 (not 1, 10, 2)
        Image.new("RGB", (320, 180), (20 * n, 60, 90)).save(os.path.join(few, f"{n}.png"))
    long_words, long_dur = words_for(" ".join([SPOKEN] * 4))
    q = prj.new_project()
    q.update(name="few", media_dir=few, words=long_words, duration=long_dur)
    q["settings"].update(scene_mode="auto", hook_fast_cuts=False)
    prj.replan(q, log=lambda m: None)
    used = [os.path.basename(s["media"]) for s in q["scenes"]]
    assert used == ["1.png", "2.png", "10.png"], used
    q["settings"]["repeat_media"] = True
    prj.replan(q, log=lambda m: None)
    assert len(q["scenes"]) > 3, "with repeating allowed, auto mode keeps its natural cuts"
    print(f"  auto mode with 3 images and {len(q['scenes'])} pauses: each image used once in numeric order "
          "(1, 2, 10); repeating only when allowed")

    # ---- directions box: one paragraph per image, matched to the recording
    directions = ("Scene 1: In 1910 seven men boarded a private train in New Jersey.\n\n"
                  "They told the staff they were duck hunters. The island was silent and cold.\n\n"
                  "3) For nine days they worked in secret. What they wrote would change American money forever. "
                  "Nobody outside the room knew.")
    q2 = prj.new_project()
    q2.update(name="dir", media_dir=few, words=words, duration=dur, directions=directions)
    q2["settings"]["scene_mode"] = "script_cues"
    prj.replan(q2, log=lambda m: None)
    used = [os.path.basename(s["media"]) for s in q2["scenes"]]
    assert used == ["1.png", "2.png", "10.png"], used
    i2 = start_of(words, "they told the staff")
    i3 = start_of(words, "for nine days")
    assert words[i2 - 1]["e"] <= q2["scenes"][1]["start"] <= words[i2]["s"]
    assert words[i3 - 1]["e"] <= q2["scenes"][2]["start"] <= words[i3]["s"]
    cl, note = C.parse_directions(directions + "\n\nExtra paragraph.", [os.path.join(few, f) for f in ("1.png", "2.png")])
    assert "more than images" in note, note
    print("  directions: paragraph N -> image N, starting exactly where it is spoken; 'Scene 1:' / '3)' numbering "
          "ignored; count mismatches explained")

    # ---- GUI: dialog + properties timing
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.setOrganizationName("AECueTest")
    QApplication.setApplicationName("AECueTest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    import app as A
    A.ask_unsaved = lambda *a, **k: "discard"
    w = A.MainWindow()
    w.resize(1366, 740)
    w.show()
    p["settings"]["scene_mode"] = "auto"
    prj.replan(p, log=lambda m: None)
    w.project = p
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    app.processEvents()
    before = [dict(s) for s in p["scenes"]]
    from autoedit.cue_dialog import CueDialog
    dlg = CueDialog(w, w.project)
    assert dlg.table.rowCount() == len(C.segments(words)) >= 6
    refs = {0: "1", 1: "2", 2: "3", 3: "4, 5", 5: "nope.png"}
    dlg.set_refs(refs)
    assert "weren't found" in dlg.status.text()
    assert dlg.table.item(4, 3).text().startswith("↳")
    if os.environ.get("AE_SHOT"):
        from autoedit.theme import apply_theme
        apply_theme(app)
        dlg.show()
        app.processEvents()
        dlg.grab().save(os.environ["AE_SHOT"])
    dlg.apply()
    assert dlg.result
    scenes, report = dlg.result
    assert [os.path.basename(s["media"]) for s in scenes] == ["01.png", "02.png", "03.png", "04.png", "05.png"]
    # apply through the window (undoable)
    w.project["scenes"] = scenes
    w.commit("Match images to narration")
    w.refresh_all()
    w.do_undo()
    assert [s["media"] for s in w.project["scenes"]] == [s["media"] for s in before]
    w.do_redo()
    # properties: move scene 2's start to a time, neighbours follow, narration unchanged
    w.select_scene(1)
    old_prev_end = w.project["scenes"][0]["end"]
    w.props.s_start.setValue(old_prev_end + 0.5)
    assert w.project["scenes"][0]["end"] == w.project["scenes"][1]["start"] == round(old_prev_end + 0.5, 3)
    assert w.project["scenes"][-1]["end"] == dur
    w.do_undo()
    assert w.project["scenes"][0]["end"] == old_prev_end
    # directions box in the window: typing switches the mode, Apply builds the scenes
    w.project["directions"] = ""
    w.project["settings"]["scene_mode"] = "auto"
    w.load_controls()
    w.dir_box.setPlainText(directions)
    w._save_directions(w.dir_box)
    assert w.project["settings"]["scene_mode"] == "script_cues" and w.mode.currentData() == "script_cues"
    assert w.home_mode.currentData() == "script_cues"
    w.apply_directions()
    assert [os.path.basename(s["media"]) for s in w.project["scenes"]][:3] == ["01.png", "02.png", "03.png"]
    w.do_undo()
    print("  dialog: rows per sentence, numbers -> images ('↳' continues), unknown names flagged, Apply builds "
          "the scenes (undoable); Properties 'Starts at' moves the cut")
    print("CUES OK")


if __name__ == "__main__":
    main()
