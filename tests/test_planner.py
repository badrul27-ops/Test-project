"""v7 smart planning - acceptance checks 1 and 2 (logic level).

  1. a script with three clearly different actions + described assets -> each action gets the visual that
     shows it, a part with no suitable visual is FLAGGED (not silently filled), renaming files changes nothing.
  2. locked scenes survive re-analysis and save/reload.
  + script-vs-recording check, project / scene instructions, scene-map import validation, estimated
    alignment fallback is labelled.

    python -m tests.test_planner
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from autoedit import planner as P  # noqa: E402
from autoedit import project as prj  # noqa: E402
from autoedit.asset_info import asset_id, import_prompts  # noqa: E402

SCRIPT = """A fisherman pulls his heavy net from the cold river at dawn. The net is full of silver fish and he smiles.

Later the village market fills with traders. Women sell bread, spices and baskets while children run between the stalls.

At night a violent storm hits the harbor. Waves smash the wooden boats against the rocks and the wind tears the sails.

Years later an old lighthouse keeper writes a letter by candlelight. He folds the letter and seals it with wax."""

PROMPTS = [  # deliberately NOT in story order, file names say nothing
    {"asset_filename": "01.png", "prompt": "violent storm at night over a harbor, waves smashing wooden boats on rocks, torn sails"},
    {"asset_filename": "02.png", "prompt": "busy village market with traders selling bread and spices, baskets, children running between stalls"},
    {"asset_filename": "03.png", "prompt": "fisherman pulling a heavy net full of silver fish from a cold river at dawn"},
]


def words_for(text, t0=0.3):
    out, t = [], t0
    for w in text.split():
        d = 0.16 + 0.03 * len(w)
        out.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
        t += d + (0.6 if w.endswith(".") else 0.07)
    return out, round(t + 0.3, 3)


def make_media(folder, n=3, size=(640, 360)):
    os.makedirs(folder, exist_ok=True)
    for i in range(1, n + 1):
        img = Image.effect_noise(size, 40 + 20 * i).convert("RGB")
        img.save(os.path.join(folder, f"{i:02d}.png"))


def scene_for(scenes, phrase, words):
    i = P.find_phrase(words, phrase)[0]
    t = (words[i]["s"] + words[i]["e"]) / 2
    return next(s for s in scenes if s["start"] <= t < s["end"])


def base_project(tmp, media):
    words, dur = words_for(P.C.strip_markers(SCRIPT).replace("\n", " "))
    p = prj.new_project()
    p.update(name="plan", media_dir=media, words=words, duration=dur, script_text=SCRIPT)
    p["settings"]["scene_mode"] = "smart"
    return p, words, dur


def main():
    tmp = tempfile.mkdtemp()
    media = os.path.join(tmp, "media")
    make_media(media)
    p, words, dur = base_project(tmp, media)
    assert prj.new_project()["settings"]["scene_mode"] == "timed"     # v7.2 default: the user's timed plan
    csvp = os.path.join(tmp, "prompts.csv")
    with open(csvp, "w", encoding="utf-8") as f:
        f.write("asset_filename,prompt\n" + "\n".join(f'{r["asset_filename"]},"{r["prompt"]}"' for r in PROMPTS))
    n, probs = import_prompts(p, csvp, prj.project_media(p))
    assert n == 3 and not probs, (n, probs)

    # ---------------- check 1: meaning, not file order; missing visual flagged
    prj.replan(p, log=lambda m: None)
    sc = p["scenes"]
    get = lambda ph: scene_for(sc, ph, words)   # noqa: E731
    assert os.path.basename(get("fisherman pulls")["media"]) == "03.png", [(s["start"], s["media"]) for s in sc]
    assert os.path.basename(get("market fills")["media"]) == "02.png"
    assert os.path.basename(get("violent storm")["media"]) == "01.png"
    letter = get("lighthouse keeper writes")
    assert letter["status"] == "missing" and not letter["media"], letter
    assert "no visual" in letter["warnings"]
    assert all(s["id"].startswith("S") for s in sc) and len({s["id"] for s in sc}) == len(sc)
    assert sc[0]["start"] == 0 and sc[-1]["end"] == dur and all(a["end"] == b["start"] for a, b in zip(sc, sc[1:]))
    assert all(s["why"] for s in sc if s["status"] == "matched")
    print(f"  check 1: {len(sc)} scenes cut at topics; fisherman/market/storm each got the visual that shows it "
          f"(files 03/02/01 - not file order); the letter part is flagged 'missing' instead of reusing a picture")
    print("           why:", get("violent storm")["why"])

    # rename the files: same result (descriptions are attached to content IDs, not names)
    ids_before = {os.path.basename(s["media"]): s["start"] for s in sc if s["media"]}
    ren = os.path.join(tmp, "renamed")
    shutil.copytree(media, ren)
    for a, b in (("01.png", "zz_last.png"), ("02.png", "aa_first.png"), ("03.png", "mm_mid.png")):
        os.rename(os.path.join(ren, a), os.path.join(ren, b))
    q, _, _ = base_project(tmp, ren)
    q["asset_meta"] = p["asset_meta"]
    prj.replan(q, log=lambda m: None)
    m2 = {s["start"]: os.path.basename(s["media"]) for s in q["scenes"] if s["media"]}
    mapping = {"01.png": "zz_last.png", "02.png": "aa_first.png", "03.png": "mm_mid.png"}
    assert all(m2[t] == mapping[n] for n, t in ids_before.items()), (m2, ids_before)
    print("  check 1b: after renaming every file the same pictures land on the same narration")

    # no descriptions at all -> honest file-order fallback, labelled per scene
    r = prj.new_project()
    r["settings"]["scene_mode"] = "smart"
    r.update(media_dir=media, words=words, duration=dur, script_text=SCRIPT)
    prj.replan(r, log=lambda m: None)
    assert all(s["status"] == "order" for s in r["scenes"]), [s["status"] for s in r["scenes"]]
    assert "file order" in r["plan_report"][0]
    print("  without descriptions every scene is labelled 'file order' (no fake meaning match)")

    # ---------------- check 2: locks survive re-analysis and reload
    lock_i = next(i for i, s in enumerate(sc) if s["media"].endswith("02.png"))
    p["scenes"][lock_i].update(locked=True, media=os.path.join(media, "01.png"), zoom=1.3, transition="crossfade")
    locked_before = dict(p["scenes"][lock_i])
    p["project_instructions"] = ""
    p["settings"]["project_instructions"] = "no camera motion\ncuts only"
    prj.replan(p, log=lambda m: None)
    L = [s for s in p["scenes"] if s.get("locked")]
    assert len(L) == 1
    for k in ("start", "end", "media", "zoom", "transition", "id"):
        assert L[0][k] == locked_before[k], (k, L[0][k], locked_before[k])
    assert all(s["transition"] == "cut" for s in p["scenes"] if not s.get("locked"))
    assert all(s["motion"] == "none" for s in p["scenes"] if not s.get("locked") and s["media"])
    # 01.png is used by the locked scene -> not offered again to the storm part
    assert sum(1 for s in p["scenes"] if s["media"].endswith("01.png")) == 1
    path = os.path.join(tmp, "p.json")
    prj.save(p, path)
    p2 = prj.load(path)
    prj.replan(p2, log=lambda m: None)
    L2 = [s for s in p2["scenes"] if s.get("locked")]
    assert len(L2) == 1 and all(L2[0][k] == locked_before[k] for k in ("start", "end", "media", "zoom", "id"))
    print("  check 2: a locked scene keeps its visual, times, zoom, transition and ID through re-planning "
          "(with new project instructions) and save + reload; its picture isn't reused elsewhere")

    # ---------------- script vs recording
    spoken = words_for("A fisherman pulls his net from the river at dawn. The net is full of fish and he smiles. "
                       "Later the village market fills with traders and merchants.")[0]
    issues = P.script_check("A fisherman pulls his heavy net from the cold river at dawn. The net is full of "
                            "silver fish and he smiles. Later the village market fills with traders.", spoken)
    kinds = sorted(i["kind"] for i in issues)
    assert kinds.count("missing") >= 1 and "extra" in kinds, issues
    assert P.script_check("In 1910 they met.", words_for("In nineteen ten they met.")[0]) == []
    print(f"  script vs recording: {P.check_summary(issues)} (e.g. {issues[0]})")

    # ---------------- scene instructions (natural language)
    s = prj.new_project()
    s["settings"]["scene_mode"] = "smart"
    s.update(media_dir=media, words=words, duration=dur, script_text=SCRIPT,
             directions="Use image 003 while the narrator discusses the lighthouse keeper; keep it until the next "
                        "topic.\nUse 1 from 0:02 to 0:05")
    prj.replan(s, log=lambda m: None)
    lk = scene_for(s["scenes"], "lighthouse keeper writes", words)
    assert lk["media"].endswith("03.png") and lk["status"] == "assigned" and lk["end"] == dur, lk
    first = [x for x in s["scenes"] if x["media"].endswith("01.png")]
    assert len(first) == 1 and abs(first[0]["start"] - 2) < 0.01 and abs(first[0]["end"] - 5) < 0.01, first
    ex, iss = P.parse_scene_instructions("Make it spooky\nUse image 9 when the narrator says hello", words,
                                         prj.project_media(s))
    assert any("Not understood" in i for i in iss) and any("9" in i for i in iss), iss
    print("  scene instructions: 'Use image 003 while the narrator discusses …; keep it until the next topic' and "
          "'Use 1 from 0:02 to 0:05' applied; unknown lines/images reported")
    ov, lines = P.parse_project_instructions("Keep the images in chronological order\nscenes of 3-8 seconds\n"
                                             "make it look like Netflix")
    assert ov == {"order": True, "min_len": 3.0, "max_len": 8.0} and lines[2][1] is False, (ov, lines)

    # ---------------- scene map import (CSV) with validation
    mp = os.path.join(tmp, "map.csv")
    with open(mp, "w", encoding="utf-8") as f:
        f.write("scene_id,asset_filename,script_excerpt,start_seconds,end_seconds,fit_mode,motion,locked\n"
                "A1,02.png,,0,4,fit,zoom_in,yes\n"
                "A1,03.png,,5,6,,,\n"                       # duplicate id
                "A2,nothere.png,,6,7,,,\n"                  # missing file
                "A3,03.png,,9,8,,,\n"                       # bad times
                "A4,01.png,a violent storm hits the harbor,,,stretch,,\n")   # unknown fit -> default
    rows, probs = P.load_scene_map(mp, prj.project_media(s), dur)
    txt = " | ".join(probs)
    assert "duplicate scene_id" in txt and "nothere.png" in txt and "end_seconds must be after" in txt \
        and "unknown fit_mode" in txt, txt
    t = prj.new_project()
    t["settings"]["scene_mode"] = "smart"
    t.update(media_dir=media, words=words, duration=dur, script_text=SCRIPT, scene_map=rows)
    prj.replan(t, log=lambda m: None)
    a1 = t["scenes"][0]
    assert a1["media"].endswith("02.png") and a1["fit"] == "fit_blur" and a1["locked"] and a1["end"] == 4
    storm = scene_for(t["scenes"], "violent storm", words)
    assert storm["media"].endswith("01.png") and storm["status"] == "assigned", [(x["start"], x["end"], os.path.basename(x["media"]), x["status"]) for x in t["scenes"]]
    assert any("nothere.png" in r for r in t["plan_report"])
    assert rows[0]["asset_id"] == asset_id(os.path.join(media, "02.png"))
    print(f"  scene map CSV: {len(rows)} usable rows; problems reported: {len(probs)} (duplicate ID, missing file, "
          "bad times, unknown fit); times, fit, lock and script excerpts applied")

    # ---------------- estimated alignment fallback is labelled
    est = prj.estimate_words("one two three four. five six", 10.0)
    assert len(est) == 6 and est[-1]["e"] <= 10.0
    print("PLANNER OK")


if __name__ == "__main__":
    main()
