"""Editing plan → the real image files (v7.6). The user's 96-row plan names pictures as 'Numbered_Images/004.jpg';
the images folder has 001.jpg-096.jpg at its root and a Character_References folder that also has a 004.jpg.
Every row must get its own scene picture at its own times - in the timeline, the preview, after save/reopen,
and in export validation - and a broken plan must never replace the current timeline.

    python -m tests.test_plan_assets
"""
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from autoedit import instructions as I  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit import timedplan as TP  # noqa: E402
from autoedit.util import ffmpeg_exe  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "plan96_numbered_images.txt")
AUDIO_S = 635.611429                     # the supplied narration's measured length
FPS = 30


def tint(i):
    """A distinct colour per numbered image (so frames show WHICH picture is on screen)."""
    return ((i * 53) % 200 + 30, (i * 97) % 200 + 30, (i * 31) % 200 + 30)


def layout(tmp):
    """The user's ZIP layout: 001-096.jpg at the root + Character_References/ (with its own 004.jpg)."""
    root = os.path.join(tmp, "images")
    os.makedirs(os.path.join(root, "Character_References"))
    for i in range(1, 97):
        Image.new("RGB", (172, 96), tint(i)).save(os.path.join(root, f"{i:03d}.jpg"), quality=95)
    Image.new("RGB", (172, 96), (128, 128, 128)).save(os.path.join(root, "Character_References", "004.jpg"))
    Image.new("RGB", (172, 96), (250, 250, 250)).save(os.path.join(root, "Character_References", "hero.png"))
    return root


def project(tmp, root):
    voice = os.path.join(tmp, "narration.wav")
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=8000:cl=mono", "-t", f"{AUDIO_S:.6f}", voice], check=True)
    p = prj.new_project()
    p.update(name="plan96", voiceover=voice, media_dir=root, output_dir=tmp, duration=AUDIO_S,
             words=[{"w": f"w{k}", "s": round(k * 0.5, 3), "e": round(k * 0.5 + 0.4, 3)} for k in range(1271)])
    p["settings"].update(scene_mode="timed", captions=False, atmosphere="None")
    return p


def entries(text, media, ctx=None, dur=None):
    return TP.parse_entries(text, media, dur, FPS, None, ctx=ctx or {})


def main():
    tmp = tempfile.mkdtemp(prefix="ae_assets_")
    root = layout(tmp)
    media = prj.project_media({"media_dir": root})
    ctx = {"root": root, "roots": {}}
    assert len(media) == 98 and sum(os.path.basename(m) == "004.jpg" for m in media) == 2   # 5: both kept

    # 1. pipe syntax keeps the WHOLE path token
    es, rows, probs = entries("0:11.240 - 0:18.500 | Numbered_Images/004.jpg", media, ctx)
    e = es[0]
    assert (e["ref"], e["start"], e["end"]) == ("Numbered_Images/004.jpg", 11.24, 18.5), e
    assert rows and os.path.normpath(rows[0]["media"]) == os.path.join(root, "004.jpg")
    # 2. the advertised table row resolves to the same file
    es2, rows2, _ = entries("| Numbered_Images/004.jpg | 0:11.240 | 0:18.500 |", media, ctx)
    assert (es2[0]["ref"], es2[0]["start"], es2[0]["end"]) == ("Numbered_Images/004.jpg", 11.24, 18.5)
    assert rows2[0]["media"] == rows[0]["media"]
    # 3. decimal seconds: .24 == .240 (240 ms), never frames; malformed times are rejected
    assert TP.parse_time("0:11.24") == TP.parse_time("0:11.240") == 11.24 and TP.parse_time("1:05.5") == 65.5
    assert TP.parse_time("0:61.0") is None and TP.parse_time("1:2x") is None
    bad_t, _r, bad_p = entries("0:11.24 - 0:61.00 | 004.jpg", media, ctx)
    assert not _r and bad_t[0]["kind"] == "timing", bad_p
    print("  1-3: '0:11.240 - 0:18.500 | Numbered_Images/004.jpg' → start 11.24, end 18.5, the full path; the table "
          "row resolves to the same file; .24 = .240; malformed times rejected")

    # 4. Windows separators, spaces, parentheses, upper-case extension, quotes/backticks
    odd = os.path.join(root, "My Pics (2)")
    os.makedirs(odd)
    Image.new("RGB", (32, 18), (1, 2, 3)).save(os.path.join(odd, "004 final.JPG"))
    media4 = prj.project_media({"media_dir": root})
    for token in ("My Pics (2)\\004 final.JPG", "`My Pics (2)/004 final.JPG`", '"My Pics (2)/004 final.jpg"'):
        es4, rows4, p4 = entries(f"0:00.000 - 0:02.000 | {token}", media4, ctx)
        assert rows4 and rows4[0]["media"] == os.path.join(odd, "004 final.JPG"), (token, p4)
    # 5. two 004.jpg kept; an exact folder path picks the intended one (the reference only when asked for)
    nb = os.path.join(tmp, "images2")
    for d in ("Numbered_Images", "Character_References"):
        os.makedirs(os.path.join(nb, d))
        Image.new("RGB", (32, 18), (9, 9, 9)).save(os.path.join(nb, d, "004.jpg"))
    m5 = prj.project_media({"media_dir": nb})
    c5 = {"root": nb, "roots": {}}
    assert entries("0 - 2 | Numbered_Images/004.jpg", m5, c5)[1][0]["media"] == os.path.join(nb, "Numbered_Images",
                                                                                           "004.jpg")
    assert entries("0 - 2 | Character_References/004.jpg", m5, c5)[1][0]["media"] == \
        os.path.join(nb, "Character_References", "004.jpg")
    print("  4-5: backslashes, spaces, parentheses, .JPG, quotes/backticks work; both 004.jpg are kept and the "
          "written folder decides which one")

    # 7. name-only fallback never picks a reference picture or one of several scene duplicates
    assert entries("0 - 2 | 004.jpg", media, ctx)[1][0]["media"] == os.path.join(root, "004.jpg")
    dup = os.path.join(tmp, "images3")
    for d in ("a", "b"):
        os.makedirs(os.path.join(dup, d))
        Image.new("RGB", (32, 18)).save(os.path.join(dup, d, "004.jpg"))
    m7 = prj.project_media({"media_dir": dup})
    es7, r7, p7 = entries("0 - 2 | Numbered_Images/004.jpg", m7, {"root": dup, "roots": {}})
    assert not r7 and es7[0]["kind"] == "ambiguous", p7
    only_ref = os.path.join(tmp, "images4")
    os.makedirs(os.path.join(only_ref, "Character_References"))
    Image.new("RGB", (32, 18)).save(os.path.join(only_ref, "Character_References", "004.jpg"))
    es7b, r7b, _ = entries("0 - 2 | Numbered_Images/004.jpg", prj.project_media({"media_dir": only_ref}),
                           {"root": only_ref, "roots": {}})
    assert not r7b and es7b[0]["kind"] == "not_found" and "reference folder" in es7b[0]["errors"][0]
    # 11. no image on the line ≠ a missing file
    es11, _, _ = entries("0:00 - 0:02 |\n0:02 - 0:04 | 099.jpg", media, ctx)
    assert [x["kind"] for x in es11] == ["no_asset", "not_found"], [x.get("kind") for x in es11]
    es11b, _, p11 = entries("0 - 2 | Elsewhere/777.jpg", media, ctx)
    assert es11b[0]["kind"] == "unmapped" and "Map folder" in es11b[0]["errors"][0]
    print("  7, 11: a reference picture or one of two scene duplicates is never chosen by name; 'no image on the "
          "line', 'not in the images folder' and 'folder can't be found' are reported differently")

    # a pasted guide: a table that only DESCRIBES images (no times) gives notes, not 'needs a start and end' errors;
    # one ``` block of timed lines is used and the rest reported; two different timed blocks = nothing applied
    guide = ("# My video guide\n\n| Image | Description |\n|---|---|\n| 004.jpg | Plaque close-up |\n\n"
             "Some prose about the story.\n\n```text\n0:00.000 - 0:02.000 | Numbered_Images/001.jpg\n"
             "0:02.000 - 0:04.000 | Numbered_Images/004.jpg\n```\n")
    esg, rg, pg = entries(guide, media, ctx, 4.0)
    assert len(rg) == 2 and not [x for x in pg if x["level"] == "error"], pg
    assert any("using the timed list in the ``` block" in x["msg"] for x in pg)
    esg2, rg2, pg2 = entries("```text\n0:00 - 0:04 | 002.jpg\n0:04 - 0:09 | 003.jpg\n```\n" + guide, media, ctx, 4.0)
    assert not rg2 and "separate timed lists" in pg2[0]["msg"]
    desc = "| Image | Description |\n|---|---|\n| 004.jpg | Plaque close-up |\n0:00 - 0:04 | 004.jpg\n"
    esd, rd, pd = entries(desc, media, ctx, 4.0)
    assert len(rd) == 1 and rd[0]["notes"] == "Plaque close-up" and not [x for x in pd if x["level"] == "error"]
    print("  guide: an image-description table becomes notes (no false 'needs a start and an end time'); one timed "
          "``` block is used and the rest reported; two timed blocks → nothing applied")

    # 12. the complete 96-row fixture
    plan = open(FIXTURE, encoding="utf-8").read()
    es12, rows12, p12 = entries(plan, media, ctx, AUDIO_S)
    assert len(es12) == 96 and len(rows12) == 96 and not [x for x in p12 if x["level"] == "error"], p12[:3]
    warn = [x["msg"] for x in p12 if x["level"] == "warning"]
    assert len(warn) == 1 and "“Numbered_Images/” isn't a folder" in warn[0] and "96 row(s)" in warn[0], warn
    want = [ln.split("|")[1].strip().split("/")[-1] for ln in plan.splitlines()]
    assert [os.path.basename(r["media"]) for r in rows12] == want
    assert want[36:38] == ["038.jpg", "037.jpg"] and want[42] == "039.jpg"           # reordered beats kept
    # 6. with the folder mapped there is nothing to warn about
    es6, rows6, p6 = entries(plan, media, {"root": root, "roots": {"Numbered_Images": root}}, AUDIO_S)
    assert len(rows6) == 96 and not p6, p6[:2]
    alt = os.path.join(tmp, "elsewhere_scene_images")         # images kept in another folder → mapped there
    os.makedirs(alt)
    for i in range(1, 97):
        os.link(os.path.join(root, f"{i:03d}.jpg"), os.path.join(alt, f"{i:03d}.jpg"))
    m6 = media + prj.project_media({"media_dir": alt})
    es6b, rows6b, _ = entries(plan, m6, {"root": root, "roots": {"Numbered_Images": alt}}, AUDIO_S)
    assert all(os.path.dirname(r["media"]) == alt for r in rows6b) and len(rows6b) == 96
    print("  6, 12: the 96-row plan resolves all 96 rows in order (038 before 037, 039 after 043), one short note "
          "about the folder name; mapping 'Numbered_Images' to a folder (any folder) resolves all rows there")

    # 9 + 10 + 8: apply through the project, a broken plan keeps the timeline, save/reload keeps the assignment
    p = project(tmp, root)
    p["timed_plan"] = plan
    prj.replan(p, log=lambda m: None)
    sc = p["scenes"]
    assert len(sc) == 96 and [os.path.basename(s["media"]) for s in sc] == want
    assert all(s["media"] == os.path.normpath(s["media"]) for s in sc)          # the form a manual choice stores
    four = sc[3]
    assert os.path.basename(four["media"]) == "004.jpg" and "Character_References" not in four["media"]
    assert (four["start"], four["end"]) == (I.snap(11.24, FPS), I.snap(18.5, FPS)), (four["start"], four["end"])
    assert all(a["end"] == b["start"] for a, b in zip(sc, sc[1:]))               # 10: shared boundaries, no gaps
    assert all(abs(x * FPS - round(x * FPS)) < 1e-3 for s_ in sc for x in (s_["start"],)), "cuts on whole frames"
    assert sc[-1]["end"] == AUDIO_S and any("635.611" in m and "ms" in m for m in p["plan_report"])
    assert not [i for i in I.validate(p) if i["level"] == "fatal"]
    from autoedit.render import preflight
    preflight(p, log=lambda m: None)                                             # export validation passes
    before = [(s["start"], s["media"]) for s in sc]
    p["timed_plan"] = plan.replace("Numbered_Images/050.jpg", "Numbered_Images/950.jpg")
    prj.replan(p, log=lambda m: None)
    assert [(s["start"], s["media"]) for s in p["scenes"]] == before                # 9: timeline kept
    assert p["plan_report"][0].startswith("Editing plan NOT applied")
    p["timed_plan"] = plan
    prj.replan(p, log=lambda m: None)
    path = os.path.join(tmp, "plan96.autoedit.json")
    prj.save(p, path)
    q = prj.load(path)
    assert [(s["start"], s["end"], s["media"]) for s in q["scenes"]] == [(s["start"], s["end"], s["media"]) for s in sc]
    import app as A                                                              # 8: same field as a manual choice
    manual = {"start": 0, "end": 1, "status": "assigned", "motion": "zoom_in"}
    A.MainWindow._set_visual(manual, os.path.join(root, "004.jpg"))
    assert manual["media"] == four["media"]
    print("  8-10: Apply → 96 scenes with their pictures; 004 = the scene picture at 11.233-18.500 s (frame-exact "
          "11.240); every cut shared and frame-aligned; the last scene ends with the narration (635.611429 s); a "
          "broken plan leaves the timeline as it was; save/reopen keeps every picture; same field as a manual choice")

    # preview shows the planned picture (not the reference 004) - preview and export draw the same timeline
    from autoedit.engine import Compositor
    comp = Compositor(q, 320, 180, FPS, preview=True, captions=False)
    for k, t in ((4, 15.0), (38, (sc[36]["start"] + sc[36]["end"]) / 2), (37, (sc[37]["start"] + sc[37]["end"]) / 2)):
        got = comp.frame_at(t)[60:120, 100:220].reshape(-1, 3).mean(axis=0)
        assert np.abs(got - np.array(tint(k))).max() < 25, (k, t, got, tint(k))
    comp.close()
    print("  preview: 004 at 15 s, then 038 and 037 in the planned order - the planned pictures' colours")
    window(tmp, root, plan)
    print("PLAN ASSETS OK")


def window(tmp, root, plan):
    """The Editing plan window: paste, Apply, banner, compact summary, Map folder, Choose image."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.setOrganizationName("AEAssetsTest")
    QApplication.setApplicationName("AEAssetsTest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    import app as A
    A.ask_unsaved = lambda *a, **k: "discard"
    shown = []
    QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    w = A.MainWindow()
    w.resize(1366, 768)
    w.show()
    w.open_instructions = lambda *a, **k: None
    p = project(tmp, root)
    w.project = p
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    w.tools.setCurrentIndex([b.text() for b in w.tools.buttons].index("Editing Plan"))
    app.processEvents()
    panel = w.instr_panel.plan_panel
    broken = plan.replace(" | Numbered_Images/012.jpg", " |").replace("Numbered_Images/030.jpg", "Pics/x030.jpg")
    panel._set_text(broken, "Paste plan")
    assert not panel.apply()
    assert len(shown) == 1 and shown[0].count("\n") < 8, shown            # ONE compact message, not 96 lines
    st = panel.status.text()
    assert "96 timed row(s); 94 image(s) assigned" in st and "1 row(s) without an image" in st and \
        "can't be found in your images folder" in st and "gap" not in st, st
    assert panel.table.item(panel.table.currentRow(), 6).text().startswith("✖")
    assert w.plan_banner.isVisibleTo(w)
    # Choose image… writes the picture into that plan line (in place)
    r = next(i for i, e in enumerate(panel.entries) if e.get("kind") == "unmapped")
    panel.table.setCurrentCell(r, 0)
    assert panel.b_choose.isVisibleTo(w) and panel.b_map.isVisibleTo(w)
    panel._edit(r, "image", "030.jpg")
    assert w.project["timed_plan"].splitlines()[r] == "2:46.920 - 2:51.140 | 030.jpg"      # only that line changed
    assert len(w.project["timed_plan"].splitlines()) == 96 and w.project["timed_plan"].count("|") == 96
    panel._set_text(plan, "Paste plan")
    panel.set_folder_mapping("Numbered_Images", root)                     # Map folder… (one choice, all rows)
    assert w.project["settings"]["plan_roots"] == {"Numbered_Images": os.path.normpath(root)}
    assert prj.DEFAULT_SETTINGS["plan_roots"] == {}, "the mapping must belong to this project only"
    assert panel.apply()
    assert len(w.project["scenes"]) == 96 and not w.plan_banner.isVisibleTo(w)
    assert all(panel.table.item(i, 6).text() == "✓ On the timeline" for i in range(96))
    assert "96 image(s) assigned" in panel.status.text() and "Ready to apply" in panel.status.text()
    path = os.path.join(tmp, "win.autoedit.json")
    w.project_path = path
    prj.save(w.project, path)
    w2 = prj.load(path)
    assert w2["settings"]["plan_roots"] == {"Numbered_Images": os.path.normpath(root)}
    assert [s["media"] for s in w2["scenes"]] == [s["media"] for s in w.project["scenes"]]
    if os.environ.get("AE_SHOTS"):
        os.makedirs(os.environ["AE_SHOTS"], exist_ok=True)
        panel._set_text(broken, "Paste plan")
        panel.validate(quiet=True)
        panel.show_first_problem()
        app.processEvents()
        w.grab().save(os.path.join(os.environ["AE_SHOTS"], "plan_assets_broken.png"))
    print("  window: a broken paste → one short message, '96 timed rows; 94 images assigned' with the problems "
          "grouped, no false gap, broken row selected, red banner; Choose image fixes the line in place; Map folder "
          "is stored in the project; Apply → 96 scenes on the timeline, banner gone; save/reopen keeps it all")


if __name__ == "__main__":
    main()
