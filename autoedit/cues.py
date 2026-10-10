"""Image cues: tell AutoEditor exactly which image goes with which part of the narration.

Two ways in:
  1. A script file with image markers - the image is shown from the first word after its marker
     until the next marker:
         [1] In 1910 seven men boarded a private train...
         [2] They told the staff they were duck hunters.
         [3, 4] The island was ...          (two images share this part, split at a pause)
         [05.png] ...                       (a file name instead of a number)
     "Image 6:" / "IMG 6:" at the start of a line also works. A .csv with columns
     image,text (or image,start with a time like 1:23.5) works too.
  2. The "Match images to narration" dialog: type an image number next to the sentence where
     it should start.

The markers are matched to the TRANSCRIPT (word timings from the voiceover), so small wording
differences between script and recording are fine.
"""
import csv
import difflib
import os
import re

from .util import is_video

MARK_RE = re.compile(r"\[\s*((?:img|image|pic|scene|shot)?\s*#?\s*[^\[\]\n]{1,60}?)\s*\]", re.I)
LINE_RE = re.compile(r"^\s*(?:img|image|pic|shot)\s*#?\s*([\w\-., ]{1,40}?)\s*[:\-–]\s*", re.I | re.M)


def _tok(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower().replace("'", "").replace("’", ""))


def media_number(path):
    """First number in the file name: '012.png' -> 12, 'image_7.jpg' -> 7, 'Scene 3 (2).png' -> 3."""
    m = re.search(r"\d+", os.path.splitext(os.path.basename(path))[0])
    return int(m.group(0)) if m else None


def media_index(media):
    """{number: [paths]} and {lower basename / stem: path}"""
    by_num, by_name = {}, {}
    for m in media:
        n = media_number(m)
        if n is not None:
            by_num.setdefault(n, []).append(m)
        b = os.path.basename(m).lower()
        by_name[b] = m
        by_name.setdefault(os.path.splitext(b)[0], m)
    return by_num, by_name


def resolve(ref, media, index=None):
    """'3' / '3,4' / '3-5' / '03.png' / 'image 7' -> ([paths], [problems])"""
    by_num, by_name = index or media_index(media)
    out, bad = [], []
    ref = re.sub(r"^(?:img|image|pic|scene|shot)\s*#?\s*", "", ref.strip(), flags=re.I)
    for part in re.split(r"\s*[,;+&]\s*|\s+and\s+", ref):
        part = part.strip()
        if not part:
            continue
        rng = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        nums = list(range(int(rng.group(1)), int(rng.group(2)) + 1)) if rng else \
            ([int(part)] if part.isdigit() else None)
        if nums:
            for n in nums:
                hits = by_num.get(n)
                if hits:
                    out.append(hits[0])
                    if len(hits) > 1:
                        bad.append(f"image {n}: {len(hits)} files have this number - used "
                                   f"{os.path.basename(hits[0])}")
                else:
                    bad.append(f"image {n}: no file with this number")
            continue
        p = by_name.get(part.lower()) or by_name.get(os.path.splitext(part.lower())[0])
        if p:
            out.append(p)
        else:
            bad.append(f"'{part}': no such file in the project's media")
    return out, bad


def has_markers(text):
    return bool(MARK_RE.search(text or "") or LINE_RE.search(text or ""))


def strip_markers(text):
    """Script text without the image markers (used for caption spelling)."""
    return LINE_RE.sub("", MARK_RE.sub(" ", text or ""))


def parse_script(text):
    """-> [{'ref': '3', 'text': 'narration after the marker'}] in order."""
    marks = []
    for m in MARK_RE.finditer(text):
        ref = m.group(1)
        # only real image refs: a number or a file name - not stage notes like [pause] or [music]
        if re.search(r"\d", ref) or re.search(r"\.(jpe?g|png|webp|gif|bmp|tiff?|mp4|mov|mkv|webm)$", ref, re.I):
            marks.append((m.start(), m.end(), ref))
    for m in LINE_RE.finditer(text):
        if not any(a <= m.start() < b for a, b, _ in marks):
            marks.append((m.start(), m.end(), m.group(1)))
    marks.sort()
    cues = []
    for i, (a, b, ref) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        cues.append({"ref": ref.strip(), "text": text[b:end].strip()})
    return cues


def parse_time(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        parts = [float(x) for x in s.replace(",", ".").split(":")]
    except ValueError:
        return None
    t = 0.0
    for x in parts:
        t = t * 60 + x
    return t


def parse_csv(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    has_head = any(h in ("image", "file", "media", "text", "start", "time", "narration") for h in head)
    if has_head:
        col = {h: i for i, h in enumerate(head)}
        ci = next((col[k] for k in ("image", "file", "media") if k in col), 0)
        ct = next((col[k] for k in ("text", "narration", "line") if k in col), None)
        cs = next((col[k] for k in ("start", "time") if k in col), None)
        rows = rows[1:]
    else:
        ci, ct, cs = 0, 1, None
    cues = []
    for r in rows:
        if ci >= len(r) or not r[ci].strip():     # short or empty row
            continue
        cue = {"ref": r[ci].strip(), "text": r[ct].strip() if ct is not None and ct < len(r) else ""}
        if cs is not None and cs < len(r):
            cue["t"] = parse_time(r[cs])
        elif ct is not None and ct < len(r) and parse_time(r[ct]) is not None:     # '1:23.5' = a start time
            cue["t"] = parse_time(r[ct])
            cue["text"] = ""
        cues.append(cue)
    return cues


def load_cues(path):
    if path.lower().endswith(".csv"):
        return parse_csv(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_script(f.read())


def align(words, cues):
    """Find where each cue starts in the transcript. Adds 'wi' (word index), 'score' (0-1)."""
    wt = []
    for w in words:
        wt.append((_tok(w["w"]) or [""])[0])
    st, owner = [], []
    for ci, c in enumerate(cues):
        for t in _tok(c.get("text", "")):
            st.append(t)
            owner.append(ci)
    sm = difflib.SequenceMatcher(None, st, wt, autojunk=False)
    m = [None] * len(st)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            m[a + k] = b + k
    for ci, c in enumerate(cues):
        idx = [i for i, o in enumerate(owner) if o == ci]
        hit = [m[i] for i in idx if m[i] is not None]
        c["score"] = (len(hit) / len(idx)) if idx else 0.0
        if c.get("t") is not None:
            c["wi"] = next((i for i, w in enumerate(words) if (w["s"] + w["e"]) / 2 >= c["t"]), len(words))
            c["score"] = 1.0
        elif hit:
            # first matched word, stepped back by the unmatched words before it in this cue
            first = next(i for i in idx if m[i] is not None)
            c["wi"] = max(0, m[first] - (first - idx[0]))
        else:
            c["wi"] = None
    # fill gaps / keep order
    last = 0
    for i, c in enumerate(cues):
        if c["wi"] is None:
            nxt = next((d["wi"] for d in cues[i + 1:] if d.get("wi") is not None), len(words))
            c["wi"] = (last + nxt) // 2 if i else 0
        c["wi"] = max(c["wi"], last if i else 0)
        last = c["wi"]
    return cues


def cut_time(words, i):
    if i <= 0 or not words:
        return 0.0
    if i >= len(words):
        return words[-1]["e"]
    return round((words[i - 1]["e"] + words[i]["s"]) / 2, 3)


def build_scenes(words, duration, cues, preset, log=print):
    """cues: [{'wi': word index, 'media': [paths]}] (sorted) -> scene list.
    Several images on one cue share its time, split at the pauses nearest to equal parts."""
    from .scenes import _weighted, scene_text
    import random
    rng = random.Random(11)
    cues = [c for c in cues if c.get("media")]
    if not cues:
        raise ValueError("No image markers matched any images.")
    cues = sorted(cues, key=lambda c: c["wi"])
    starts = [0.0] + [cut_time(words, c["wi"]) for c in cues[1:]]
    ends = starts[1:] + [duration]
    gaps = [cut_time(words, i) for i in range(1, len(words))]
    scenes = []
    for c, a, b in zip(cues, starts, ends):
        k = len(c["media"])
        cuts = [a]
        for j in range(1, k):
            target = a + (b - a) * j / k
            near = [g for g in gaps if cuts[-1] + 0.4 < g < b - 0.4]
            cuts.append(min(near, key=lambda g: abs(g - target)) if near else target)
        cuts.append(b)
        for m, s, e in zip(c["media"], cuts, cuts[1:]):
            if e - s < 0.05:
                continue
            scenes.append({"start": round(s, 3), "end": round(e, 3), "text": scene_text(words, s, e), "media": m,
                           "motion": "none" if is_video(m) else _weighted(rng, preset["motions"]),
                           "transition": "cut", "zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0})
    if scenes:
        scenes[0]["start"] = 0.0
        scenes[-1]["end"] = duration
    return scenes


def plan_from_cues(project, cues, log=print):
    """Resolve, align and build. Returns (scenes, report_lines). Doesn't change the project."""
    from .presets import PRESETS
    from .project import project_media
    from .scenes import apply_motion_level, reassign_transitions
    st = project["settings"]
    media = project_media(project)
    index = media_index(media)
    words, dur = project.get("words") or [], project.get("duration") or 0.0
    if not words:
        raise ValueError("Analyze the voiceover first - cues are matched to the transcript.")
    report = []
    for c in cues:
        if "media" not in c:
            c["media"], bad = resolve(c["ref"], media, index)
            report += [f"[{c['ref']}] {b}" for b in bad]
    need_align = [c for c in cues if c.get("wi") is None]
    if need_align:
        align(words, cues)
    weak = [c for c in cues if c.get("score", 1) < 0.5 and c.get("text")]
    for c in weak:
        report.append(f"[{c['ref']}] text only partly found in the recording ({c['score']:.0%}) - check its "
                      f"start: \"{c['text'][:50]}\"")
    preset = PRESETS.get(st.get("preset"), PRESETS["Documentary"])
    scenes = build_scenes(words, dur, cues, preset, log)
    apply_motion_level(scenes, preset, st.get("motion_level", "Medium"), seed=int(st.get("seed", 7)))
    reassign_transitions(scenes, preset, words, seed=int(st.get("seed", 7)))
    used = {os.path.normcase(s["media"]) for s in scenes}
    unused = [m for m in media if os.path.normcase(m) not in used]
    report.insert(0, f"{len(scenes)} scenes from {len(cues)} cues; {len(used)} images used"
                     + (f", {len(unused)} not used" if unused else "") + ".")
    return scenes, report


def segments(words, max_words=28, pause=0.55):
    """Narration split into sentence-like rows for the cue dialog: [(first word index, last, text)]."""
    out, a = [], 0
    for i, w in enumerate(words):
        end = i == len(words) - 1
        brk = w["w"].rstrip().endswith((".", "!", "?", "…", '."', '?"')) or \
            (not end and words[i + 1]["s"] - w["e"] >= pause) or (i - a + 1) >= max_words
        if brk or end:
            out.append((a, i, " ".join(x["w"] for x in words[a:i + 1])))
            a = i + 1
    return out


def parse_directions(text, media):
    """The 'Directions' box. Either
       - text with markers ([1] ... [2] ...) -> used as written, or
       - plain paragraphs (blank line between, or one per line if there are no blank lines):
         paragraph 1 -> the 1st image, paragraph 2 -> the 2nd image ... in file order.
    Returns (cues, note)."""
    text = (text or "").strip()
    if not text:
        return [], ""
    if has_markers(text):
        return parse_script(text), ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) <= 1:
        paras = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # strip "Scene 3:" / "3." / "3)" style numbering at the start of a paragraph
    clean = [re.sub(r"^\s*(?:scene|shot|image|img)?\s*#?\d+\s*[:.)\-–]\s*", "", p, flags=re.I) for p in paras]
    order = list(media)
    by_num, _ = media_index(media)
    if by_num:  # numbered images: follow the numbers (1, 2, 3 ...), not the folder listing
        order = [by_num[n][0] for n in sorted(by_num)] + [m for m in media if media_number(m) is None]
    if not order:
        return [], f"{len(clean)} paragraphs but no images yet - add an images folder first"
    cues = [{"ref": os.path.basename(m), "media": [m], "text": t} for m, t in zip(order, clean)]
    note = f"{len(clean)} paragraphs -> images 1..{len(cues)} in order"
    if len(clean) > len(order):
        extra = clean[len(order):]
        cues[-1]["text"] += " " + " ".join(extra)
        note += f"; {len(extra)} paragraph(s) more than images - they stay on the last image"
    elif len(order) > len(clean):
        note += f"; {len(order) - len(clean)} image(s) not used (fewer paragraphs than images)"
    return cues, note
