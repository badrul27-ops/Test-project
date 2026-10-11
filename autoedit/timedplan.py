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


TIME_FULL = re.compile(r"^\s*(?:(\d+):)?(?:(\d{1,2}):)?(\d+)(?:[.,](\d+))?\s*$")


def parse_time(s):
    """'116.84', '1:56.84', '01:56.840', '0:01:56.84' -> seconds. '.24' and '.240' are both 240 ms (decimal
    seconds, never frames). Malformed values (seconds/minutes of 60 or more after a colon) -> None."""
    t = (s or "").strip()
    m = TIME_FULL.match(t)
    if not m:
        return None
    h_, m_, s_, frac = m.groups()
    if h_ is not None and m_ is None:          # m:ss
        h_, m_ = None, h_
    if (m_ is not None and int(s_) >= 60) or (h_ is not None and int(m_) >= 60):
        return None
    v = int(s_) + (int(m_) * 60 if m_ else 0) + (int(h_) * 3600 if h_ else 0) + (float("0." + frac) if frac else 0.0)
    return round(v, 6)


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


ASSET_EXT = r"(?:png|jpe?g|webp|gif|bmp|tiff?|mp4|mov|mkv|webm|m4v|avi)"
# an image/clip reference on a plan line: a name or a path (folders, spaces, parentheses, a drive letter), maybe in
# quotes or backticks; anything after a comma is the look
ASSET_REF = re.compile(r"^[`\"'“]?(?P<ref>(?:[A-Za-z]:[\\/])?[^,|`\"“”]*?\." + ASSET_EXT + r")[`\"'”]?"
                       r"(?=\s*(?:,|$))\s*,?\s*(?P<rest>.*)$", re.I)
ASSET_TOKEN = re.compile(r"(?:[A-Za-z]:[\\/])?[^|`\"“”]*?\." + ASSET_EXT, re.I)
# folders holding character / style reference pictures: never picked for a scene by file name alone
REF_DIR = re.compile(r"(?:^|[\s_\-.])(?:char(?:acter)?s?[\s_\-]*)?ref(?:erence)?s?(?:$|[\s_\-.])", re.I)


def _clean_ref(t):
    """Surrounding spaces, quotes, backticks or **bold** off - never characters inside the name."""
    t = (t or "").strip()
    while t.startswith("**") and t.endswith("**") and len(t) > 4:
        t = t[2:-2].strip()
    if len(t) >= 2 and ((t[0] == t[-1] and t[0] in "`\"'") or (t[0] == "“" and t[-1] == "”")):
        t = t[1:-1].strip()
    return t


def _pkey(path):
    """Lookup key for a path: one separator style, no '..' tricks, case-insensitive like Windows."""
    import posixpath
    return posixpath.normpath((path or "").replace("\\", "/")).lower()


def _is_reference(path, root):
    """Is this picture inside a reference folder (Character_References, refs, …) of the images folder?"""
    rel = os.path.relpath(path, root) if root and os.path.isabs(path) and os.path.isabs(root) else path
    try:
        parts = rel.replace("\\", "/").split("/")[:-1]
    except ValueError:
        parts = []
    return any(REF_DIR.search(x) for x in parts if x not in ("..", "."))


def plan_context(project):
    """What the resolver needs from the project: the images folder and folder mappings chosen for this plan."""
    return {"root": project.get("media_dir") or "", "roots": dict(project["settings"].get("plan_roots") or {})}


def _resolve_image(ref, media, index, n, problems, what="line", src_line=None, ctx=None):
    """Exact file for a reference, or None (with the reason in problems, each with a 'kind'). Never guesses
    between files. Order: exact path → path inside the images folder → a folder mapping you chose → the same
    sub-path anywhere in the project's images → the only scene picture with that name (reference folders are
    never used this way; the chosen file is reported). n names the shot/scene in messages; src_line is the text
    line to jump to (default: n)."""
    ctx = ctx if ctx is not None else {}
    where = f"{what} {n}"
    ln = src_line or n

    def fail(kind, msg, level="error"):
        problems.append({"line": ln, "level": level, "kind": kind, "msg": f"{where}: {msg}"})
        return None
    ref = _clean_ref(ref)
    rs = ref.replace("\\", "/")
    root = ctx.get("root") or ""
    keys = ctx.get("_keys")
    if keys is None or ctx.get("_keys_for") is not media:
        keys = {}
        for m in media:
            keys.setdefault(_pkey(m), m)
        ctx["_keys"], ctx["_keys_for"] = keys, media

    def scene_only(ms):
        return [m for m in ms if not _is_reference(m, root)]
    if "/" in rs or re.match(r"^[A-Za-z]:", rs):
        if os.path.isabs(ref) or re.match(r"^[A-Za-z]:/", rs):
            m = keys.get(_pkey(rs))
            if m:
                return m
        if root:
            m = keys.get(_pkey(os.path.join(root, rs)))
            if m:
                return m
        for pre in sorted(ctx.get("roots") or {}, key=len, reverse=True):
            pk = pre.replace("\\", "/").strip("/").lower()
            if rs.lower().startswith(pk + "/"):
                target = os.path.join(ctx["roots"][pre], rs[len(pk) + 1:])
                m = keys.get(_pkey(target))
                if m:
                    return m
                return fail("not_found", f"{rs} isn't in the folder you chose for “{pre}” ({ctx['roots'][pre]})")
        tail = "/" + _pkey(rs).lstrip("/")
        hits = [m for k_, m in keys.items() if k_.endswith(tail)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return fail("ambiguous", f"{rs} matches {len(hits)} files ({', '.join(hits[:3])}) - choose one")
        base = os.path.basename(rs).lower()
        same = [m for m in media if os.path.basename(m).lower() == base]
        scene = scene_only(same)
        if len(scene) == 1:
            folder = os.path.dirname(rs)
            ctx.setdefault("prefix_fallback", {}).setdefault(folder, []).append((ln, rs, scene[0]))
            return scene[0]
        if len(scene) > 1:
            return fail("ambiguous", f"{rs}: there is no folder “{os.path.dirname(rs)}”, and {len(scene)} scene "
                                     f"pictures are named {os.path.basename(rs)} - choose one, or map the folder")
        if same:
            return fail("not_found", f"{rs}: the only picture with this name is in a reference folder "
                                     f"({same[0]}) - write that path if you really want it")
        return fail("unmapped", f"{rs} can't be found - there is no folder “{os.path.dirname(rs)}” in your images "
                                "folder and no picture with this name. Use “Map folder…” to choose where these "
                                "images are")
    base = os.path.basename(rs).lower()
    same = [m for m in media if os.path.basename(m).lower() == base] if "." in base else []
    if len(same) > 1:
        scene = scene_only(same)
        if len(scene) == 1:
            ctx["ref_skipped"] = ctx.get("ref_skipped", 0) + 1
            return scene[0]
        folders = ", ".join(sorted({os.path.basename(os.path.dirname(m)) or "." for m in same}))
        return fail("ambiguous", f"{len(same)} files are named {os.path.basename(ref)} (in {folders}) - write the "
                                 f"folder too, e.g. {os.path.basename(os.path.dirname(same[-1]))}/"
                                 f"{os.path.basename(ref)}, or choose one")
    if "." in base:                            # a file name: exact, or one clearly-meant file (always reported)
        stem = os.path.splitext(base)[0]
        norm = re.sub(r"\s+", "", base)
        num = int(re.sub(r"\D", "", stem)) if re.fullmatch(r"\D*\d+\D*", stem) else None
        if not same:
            pool = scene_only(media)
            close = [m for m in pool if os.path.splitext(os.path.basename(m))[0].lower() == stem]
            how = "a different file type"
            if not close:
                close = [m for m in pool if re.sub(r"\s+", "", os.path.basename(m).lower()) == norm]
                how = "extra spaces in the name"
            if not close and num is not None:
                close = [m for m in pool if os.path.splitext(os.path.basename(m))[0].isdigit()
                         and int(os.path.splitext(os.path.basename(m))[0]) == num]
                how = "the same number written differently"
            if len(close) == 1:
                fail("close", f"the plan says {os.path.basename(ref)} but the folder has "
                              f"{os.path.basename(close[0])} ({how}) - that file is used", level="warning")
                return close[0]
            if len(close) > 1:
                return fail("ambiguous", f"{os.path.basename(ref)} isn't in the folder, and {len(close)} files could "
                                         f"be meant ({', '.join(os.path.basename(m) for m in close[:4])}) - write "
                                         "the exact file name")
            import difflib
            names = [os.path.basename(m) for m in media]
            near = difflib.get_close_matches(os.path.basename(ref), names, n=3, cutoff=0.6)
            return fail("not_found", f"{os.path.basename(ref)} isn't in the images folder"
                                     + (f" - did you mean {' or '.join(near)}?" if near else
                                        " - check the name, or add the file to the folder"))
        return same[0]
    paths, bad = C.resolve(ref, media, index)
    if any("files have this number" in b_ for b_ in bad):
        return fail("ambiguous", next(b_ for b_ in bad if "files have this number" in b_).split(" - used")[0]
                    + " - write the file name instead")
    if not paths:
        return fail("not_found", f"{ref} isn't in the images folder - check the name or number")
    if len(paths) > 1:
        return fail("ambiguous", f"'{ref}' means {len(paths)} files - give one file name")
    for b_ in bad:
        problems.append({"line": ln, "level": "warning", "msg": f"{where}: {b_}"})
    return paths[0]


def _lab(r):
    return r.get("label") or f"line {r['line']}"


def _coverage(rows, duration, problems, what="line"):
    rows.sort(key=lambda r: r["start"])
    for x, y in zip(rows, rows[1:]):
        if y["start"] < x["end"] - 1e-6:
            problems.append({"line": y.get("src_line") or y["line"], "level": "error", "kind": "overlap",
                             "msg": f"{_lab(y)} starts at {_f(y['start'])} but {_lab(x)} runs until "
                                    f"{_f(x['end'])} - shots can't overlap"})
        elif y["start"] > x["end"] + 1e-6:
            problems.append({"line": y.get("src_line") or y["line"], "level": "error", "kind": "gap",
                             "msg": f"nothing is assigned from {_f(x['end'])} to {_f(y['start'])} (between {_lab(x)} "
                                    f"and {_lab(y)})"})
    if rows and rows[0]["start"] > 1e-6:
        problems.append({"line": rows[0].get("src_line") or rows[0]["line"], "level": "error", "kind": "gap",
                         "msg": f"nothing is assigned from 0:00 to {_f(rows[0]['start'])} - the first shot must start "
                                "at 0:00"})
    if rows and duration:
        end = rows[-1]["end"]
        if end < duration - 0.5:
            problems.append({"line": rows[-1].get("src_line") or rows[-1]["line"], "level": "error", "kind": "gap",
                             "msg": f"the narration continues until {_f(duration)} but the plan ends at {_f(end)} - "
                                    "add shots for the rest"})
        elif end > duration + 0.5:
            problems.append({"line": rows[-1].get("src_line") or rows[-1]["line"], "level": "error", "kind": "overlap",
                             "msg": f"the plan runs until {_f(end)} but the narration ends at {_f(duration)} - the "
                                    "narration is never extended; shorten the last shot"})


def _strip_comment(line):
    """Text after # is a comment - except inside quotes ('text "Top #1"') and image numbers ('image #3')."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote or (quote == "“" and ch == "”"):
                quote = None
        elif ch in "\"“":
            quote = ch
        elif ch == "#" and not line[i + 1:i + 2].isdigit():
            return line[:i]
    return line


# ---------------------------------------------------------------------------- table / CSV columns
COLUMNS = {
    "scene": ("scene", "#", "no", "no.", "nr", "shot", "scene #", "scene no", "scene number", "number", "id",
              "scene_id", "shot #"),
    "image": ("image", "images", "file", "filename", "file name", "asset", "asset_filename", "visual", "media",
              "picture", "photo", "clip", "img"),
    "start": ("start", "from", "in", "start time", "start_seconds", "begin", "starts"),
    "end": ("end", "to", "out", "end time", "end_seconds", "until", "ends"),
    "duration": ("duration", "dur", "length", "seconds", "secs", "duration (s)", "duration_seconds", "time (s)"),
    "time": ("time", "times", "timecode", "time range", "range", "timing"),
    "motion": ("motion", "camera", "camera motion", "move", "movement", "animation", "ken burns"),
    "notes": ("notes", "note", "description", "comment", "comments", "script", "narration", "script sentence",
              "sentence", "purpose", "why", "what it shows", "content", "subject", "voiceover", "text (script)"),
    "look": ("look", "extras", "instructions", "instruction", "edit", "editing", "details", "style"),
    "transition": ("transition", "transition out", "transition to next", "transitions"),
    "sfx": ("sfx", "sound", "sound effect", "sound effects", "audio"),
    "broll": ("b-roll", "broll", "b roll", "v2", "cutaway"),
    "effects": ("effect", "effects", "filter", "grade", "colour", "color"),
    "text": ("text", "overlay", "on-screen text", "label", "title", "caption text"),
    "framing": ("framing", "fit", "crop"),
    "captions": ("captions", "subtitles"),
    "lock": ("lock", "locked", "locks", "lock visual and timing"),
}
_COL = {name: key for key, names in COLUMNS.items() for name in names}
MD_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$|^\s*\|\s*:?-+:?\s*\|\s*$")
TIME_ONLY = re.compile(r"^\s*" + TIME + r"\s*$", re.I)
NOTE_FILE = re.compile(r"^(?P<ref>" + I.FILE_RE + r")\s*[:\-–—]+\s*(?P<note>.+)$", re.I)
NOTE_SCENE = re.compile(r"^(?:scene|shot)\s*#?\s*(?P<num>\d+)\s*[:\-–—]+\s*(?P<note>.+)$", re.I)
MOTION_WORDS = re.compile(r"\b(zoom(?! transition)|pan|push|pull|drift|still|static|no motion|ken burns|tilt|"
                          r"truck|dolly|hold)\b", re.I)


def _cells(line):
    """'| a | b |' -> ['a', 'b'] (outer pipes optional)."""
    t = line.strip()
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|"):
        t = t[:-1]
    return [c.strip() for c in t.split("|")]


def _unmd(t):
    """Markdown decoration off: **bold**, `code`, leading bullets / numbering."""
    t = re.sub(r"(\*\*|__|`)", "", t or "").strip()
    return re.sub(r"^(?:[-*•>+]|\d+[.)])\s+", "", t).strip()


def _header(cells):
    """Column keys for a header row, or None if this isn't one (needs an image column + 2 known columns and
    no times)."""
    keys = [_COL.get(_unmd(c).lower().rstrip(":")) for c in cells]
    if "image" not in keys or sum(1 for k in keys if k) < 2 or any(TIME_ONLY.match(_unmd(c)) for c in cells if c):
        return None
    return keys


NONE_PHRASE = {"motion": "no motion", "transition": "cut to the next scene", "sfx": "no sound effects",
               "broll": "no b-roll", "effects": "no effects", "captions": "no captions"}
COL_LOCK = {"lock", "locked", "lock visual and timing", "locks"}


def _motion_value(v):
    """'zoom_in' / 'zoom_in+pan_right' / 'none' / 'static' -> words Editing instructions understands."""
    t = (v or "").strip()
    lt = t.lower().rstrip(".")
    if lt in ("none", "no", "off", "static", "still", "hold", "no motion", "no movement"):
        return "no motion"
    if re.fullmatch(r"[a-z_+ ]+", lt) and "_" in lt:
        return " + ".join(x.strip().replace("_", " ") for x in lt.split("+"))
    return t


def _col_phrase(key, v):
    """A cell from a named column (or key=value) -> a phrase Editing instructions understands.
    Empty = follow the guidelines (inherit). 'None' = switched OFF for this scene - never a fallback."""
    v = (v or "").strip()
    if not v:
        return ""
    mw = _mode_word(v)
    if mw == "none" and key in NONE_PHRASE:
        return NONE_PHRASE[key]
    if mw == "none" and key == "text":
        return ""                              # no text card: nothing to add
    if mw == "inherit" or mw == "auto" and key != "motion":
        return ""
    if key == "motion":
        return _motion_value(v)
    if key == "transition":
        return _phrase(v.replace("_", " "))
    if key == "sfx" and not re.match(r"^(sfx|sound|play)\b", v, re.I):
        return f"sfx {v}"
    if key == "broll" and re.fullmatch(I.FILE_RE, v, re.I):
        return f"show {v} as b-roll"
    if key == "text" and not re.match(r"^text\b", v, re.I):
        return f'text "{v.strip(chr(34))}"'
    if key == "framing":
        return {"fill": "fill the frame", "crop": "fill the frame", "fill / crop": "fill the frame",
                "fit": "show the whole image"}.get(v.lower(), v)
    if key == "lock":
        return "lock this scene" if v.lower() in ("yes", "y", "true", "1", "on", "lock", "locked") else ""
    return v


def _kv_phrase(x):
    """'motion=zoom_in' / 'transition: cut' inside a plan line -> (column key, phrase); None if not key=value."""
    m = re.match(r"^\s*([A-Za-z][\w -]{0,24}?)\s*=\s*(.*)$", x)
    if not m:
        return None
    key = _COL.get(m.group(1).strip().lower()) or ("lock" if m.group(1).strip().lower() in COL_LOCK else None)
    if not key or key in ("image", "start", "end", "duration", "time", "scene"):
        return None
    return key, _col_phrase(key, m.group(2))


def _split_motion(extras):
    """Phrases that are camera motion vs everything else (for the Motion / Look columns)."""
    mo, rest = [], []
    for x in _split_extras(extras or ""):
        (mo if MOTION_WORDS.search(x) and not re.search(r"transition", x, re.I) else rest).append(x)
    return ", ".join(mo), ", ".join(rest)


def parse_entries(text, media, duration=None, fps=30, words=None, ctx=None):
    """Every format -> one scene-plan schema. Returns (entries, rows, problems):
      entries: one per scene line the plan tries to define (also the broken ones, for review) -
               {line, scene, start, end, duration, ref, media, motion, look, notes, errors[], warnings[], fmt}
      rows:    the valid entries, ready to build (times snapped to frames, exact values in start_raw/end_raw)
      problems: [{'line', 'level': error|warning, 'msg'}]
    Formats: compact lines (start - end | image | look), Markdown tables, CSV with a header row, scene sheets
    (SCENE 001 blocks). Note lines ('- **065.jpg:** …', 'Scene 12: …') are attached to their scenes as notes -
    never read as timing rows. Nothing is silently dropped: every other line is listed."""
    ctx = dict(ctx or {})
    if is_sheet(text):
        rows, problems = parse_sheet(text, media, duration, fps, words, ctx=ctx)
        entries = [dict(r, scene=r["line"], duration=r["end"] - r["start"], motion="", look="",
                        errors=[], warnings=[p["msg"] for p in problems if p.get("line") and p["level"] == "warning"
                                             and str(r["label"]) in p["msg"]], fmt="sheet",
                        start_raw=r["start"], end_raw=r["end"]) for r in rows]
        for e in entries:
            e["motion"] = "; ".join(m for c, m in e.get("interp") or [] if m.startswith("motion"))
        return entries, rows, problems
    import csv
    index = C.media_index(media)
    entries, problems, notes, prose = [], [], [], []
    header, csv_header = None, None

    def err(e, msg, kind="timing"):
        e["errors"].append(msg)
        e.setdefault("kind", kind)
        problems.append({"line": e["line"], "level": "error", "kind": kind, "msg": msg})

    def fill(e, keys, cells, where, extra_notes):
        """Named cells (table / CSV / JSON) -> the entry. Empty cell = follow the guidelines; None = off."""
        look = []
        for k, c in zip(keys, cells):
            c = c.strip() if k in ("notes", "image") else _unmd(c)
            if not c:
                continue
            if k == "scene":
                dg = re.search(r"\d+", c)
                e["scene"] = int(dg.group(0)) if dg else None
                e["scene_id"] = c
            elif k == "image":
                e["ref"] = _clean_ref(c)
            elif k in ("start", "end"):
                e[k] = parse_time(re.sub(r"\s*(s|sec|secs|seconds?)\s*$", "", c, flags=re.I))
                if e[k] is None:
                    err(e, f"{where}: {k} time “{c}” can't be read - write it like 01:56.84, 1:02:03.5 or 116.84")
            elif k == "duration":
                e["duration"] = parse_time(re.sub(r"\s*(s|sec|secs|seconds?)\s*$", "", c, flags=re.I))
                if e["duration"] is None:
                    msg = f"{where}: duration “{c}” can't be read - it is only a check, start/end are used"
                    e["warnings"].append(msg)
                    problems.append({"line": e["line"], "level": "warning", "msg": msg})
            elif k == "time":
                mr = RANGE.match(c)
                if mr:
                    e["start"], e["end"] = parse_time(mr.group(1)), parse_time(mr.group(2))
                else:
                    err(e, f"{where}: time “{c}” isn't a range - write it like 01:56.84 - 02:06.76")
            elif k == "motion":
                e["motion"] = _col_phrase("motion", c)
            elif k == "notes":
                extra_notes.append(c)
            elif k:
                ph = _col_phrase(k, c)
                if ph:
                    look.append(ph)
            else:
                extra_notes.append(c)
        for c in cells[len(keys):]:
            if c:
                extra_notes.append(c)
        e["look"] = ", ".join(look)

    def blank(n, fmt=""):
        return {"line": n, "scene": None, "start": None, "end": None, "duration": None, "ref": "", "media": None,
                "motion": "", "look": "", "notes": "", "errors": [], "warnings": [], "fmt": fmt}

    guide_lines, guide_mode, is_json = [], False, False
    stripped = (text or "").strip()
    if stripped[:1] in ("{", "["):                 # versioned JSON: {"version": 1, "guidelines": [...], "scenes": [...]}
        import json
        is_json = True
        try:
            data = json.loads(stripped)
        except ValueError as ex:
            problems.append({"line": getattr(ex, "lineno", 0) or 0, "level": "error",
                             "msg": f"the JSON plan can't be read: {getattr(ex, 'msg', ex)} (line "
                                    f"{getattr(ex, 'lineno', '?')}, column {getattr(ex, 'colno', '?')})"})
            return [], [], problems
        if isinstance(data, list):
            data = {"scenes": data}
        if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
            problems.append({"line": 0, "level": "error", "msg": 'a JSON plan needs a list of scenes, e.g. {"version": '
                             '1, "scenes": [{"image": "001.png", "start": "00:00.000", "end": "00:04.200"}]}'})
            return [], [], problems
        try:
            ver = int(data.get("version", 1) or 1)
        except (TypeError, ValueError):
            ver = 0
        if ver != 1:
            problems.append({"line": 0, "level": "warning", "msg": f"JSON plan version {data.get('version')} - this "
                             "AutoEditor reads version 1; fields it doesn't know are listed"})
        g = data.get("guidelines") or []
        guide_lines = [(0, x) for x in ([g] if isinstance(g, str) else g) if isinstance(x, str)]
        unknown = []
        for i, sc in enumerate(data["scenes"], 1):
            e = blank(0, "json")
            e["label"] = f"scene #{i}"
            entries.append(e)
            if not isinstance(sc, dict):
                err(e, f"scene #{i}: must be an object with image, start and end")
                continue
            ks = list(sc.keys())
            keys = [_COL.get(str(k).strip().lower()) or _COL.get(str(k).strip().lower().replace("_", " ")) for k in ks]
            unknown += [str(k) for k, kk in zip(ks, keys) if not kk and str(k) not in unknown]
            ex_notes = []
            fill(e, keys, ["" if sc[k] is None else str(sc[k]) for k in ks], f"scene #{i}", ex_notes)
            e["notes"] = " · ".join(ex_notes)
        if unknown:
            problems.append({"line": 0, "level": "warning", "msg": f"JSON field(s) {', '.join(unknown)} not understood "
                             "- their values are kept as notes, NOT applied"})

    all_lines = (text or "").splitlines() if not is_json else []
    use = None                                 # a guide with ONE ``` block of timed lines: use only that block
    fences = [i for i, ln_ in enumerate(all_lines, 1) if ln_.strip().startswith("```")]
    blocks = [(fences[i], fences[i + 1]) for i in range(0, len(fences) - 1, 2)]
    timed_blocks = [(a, b) for a, b in blocks
                    if sum(1 for ln_ in all_lines[a:b - 1] if RANGE.match(ln_.strip())) >= 2]
    if len(timed_blocks) > 1:
        problems.append({"line": timed_blocks[1][0], "level": "error", "kind": "timing",
                         "msg": f"this document has {len(timed_blocks)} separate timed lists (in ``` blocks at lines "
                                + ", ".join(f"{a + 1}-{b - 1}" for a, b in timed_blocks)
                                + ") - keep only the one you want; nothing is applied"})
        return [], [], problems
    if timed_blocks:
        a, b = timed_blocks[0]
        use = set(range(a + 1, b))
        outside = sum(1 for i, ln_ in enumerate(all_lines, 1) if i not in use and ln_.strip()
                      and not ln_.strip().startswith("```"))
        if outside:
            problems.append({"line": a + 1, "level": "warning", "kind": "info",
                             "msg": f"using the timed list in the ``` block (lines {a + 1}-{b - 1}); the other "
                                    f"{outside} line(s) of the document are NOT used"})
    for n, raw in enumerate(all_lines, 1):
        if use is not None and n not in use:
            continue
        if GUIDE_HEAD.match(raw) or TITLE_LINE.match(raw):
            guide_mode = True                  # full-video guidelines follow (until the first scene)
            continue
        line = _strip_comment(raw).strip()
        if not line or line.startswith("//"):
            continue
        if MD_SEP.match(line) or re.fullmatch(r"[-=*_ ]{3,}", line):    # table separator / horizontal rule
            continue
        e = blank(n)
        m = RANGE.match(line)
        if m and not line.startswith("|"):                      # compact: 0:04 - 0:09 | 002.png | look
            e["fmt"] = "lines"
            e["start"], e["end"] = parse_time(m.group(1)), parse_time(m.group(2))
            rest = m.group("rest").strip()
            parts = [x.strip() for x in rest.split("|")] if "|" in rest else None
            if parts is not None:
                ref, extras = _clean_ref(parts[0]), ", ".join(x for x in parts[1:] if x)
            else:
                ma = ASSET_REF.match(rest)
                mm = None if ma else re.match(r"^(?:image|img|picture|photo|clip|file)?\s*#?\s*(\d+|\?+)\s*,?\s*(.*)$",
                                              rest, re.I)
                ref, extras = ((ma.group("ref"), ma.group("rest")) if ma else
                               (mm.group(1), mm.group(2)) if mm else ("", rest))
            e["ref"] = ref
            plain, mo, look, nts = [], [], [], []
            for x in _split_extras(extras):
                kv = _kv_phrase(x)
                if kv is None:
                    plain.append(x)
                elif kv[0] == "notes":
                    nts.append(x.split("=", 1)[1].strip())
                elif kv[1]:
                    (mo if kv[0] == "motion" else look).append(kv[1])
            m2, l2 = _split_motion(", ".join(plain))
            e["motion"] = ", ".join(x for x in mo + [m2] if x)
            e["look"] = ", ".join(x for x in look + [l2] if x)
            e["notes"] = " · ".join(nts)
            entries.append(e)
            continue
        cells = None
        if "|" in line and line.count("|") >= 2 or line.startswith("|"):
            cells = _cells(line)
            h = _header(cells)
            if h:
                header, csv_header = h, None
                bad = [c for c, k in zip(cells, h) if not k and c]
                if bad:
                    problems.append({"line": n, "level": "warning", "msg": f"line {n}: column(s) "
                                     f"{', '.join(bad)} not understood - their cells are kept as notes"})
                continue
            e["fmt"] = "table"
        elif "," in line and (csv_header or _header(next(csv.reader([line])))):
            cells = [c.strip() for c in next(csv.reader([line]))]
            h = _header(cells)
            if h:
                csv_header, header = h, None
                bad = [c for c, k in zip(cells, h) if not k and c]
                if bad:
                    problems.append({"line": n, "level": "warning", "msg": f"line {n}: column(s) "
                                     f"{', '.join(bad)} not understood - their cells are kept as notes"})
                continue
            e["fmt"] = "csv"
        if cells is not None:
            keys = header if e["fmt"] == "table" else csv_header
            extra_notes = []
            if keys and not any(k in ("start", "end", "time") for k in keys):
                ci = keys.index("image") if "image" in keys else None
                ref_ = _clean_ref(cells[ci]) if ci is not None and ci < len(cells) else ""
                txt_ = " · ".join(_unmd(c) for k, c in zip(keys, cells) if k != "image" and c.strip())
                if ref_ and txt_:              # a table that describes images (no times): notes, not scenes
                    notes.append((n, os.path.basename(ref_.replace("\\", "/")), None, txt_))
                elif ref_ or txt_:
                    prose.append((n, line))
                continue
            if keys:
                fill(e, keys, cells, f"line {n}", extra_notes)
            else:                                   # table row with no header: find the image and the times
                plain = [_unmd(c) for c in cells]
                im = next((i for i, c in enumerate(cells) if ASSET_TOKEN.fullmatch(_clean_ref(c))), None)
                if im is None:
                    prose.append((n, line))
                    continue
                e["ref"] = _clean_ref(cells[im])
                for c in plain[:im]:
                    if c.isdigit() and e["scene"] is None:
                        e["scene"] = int(c)
                times = []
                for c in plain[im + 1:] + plain[:im]:
                    mr = RANGE.match(c)
                    if mr and not mr.group("rest"):
                        times += [parse_time(mr.group(1)), parse_time(mr.group(2))]
                    elif TIME_ONLY.match(c) and (":" in c or c not in [str(e["scene"])]):
                        times.append(parse_time(c))
                    elif c and c not in plain[:im]:
                        extra_notes.append(c)
                if len(times) >= 2:
                    e["start"], e["end"] = times[0], times[1]
                if len(times) >= 3:
                    e["duration"] = times[2]
            e["notes"] = " · ".join(extra_notes)
            entries.append(e)
            continue
        # not a scene row: a note for an image / a scene, or other text (listed, never silently dropped)
        plain = _unmd(line)
        mn = NOTE_FILE.match(plain) or NOTE_SCENE.match(plain)
        if mn:
            notes.append((n, mn.groupdict().get("ref"), mn.groupdict().get("num"), mn.group("note").strip()))
        elif guide_mode and not entries and header is None and csv_header is None:
            guide_lines.append((n, line))
        else:
            prose.append((n, line))
    # ---- check each entry; times: start/end are authoritative, a given duration is only checked
    seen_scene = {}
    for e in entries:
        n = e["line"]
        lab = e.get("label") or (f"line {n}" + (f" (scene {e['scene']})" if e["scene"] is not None else ""))
        e["label"] = lab
        if e["start"] is None or e["end"] is None:
            if not e["errors"]:
                err(e, f"{lab}: needs a start and an end time, e.g. '0:04 - 0:09 | 002.png' or a table row "
                       f"'| 002.png | 0:04 | 0:09 |'")
            continue
        if e["end"] <= e["start"]:
            err(e, f"{lab}: ends ({_f(e['end'])}) before or when it starts ({_f(e['start'])})")
            continue
        if e["duration"] is not None and abs((e["end"] - e["start"]) - e["duration"]) > 0.02:
            msg = (f"{lab}: duration {e['duration']:.2f} s doesn't match start/end ({e['end'] - e['start']:.2f} s) "
                   f"- start/end are used")
            e["warnings"].append(msg)
            problems.append({"line": n, "level": "warning", "msg": msg})
        if e["scene"] is not None:
            if e["scene"] in seen_scene:
                msg = f"{lab}: scene {e['scene']} also appears on line {seen_scene[e['scene']]}"
                e["warnings"].append(msg)
                problems.append({"line": n, "level": "warning", "msg": msg})
            seen_scene.setdefault(e["scene"], n)
        ref = re.sub(r"^(?:image|img|picture|photo|clip|file)\s*#?\s*", "", e["ref"].strip(), flags=re.I)
        e["ref"] = ref
        if not ref or set(ref) <= {"?"}:
            err(e, f"{lab} ({_f(e['start'])}-{_f(e['end'])}): no image on this line - add its file name, e.g. "
                   f"'{_f(e['start'])} - {_f(e['end'])} | 004.jpg'" if ref == "" else
                   f"{lab} ({_f(e['start'])}-{_f(e['end'])}): choose an image", kind="no_asset")
            continue
        before = len(problems)
        e["media"] = _resolve_image(ref, media, index, n, problems, ctx=ctx)
        for pr in problems[before:]:
            pr["msg"] = pr["msg"].replace(f"line {n}:", f"{lab}:", 1)
            if pr["level"] == "error":
                e["errors"].append(pr["msg"])
                e.setdefault("kind", pr.get("kind", "not_found"))
            else:
                e["warnings"].append(pr["msg"])
        if e["media"] and os.path.basename(e["media"]).lower() != os.path.basename(ref.replace("\\", "/")).lower() \
                or e["media"] and ("/" in ref or "\\" in ref):
            e["resolved_note"] = e["media"]
    rows = []
    for k, e in enumerate(entries):
        if e["errors"] or not e["media"]:
            continue
        e["extras"] = ", ".join(x for x in (e["motion"], e["look"]) if x)
        rows.append({"line": e["line"], "label": e["label"], "scene": e["scene"], "start": I.snap(e["start"], fps),
                     "end": I.snap(e["end"], fps), "start_raw": e["start"], "end_raw": e["end"], "ref": e["ref"],
                     "media": e["media"], "extras": e["extras"], "notes": e["notes"], "entry": e})
    # ---- notes lines -> their scenes (by image file or scene number)
    attached = 0
    for n, ref, num, note in notes:
        if ref:
            hit = [e for e in entries if e["ref"] and os.path.basename(e["ref"]).lower() == ref.lower()
                   or e["media"] and os.path.basename(e["media"]).lower() == ref.lower()]
            what = ref
        else:
            hit = [e for e in entries if e["scene"] == int(num)] or (
                [entries[int(num) - 1]] if not any(e["scene"] is not None for e in entries)
                and 0 < int(num) <= len(entries) else [])
            what = f"scene {num}"
        if not hit:
            problems.append({"line": n, "level": "warning",
                             "msg": f"line {n}: note for {what} - {what} isn't in the plan, so the note is NOT "
                                    "attached to anything (it stays in the plan text)"})
            continue
        if len(hit) > 1:
            problems.append({"line": n, "level": "warning",
                             "msg": f"line {n}: note for {what} - {what} is used by {len(hit)} scenes (lines "
                                    f"{', '.join(str(e['line']) for e in hit)}); attached to all of them"})
        for e in hit:
            e["notes"] = (e["notes"] + " · " if e["notes"] else "") + note
            e.setdefault("note_lines", []).append(n)
        attached += 1
    for r in rows:
        r["notes"] = r["entry"]["notes"]
    with_notes = sum(1 for e in entries if e["notes"])
    if with_notes:
        problems.append({"line": 0, "level": "warning",
                         "msg": f"{with_notes} scene(s) have notes ({attached} note line(s) attached). Notes are "
                                "kept with the scene but NOT applied as editing instructions - put editing words "
                                "in the Motion / Look columns (or the scene details)."})
    for n, line in prose:
        problems.append({"line": n, "level": "warning",
                         "msg": f"line {n}: not a scene row or a note - NOT used: “{_unmd(line)[:70]}”"})
    fmt, guide = _guidelines(guide_lines, problems) if guide_lines else ({}, {})
    for r in rows:
        r["project"], r["guide"] = fmt, guide
    for folder, used in (ctx.get("prefix_fallback") or {}).items():
        ex_ln, ex_ref, ex_path = used[0]
        problems.append({"line": ex_ln, "level": "warning", "kind": "mapped",
                         "msg": f"“{folder}/” isn't a folder inside your images folder - {len(used)} row(s) use the "
                                f"scene picture with the same file name instead (e.g. {ex_ref} → {ex_path}). "
                                "Reference folders are never used this way. To choose the folder yourself: Map "
                                "folder…"})
    if ctx.get("ref_skipped"):
        problems.append({"line": 0, "level": "warning", "kind": "mapped",
                         "msg": f"{ctx['ref_skipped']} row(s): a picture with the same name in a reference folder "
                                "(e.g. Character_References) was ignored - the scene picture is used"})
    order = [e for e in entries if e.get("start") is not None and not e.get("errors")]
    late = next((b for a, b in zip(order, order[1:]) if b["start"] < a["start"] - 1e-6), None)
    if late is not None:
        problems.append({"line": late["line"], "level": "warning", "kind": "order",
                         "msg": f"{late['label']} starts earlier than the row above it - scenes play by their times, "
                                "not by row order"})
    covered = rows + [{"line": e["line"], "label": e["label"], "start": I.snap(e["start"], fps),
                       "end": I.snap(e["end"], fps)}
                      for e in entries if e.get("errors") and e.get("kind") in
                      ("no_asset", "not_found", "ambiguous", "unmapped") and e.get("start") is not None
                      and e.get("end") is not None and e["end"] > e["start"]]
    _coverage(covered, duration, problems)
    rows.sort(key=lambda r: r["start"])
    # look phrases: every phrase must be understood (listed otherwise, never silently ignored)
    for r in rows:
        if not r["extras"]:
            r["text"] = ""
            continue
        phrases = [_phrase(x) for x in _split_extras(r["extras"])]
        r["text"] = ". ".join(x.rstrip(".") for x in phrases) + "."
        _, interp, unresolved, _ = I.parse(r["text"])
        r["interp"] = r["entry"]["interp"] = interp
        for u in unresolved:
            msg = f"{r['label']}: “{u}” not understood - NOT applied"
            r["entry"]["warnings"].append(msg)
            problems.append({"line": r["line"], "level": "warning", "msg": msg})
        if any(m_.startswith("visual ") for _, m_ in interp):
            msg = f"{r['label']}: put one image per scene (extra images on a line are ignored)"
            r["entry"]["warnings"].append(msg)
            problems.append({"line": r["line"], "level": "warning", "msg": msg})
    if not entries and not any(p["level"] == "error" for p in problems):
        problems.append({"line": 0, "level": "error", "msg": "The editing plan has no scenes yet."})
    return entries, rows, problems


TABLE_HEAD = "| Scene | Image | Start | End | Duration | Motion | Look | Notes |"
SHEET_LABELS = {"image": "Image", "start": "Start", "end": "End", "duration": "Duration", "motion": "Motion",
                "notes": "Notes"}


def _structure_line(line):
    """Header / separator / rule lines of a table or CSV (not content)."""
    t = _strip_comment(line).strip()
    if not t:
        return False                           # a comment line: kept as it is
    if MD_SEP.match(t) or re.fullmatch(r"[-=*_ ]{3,}", t):
        return True
    if "|" in t and _header(_cells(t)):
        return True
    if "," in t and "|" not in t:
        import csv
        return bool(_header([c.strip() for c in next(csv.reader([t]))]))
    return False


def plan_as_table(text, entries):
    """The plan as ONE Markdown table (all scenes, notes merged into the Notes column). Lines that are not
    scenes and not attached notes (comments, unattached notes, other text) are kept below it unchanged."""
    used = {e["line"] for e in entries} | {n for e in entries for n in e.get("note_lines", [])}
    out = [TABLE_HEAD, "|---|---|---|---|---|---|---|---|"]

    def cell(v):
        return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()
    for k, e in enumerate(entries, 1):
        a, b = e.get("start"), e.get("end")
        dur = f"{b - a:.3f}" if a is not None and b is not None else (
            f"{e['duration']:.3f}" if e.get("duration") is not None else "")
        out.append("| " + " | ".join(cell(x) for x in (
            e["scene"] if e.get("scene") is not None else k, e.get("ref") or "",
            _tc(a) if a is not None else "", _tc(b) if b is not None else "", dur,
            e.get("motion") or "", e.get("look") or "", e.get("notes") or "")) + " |")
    first = min(used) if used else 10 ** 9
    keep = [(n, ln) for n, ln in enumerate((text or "").splitlines(), 1)
            if n not in used and ln.strip() and not _structure_line(ln)]
    head = [ln for n, ln in keep if n < first]          # e.g. a title comment stays above the table
    tail = [ln for n, ln in keep if n > first]
    return "\n".join(head + out + ([""] + tail if tail else [])) + "\n"


def _sheet_set(text, block_line, key, value):
    """Set one field of a SCENE block in place (added at the end of the block if it isn't there)."""
    lines = (text or "").splitlines()
    i0 = block_line - 1
    i1 = next((j for j in range(i0 + 1, len(lines)) if SHEET_RE.match(lines[j])), len(lines))
    new = f"{SHEET_LABELS[key]}: {value}"
    for j in range(i0 + 1, i1):
        fm = FIELD_RE.match(lines[j])
        if fm and _key(fm.group(1)) == key:
            lines[j] = new
            break
    else:
        last = i1
        while last - 1 > i0 and not lines[last - 1].strip():
            last -= 1
        lines.insert(last, new)
    return "\n".join(lines) + "\n"


def edit_entry(text, entries, idx, field, value):
    """A change made in the review table -> new plan text. field: image/start/end/motion/notes/look.
    Returns (new_text, message). Times must be readable; nothing changes otherwise."""
    e = entries[idx]
    value = (value or "").strip()
    if field in ("start", "end"):
        t = parse_time(re.sub(r"\s*(s|sec|secs|seconds?)\s*$", "", value, flags=re.I))
        if t is None:
            return text, f"“{value}” isn't a time - use 01:56.84 or 116.84. Nothing was changed."
    if e.get("fmt") == "sheet":
        if field == "look":
            return text, "Scene sheets keep sound, B-roll, effects and transitions in their own fields - edit them " \
                         "in the plan text. Nothing was changed."
        bl = e.get("src_line") or e["line"]
        if field in ("start", "end"):
            a = t if field == "start" else e["start"]
            b = t if field == "end" else e["end"]
            text = _sheet_set(text, bl, field, _tc(t))
            if a is not None and b is not None and b > a:
                text = _sheet_set(text, bl, "duration", f"{b - a:.3f} seconds")
        else:
            text = _sheet_set(text, bl, "image" if field == "image" else field, value)
        return text, ""
    if e.get("fmt") == "lines" and field == "image" and e.get("ref"):
        lines = (text or "").splitlines()       # a plain plan line: change just the picture, in place
        i = e["line"] - 1
        if 0 <= i < len(lines) and e["ref"] in lines[i]:
            head, sep, tail = lines[i].partition("|")
            if sep and e["ref"] in tail:
                lines[i] = head + sep + tail.replace(e["ref"], value, 1)
            else:
                lines[i] = lines[i].replace(e["ref"], value, 1)
            return "\n".join(lines) + "\n", ""
    e = dict(e)
    if field in ("start", "end"):
        e[field] = t
        e["duration"] = None
    elif field == "image":
        e["ref"] = value
    else:
        e[field] = value
    new = list(entries)
    new[idx] = e
    was_table = all(x.get("fmt") == "table" for x in entries) and TABLE_HEAD in (text or "").splitlines()
    return plan_as_table(text, new), ("" if was_table else
                                      "The plan text was rewritten as one table so your change could be saved "
                                      "(nothing else was changed; Ctrl+Z undoes).")


def parse_plan(text, media, duration=None, fps=30, words=None, ctx=None):
    """-> (rows, problems). Nothing is changed. See parse_entries for the formats."""
    _entries, rows, problems = parse_entries(text, media, duration, fps, words, ctx=ctx)
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
    rows, problems = parse_plan(project.get("timed_plan") or "", project_media(project), dur, fps, words,
                                ctx=plan_context(project))
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
        rows, problems = parse_plan(project.get("timed_plan") or "", project_media(project), dur, fps, words,
                                    ctx=plan_context(project))
    end_gap = dur - rows[-1].get("end_raw", rows[-1]["end"])
    rows[-1]["end"] = dur                       # within 0.5 s: the last shot ends with the narration
    keep_state = {k: copy.deepcopy(project.get(k)) for k in ("scenes", "broll", "sfx_events", "overlays",
                                                              "edit_instructions", "plan_report")}
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
    guide = rows[0].get("guide") or {}            # full-video guidelines written in the plan itself
    apply_motion_level(scenes, preset, guide.get("motion_level", st.get("motion_level", "Medium")),
                       seed=int(st.get("seed", 7)))
    reassign_transitions(scenes, preset, words, seed=int(st.get("seed", 7)))
    apply_guidelines(project, scenes)
    for s_ in scenes:                          # explicit per-scene settings are applied after this and win
        if guide.get("transition"):
            s_["transition"] = guide["transition"]
        if guide.get("fit"):
            s_["fit"] = guide["fit"]
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
            if r.get("notes"):                  # descriptive notes from the plan: kept with the scene, not applied
                notes = (notes or []) + [r["notes"]]
        s.update(timing_mode="absolute", start_seconds=r["start"], end_seconds=r["end"], narration_anchor=None,
                 end_anchor=None, primary_visual={"asset_filename": os.path.basename(r["media"])}, segments=[])
        s.update({k_: v for k_, v in keep.items() if v})
        rec.update(settings=s, instruction_text=(f"{_lab(r)}: {r['ref']} {_f(r['start'])}-{_f(r['end'])}"
                                                 if r.get("settings") is not None else
                                                 f"{_f(r['start'])} - {_f(r['end'])} | {r['ref']} | {r['extras']}"),
                   interpretation=[[c, m] for c, m in interp], unresolved=unresolved, from_plan=True,
                   choices={"primary_visual": 0},
                   plan_times=[r.get("start_raw", r["start"]), r.get("end_raw", r["end"])])
        if notes:
            rec["notes"] = "\n".join(notes)
        ok, msg = I.apply(project, sid, log, ctx, reviewed=True, placed=True)
        if r["extras"] or r.get("settings") is not None:
            n_look += 1
        if not ok:
            failed.append(f"{_lab(r)}: NOT fully applied - {msg}")
    by_sid = {r["sid"]: r for r in rows}
    for s in project["scenes"]:                # the plan decides the picture: nothing replaces it
        s["media"] = os.path.normpath(by_sid[s["id"]]["media"])       # the same form a manual choice stores
    # every planned scene must now be on the timeline with ITS picture and times - otherwise undo everything
    got = {s["id"]: s for s in project["scenes"]}
    bad = [r for r in rows if r["sid"] not in got
           or os.path.normcase(got[r["sid"]]["media"]) != os.path.normcase(os.path.normpath(r["media"]))
           or abs(got[r["sid"]]["start"] - r["start"]) > 1e-3]
    if bad:
        for k_, v_ in keep_state.items():
            project[k_] = v_
        raise ValueError(f"{len(bad)} scene(s) could not be put on the timeline as planned (first: {_lab(bad[0])}) - "
                         "nothing was changed")
    if os.environ.get("AUTOEDIT_DEBUG"):
        for r in rows:
            log(f"[plan] {_lab(r)}: “{r['ref']}” → {r['media']} @ {r['start']:.3f}-{r['end']:.3f}")
    used = {os.path.normcase(r["media"]) for r in rows}
    unused = [m for m in project_media(project) if os.path.normcase(m) not in used]
    sheet = rows[0].get("settings") is not None
    rep = [f"{'Scene sheet' if sheet else 'Timed edit plan'}: {len(rows)} scenes built exactly as written "
           f"({_f(0)}-{_f(dur)})" + ("; anything not specified is None." if sheet else
                                    f"; {n_look} with their own look, the rest follow the project guidelines.")]
    if fmt_changes:
        rep.append("Export format from the plan's guidelines: " + "; ".join(fmt_changes))
    if abs(end_gap) > 1e-6:
        rep.append(f"The plan's last time is {rows[-1].get('end_raw', dur):.3f} s and the narration lasts {dur:.6f} s "
                   f"({end_gap * 1000:+.1f} ms): the last scene runs to the end of the narration.")
    if guide:
        rep.append("Guidelines from the plan (scenes without their own setting): " +
                   ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in guide.items()))
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
                             float(project.get("duration") or 0), int(st.get("fps", 30)), ctx=plan_context(project))
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


def parse_sheet(text, media, duration=None, fps=30, words=None, ctx=None):
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
    project, guide = _guidelines(general, problems)
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
        path = _resolve_image(img.strip(), media, index, b["num"], problems, what="SCENE", src_line=ln, ctx=ctx)
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
        r["project"], r["guide"] = project, guide
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


GUIDE_HEAD = re.compile(r"^\s*(?:#+\s*)?\**\s*(project guidelines|guidelines|general rules|project rules|"
                        r"rules for the whole video)\s*\**\s*:?\s*$", re.I)
TITLE_LINE = re.compile(r"^\s*(?:#+\s*)?\**\s*strict visual editing instructions\s*\**\s*$", re.I)


def _guidelines(lines, problems):
    """Full-video guidelines -> (export format, overrides for the strict build). Every sentence is used,
    acknowledged, or listed as NOT applied - never silently dropped."""
    from .planner import parse_project_instructions
    fmt = _general_rules(lines, problems)
    ov = {}
    for n, raw in lines:
        t = _unmd(raw)
        if not t or GUIDE_HEAD.match(raw) or TITLE_LINE.match(raw):
            continue
        for sent in [x.strip() for x in re.split(r"(?<=[.;!])\s+", t) if x.strip()]:
            low = sent.lower()
            if re.search(r"\b(16:9|9:16|1:1|\d+\s*fps|landscape|portrait|vertical|horizontal|square|one video|"
                         r"export)\b|\d{3,4}\s*[x×]\s*\d{3,4}", low):
                if "aspect" not in fmt:
                    for pat, asp in ((r"\b(landscape|horizontal)\b", "16:9"), (r"\b(portrait|vertical)\b", "9:16"),
                                     (r"\bsquare\b", "1:1")):
                        if re.search(pat, low):
                            fmt["aspect"] = asp
                continue                       # export format: handled above
            if re.search(r"\b(narration|voice ?over|speech|audio)\b", low) and \
                    re.search(r"\b(unchanged|never|don'?t|do not|keep|as (it )?is)\b", low):
                continue                       # always true: the narration is never changed
            o, res = parse_project_instructions(sent)
            if not (res and res[0][1]):
                problems.append({"line": n, "level": "warning",
                                 "msg": f"line {n}: guideline “{sent[:70]}” not understood - NOT applied"})
                continue
            for k, v in o.items():
                if k in ("motion_level", "transition", "fit"):
                    ov[k] = v
                else:
                    problems.append({"line": n, "level": "warning",
                                     "msg": f"line {n}: guideline “{sent[:70]}” doesn't apply to a strict plan (your "
                                            "plan sets the pictures, order and times) - NOT applied"})
    return fmt, ov


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
        if mw in ("none", "inherit", "auto"):
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
                m_end = re.match(r"^\s*" + TIME + r"\s*(?:-|–|—|to|until)\s*(?:end|the end)\s*$", sv, re.I)
                if ci is None and m_end:            # 'from 00:02.000 to the end of the clip'
                    ci, co = parse_time(m_end.group(1)), None
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
                       "cut" if "cut" in lv else "slide" if "slide" in lv else "whip" if "whip" in lv else
                       "zoom" if re.search(r"\bzoom\b", lv) else None)
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
                out.append(f"B-roll source in/out: {_tc(b.get('clip_in') or 0)} - "
                           + (_tc(b["clip_out"]) if b.get("clip_out") is not None else "end"))
            out += [f"B-roll placement: {'Full-frame replacement' if b.get('mode') != 'overlay' else 'Overlay ' + b.get('position', 'top_right').replace('_', ' ')}",
                    f"B-roll source audio: {'Muted' if b.get('audio_gain_db') is None else str(b['audio_gain_db']) + ' dB'}"]
        else:
            out.append("B-roll: " + {"auto": "Automatic", "inherit": "Inherit"}.get(s.get("b_roll_mode"), "None"))
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


# ============================================================================ diagnostics
TIMELINE_KEYS = ("scenes", "broll", "sfx_events", "overlays", "words", "duration", "voiceover", "music", "settings")


def timeline_revision(project):
    """Short content hash of everything an export draws - logged with each export, so a video can be traced
    back to the exact timeline it was made from."""
    import hashlib
    import json
    blob = json.dumps({k: project.get(k) for k in TIMELINE_KEYS}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def diagnostic_plan(project):
    """One row per timeline scene: ID, asset/path, the times the plan asked for, the frame-snapped times on the
    timeline, and the settings in force (with where each came from)."""
    recs = I.records(project)
    out = []
    for k, sc in enumerate(project.get("scenes") or [], 1):
        rec = recs.get(sc.get("id")) or {}
        req = rec.get("plan_times") or [None, None]
        sid = sc.get("id")
        out.append({
            "n": k, "scene_id": sid, "plan_line": sc.get("plan_line"), "asset": os.path.basename(sc.get("media") or ""),
            "path": sc.get("media") or "", "requested_start": req[0], "requested_end": req[1],
            "start": round(sc["start"], 6), "end": round(sc["end"], 6), "duration": round(sc["end"] - sc["start"], 6),
            "motion": sc.get("motion"), "motion_amount": sc.get("motion_amount"), "transition_out": sc.get("transition"),
            "transition_s": sc.get("transition_s"), "fit": sc.get("fit") or project["settings"].get("fit"),
            "focus": sc.get("focus"), "effects": sc.get("effects") or [], "captions": sc.get("captions", "inherit"),
            "broll": [{k_: b.get(k_) for k_ in ("media", "start", "end", "mode", "clip_in", "clip_out", "audio_gain_db")}
                      for b in project.get("broll") or [] if b.get("source") == sid],
            "sfx": [{k_: e.get(k_) for k_ in ("file", "t", "rel_db", "fade_in", "fade_out")}
                    for e in project.get("sfx_events") or [] if e.get("source") == sid],
            "locked": bool(sc.get("locked")), "locked_fields": sc.get("locked_fields") or [],
            "status": sc.get("status"), "why": sc.get("why"), "provenance": sc.get("provenance") or {},
            "notes": rec.get("notes") or "", "warnings": sc.get("warnings") or []})
    return {"autoeditor_diagnostic_plan": 1, "timeline_revision": timeline_revision(project),
            "duration": project.get("duration"), "fps": project["settings"].get("fps"),
            "aspect": project["settings"].get("aspect"), "resolution": project["settings"].get("resolution"),
            "scene_mode": project["settings"].get("scene_mode"), "scenes": out}


def save_diagnostic_plan(project, path):
    """Write the diagnostic plan as JSON (and a CSV next to it for a spreadsheet)."""
    import csv
    import json
    d = diagnostic_plan(project)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, default=str)
    cols = ("n", "scene_id", "asset", "requested_start", "requested_end", "start", "end", "duration", "motion",
            "transition_out", "fit", "status", "notes", "path")
    with open(os.path.splitext(path)[0] + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in d["scenes"]:
            w.writerow([r.get(c) for c in cols])
    return d


# ============================================================================ templates (More → Save a plan template)
TEMPLATES = {
    ".md": """# My editing plan

PROJECT GUIDELINES
- Use subtle motion
- Cuts only

| Scene | Image   | Start     | End       | Duration | Motion       | Transition | Notes                 |
|-------|---------|-----------|-----------|----------|--------------|------------|-----------------------|
| 1     | 001.png | 00:00.000 | 00:04.200 | 4.200    | slow zoom in | cut        | opening shot          |
| 2     | 002.png | 00:04.200 | 00:08.800 | 4.600    | none         | crossfade  |                       |
| 3     | 003.png | 00:08.800 | 00:12.000 | 3.200    | pan right    | cut        |                       |

- **002.png:** notes like this are kept with the scene that uses 002.png (not applied as edits)
""",
    ".csv": """scene_id,image,start,end,motion,transition,sfx,notes
S001,001.png,00:00.000,00:04.200,zoom_in,cut,,opening shot
S002,002.png,00:04.200,00:08.800,none,crossfade,whoosh.wav,
S003,003.png,00:08.800,00:12.000,pan right,cut,none,
""",
    ".json": """{
  "version": 1,
  "guidelines": ["Use subtle motion", "Cuts only"],
  "scenes": [
    {"scene_id": "S001", "image": "001.png", "start": "00:00.000", "end": "00:04.200", "motion": "zoom_in",
     "transition": "cut", "notes": "opening shot"},
    {"scene_id": "S002", "image": "002.png", "start": "00:04.200", "end": "00:08.800", "motion": "none",
     "transition": "crossfade", "sfx": "whoosh.wav"},
    {"scene_id": "S003", "image": "003.png", "start": "00:08.800", "end": "00:12.000", "motion": "pan right",
     "transition": "cut", "sfx": "none"}
  ]
}
""",
    ".txt": """# One line per scene:  start - end | image | how it looks (optional)
00:00.000 - 00:04.200 | 001.png | motion=zoom_in | transition=cut
00:04.200 - 00:08.800 | 002.png | motion=none | transition=crossfade
00:08.800 - 00:12.000 | 003.png | pan right, cut
""",
}
