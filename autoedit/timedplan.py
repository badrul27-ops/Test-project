"""Timed edit plan: YOU say which image is on screen from which second to which second, plus how it
should look. AutoEditor follows it exactly - it never moves a cut or swaps a picture on its own.

One line per shot:

    0:00 - 0:04.5  | 001.png | slow zoom in, crossfade
    0:04.5 - 0:09  | 002     | pan right, sfx whoosh.wav, text "Morocco, 15,000 years ago"
    0:09 - 0:12    | 003.png |                       # anything after # is a comment

  * times: 4  4.5  4.5s  0:04  0:04.5  1:02:03  (start - end, or start to end)
  * image: a file name (003.png) or its number (3, 003, image 3)
  * after the image (optional): how it looks - motion, transition to the next shot, sound effect, effect,
    text, framing, captions. Same phrases as Editing instructions, separated by commas. Shots without
    them follow the project guidelines.
  * the "|" separators are optional.

Rules (checked before anything is built, and again before export):
  * every line needs a time range and an image that exists;
  * shots may not overlap, and nothing may be left uncovered between 0:00 and the end of the narration
    (a gap would be a black/frozen picture) - the narration is never cut, sped up or stretched;
  * lines that aren't understood are listed with their line number.
"""
import copy
import os
import re

from . import cues as C
from . import instructions as I
from .util import is_video

TIME = r"(\d{1,2}:\d{1,2}(?::\d{1,2})?(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(?:s|sec|secs|seconds?)?"
RANGE = re.compile(r"^\s*\[?\s*" + TIME + r"\s*(?:-|–|—|to|until)\s*" + TIME + r"\s*\]?\s*[|:]?\s*(?P<rest>.*)$", re.I)
SHORTHAND = [
    (r"^(cross\s*-?fade|dissolve|crossfade)$", "dissolve into the next scene"),
    (r"^(cross\s*-?fade|dissolve)\s+" + TIME + r"$", None),
    (r"^(cut|hard cut|straight cut)$", "cut to the next scene"),
    (r"^(fade|fade to black|fade out|dip to black)$", "fade to black at the end"),
    (r"^(slide|whip|zoom transition)$", None),
    (r"^(still|static|no motion|no move|no movement|hold still)$", "no motion"),
    (r"^(b\s*&\s*w|b/w|bw|black\s*&\s*white)$", "make it black and white"),
    (r"^(fill|crop|full ?frame)$", "fill the frame"),
    (r"^(fit|whole image|whole picture)$", "show the whole image"),
    (r"^(no captions?|captions? off)$", "no captions"),
    (r"^(no sfx|no sound effects?)$", "no sound effects"),
    (r"^lock(ed)?$", "lock this scene"),
]


def parse_time(s):
    return C.parse_time((s or "").replace(",", "."))


def _split_extras(text):
    """Comma / semicolon separated phrases (commas inside quotes are kept)."""
    out, cur, q = [], "", False
    for ch in text:
        if ch in "\"“”":
            q = not q if ch == '"' else (ch == "“")
        if ch in ",;" and not q:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _phrase(x):
    """Short forms used in plans -> the sentences Editing instructions understands."""
    lx = x.lower().strip().rstrip(".")
    for pat, full in SHORTHAND:
        m = re.match(pat, lx, re.I)
        if m:
            if full:
                return full
            if lx.startswith(("crossfade", "cross fade", "cross-fade", "dissolve")):
                return f"dissolve into the next scene over {m.group(2)} seconds"
            if lx.startswith("slide"):
                return "slide transition to the next scene"
            if lx.startswith("whip"):
                return "whip transition to the next scene"
            return "zoom transition to the next scene"
    m = re.match(r"^(?:sfx|sound|sound effect)\s*:?\s*(?P<f>" + I.AUDIO_RE + r")(?P<rest>.*)$", x, re.I)
    if m:
        rest = m.group("rest").strip()
        if not re.search(r"\bat\b|“|\"", rest):
            rest = "at the start " + rest
        return f"play {m.group('f')} {rest}".strip()
    m = re.match(r"^text\s*:?\s*[\"“](.+)[\"”]$", x, re.I)
    if m:
        return f"show the text “{m.group(1)}”"
    return x


def _resolve_image(ref, media, index, n, problems, what="line"):
    """Exact file for a reference, or None (with the reason in problems). Never guesses between files."""
    where = f"{what} {n}"
    base = os.path.basename(ref.replace("\\", "/")).lower()
    same = [m for m in media if os.path.basename(m).lower() == base] if "." in base else []
    if len(same) > 1:
        want = ref.replace("\\", "/").lower()
        pick = [m for m in same if m.replace("\\", "/").lower().endswith("/" + want)] if "/" in want else []
        if len(pick) != 1:
            folders = ", ".join(sorted({os.path.basename(os.path.dirname(m)) for m in same}))
            problems.append({"line": n, "level": "error",
                             "msg": f"{where}: {len(same)} files are named {os.path.basename(ref)} (in {folders}) - "
                                    f"write the folder too, e.g. {os.path.basename(os.path.dirname(same[-1]))}/"
                                    f"{os.path.basename(ref)}"})
            return None
        return pick[0]
    paths, bad = C.resolve(ref, media, index)
    if any("files have this number" in b_ for b_ in bad):
        problems.append({"line": n, "level": "error",
                         "msg": f"{where}: " + next(b_ for b_ in bad if "files have this number" in b_)
                                .split(" - used")[0] + " - write the file name instead"})
        return None
    if not paths:
        problems.append({"line": n, "level": "error",
                         "msg": f"{where}: {ref} is missing - it isn't in the images folder. Select a replacement."})
        return None
    if len(paths) > 1:
        problems.append({"line": n, "level": "error",
                         "msg": f"{where}: '{ref}' means {len(paths)} files - give one file name"})
        return None
    for b_ in bad:
        problems.append({"line": n, "level": "warning", "msg": f"{where}: {b_}"})
    return paths[0]


def _lab(r):
    return r.get("label") or f"line {r['line']}"


def _coverage(rows, duration, problems, what="line"):
    rows.sort(key=lambda r: r["start"])
    for x, y in zip(rows, rows[1:]):
        if y["start"] < x["end"] - 1e-6:
            problems.append({"line": y["line"], "level": "error",
                             "msg": f"{_lab(y)} starts at {_f(y['start'])} but {_lab(x)} runs until "
                                    f"{_f(x['end'])} - shots can't overlap"})
        elif y["start"] > x["end"] + 1e-6:
            problems.append({"line": y["line"], "level": "error",
                             "msg": f"nothing is assigned from {_f(x['end'])} to {_f(y['start'])} (between {_lab(x)} "
                                    f"and {_lab(y)})"})
    if rows and rows[0]["start"] > 1e-6:
        problems.append({"line": rows[0]["line"], "level": "error",
                         "msg": f"nothing is assigned from 0:00 to {_f(rows[0]['start'])} - the first shot must start "
                                "at 0:00"})
    if rows and duration:
        end = rows[-1]["end"]
        if end < duration - 0.5:
            problems.append({"line": rows[-1]["line"], "level": "error",
                             "msg": f"the narration continues until {_f(duration)} but the plan ends at {_f(end)} - "
                                    "add shots for the rest"})
        elif end > duration + 0.5:
            problems.append({"line": rows[-1]["line"], "level": "error",
                             "msg": f"the plan runs until {_f(end)} but the narration ends at {_f(duration)} - the "
                                    "narration is never extended; shorten the last shot"})


def parse_plan(text, media, duration=None, fps=30, words=None):
    """-> (rows, problems). rows: [{'line', 'start', 'end', 'ref', 'media', 'extras', 'text'}];
    problems: [{'line', 'level': error|warning, 'msg'}]. Nothing is changed.
    Two formats: one line per shot, or a scene sheet (SCENE 001 / Image: / Start: / End: … blocks)."""
    if is_sheet(text):
        return parse_sheet(text, media, duration, fps, words)
    index = C.media_index(media)
    rows, problems = [], []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("//"):
            continue
        m = RANGE.match(line)
        if not m:
            problems.append({"line": n, "level": "error",
                             "msg": f"line {n}: needs a time range first, e.g. '0:04 - 0:09 | 002.png' - got “{line[:60]}”"})
            continue
        a, b = parse_time(m.group(1)), parse_time(m.group(2))
        if a is None or b is None:
            problems.append({"line": n, "level": "error", "msg": f"line {n}: can't read the times"})
            continue
        if b <= a:
            problems.append({"line": n, "level": "error",
                             "msg": f"line {n}: ends ({_f(b)}) before or when it starts ({_f(a)})"})
            continue
        rest = m.group("rest").strip()
        parts = [p.strip() for p in rest.split("|")] if "|" in rest else None
        if parts is not None:
            ref, extras = parts[0], ", ".join(p for p in parts[1:] if p)
        else:
            mm = re.match(r"^(?:image|img|picture|photo|clip|file)?\s*#?\s*(" + I.FILE_RE + r"|\d+|\?+)\s*,?\s*(.*)$",
                          rest, re.I)
            ref, extras = (mm.group(1), mm.group(2)) if mm else ("", rest)
        ref = re.sub(r"^(?:image|img|picture|photo|clip|file)\s*#?\s*", "", ref.strip(), flags=re.I)
        if not ref or set(ref) <= {"?"}:
            problems.append({"line": n, "level": "error", "msg": f"line {n} ({_f(a)}-{_f(b)}): choose an image"})
            continue
        path = _resolve_image(ref, media, index, n, problems)
        if path is None:
            continue
        a, b = I.snap(a, fps), I.snap(b, fps)
        rows.append({"line": n, "start": a, "end": b, "ref": ref, "media": path, "extras": extras.strip()})
    _coverage(rows, duration, problems)
    # extras: every phrase must be understood (listed otherwise, never silently ignored)
    for r in rows:
        if not r["extras"]:
            r["text"] = ""
            continue
        phrases = [_phrase(x) for x in _split_extras(r["extras"])]
        r["text"] = ". ".join(p.rstrip(".") for p in phrases) + "."
        _, interp, unresolved, _ = I.parse(r["text"])
        for u in unresolved:
            problems.append({"line": r["line"], "level": "warning",
                             "msg": f"line {r['line']}: “{u}” not understood - ignored"})
        if any(m_.startswith("visual ") for _, m_ in interp):
            problems.append({"line": r["line"], "level": "warning",
                             "msg": f"line {r['line']}: put one image per line (extra images on a line are ignored)"})
    if not rows and not problems:
        problems.append({"line": 0, "level": "error", "msg": "The timed edit plan is empty."})
    return rows, problems


def _f(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:05.2f}"


def template(words, duration, min_len=1.5, max_len=9.0):
    """A plan skeleton from the narration: one line per sentence (short ones merged, long ones split at the
    longest pause), times taken from the recording, image left as ??? for you to fill in."""
    from .planner import sentences
    if not words:
        return ""
    segs = []
    for a, b in sentences(words):
        if segs and words[segs[-1][1]]["e"] - words[segs[-1][0]]["s"] < min_len:
            segs[-1] = (segs[-1][0], b)
        else:
            segs.append((a, b))
    out = []
    for a, b in segs:
        stack = [(a, b)]
        while stack:
            x, y = stack.pop(0)
            if words[y]["e"] - words[x]["s"] > max_len and y - x > 3:
                gaps = [(words[i + 1]["s"] - words[i]["e"], i) for i in range(x + 1, y - 1)]
                i = max(gaps)[1]
                stack[0:0] = [(x, i), (i + 1, y)]
            else:
                out.append((x, y))
    lines = ["# Timed edit plan - one line per shot:  start - end | image | how it looks (optional)",
             "# Replace ??? with the image for that part, e.g. 012.png or 12. Times come from your recording;",
             "# change them if you like, but shots must not overlap or leave gaps. Text after # is ignored.",
             ""]
    for k, (x, y) in enumerate(out):
        st = 0.0 if k == 0 else C.cut_time(words, x)
        en = duration if k == len(out) - 1 else C.cut_time(words, out[k + 1][0])
        txt = " ".join(w["w"] for w in words[x:y + 1])
        lines.append(f"{_f(st)} - {_f(en)} | ??? |   # {txt[:150]}")
    return "\n".join(lines) + "\n"


def from_csv(path):
    """CSV with start, end, image (and optional notes/look columns) -> plan text."""
    import csv
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        rows = list(csv.DictReader(f))
    lines = []
    for r in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        st = r.get("start") or r.get("start_seconds") or r.get("from")
        en = r.get("end") or r.get("end_seconds") or r.get("to")
        im = r.get("image") or r.get("asset_filename") or r.get("file") or "???"
        look = r.get("look") or r.get("extras") or r.get("instructions") or ""
        lines.append(f"{st} - {en} | {im} | {look}")
    return "\n".join(lines) + "\n"


def build(project, log=print):
    """Build the timeline exactly from the plan. Raises ValueError (with every problem) if the plan can't
    be followed - nothing is changed in that case."""
    from .planner import apply_guidelines
    from .presets import PRESETS
    from .project import project_media
    from .scenes import apply_motion_level, reassign_transitions, scene_text
    st = project["settings"]
    words, dur = project.get("words") or [], float(project.get("duration") or 0)
    fps = int(st.get("fps", 30))
    rows, problems = parse_plan(project.get("timed_plan") or "", project_media(project), dur, fps, words)
    errors = [p for p in problems if p["level"] == "error"]
    if errors:
        raise ValueError(f"The timed edit plan has {len(errors)} problem(s) - nothing was built:\n" +
                         "\n".join(p["msg"] for p in errors[:15]))
    fmt_changes = []
    for k_, v in (rows[0].get("project") or {}).items():       # GENERAL RULES: export format
        if st.get(k_) != v:
            fmt_changes.append(f"{k_} {st.get(k_)} → {v}")
            st[k_] = v
    if fmt_changes:
        fps = int(st.get("fps", 30))
        rows, problems = parse_plan(project.get("timed_plan") or "", project_media(project), dur, fps, words)
    rows[-1]["end"] = dur                       # within 0.5 s: the last shot ends with the narration
    preset = dict(PRESETS.get(st.get("preset"), PRESETS["Documentary"]))
    # previous plan-built records are replaced; your own Editing-instruction records for other scenes stay
    recs = I.records(project)
    for sid in [s for s, r in recs.items() if r.get("from_plan")]:
        old = recs.pop(sid)
        if old.get("notes") or old["settings"].get("locked_fields") or old["settings"].get("locked"):
            recs[sid] = old                    # keep your notes / locks for that shot number
            old["from_plan"] = True
    for k, r in enumerate(rows):
        r["sid"] = f"T{(r['line'] if r.get('settings') is not None else k + 1):03d}"
    scenes = []
    for k, r in enumerate(rows):
        sid = r["sid"]
        scenes.append({"id": sid, "start": r["start"], "end": r["end"], "media": r["media"],
                       "text": scene_text(words, r["start"], r["end"]), "zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0,
                       "motion": "none" if is_video(r["media"]) else "zoom_in", "transition": "cut",
                       "status": "assigned", "why": f"timed plan {_lab(r)}", "plan_line": r["line"]})
    apply_motion_level(scenes, preset, st.get("motion_level", "Medium"), seed=int(st.get("seed", 7)))
    reassign_transitions(scenes, preset, words, seed=int(st.get("seed", 7)))
    apply_guidelines(project, scenes)
    scenes[-1]["transition"] = "cut"
    project["scenes"] = scenes
    project["broll"] = [b for b in project.get("broll") or [] if not str(b.get("source", "")).startswith("T")]
    project["sfx_events"] = [e for e in project.get("sfx_events") or [] if not str(e.get("source", "")).startswith("T")]
    project["overlays"] = [o for o in project.get("overlays") or [] if not str(o.get("source", "")).startswith("T")]
    ctx = I.Ctx(project)
    n_look, failed = 0, []
    for k, r in enumerate(rows):
        sid = r["sid"]
        rec = I.record(project, sid)
        keep = {key: rec["settings"].get(key) for key in ("locked", "locked_fields")}
        if r.get("settings") is not None:                       # scene sheet: structured fields
            s, interp, unresolved, notes = (copy.deepcopy(r["settings"]), r["interp"], r["unresolved"],
                                            [r["notes"]] if r.get("notes") else None)
            keep = {}
        else:
            s, interp, unresolved, notes = I.parse(r["text"]) if r["text"] else (I.blank_settings(), [], [], None)
        s.update(timing_mode="absolute", start_seconds=r["start"], end_seconds=r["end"], narration_anchor=None,
                 end_anchor=None, primary_visual={"asset_filename": os.path.basename(r["media"])}, segments=[])
        s.update({k_: v for k_, v in keep.items() if v})
        rec.update(settings=s, instruction_text=(f"{_lab(r)}: {r['ref']} {_f(r['start'])}-{_f(r['end'])}"
                                                 if r.get("settings") is not None else
                                                 f"{_f(r['start'])} - {_f(r['end'])} | {r['ref']} | {r['extras']}"),
                   interpretation=[[c, m] for c, m in interp], unresolved=unresolved, from_plan=True,
                   choices={"primary_visual": 0})
        if notes:
            rec["notes"] = "\n".join(notes)
        ok, msg = I.apply(project, sid, log, ctx, reviewed=True, placed=True)
        if r["extras"] or r.get("settings") is not None:
            n_look += 1
        if not ok:
            failed.append(f"{_lab(r)}: NOT fully applied - {msg}")
    by_sid = {r["sid"]: r for r in rows}
    for s in project["scenes"]:                # the plan decides the picture: nothing replaces it
        s["media"] = by_sid[s["id"]]["media"]
    used = {os.path.normcase(r["media"]) for r in rows}
    unused = [m for m in project_media(project) if os.path.normcase(m) not in used]
    sheet = rows[0].get("settings") is not None
    rep = [f"{'Scene sheet' if sheet else 'Timed edit plan'}: {len(rows)} scenes built exactly as written "
           f"({_f(0)}-{_f(dur)})" + ("; anything not specified is None." if sheet else
                                    f"; {n_look} with their own look, the rest follow the project guidelines.")]
    if fmt_changes:
        rep.append("Export format from GENERAL RULES: " + "; ".join(fmt_changes))
    rep += [p["msg"] for p in problems if p["level"] == "warning"]
    rep += failed
    if unused:
        rep.append(f"{len(unused)} image(s) in the folder aren't in the plan (not used): "
                   + ", ".join(os.path.basename(m) for m in unused[:8]) + ("…" if len(unused) > 8 else ""))
    project["plan_report"] = rep
    for line in rep:
        log(line)
    return project


def problems_for_export(project):
    """Problems when the timed plan is required (scene mode 'timed'): plan errors are fatal."""
    if project["settings"].get("scene_mode") != "timed" or not (project.get("timed_plan") or "").strip():
        return []                              # empty plan: instructions.validate says it's required
    from .project import project_media
    st = project["settings"]
    rows, probs = parse_plan(project.get("timed_plan") or "", project_media(project),
                             float(project.get("duration") or 0), int(st.get("fps", 30)))
    out = [{"level": "fatal", "scene": None, "key": "timed_plan", "msg": "Timed edit plan: " + p["msg"]}
           for p in probs if p["level"] == "error"]
    if not out:
        ids = [s.get("id") for s in project.get("scenes") or []]
        want = [f"T{(r['line'] if r.get('settings') is not None else k + 1):03d}" for k, r in enumerate(rows)]
        if ids != want or any(
                abs(s["start"] - r["start"]) > 0.02 or os.path.normcase(s.get("media") or "") != os.path.normcase(
                    r["media"]) for s, r in zip(project["scenes"], rows)):
            out.append({"level": "warning", "scene": None, "key": "timed_plan",
                        "msg": "The timeline was changed after the timed edit plan was built (or the plan was edited) "
                               "- press 'Build timeline from plan' to follow the plan exactly."})
    return out


# ============================================================================ scene sheet format
SHEET_RE = re.compile(r"^\s*(?:#+\s*)?(?:scene|shot)\s*#?\s*(\d+)\s*:?\s*$", re.I)
FIELD_RE = re.compile(r"^\s*[-•*]?\s*([A-Za-z][A-Za-z /&()'-]{1,40}?)\s*:\s*(.*)$")
PLACEHOLDER = re.compile(r"^\s*\[.*\]\s*$|^\s*(\?+|tbd|todo|-)\s*$", re.I)


def is_sheet(text):
    return any(SHEET_RE.match(ln) for ln in (text or "").splitlines())


def _key(k):
    k = re.sub(r"[^a-z/ ]", "", k.lower()).strip()
    for pat, name in ((r"^(image|visual|picture|asset|file)$", "image"), (r"^script", "script"),
                      (r"^start", "start"), (r"^end", "end"), (r"^duration|^length", "duration"),
                      (r"^framing|^fit", "framing"), (r"^motion", "motion"), (r"^focus|^focal", "focus"),
                      (r"^direction", "direction"), (r"^b ?roll timeline|^b ?roll start|^b ?roll time", "broll_time"),
                      (r"^b ?roll (source )?(audio|sound)", "broll_audio"), (r"^b ?roll source", "broll_source"),
                      (r"^b ?roll placement|^b ?roll position", "broll_place"), (r"^b ?roll", "broll"),
                      (r"^sfx start|^sound( effect)? start|^sfx time|^sfx at", "sfx_start"),
                      (r"^sfx level|^sound( effect)? level|^sfx gain|^sfx volume", "sfx_level"),
                      (r"^sfx fade", "sfx_fade"), (r"^sound effect|^sfx", "sfx"),
                      (r"^visual effect|^effect", "effect"), (r"^transition", "transition"),
                      (r"^caption", "captions"), (r"^lock", "lock"), (r"^note", "notes")):
        if re.match(pat, k):
            return name
    return None


def _empty(v):
    return v is None or not v.strip() or bool(PLACEHOLDER.match(v))


def _mode_word(v):
    lv = (v or "").strip().lower().rstrip(".")
    if lv in ("none", "no", "off", "disabled", "nothing"):
        return "none"
    if lv in ("automatic", "auto"):
        return "auto"
    if lv.startswith("inherit") or lv in ("default", "project", "project setting", "project default"):
        return "inherit"
    return None


def _range(v):
    m = re.match(r"^\s*" + TIME + r"\s*(?:-|–|—|to|until)\s*" + TIME + r"\s*$", v or "", re.I)
    if not m:
        return None, None
    return parse_time(m.group(1)), parse_time(m.group(2))


def parse_sheet(text, media, duration=None, fps=30, words=None):
    """SCENE 001 / Image: 001.png / Start: 00:00.000 / End: … blocks -> rows with structured settings.
    Every field is either applied or reported - never silently ignored. Placeholders like
    "[Paste the exact narration sentence]" count as not filled in."""
    index = C.media_index(media)
    rows, problems = [], []
    blocks, cur, general = [], None, []
    for n, raw in enumerate((text or "").splitlines(), 1):
        m = SHEET_RE.match(raw)
        if m:
            cur = {"num": int(m.group(1)), "line": n, "fields": []}
            blocks.append(cur)
            continue
        if cur is None:
            general.append((n, raw))
            continue
        fm = FIELD_RE.match(raw)
        if fm:
            cur["fields"].append((n, fm.group(1), fm.group(2).strip()))
        elif raw.strip() and cur["fields"]:               # continuation of the previous value
            ln, k, v = cur["fields"][-1]
            cur["fields"][-1] = (ln, k, (v + " " + raw.strip()).strip())
    project = _general_rules(general, problems)
    seen = set()
    for b in blocks:
        lab = f"SCENE {b['num']:03d}"
        if b["num"] in seen:
            problems.append({"line": b["line"], "level": "error", "msg": f"{lab} appears twice"})
            continue
        seen.add(b["num"])
        f, unknown = {}, []
        for ln, k, v in b["fields"]:
            key = _key(k)
            if key is None:
                unknown.append((ln, k))
            else:
                f[key] = (ln, v)
        for ln, k in unknown:
            problems.append({"line": ln, "level": "warning", "msg": f"{lab}: field “{k}” isn't supported - ignored"})
        for key, what in (("focus", "Focus not filled in - the centre is kept (adjust it in Editing instructions)"),
                          ("script", "Script sentence not filled in - its timing can't be checked against the "
                                     "narration"),
                          ("sfx_start", "SFX start not filled in"), ("sfx_level", "SFX level not filled in - project "
                                                                                 "default (-10 dB vs narration)"),
                          ("broll_time", "B-roll timeline start/end not filled in"),
                          ("broll_source", "B-roll source in/out not filled in - the clip plays from its start")):
            if key in f and _empty(f[key][1]) and (f[key][1] or "").strip():
                problems.append({"line": f[key][0], "level": "warning", "msg": f"{lab}: {what}"})
        get = lambda k: f.get(k, (b["line"], None))  # noqa: E731
        ln, img = get("image")
        if _empty(img):
            problems.append({"line": ln, "level": "error", "msg": f"{lab}: no image given"})
            continue
        path = _resolve_image(img.strip(), media, index, b["num"], problems, what="SCENE")
        a_ln, a_v = get("start")
        e_ln, e_v = get("end")
        a, e = parse_time(a_v) if not _empty(a_v) else None, parse_time(e_v) if not _empty(e_v) else None
        d_ln, d_v = get("duration")
        d = parse_time(re.sub(r"\s*(s|sec|secs|seconds?)\s*$", "", d_v or "", flags=re.I)) if not _empty(d_v) else None
        if a is None:
            problems.append({"line": a_ln, "level": "error", "msg": f"{lab}: start time missing or unreadable"})
            continue
        if e is None and d is not None:
            e = a + d
        if e is None:
            problems.append({"line": e_ln, "level": "error", "msg": f"{lab}: end time missing or unreadable"})
            continue
        if e <= a:
            problems.append({"line": e_ln, "level": "error", "msg": f"{lab}: ends ({_f(e)}) before it starts ({_f(a)})"})
            continue
        if d is not None and abs((e - a) - d) > 0.011:
            problems.append({"line": d_ln, "level": "error",
                             "msg": f"{lab}: duration {d:.3f} s doesn't match start/end ({e - a:.3f} s) - fix one"})
            continue
        if path is None:
            continue
        st, interp, unresolved, notes = _sheet_settings(lab, f, a, e, problems)
        script = f.get("script", (0, None))[1]
        if not _empty(script) and words:
            _check_sentence(lab, f["script"][0], script, a, e, words, problems)
        rows.append({"line": b["num"], "label": lab, "src_line": b["line"], "start": I.snap(a, fps),
                     "end": I.snap(e, fps), "ref": img.strip(), "media": path, "extras": "", "text": "",
                     "settings": st, "interp": interp, "unresolved": unresolved,
                     "notes": "\n".join(x for x in ([script] if not _empty(script) else []) + (notes or []) if x)})
    _coverage(rows, duration, problems)
    if not rows and not any(p["level"] == "error" for p in problems):
        problems.append({"line": 0, "level": "error", "msg": "The scene sheet has no scenes."})
    for r in rows:
        r["project"] = project
    return rows, problems


def _general_rules(lines, problems):
    """Video format from the GENERAL RULES part (aspect, size, fps). The other rules are how AutoEditor
    always works in timed mode."""
    from .exportcfg import RESOLUTIONS
    txt = " ".join(t for _, t in lines)
    out = {}
    m = re.search(r"\b(16:9|9:16|1:1)\b", txt)
    if m:
        out["aspect"] = m.group(1)
    m = re.search(r"\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", txt)
    if m:
        wh = (int(m.group(1)), int(m.group(2)))
        hit = [(r, a) for r, d in RESOLUTIONS.items() for a, v in d.items() if v == wh]
        if hit:
            out["resolution"], out["aspect"] = hit[0]
        else:
            problems.append({"line": 0, "level": "warning", "msg": f"{wh[0]} × {wh[1]} isn't one of the export sizes - "
                                                                   "choose a resolution in Export"})
    m = re.search(r"\b(24|25|30|60)\s*fps\b", txt, re.I)
    if m:
        out["fps"] = int(m.group(1))
    if re.search(r"\bfill the canvas\b|\bfill\s*/\s*crop\b", txt, re.I):
        out["fit"] = "crop"
    return out


def _sheet_settings(lab, f, a, e, problems):
    """Structured fields -> instruction settings. Unclear values are reported (and not applied)."""
    s = I.blank_settings()
    interp, unresolved, notes = [], [], []

    def val(k):
        ln, v = f.get(k, (0, None))
        return ln, (None if _empty(v) else v.strip())

    def bad(ln, k, v, why=""):
        unresolved.append(f"{k}: {v}")
        problems.append({"line": ln, "level": "warning", "msg": f"{lab}: {k} “{v}” not understood{why} - NOT applied"})

    # framing + focus
    ln, v = val("framing")
    if v:
        lv = v.lower()
        fit = ("crop" if re.search(r"fill|crop", lv) else "fit_bars" if "bar" in lv else
               "fit_color" if re.search(r"colou?r", lv) else "fit_blur" if re.search(r"fit|blur|background", lv) else None)
        mw = _mode_word(v)
        if fit:
            s["framing"] = {"mode": "explicit", "fit": fit}
            interp.append((f"Framing: {v}", f"framing {fit}"))
        elif mw == "inherit" or mw == "auto":
            pass
        else:
            bad(ln, "Framing", v)
    ln, v = val("focus")
    if v:
        lv = v.lower().strip(" .")
        m = re.match(r"^(\d{1,3})\s*%?\s*[,/ ]\s*(\d{1,3})\s*%?$", lv)
        pos = next((c for w, c in sorted(I.FOCUS_WORDS.items(), key=lambda x: -len(x[0])) if re.search(r"\b" + w + r"\b", lv)), None)
        if m:
            pos = (int(m.group(1)) / 100, int(m.group(2)) / 100)
        if pos:
            s["framing"] = dict(s["framing"] if s["framing"].get("mode") == "explicit" else {"mode": "explicit",
                                                                                               "fit": "crop"},
                                focus=list(pos))
            interp.append((f"Focus: {v}", f"focal point {pos[0]:.0%} across, {pos[1]:.0%} down"))
        elif _mode_word(v) not in ("none", "auto", "inherit"):
            unresolved.append(f"Focus: {v}")
            problems.append({"line": ln, "level": "warning",
                             "msg": f"{lab}: focus “{v}” - AutoEditor can't find that in the picture. Set the focal "
                                    "point (e.g. 'top left' or '30%, 40%') or adjust it in Editing instructions → "
                                    "Motion & framing. The centre is used until then."})
    # motion (+ direction)
    ln, v = val("motion")
    dl, dv = val("direction")
    if v:
        mw = _mode_word(v)
        if mw in ("none", "auto", "inherit"):
            s["motion"] = {"mode": mw}
            interp.append((f"Motion: {v}", f"motion {mw}"))
        else:
            phrase = v
            if dv:
                ld = dv.lower()
                dirw = ("right" if re.search(r"left\s*(to|->|→)\s*right", ld) else
                        "left" if re.search(r"right\s*(to|->|→)\s*left", ld) else
                        "down" if re.search(r"top\s*(to|->|→)\s*bottom|up\s*(to|->|→)\s*down", ld) else
                        "up" if re.search(r"bottom\s*(to|->|→)\s*top|down\s*(to|->|→)\s*up", ld) else
                        ld if ld in ("left", "right", "up", "down") else None)
                if dirw is None:
                    bad(dl, "Direction", dv)
                elif "pan" in phrase.lower() and not re.search(r"pan\s+(left|right|up|down)", phrase, re.I):
                    phrase = re.sub(r"\bpan\b", f"pan {dirw}", phrase, flags=re.I)
                elif "pan" not in phrase.lower():
                    phrase += f" and pan {dirw}"
            if re.fullmatch(r"(gentle|slow|subtle)?\s*pan", phrase.strip(), re.I):
                problems.append({"line": ln, "level": "warning",
                                 "msg": f"{lab}: “{v}” has no direction - add 'Direction: Left to right' (or right to "
                                        "left, top to bottom, bottom to top). NOT applied."})
                unresolved.append(f"Motion: {v} (no direction)")
            else:
                mo = I._parse_motion(phrase)
                if mo and mo.get("mode") == "explicit":
                    s["motion"] = mo
                    interp.append((f"Motion: {v}" + (f" / Direction: {dv}" if dv else ""), I._describe_motion(mo) +
                                   (" (camera moves " + {"pan_right": "left → right", "pan_left": "right → left",
                                                         "pan_up": "bottom → top", "pan_down": "top → bottom"}.get(
                                       mo["type"].split("+")[-1], "") + ")" if "pan" in mo["type"] else "")))
                else:
                    bad(ln, "Motion", v)
    # B-roll
    ln, v = val("broll")
    if v:
        mw = _mode_word(v)
        if mw in ("none", "inherit"):
            s["b_roll_mode"] = mw
            interp.append((f"B-roll: {v}", f"B-roll {mw}"))
        elif re.search(I.FILE_RE + "$", v, re.I):
            br = I._parse_broll(v, "")
            tl, tv = val("broll_time")
            bs, be = _range(tv) if tv else (None, None)
            if tv and bs is None:
                bad(tl, "B-roll timeline start/end", tv, " (use 00:10.000 - 00:12.000)")
            if bs is None:
                bs, be = a, e
                problems.append({"line": ln, "level": "warning",
                                 "msg": f"{lab}: B-roll timeline start/end not given - it covers the whole scene "
                                        f"({_f(a)}-{_f(e)})"})
            br.update(start_seconds=bs, end_seconds=be)
            sl, sv = val("broll_source")
            if sv:
                ci, co = _range(sv)
                if ci is None:
                    bad(sl, "B-roll source in/out", sv, " (use 00:02.000 - 00:04.000)")
                else:
                    br.update(clip_in=ci, clip_out=co)
            pl, pv = val("broll_place")
            if pv:
                lp = pv.lower()
                if re.search(r"full|replace|insert", lp):
                    br["mode"] = "insert"
                elif re.search(r"overlay|picture|corner|pip", lp):
                    br["mode"] = "overlay"
                    pm = re.search(r"(top|bottom)[\s-]+(left|right)|center|centre", lp)
                    if pm:
                        br["position"] = f"{pm.group(1)}_{pm.group(2)}" if pm.group(1) else "center"
                    sm = re.search(r"(\d{1,3})\s*%", lp)
                    if sm:
                        br["size"] = max(0.1, min(0.9, int(sm.group(1)) / 100))
                else:
                    bad(pl, "B-roll placement", pv)
            al, av = val("broll_audio")
            if av:
                if _mode_word(av) == "none" or re.search(r"mute", av, re.I):
                    br["audio_gain_db"] = None
                else:
                    g = re.search(r"(-?\d+(?:\.\d+)?)\s*db", av, re.I)
                    if g:
                        br["audio_gain_db"] = float(g.group(1))
                    else:
                        bad(al, "B-roll source audio", av, " (use Muted or a level like -12 dB)")
            s["b_roll_mode"], s["b_roll"] = "explicit", [br]
            interp.append((f"B-roll: {v}", I._describe_broll(br)))
        else:
            bad(ln, "B-roll", v, " (give a file name or None)")
    # SFX
    ln, v = val("sfx")
    if v:
        mw = _mode_word(v)
        if mw in ("none", "auto", "inherit"):
            s["sfx_mode"] = mw
            interp.append((f"Sound effect: {v}", f"sound effects {mw}"))
        elif re.search(I.AUDIO_RE + "$", v, re.I):
            ev = I._parse_sfx(v, "")
            ev.pop("_why", None)
            tl, tv = val("sfx_start")
            if tv:
                t = parse_time(re.sub(r"\s*(s|sec|seconds?)$", "", tv, flags=re.I))
                q = re.search(r"[\"“](.+?)[\"”]", tv)
                if t is not None:
                    ev["start_seconds"] = t
                    if not (a - 1e-6 <= t <= e + 1e-6):
                        problems.append({"line": tl, "level": "warning",
                                         "msg": f"{lab}: SFX start {_f(t)} is outside this scene ({_f(a)}-{_f(e)})"})
                elif q:
                    ev["anchor"] = {"text": q.group(1), "occurrence_in_scene": None}
                else:
                    bad(tl, "SFX start", tv, " (use a time like 00:05.200 or a word in quotes)")
            else:
                ev["start_seconds"] = a
                problems.append({"line": ln, "level": "warning",
                                 "msg": f"{lab}: SFX start not given - it plays at the scene start ({_f(a)})"})
            gl, gv = val("sfx_level")
            if gv and _mode_word(gv) != "inherit":
                g = re.search(r"(-?\d+(?:\.\d+)?)\s*db", gv, re.I)
                if g:
                    ev["gain_db"] = float(g.group(1))
                else:
                    bad(gl, "SFX level", gv, " (use a level in dB like -14 dB, or inherit)")
            else:
                ev["gain_db"] = -10.0
            fl, fv = val("sfx_fade")
            if fv:
                fm = re.findall(r"(in|out)\s*:?\s*(\d+(?:\.\d+)?)", fv, re.I)
                for which, sec in fm:
                    ev["fade_" + which.lower()] = float(sec)
                if not fm:
                    bad(fl, "SFX fade", fv, " (use 'in 0.2, out 0.3')")
            s["sfx_mode"], s["sfx"] = "explicit", [ev]
            interp.append((f"Sound effect: {v}", I._describe_sfx(ev)))
        else:
            bad(ln, "Sound effect", v, " (give a file name or None)")
    # visual effect
    ln, v = val("effect")
    if v:
        mw = _mode_word(v)
        if mw in ("none", "inherit"):
            s["effects_mode"] = mw
            interp.append((f"Visual effect: {v}", f"effects {mw}"))
        else:
            effs = []
            for part in _split_extras(v):
                eff = I._parse_effect(_phrase(part))
                if eff == "unsupported":
                    unresolved.append(f"Visual effect: {part}")
                    problems.append({"line": ln, "level": "warning",
                                     "msg": f"{lab}: visual effect “{part}” is unavailable in the current renderer - "
                                            "NOT applied"})
                elif eff:
                    effs.append(eff)
                else:
                    bad(ln, "Visual effect", part)
            if effs:
                s["effects_mode"], s["effects"] = "explicit", effs
                interp.append((f"Visual effect: {v}", "; ".join(I._describe_effect(x) for x in effs)))
    # transition
    ln, v = val("transition")
    if v:
        mw = _mode_word(v)
        if mw == "none":
            s["transition_out"] = {"mode": "none", "type": "cut", "duration_seconds": 0.0}
            interp.append((f"Transition: {v}", "transition: none (cut)"))
        elif mw in ("auto", "inherit"):
            s["transition_out"] = {"mode": mw}
            interp.append((f"Transition: {v}", f"transition {mw}"))
        else:
            tr = I._parse_transition(_phrase(v))
            if tr is None:
                lv = v.lower()
                d = re.search(TIME, lv)
                typ = ("crossfade" if re.search(r"dissolve|cross", lv) else "dip_black" if "fade" in lv else
                       "cut" if "cut" in lv else "slide" if "slide" in lv else "whip" if "whip" in lv else None)
                tr = {"mode": "explicit", "type": typ, "duration_seconds": parse_time(d.group(1)) if d and typ != "cut"
                      else (0.0 if typ == "cut" else None)} if typ else None
            if tr:
                s["transition_out"] = tr
                interp.append((f"Transition: {v}", "transition " + I.TRANSITIONS.get(tr.get("type"), "?") +
                               (f" {tr['duration_seconds']:.2f} s" if tr.get("duration_seconds") else "")))
            else:
                bad(ln, "Transition", v)
    # captions
    ln, v = val("captions")
    if v:
        lv = v.lower()
        if _mode_word(v) == "none":
            s["captions"] = {"mode": "none"}
        elif _mode_word(v) in ("inherit", "auto"):
            pass
        elif re.search(r"\b(on|yes|top|middle|bottom)\b", lv):
            pos = re.search(r"top|middle|bottom", lv)
            s["captions"] = {"mode": "explicit", **({"position": pos.group(0).capitalize()} if pos else {})}
        else:
            bad(ln, "Captions", v)
        interp.append((f"Captions: {v}", f"captions {s['captions'].get('mode')}"))
    # locks
    ln, v = val("lock")
    if v:
        lv = v.lower()
        if re.match(r"^(yes|y|true|locked|on)\b", lv):
            s["locked_fields"] = ["primary_visual", "timing"]
            interp.append((f"Lock: {v}", "lock visual and timing"))
        elif re.match(r"^(no|n|false|off)\b", lv):
            pass
        else:
            bad(ln, "Lock", v)
    ln, v = val("notes")
    if v:
        notes.append(v)
    # rule: apply ONLY what is specified - a field left out means None (not "automatic")
    missing = []
    if "motion" not in f:
        s["motion"] = {"mode": "none"}
        missing.append("motion")
    if "transition" not in f:
        s["transition_out"] = {"mode": "none", "type": "cut", "duration_seconds": 0.0}
        missing.append("transition (cut)")
    if "broll" not in f:
        s["b_roll_mode"] = "none"
        missing.append("B-roll")
    if "sfx" not in f:
        s["sfx_mode"] = "none"
        missing.append("sound effect")
    if "effect" not in f:
        s["effects_mode"] = "none"
        missing.append("visual effect")
    if missing:
        interp.append(("(not specified)", "None: " + ", ".join(missing)))
    return s, interp, unresolved, notes


def _check_sentence(lab, ln, script, a, e, words, problems):
    """The sentence written for a scene should be spoken during that scene - report where it really is."""
    occ = I.find_occurrences(words, " ".join(script.split()[:8]))
    if not occ:
        problems.append({"line": ln, "level": "warning",
                         "msg": f"{lab}: the script sentence isn't found in the recording - check it"})
        return
    hits = [(words[i]["s"], words[min(j, len(words)) - 1]["e"]) for i, j, _ in occ]
    if not any(s0 < e - 0.05 and s1 > a + 0.05 for s0, s1 in hits):
        s0, s1 = hits[0]
        problems.append({"line": ln, "level": "warning",
                         "msg": f"{lab}: its sentence is spoken at {_f(s0)}-{_f(s1)}, but the scene is {_f(a)}-{_f(e)} "
                                "- the picture won't match the narration. Check the times."})


def sheet_template(words, duration):
    """Scene sheet skeleton (your format) with each sentence and its exact times filled in."""
    lines_ = [ln for ln in template(words, duration).splitlines() if ln and not ln.startswith("#")]
    out = ["STRICT VISUAL EDITING INSTRUCTIONS", "", "GENERAL RULES",
           "Export only one video: 16:9, 1920 × 1080, 30 fps.", "Fill the canvas proportionally without stretching.", ""]
    for k, ln in enumerate(lines_, 1):
        m = RANGE.match(ln.split("#", 1)[0])
        a, e = parse_time(m.group(1)), parse_time(m.group(2))
        txt = ln.split("#", 1)[1].strip() if "#" in ln else ""
        out += [f"SCENE {k:03d}", "Image: [file name]", f"Script sentence: {txt}", f"Start: {_tc(a)}",
                f"End: {_tc(e)}", f"Duration: {e - a:.3f} seconds", "Framing: Fill / Crop", "Motion: Automatic",
                "B-roll: None", "Sound effect: None", "Visual effect: None", "Transition to next scene: Cut",
                "Lock visual and timing: Yes", ""]
    return "\n".join(out)


def _tc(t):
    m, s_ = divmod(max(0.0, float(t)), 60)
    return f"{int(m):02d}:{s_:06.3f}"


def export_sheet(project):
    """The CURRENT timeline (after any edits in Editing instructions) written back as a scene sheet."""
    st = project["settings"]
    from .exportcfg import dims
    W, H = dims(st.get("aspect", "16:9"), st.get("resolution", "Full HD"))
    out = ["STRICT VISUAL EDITING INSTRUCTIONS", "", "GENERAL RULES",
           f"Export only one video: {st.get('aspect', '16:9')}, {W} × {H}, {st.get('fps', 30)} fps.", ""]
    recs = I.records(project)
    for k, sc in enumerate(project.get("scenes") or [], 1):
        rec = recs.get(sc.get("id")) or {}
        s = rec.get("settings") or I.blank_settings()
        mo = s.get("motion") or {}
        motion = {"none": "None", "auto": "Automatic", "inherit": "Inherit"}.get(mo.get("mode"), None) or \
            (f"{mo.get('strength', 'medium')} {mo.get('type', '').replace('_', ' ').replace('+', ' + ')}"
             if mo.get("mode") == "explicit" else (sc.get("motion") or "none").replace("_", " "))
        tr = sc.get("transition", "cut")
        trs = {"cut": "Cut", "crossfade": "Dissolve", "dip_black": "Fade to black", "slide": "Slide", "whip": "Whip",
               "zoom": "Zoom"}.get(tr, tr) + (f" {float(sc['transition_s']):.2f} s" if sc.get("transition_s") and
                                               tr != "cut" else "")
        fit = {"crop": "Fill / Crop", "fit_blur": "Fit / Background", "fit_color": "Fit / Colour",
               "fit_bars": "Fit / Black bars"}.get(sc.get("fit") or st.get("fit", "crop"), "Fill / Crop")
        out += [f"SCENE {k:03d}", f"Image: {os.path.basename(sc.get('media') or '')}",
                f"Script sentence: {(sc.get('text') or '').strip()}", f"Start: {_tc(sc['start'])}",
                f"End: {_tc(sc['end'])}", f"Duration: {sc['end'] - sc['start']:.3f} seconds", f"Framing: {fit}"]
        if sc.get("focus"):
            out.append(f"Focus: {round(sc['focus'][0] * 100)}%, {round(sc['focus'][1] * 100)}%")
        out.append(f"Motion: {motion}")
        brs = [b for b in project.get("broll") or [] if b.get("source") == sc.get("id")]
        if brs:
            b = brs[0]
            out += [f"B-roll: {os.path.basename(b['media'])}", f"B-roll timeline start/end: {_tc(b['start'])} - "
                    f"{_tc(b['end'])}"]
            if b.get("clip_in") is not None or b.get("clip_out") is not None:
                out.append(f"B-roll source in/out: {_tc(b.get('clip_in') or 0)} - {_tc(b.get('clip_out') or 0)}")
            out += [f"B-roll placement: {'Full-frame replacement' if b.get('mode') != 'overlay' else 'Overlay ' + b.get('position', 'top_right').replace('_', ' ')}",
                    f"B-roll source audio: {'Muted' if b.get('audio_gain_db') is None else str(b['audio_gain_db']) + ' dB'}"]
        else:
            out.append("B-roll: " + ("None" if s.get("b_roll_mode") in ("none", "inherit", None) else "None"))
        evs = [e for e in project.get("sfx_events") or [] if e.get("source") == sc.get("id")]
        if evs:
            e = evs[0]
            out += [f"Sound effect: {os.path.basename(e['file'])}", f"SFX start: {_tc(e['t'])}",
                    f"SFX level: {float(e.get('rel_db', -10)):.0f} dB"]
        else:
            out.append("Sound effect: " + {"auto": "Automatic", "inherit": "Inherit"}.get(s.get("sfx_mode"), "None"))
        effs = sc.get("effects") or []
        out.append("Visual effect: " + (", ".join(I.EFFECTS[x["type"]]["label"] + (f" {x['amount']}" if x.get(
            "amount") not in (None, I.EFFECTS[x["type"]].get("default")) else "") for x in effs) if effs else "None"))
        out.append(f"Transition to next scene: {trs}")
        out.append(f"Lock visual and timing: {'Yes' if set(['primary_visual', 'timing']) <= set(sc.get('locked_fields') or []) or sc.get('locked') else 'No'}")
        extra = [x for x in (rec.get("notes") or "").splitlines() if x.strip() and x.strip() not in (sc.get("text") or "")]
        if rec.get("unresolved"):
            extra.append("NOT applied: " + " | ".join(rec["unresolved"]))
        if extra:
            out.append("Notes: " + " / ".join(extra))
        out.append("")
    return "\n".join(out)
