"""Editing Instructions window + Project guidelines (GUI, offscreen): parse → choose → apply, structured
controls, locks, B-roll / SFX lists, import preview, undo/redo, save/reload.

    python -m tests.test_instructions_gui        (AE_SHOTS=folder saves screenshots)
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoedit import instructions as I  # noqa: E402
from autoedit import project as prj  # noqa: E402
from tests.test_edit_instructions import build  # noqa: E402


def shot(w, name):
    d = os.environ.get("AE_SHOTS")
    if d:
        os.makedirs(d, exist_ok=True)
        w.grab().save(os.path.join(d, name + ".png"))


def main():
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.setOrganizationName("AEInsTest")
    QApplication.setApplicationName("AEInsTest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    import app as A
    from autoedit.theme import apply_theme
    apply_theme(app)
    A.ask_unsaved = lambda *a, **k: "discard"
    tmp = tempfile.mkdtemp()
    p = build(tmp)
    w = A.MainWindow()
    w.resize(1500, 900)
    w.show()
    w.project = p
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    app.processEvents()
    w.a_instr.trigger()
    app.processEvents()
    iw = w.ins_win
    assert iw.isVisible() and iw.table.rowCount() == len(p["scenes"])
    # ---- natural language: parse, see interpretation, resolve the duplicate file name, apply
    iw.table.selectRow(1)
    app.processEvents()
    sid = iw.sid
    assert sid == p["scenes"][1]["id"] and w.sel == 1
    iw.text.setPlainText("Use 003.png when the narrator says “She opened the letter.” Hold it until “She knew what "
                         "to do next.” Slowly zoom in. Play paper.wav at “opened,” quietly below the narration. "
                         "Make it fly. Cut to the next scene.")
    iw.parse_current()
    rows = [(iw.interp.item(r, 0).text(), iw.interp.item(r, 1).text()) for r in range(iw.interp.rowCount())]
    assert any("NOT understood" in m and "fly" in c for c, m in rows), rows
    assert iw.choice_lay.count() == 1, "duplicate 003.png needs a choice"
    shot(iw, "instr_parsed")
    cb = iw.choice_lay.itemAt(0).widget().findChildren(type(iw.vis))[0]
    k = next(i for i in range(cb.count()) if cb.itemData(i) is not None and "alt" not in cb.itemText(i))
    cb.setCurrentIndex(k)
    cb.activated.emit(k)                     # auto-apply
    app.processEvents()
    sc = next(s for s in w.project["scenes"] if s["id"] == sid)
    assert sc["media"].endswith(os.path.join("media", "003.png")) and sc["motion"] == "zoom_in"
    rec = I.records(w.project)[sid]
    assert rec["applied"] and rec["status"] == "unresolved" and "Make it fly" in rec["unresolved"][0]
    assert any(e.get("source") == sid for e in w.project["sfx_events"])
    print("  parse → 'Make it fly' listed as NOT understood; duplicate 003.png chosen in the panel; applied: "
          "visual, times, zoom and paper.wav on the timeline")
    # undo / redo
    w.do_undo()
    assert not any(e.get("source") == sid for e in w.project["sfx_events"])
    w.do_redo()
    assert any(e.get("source") == sid for e in w.project["sfx_events"])
    # ---- structured controls (auto-apply): motion None, framing, transition, captions
    iw.tabs.setCurrentIndex(2)
    iw.mmode.setCurrentIndex(iw.mmode.findData("none"))
    iw.mmode.activated.emit(iw.mmode.currentIndex())
    sc = next(s for s in w.project["scenes"] if s["id"] == sid)
    assert sc["motion"] == "none"
    iw.tabs.setCurrentIndex(6)
    iw.tr_mode.setCurrentIndex(iw.tr_mode.findData("explicit"))
    iw.tr_type.setCurrentIndex(iw.tr_type.findData("crossfade"))
    iw.tr_mode.activated.emit(iw.tr_mode.currentIndex())
    iw.tr_dur.setValue(0.8)
    sc = next(s for s in w.project["scenes"] if s["id"] == sid)
    assert sc["transition"] == "crossfade" and sc["transition_s"] == 0.8
    assert "overlap" in iw.tr_note.text() and "frames" in iw.tr_note.text(), iw.tr_note.text()
    iw.cap_mode.setCurrentIndex(iw.cap_mode.findData("none"))
    iw.cap_mode.activated.emit(iw.cap_mode.currentIndex())
    assert next(s for s in w.project["scenes"] if s["id"] == sid)["captions"] == "off"
    shot(iw, "instr_transition")
    # ---- B-roll + SFX lists
    iw.tabs.setCurrentIndex(3)
    iw._list_add("b_roll")
    rec = I.records(w.project)[sid]
    assert rec["settings"]["b_roll_mode"] == "explicit" and len(rec["settings"]["b_roll"]) == 1
    iw.b_anchor.setText("airport")
    iw.b_anchor.editingFinished.emit()
    br = [b for b in w.project["broll"] if b.get("source") == sid]
    assert len(br) == 1 and br[0]["mode"] == "insert", (br, I.records(w.project)[sid]["issues"])
    iw.b_kind.setCurrentIndex(1)
    iw.b_kind.activated.emit(1)
    assert [b for b in w.project["broll"] if b.get("source") == sid][0]["mode"] == "overlay"
    shot(iw, "instr_broll")
    iw.tabs.setCurrentIndex(4)
    iw.slist.setCurrentRow(0)
    iw.s_gain.setValue(-20)
    assert [e for e in w.project["sfx_events"] if e.get("source") == sid][0]["rel_db"] == -20
    print("  structured controls: motion None, crossfade 0.8 s (overlap shown in frames), captions off, a B-roll "
          "insert anchored on “airport” then switched to overlay, SFX level -20 dB - each applied + undoable")
    # ---- field lock from the panel survives re-planning
    iw.tabs.setCurrentIndex(7)
    k0 = next(i for i, s in enumerate(w.project["scenes"]) if s["id"] != sid and s.get("media", "").endswith(".png"))
    iw.sid = w.project["scenes"][k0]["id"]
    iw.load()
    media0 = w.project["scenes"][k0]["media"]
    iw.lock_cb["primary_visual"].setChecked(True)
    w.replan(label_text="Re-plan")
    s0 = next(s for s in w.project["scenes"] if s["id"] == iw.sid)
    assert s0["media"] == media0 and "primary_visual" in s0["locked_fields"]
    print("  locking a scene's visual in the panel keeps it through re-planning")
    # ---- import preview + validation
    jp = os.path.join(tmp, "imp.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "scenes": [{"scene_id": sid, "motion": {"mode": "auto"},
                                                    "effects_mode": "explicit",
                                                    "effects": [{"type": "vignette", "params": {"amount": 0.6},
                                                                 "start_offset": 0, "end_offset": None}]}]}, f)
    assert iw.import_file(jp, confirm=False)
    done, skipped = iw.apply_valid()
    sc = next(s for s in w.project["scenes"] if s["id"] == sid)
    assert sc["effects"] and sc["effects"][0]["type"] == "vignette"
    v = I.validate(w.project)
    assert not [i for i in v if i["level"] == "fatal"], v
    # ---- guidelines: style preview + apply, then save/reload keeps everything
    gb = w.instr_panel.guide
    gb.style.setCurrentIndex(gb.style.findData("documentary"))
    assert "Transition" in gb.style_note.text() and gb.b_style.isEnabled()
    gb._apply_style()
    st = w.project["settings"]
    assert st["style"] == "documentary" and st["transition_default"] == "crossfade" and st["sfx_mode"] == "explicit_only"
    w.do_undo()
    assert w.project["settings"]["style"] != "documentary"
    w.do_redo()
    path = os.path.join(tmp, "p.json")
    prj.save(w.project, path)
    q = prj.load(path)
    assert q["edit_instructions"][sid]["settings"]["effects_mode"] == "explicit"
    assert q["settings"]["transition_default"] == "crossfade"
    shot(w, "instr_main_window")
    print("  JSON import previewed + applied (vignette), validation clean; Documentary style preview → apply → "
          "undo/redo; everything survives save + reload")
    print("INSTRUCTIONS GUI OK")


if __name__ == "__main__":
    main()
