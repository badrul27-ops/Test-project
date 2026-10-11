"""Scene sheet (v7.3): the "STRICT VISUAL EDITING INSTRUCTIONS" format - SCENE blocks with Image / Start / End /
Framing / Motion / B-roll / SFX / Effect / Transition / Lock - turned into editable timeline settings and
followed exactly. Checked on a real 1920x1080 export and in the window.

    python -m tests.test_scene_sheet
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from autoedit import instructions as I  # noqa: E402
from autoedit import mediainfo  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit import timedplan as TP  # noqa: E402
from autoedit.render import render_all  # noqa: E402
from autoedit.visuals import ImageSource  # noqa: E402
from tests.test_edit_instructions import build, onset, read_audio  # noqa: E402

SHEET = """STRICT VISUAL EDITING INSTRUCTIONS

Follow the scene plan below as the final authority.

GENERAL RULES
1. Use each image by its exact filename.
9. Export only one video: 16:9, 1920 × 1080, 30 fps.
10. Fill the canvas proportionally without stretching.

SCENE 001
Image: 001.png
Script sentence: A man walks into the old station.
Start: 00:00.000
End: 00:04.200
Duration: 4.200 seconds
Framing: Fill / Crop
Motion: Slow zoom in
Focus: [Specify the subject or area]
B-roll: None
Sound effect: None
Visual effect: None
Transition to next scene: Cut
Lock visual and timing: Yes

SCENE 002
Image: 002.png
Script sentence: He buys a ticket at the counter.
Start: 00:04.200
End: 00:08.800
Duration: 4.600 seconds
Framing: Fill / Crop
Motion: None
B-roll: None
Sound effect: paper.wav
SFX start: 00:05.000
SFX level: -14 dB
Visual effect: None
Transition to next scene: Cut
Lock visual and timing: Yes

SCENE 003
Image: 004.png
Script sentence: She opened the letter.
Start: 00:08.800
End: 00:13.500
Duration: 4.700 seconds
Framing: Fill / Crop
Motion: Gentle pan
Direction: Left to right
B-roll: 006.mp4
B-roll timeline start/end: 00:10.000 - 00:12.000
B-roll source in/out: 00:01.000 - 00:03.000
B-roll placement: Full-frame replacement
B-roll source audio: Muted
Sound effect: None
Visual effect: Lens flare
Transition to next scene: Crossfade 0.5 s
Lock visual and timing: Yes

SCENE 004
Image: 005.png
Script sentence: A man walks into the old station.
Start: 00:13.500
End: {END}
Framing: Fill / Crop
Motion: Automatic
Camera shake: strong
"""


def main():
    tmp = tempfile.mkdtemp(prefix="ae_sheet_")
    p = build(tmp)
    p["settings"].update(scene_mode="timed", sfx_mode="auto", sfx_level="Normal")
    media = prj.project_media(p)
    sheet = SHEET.replace("{END}", f"00:{p['duration']:06.3f}")
    rows, probs = TP.parse_plan(sheet, media, p["duration"], 30, p["words"])
    msgs = " | ".join(x["msg"] for x in probs)
    assert not [x for x in probs if x["level"] == "error"], msgs
    for want in ("SCENE 001: Focus not filled in", "SCENE 003: visual effect “Lens flare” is unavailable",
                 "SCENE 004: field “Camera shake” isn't supported", "SCENE 004: its sentence is spoken at"):
        assert want in msgs, (want, msgs)
    assert [r["media"].endswith(n) for r, n in zip(rows, ("001.png", "002.png", "004.png", "005.png"))] == [True] * 4
    # errors: duration mismatch, pan without direction, missing image, gap
    bad = sheet.replace("Duration: 4.600 seconds", "Duration: 5.000 seconds").replace(
        "Direction: Left to right\n", "").replace("Image: 005.png", "Image: 050.png").replace(
        "Start: 00:13.500", "Start: 00:14.000")
    rows_b, pb = TP.parse_plan(bad, media, p["duration"], 30, p["words"])
    mb = " | ".join(x["msg"] for x in pb)
    for want in ("SCENE 002: duration 5.000 s doesn't match start/end (4.600 s)", "“Gentle pan” has no direction",
                 "SCENE 4: 050.png isn't in the images folder", "nothing is assigned from"):
        assert want in mb, (want, mb)
    print("  parse: 4 scenes; unfilled placeholder, unsupported effect, unknown field and a sentence spoken outside its "
          "scene are reported; duration mismatch, pan without direction, missing image and gaps are errors")

    # ---- build = editable timeline settings; strict: what isn't specified is None
    p["timed_plan"] = sheet
    prj.replan(p, log=lambda m: None)
    st = p["settings"]
    assert (st["aspect"], st["resolution"], st["fps"]) == ("16:9", "Full HD", 30)
    sc = {s["id"]: s for s in p["scenes"]}
    assert list(sc) == ["T001", "T002", "T003", "T004"]
    s1, s2, s3, s4 = sc["T001"], sc["T002"], sc["T003"], sc["T004"]
    assert (s1["start"], s1["end"], s2["end"], s3["end"]) == (0.0, 4.2, 8.8, 13.5)
    assert s1["motion"] == "zoom_in" and s2["motion"] == "none" and s3["motion"] == "pan_right"
    assert s1["locked_fields"] == ["primary_visual", "timing"] and s4.get("locked_fields") == []
    assert s3["transition"] == "crossfade" and s3["transition_s"] == 0.5 and s1["transition"] == "cut"
    assert s4["transition"] == "cut" and all(s.get("sfx_mode") in ("none", "explicit") for s in p["scenes"])
    br = [b for b in p["broll"] if b.get("source") == "T003"][0]
    assert (br["start"], br["end"], br["clip_in"], br["clip_out"], br["mode"], br["audio_gain_db"]) == \
        (10.0, 12.0, 1.0, 3.0, "insert", None)
    ev = [e for e in p["sfx_events"] if e.get("source")]
    assert len(ev) == 1 and ev[0]["t"] == 5.0 and ev[0]["rel_db"] == -14.0
    rec3 = I.records(p)["T003"]
    assert any("Lens flare" in u for u in rec3["unresolved"]) and rec3["status"] == "unresolved"
    v = I.validate(p)
    assert not [i for i in v if i["level"] == "fatal"] and any("Lens flare" in i["msg"] for i in v)
    print("  build: times, images, motion (camera left → right = pan right), crossfade 0.5 s, B-roll 10-12 s from "
          "source 1-3 s, paper.wav at 5.000 s at -14 dB, locks; nothing unspecified added; export format "
          "1920×1080 30 fps from GENERAL RULES; the lens flare is flagged, not faked")

    # ---- direction check: "left to right" really moves the view to the right
    a = np.zeros((900, 1600, 3), np.uint8)
    a[:, :, 0] = np.linspace(0, 255, 1600)[None, :]
    g = os.path.join(tmp, "grad.png")
    Image.fromarray(a).save(g)
    src = ImageSource(g, 320, 180, "pan_right", 0.12)
    assert src.frame(1.0)[90, 160, 0] > src.frame(0.0)[90, 160, 0] + 10

    # ---- real export: one 1920x1080 30 fps file following the sheet
    p["sfx_planned"] = False
    out = render_all(dict(p, name="sheet"), log=lambda m: None)
    assert len(out) == 1 and not mediainfo.check_export(out[0], 1920, 1080, 30, p["duration"] + 0.4)
    x = read_audio(out[0])
    t_on, _ = onset(x, 3000, after=4.0)
    assert abs(t_on - 5.0) <= 1 / 30, t_on
    import subprocess
    from autoedit.util import ffmpeg_exe

    def fr(t):
        n = int(round(t * 30))
        r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", out[0], "-vf", f"select=eq(n\\,{n})",
                            "-vsync", "0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE, check=True)
        return np.frombuffer(r.stdout, np.uint8)[:1920 * 1080 * 3].reshape(1080, 1920, 3)
    mag = lambda im: im[..., 0].mean() > 200 and im[..., 1].mean() < 60  # noqa: E731
    assert mag(fr(11.0)) and not mag(fr(9.8)) and not mag(fr(12.2))
    print(f"  export: one file, 1920×1080, 30 fps, verified; paper.wav starts at {t_on:.3f} s; the B-roll clip is on "
          "screen only 10-12 s")

    # ---- the window: every scene with image, times and settings; edits write back into the sheet
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.setOrganizationName("AESheetTest")
    QApplication.setApplicationName("AESheetTest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    import app as A
    from autoedit.theme import apply_theme
    apply_theme(app)
    A.ask_unsaved = lambda *a, **k: "discard"
    w = A.MainWindow()
    w.resize(1500, 900)
    w.show()
    w.project = p
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    app.processEvents()
    assert w.instr_panel.timed.build()                    # opens Editing instructions
    app.processEvents()
    iw = w.ins_win
    summ = [iw.table.item(r, 9).text() for r in range(iw.table.rowCount())]
    assert "001.png" in summ[0] and "zoom in" in summ[0] and "→ Cut" in summ[0]
    assert "B-roll 006.mp4" in summ[2] and "Dissolve" in summ[2] and "SFX paper.wav" in summ[1], summ
    iw.table.selectRow(1)
    app.processEvents()
    iw.tabs.setCurrentIndex(2)
    iw.mmode.setCurrentIndex(iw.mmode.findData("explicit"))
    iw.mtype.setCurrentIndex(iw.mtype.findData("zoom_out"))
    iw.mmode.activated.emit(iw.mmode.currentIndex())
    assert next(s for s in w.project["scenes"] if s["id"] == "T002")["motion"] == "zoom_out"
    plan = w.project["timed_plan"]
    blk = plan.split("SCENE 002")[1].split("SCENE 003")[0]
    assert "Motion: medium zoom out" in blk and "Sound effect: paper.wav" in blk, blk
    assert "NOT applied: Visual effect: Lens flare" in plan.split("SCENE 003")[1]
    assert w.instr_panel.timed.edit.toPlainText() == plan
    rows2, probs2 = TP.parse_plan(plan, media, p["duration"], 30, p["words"])
    assert not [x for x in probs2 if x["level"] == "error"], probs2
    if os.environ.get("AE_SHOTS"):
        os.makedirs(os.environ["AE_SHOTS"], exist_ok=True)
        iw.grab().save(os.path.join(os.environ["AE_SHOTS"], "sheet_instructions.png"))
    print("  window: Build opens Editing instructions - every scene shows its image, times and resolved settings; "
          "changing scene 2's motion there updates the timeline AND the sheet text (which still parses cleanly)")
    print("SCENE SHEET OK")


if __name__ == "__main__":
    main()
