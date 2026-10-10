"""Smart scene planning: the exact script + the recording decide WHEN, the content decides WHAT.

Order of authority (highest first):
  1. locked scenes (their visual and times are never changed by re-planning)
  2. explicit assignments: scene instructions, [n] markers, one-paragraph-per-image directions,
     an imported scene map
  3. project instructions (motion, transitions, framing, order, repeats, scene length)
  4. automatic defaults

Scenes are cut at topic changes (where the vocabulary of the script changes), paragraph breaks and
pauses - not by dividing the audio equally. Visuals are matched by the words their descriptions
share with each scene (descriptions come only from real data: imported prompts, sidecar files,
embedded metadata - see assets.py). Without descriptions the plan falls back to file order and
SAYS so for every scene. Similarity is a relative ranking, not an accuracy probability.
"""
import difflib
import math
import os
import re

from . import cues as C
from .asset_info import describe
from .util import is_video

STOP = set("""a an the and or but if then than so of to in on at by for with from into onto over under about after
before during while as is was were be been being are am it its this that these those there here he she they them
his her their our we you your i me my not no yes very just also only can could would should will shall may might
must do does did done have has had having up down out off again once more most other some such own same too
now when where why how all any both each few many much one two three first last new old said says say like get got
make made what which who whom whose because through until till per via upon among never ever even still yet
image photo picture shot scene clip video cinematic realistic style detailed lighting highly ultra hd 4k 8k
render background view close closeup wide angle""".split())


def stem(w):
    w = w.lower()
    for suf in ("ings", "ing", "ers", "ies", "ied", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def content(text):
    return [stem(t) for t in re.findall(r"[a-z][a-z'-]+", (text or "").lower().replace("'", "")) if t not in STOP
            and len(t) > 2]


def _norm(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", "").replace("’", ""))


# ============================================================================ script vs recording
def script_text(project):
    t = (project.get("script_text") or "").strip()
    if t:
        return C.strip_markers(t)
    sp = project.get("script")
    if sp and os.path.isfile(sp):
        with open(sp, encoding="utf-8", errors="replace") as f:
            return C.strip_markers(f.read())
    return ""


def script_check(script, words):
    """Compare the exact script with what the recording says. -> list of
    {'kind': missing|extra|changed, 'script', 'spoken', 't'} (t = where in the audio)."""
    st, wt = _norm(script), [(_norm(w["w"]) or [""])[0] for w in words]
    if not st or not wt:
        return []
    sm = difflib.SequenceMatcher(None, st, wt, autojunk=False)
    out = []
    for tag, a0, a1, b0, b1 in sm.get_opcodes():
        if tag == "equal":
            continue
        s_txt, w_txt = " ".join(st[a0:a1]), " ".join(wt[b0:b1])
        if tag == "replace" and _numberish(st[a0:a1], wt[b0:b1]):
            continue        # "1910" vs "nineteen ten" style differences
        t = words[min(b0, len(words) - 1)]["s"]
        kind = {"delete": "missing", "insert": "extra", "replace": "changed"}[tag]
        if max(a1 - a0, b1 - b0) == 1 and kind == "changed":
            kind = "word"
        out.append({"kind": kind, "script": s_txt, "spoken": w_txt, "t": round(t, 2)})
    return out


NUMWORDS = set("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million
billion and oh first second third fourth fifth sixth seventh eighth ninth tenth percent bc ad""".split())


def _numberish(a, b):
    """'1910' vs 'nineteen ten', '3' vs 'three': same words, written differently - not a script change."""
    def digits(x):
        return x and all(t.isdigit() or t in ("st", "nd", "rd", "th", "percent") for t in x)

    def spelled(x):
        return x and all(t in NUMWORDS or t.isdigit() for t in x)
    return (digits(a) and spelled(b)) or (digits(b) and spelled(a))


def check_summary(issues):
    big = [i for i in issues if i["kind"] in ("missing", "extra", "changed")]
    if not issues:
        return "The recording matches the script."
    return (f"{len(big)} passage(s) differ between script and recording"
            + (f", {len(issues) - len(big)} single-word differences" if len(issues) > len(big) else "") + ".")


# ============================================================================ sentences + topics
def sentences(words):
    """[(first word index, last word index)] split at sentence ends / long pauses."""
    out, a = [], 0
    for i, w in enumerate(words):
        end = i == len(words) - 1
        if end or w["w"].rstrip().endswith((".", "!", "?", "…", '."', '?"')) or \
                (words[i + 1]["s"] - w["e"] >= 0.9):
            out.append((a, i))
            a = i + 1
    return out


def paragraph_starts(project, words):
    """Word indices where the script starts a new paragraph (author's topic breaks)."""
    t = (project.get("script_text") or "")
    if not t.strip():
        sp = project.get("script")
        if sp and os.path.isfile(sp):
            with open(sp, encoding="utf-8", errors="replace") as f:
                t = f.read()
    paras = [p.strip() for p in re.split(r"\n\s*\n", C.strip_markers(t)) if p.strip()]
    if len(paras) < 2:
        return set()
    cl = [{"ref": str(i), "text": p} for i, p in enumerate(paras)]
    C.align(words, cl)
    return {c["wi"] for c in cl[1:] if c.get("score", 0) >= 0.4}


def boundary_scores(words, sents, para_starts):
    """Score for cutting after each sentence: vocabulary change + paragraph break + pause."""
    bags = [set(content(" ".join(w["w"] for w in words[a:b + 1]))) for a, b in sents]
    scores = []
    for j in range(len(sents) - 1):
        left = set().union(*bags[max(0, j - 1): j + 1])
        right = set().union(*bags[j + 1: j + 3])
        sim = len(left & right) / math.sqrt(max(1, len(left)) * max(1, len(right)))
        nxt = sents[j + 1][0]
        pause = words[nxt]["s"] - words[sents[j][1]]["e"]
        scores.append((1.0 - sim) + (0.8 if nxt in para_starts else 0.0) + min(1.0, max(0.0, pause)) * 0.5)
    return scores


def choose_cuts(words, sents, scores, k, lo_wi, hi_wi, min_len=1.2, max_len=None):
    """Pick k-1 cut positions (sentence ends) between word lo_wi..hi_wi with the best scores, keeping
    every scene at least min_len seconds."""
    cand = [(scores[j], sents[j + 1][0]) for j in range(len(sents) - 1)
            if lo_wi < sents[j + 1][0] <= hi_wi]
    cand.sort(reverse=True)
    t_lo = words[lo_wi]["s"] if lo_wi < len(words) else 0
    t_hi = words[hi_wi]["e"] if hi_wi < len(words) else words[-1]["e"]
    chosen = []
    for sc, wi in cand:
        if len(chosen) >= k - 1:
            break
        t = words[wi]["s"]
        pts = sorted([t_lo] + [words[c]["s"] for c in chosen] + [t_hi])
        ok = t - t_lo >= min_len and t_hi - t >= min_len and all(abs(t - p) >= min_len for p in pts[1:-1])
        if ok:
            chosen.append(wi)
    if max_len:     # split anything still too long at its best internal sentence end
        changed = True
        while changed:
            changed = False
            edges = [lo_wi] + sorted(chosen) + [hi_wi + 1]
            for a, b in zip(edges, edges[1:]):
                ta, tb = words[a]["s"], words[min(b, len(words)) - 1]["e"]
                if tb - ta > max_len:
                    inner = [(s, w) for s, w in cand if a < w < b and w not in chosen
                             and words[w]["s"] - ta >= min_len and tb - words[w]["s"] >= min_len]
                    if inner:
                        chosen.append(max(inner)[1])
                        changed = True
                        break
    return sorted(chosen)


# ============================================================================ matching
def similarity_matrix(scene_texts, asset_texts):
    docs = [content(t) for t in scene_texts + asset_texts]
    df = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    n = len(docs)
    idf = {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}

    def vec(d):
        v = {}
        for w in d:
            v[w] = v.get(w, 0) + idf[w]
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}
    sv = [vec(d) for d in docs[: len(scene_texts)]]
    av = [vec(d) for d in docs[len(scene_texts):]]
    S = [[sum(x * a.get(w, 0) for w, x in s.items()) for a in av] for s in sv]
    shared = [[sorted((w for w in s if w in a), key=lambda w: -idf[w])[:4] for a in av] for s in sv]
    return S, shared


def assign(S, ordered=None, min_score=0.06):
    """Each scene -> one asset, each asset used at most once.
    ordered=None: best total match (greedy best-first), so file names / order don't matter.
    ordered=list of asset indices in story order: monotonic best match (chronology kept)."""
    ns = len(S)
    na = len(S[0]) if S else 0
    res = [None] * ns
    if not na:
        return res
    if ordered is None:
        pairs = sorted(((S[i][j], i, j) for i in range(ns) for j in range(na)), reverse=True)
        used_a = set()
        for sc, i, j in pairs:
            if sc < min_score:
                break
            if res[i] is None and j not in used_a:
                res[i] = j
                used_a.add(j)
        return res
    # monotonic DP over (scene i, position p in `ordered`)
    m = len(ordered)
    NEG = -1e9
    best = [[NEG] * (m + 1) for _ in range(ns + 1)]
    take = [[False] * (m + 1) for _ in range(ns + 1)]
    for p in range(m + 1):
        best[0][p] = 0.0
    for i in range(1, ns + 1):
        best[i][0] = 0.0 + best[i - 1][0]
        for p in range(1, m + 1):
            sc = S[i - 1][ordered[p - 1]]
            opt_take = best[i - 1][p - 1] + (sc if sc >= min_score else -0.5)
            opt_skip_asset = best[i][p - 1]
            opt_skip_scene = best[i - 1][p]
            v = max(opt_take, opt_skip_asset, opt_skip_scene)
            best[i][p] = v
            take[i][p] = (v == opt_take, v == opt_skip_asset)
    i, p = ns, m
    while i > 0 and p > 0:
        t = take[i][p]
        if t and t[0]:
            if S[i - 1][ordered[p - 1]] >= min_score:
                res[i - 1] = ordered[p - 1]
            i, p = i - 1, p - 1
        elif t and t[1]:
            p -= 1
        else:
            i -= 1
    return res


# ============================================================================ instructions
PROJECT_RULES = [
    (r"\b(no|without)\s+(camera\s+)?motion\b|\bmotion\s*[:=]\s*none\b|\bkeep (images|pictures) still\b",
     ("motion_level", "None")),
    (r"\bmotion\s*[:=]\s*subtle\b|\b(subtle|gentle|slow)\s+(motion|zooms?|moves?)\b", ("motion_level", "Subtle")),
    (r"\bmotion\s*[:=]\s*medium\b", ("motion_level", "Medium")),
    (r"\bmotion\s*[:=]\s*dynamic\b|\b(dynamic|energetic|fast)\s+(motion|moves?|zooms?)\b", ("motion_level", "Dynamic")),
    (r"\b(cuts only|only cuts|hard cuts|no transitions)\b|\btransitions?\s*[:=]\s*cut\b", ("transition", "cut")),
    (r"\btransitions?\s*[:=]\s*(cross ?fade|fade|dissolve)\b|\b(crossfades?|dissolves?)\b",
     ("transition", "crossfade")),
    (r"\bframing\s*[:=]\s*(fill|crop)\b|\bfill the (frame|screen)\b|\bno (black )?bars\b", ("fit", "crop")),
    (r"\bframing\s*[:=]\s*(fit|blur)|\bblurred background\b|\bshow (the )?whole (image|picture)\b",
     ("fit", "fit_blur")),
    (r"\bblack bars\b|\bframing\s*[:=]\s*bars\b", ("fit", "fit_bars")),
    (r"\bkeep (the )?(images|pictures|visuals|assets) in order\b|\bchronological(ly)?\b|\bin (story|file) order\b",
     ("order", True)),
    (r"\b(any order|match by meaning|order doesn'?t matter)\b", ("order", False)),
    (r"\b(no|don'?t|do not) (repeat|reuse)\b|\beach (image|picture) once\b", ("repeat", False)),
    (r"\b(allow|ok to|can) (repeat|reuse)\b", ("repeat", True)),
]
_RANGE = re.compile(r"scenes?\s+(?:of\s+|between\s+)?(\d+(?:\.\d+)?)\s*(?:-|to|and)\s*(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b")


def parse_project_instructions(text):
    """-> (overrides dict, [(line, understood?)]). Rule-based (not AI): unknown lines are reported."""
    ov, lines = {}, []
    for ln in [x.strip() for x in (text or "").splitlines() if x.strip()]:
        hit = False
        low = ln.lower()
        for pat, (k, v) in PROJECT_RULES:
            if re.search(pat, low):
                ov[k] = v
                hit = True
        m = _RANGE.search(low)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            ov["min_len"], ov["max_len"] = min(a, b), max(a, b)
            hit = True
        lines.append((ln, hit))
    return ov, lines


_TIME = r"(\d+:\d{1,2}(?:\.\d+)?|\d+(?:\.\d+)?\s*s(?:ec(?:onds?)?)?)"
_USE = re.compile(
    r"^\s*(?:use|show|put)\s+(?:the\s+)?(?:image|img|picture|photo|clip|video|file|asset)?\s*#?\s*"
    r"(?P<ref>[\w\-. ()]+?\.(?:png|jpe?g|webp|gif|bmp|tiff?|mp4|mov|mkv|webm|m4v)|\d+(?:\s*(?:,|and|\+)\s*\d+)*)\b"
    r"(?P<rest>.*)$", re.I)


def _parse_time(s):
    s = s.strip().lower().rstrip("seconds").rstrip("sec").strip()
    return C.parse_time(s)


def find_phrase(words, phrase, start=0):
    """Best fuzzy match of a phrase in the transcript at/after word `start`. -> (word index, ratio)."""
    pt = _norm(phrase)
    if not pt:
        return None, 0.0
    wt = [(_norm(w["w"]) or [""])[0] for w in words]
    n = len(pt)
    best, bi = 0.0, None
    for i in range(start, max(start, len(wt) - n + 1) + 1):
        win = wt[i:i + n + 2]
        r = difflib.SequenceMatcher(None, pt, win, autojunk=False).ratio()
        if r > best + 1e-9:
            best, bi = r, i
    if bi is not None:      # start at the first word that actually matches, not the window start
        sm = difflib.SequenceMatcher(None, pt, wt[bi:bi + n + 2], autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size]
        if blocks:
            bi += max(0, blocks[0].b - blocks[0].a)
    return bi, best


def parse_scene_instructions(text, words, media):
    """Natural-ish scene instructions, one per line, e.g.
         Use image 003 while the narrator discusses the letter; keep it until the next topic.
         Use 12.png from 1:20 to 1:31
         Show clip 7 when the narrator says "the river burst its banks" for 5 seconds
    -> (cues, issues). Lines that aren't instructions are returned as issues (never silently dropped).
    Markers / one-paragraph-per-image text are handled by cues.parse_directions instead."""
    out, issues = [], []
    index = C.media_index(media)
    for ln in [x.strip() for x in (text or "").splitlines() if x.strip()]:
        m = _USE.match(ln)
        if not m:
            issues.append(f"Not understood: \"{ln[:80]}\"")
            continue
        paths, bad = C.resolve(m.group("ref"), media, index)
        issues += [f"\"{ln[:50]}…\": {b}" for b in bad]
        if not paths:
            continue
        rest = m.group("rest")
        cue = {"ref": m.group("ref").strip(), "media": paths, "text": "", "line": ln, "explicit": True}
        tm = re.search(r"(?:from|at)\s+" + _TIME + r"(?:\s*(?:to|-|until)\s*" + _TIME + r")?", rest, re.I)
        if tm:
            cue["t"] = _parse_time(tm.group(1))
            if tm.group(2):
                cue["t_end"] = _parse_time(tm.group(2))
        ph = re.search(r"(?:while|when|as|where)\s+(?:the\s+)?(?:narrator|voice(?:over)?|narration|script|speaker)?"
                       r"\s*(?:is\s+)?(?:discuss\w*|talk\w*\s+about|say\w*|mention\w*|describ\w*|read\w*|"
                       r"reach\w*|explain\w*|start\w*\s+talking\s+about)\s+[\"“']?(?P<p>[^\"”';,.]+)", rest, re.I)
        if ph and "t" not in cue:
            wi, r = find_phrase(words, ph.group("p"))
            if wi is None or r < 0.5:
                issues.append(f"\"{ln[:60]}\": couldn't find \"{ph.group('p').strip()}\" in the recording")
                continue
            cue["wi"], cue["phrase_score"] = wi, r
        if "t" not in cue and "wi" not in cue:
            issues.append(f"\"{ln[:60]}\": says which image but not when (add 'when the narrator says …' "
                          "or 'from 1:20 to 1:31')")
            continue
        um = re.search(r"until\s+(?:the\s+)?(?:narrator\s+)?(?:says\s+)?[\"“']?(?P<p>[^\"”';,.]+)", rest, re.I)
        if re.search(r"until\s+the\s+next\s+(topic|scene|section|instruction)", rest, re.I):
            cue["end"] = "topic"
        elif um and "t_end" not in cue:
            wi, r = find_phrase(words, um.group("p"), cue.get("wi", 0))
            if wi is not None and r >= 0.5:
                cue["end_wi"] = wi
            else:
                issues.append(f"\"{ln[:60]}\": couldn't find the end phrase \"{um.group('p').strip()}\"")
        fm = re.search(r"for\s+(\d+(?:\.\d+)?)\s*s", rest, re.I)
        if fm:
            cue["dur"] = float(fm.group(1))
        if re.search(r"\block(ed)?\b", rest, re.I):
            cue["locked"] = True
        out.append(cue)
    return out, issues


# ============================================================================ the plan
def cut_time(words, i):
    return C.cut_time(words, i)


def _new_id(project):
    n = int(project.get("next_scene_id", 1))
    project["next_scene_id"] = n + 1
    return f"S{n:03d}"


def plan(project, log=print):
    """Build the scene list. Returns (scenes, report). Doesn't modify project['scenes']."""
    from .presets import PRESETS
    from .project import project_media
    from .scenes import _weighted, apply_motion_level, reassign_transitions, scene_text
    import random
    st = project["settings"]
    words, dur = project.get("words") or [], float(project.get("duration") or 0)
    if not words:
        raise ValueError("Analyze the voiceover first.")
    media = project_media(project)
    report, issues = [], []
    ov, pi_lines = parse_project_instructions(st.get("project_instructions") or "")
    for ln, ok in pi_lines:
        if not ok:
            issues.append(f"Project instruction not understood (ignored): \"{ln[:80]}\" - see the list of "
                          "supported phrases.")
    preset = dict(PRESETS.get(st.get("preset"), PRESETS["Documentary"]))
    pace = PACING.get(st.get("pacing") or "balanced", PACING["balanced"])
    min_len = float(ov.get("min_len", st.get("shot_min") or pace[0] or max(1.2, preset["min_scene"] * 0.5)))
    max_len = ov.get("max_len", st.get("shot_max") or pace[1])
    # ---- locked scenes stay exactly as they are
    locked = [dict(s) for s in project.get("scenes") or [] if s.get("locked")]
    locked.sort(key=lambda s: s["start"])
    locked_media = {os.path.normcase(s.get("media") or "") for s in locked}
    pool = [m for m in media if os.path.normcase(m) not in locked_media]
    # ---- explicit assignments
    explicit, notes = [], []
    dtext = project.get("directions") or ""
    if dtext.strip():
        if any(_USE.match(x.strip()) for x in dtext.splitlines() if x.strip()):
            ex, iss = parse_scene_instructions(dtext, words, media)
            explicit += ex
            issues += iss
        else:
            cl, note = C.parse_directions(dtext, media)
            if note:
                notes.append("Scene instructions: " + note)
            for c in cl:
                c.setdefault("explicit", True)
            explicit += cl
    from .instructions import explicit_cues
    explicit += explicit_cues(project)            # applied editing instructions with their own timing
    for row in project.get("scene_map") or []:
        c = dict(row, explicit=True)
        c["media"] = _map_media(row, media)
        if not c["media"]:
            issues.append(f"Scene map {row.get('scene_id') or ''}: '{row.get('asset_filename')}' is not in the "
                          "project media - that row was skipped")
            continue
        explicit.append(c)
    # resolve explicit cues -> word index / times
    need = [c for c in explicit if c.get("wi") is None and c.get("t") is None and c.get("text")]
    if need:
        C.align(words, need)
        for c in need:
            if c.get("score", 1) < 0.5:
                issues.append(f"[{c.get('ref')}] text only partly found in the recording ({c['score']:.0%})")
    for c in explicit:
        if "media" not in c or not c["media"]:
            paths, bad = C.resolve(str(c.get("ref") or ""), media)
            c["media"] = paths
            issues += [f"[{c.get('ref')}] {b}" for b in bad]
        if c.get("t") is None:
            c["t"] = cut_time(words, c.get("wi") or 0)
        if c.get("wi") is None:
            c["wi"] = next((i for i, w in enumerate(words) if (w["s"] + w["e"]) / 2 >= c["t"]), len(words) - 1)
    explicit = [c for c in explicit if c.get("media")]
    explicit.sort(key=lambda c: c["t"])
    # ---- sentences + topic boundaries
    sents = sentences(words)
    paras = paragraph_starts(project, words)
    scores = boundary_scores(words, sents, paras)
    if paras:       # the author's paragraphs are the strongest topic signal
        topic_cut_wis = sorted(paras)
    else:
        topic_cut_wis = sorted(sents[j + 1][0] for j, s in enumerate(scores) if s >= 1.25)

    def next_topic_t(after_t):
        for wi in topic_cut_wis:
            if words[wi]["s"] > after_t + min_len:
                return cut_time(words, wi)
        return dur
    # explicit spans
    spans = []                       # (start, end, cue) for explicit
    for k, c in enumerate(explicit):
        a = c["t"]
        nxt = explicit[k + 1]["t"] if k + 1 < len(explicit) else dur
        if c.get("t_end") is not None:
            b = c["t_end"]
        elif c.get("end_wi") is not None:
            b = cut_time(words, c["end_wi"])
        elif c.get("dur"):
            b = a + c["dur"]
        elif c.get("end") == "topic" or c.get("line") or c.get("source") == "map":
            b = next_topic_t(a)
        else:
            b = nxt                  # paragraphs / markers: until the next one
        if b > nxt + 1e-6:
            if c.get("line") or c.get("source") == "map":
                issues.append(f"[{c.get('ref')}] overlaps the next instruction at {nxt:.1f}s - it ends there")
            b = nxt
        if b - a < 0.3:
            issues.append(f"[{c.get('ref')}] is shorter than 0.3 s - skipped (check its times)")
            continue
        spans.append((round(a, 6), round(min(b, dur), 6), c))
    if spans and not spans[0][2].get("line") and spans[0][2].get("source") != "map" and spans[0][0] > 0:
        a, b, c = spans[0]
        spans[0] = (0.0, b, c)       # directions/markers: the first image starts at 0:00
    # gaps (not covered by explicit or locked) -> automatic scenes
    taken = sorted([(a, b) for a, b, _ in spans] + [(s["start"], s["end"]) for s in locked])
    gaps, cur = [], 0.0
    for a, b in taken:
        if a > cur + 0.3:
            gaps.append((cur, a))
        cur = max(cur, b)
    if dur > cur + 0.3:
        gaps.append((cur, dur))
    used = {os.path.normcase(p) for _, _, c in spans for p in c["media"]}
    auto_pool = [m for m in pool if os.path.normcase(m) not in used]
    descs = [describe(m, project) for m in auto_pool]
    described = [d for d in descs if not d["uncertain"]]
    meaning_mode = bool(auto_pool) and len(described) >= max(1, len(descs) // 2)
    gap_total = sum(b - a for a, b in gaps) or 1.0
    auto_scenes = []
    for ga, gb in gaps:
        wi_lo = next((i for i, w in enumerate(words) if w["e"] > ga), len(words) - 1)
        wi_hi = max(wi_lo, next((i for i in range(len(words) - 1, -1, -1) if words[i]["s"] < gb), wi_lo))
        n_sent = sum(1 for a, b in sents if wi_lo <= a <= wi_hi) or 1
        n_topics = 1 + sum(1 for wi in topic_cut_wis if wi_lo < wi <= wi_hi)
        if auto_pool:
            k = int(round(len(auto_pool) * (gb - ga) / gap_total))
            if meaning_mode:        # every topic gets its own scene, even if no visual fits it (flagged)
                k = max(k, n_topics)
            k = max(1, min(n_sent, k))
        else:
            k = max(1, int((gb - ga) // max(4.0, preset["max_scene"])))
        if max_len:
            k = max(k, int(math.ceil((gb - ga) / max_len)))
        cuts = choose_cuts(words, sents, scores, k, wi_lo, wi_hi, min_len, max_len)
        edges = [ga] + [cut_time(words, c) for c in cuts] + [gb]
        for a, b in zip(edges, edges[1:]):
            if b - a > 0.05:
                auto_scenes.append((a, b))
    # ---- match automatic scenes to the remaining visuals
    order_wanted = ov.get("order")
    texts = [scene_text(words, a, b) for a, b in auto_scenes]
    assignment, why = [None] * len(auto_scenes), [""] * len(auto_scenes)
    status = ["auto"] * len(auto_scenes)
    if meaning_mode:
        S, shared = similarity_matrix(texts, [d["text"] for d in descs])
        ordered = None
        if order_wanted:
            ordered = sorted(range(len(descs)), key=lambda j: _order_key(descs[j]))
        assignment = assign(S, ordered)
        for i, j in enumerate(assignment):
            if j is not None:
                status[i] = "matched"
                why[i] = ("shares: " + ", ".join(shared[i][j])) if shared[i][j] else "closest description"
        mode_note = "by meaning (descriptions)" + (" in story order" if ordered else "")
    else:
        # no descriptions: honest file-order fallback
        order = sorted(range(len(auto_pool)), key=lambda j: _order_key(descs[j]) if descs else j)
        for i in range(min(len(auto_scenes), len(order))):
            assignment[i] = order[i]
            status[i] = "order"
            why[i] = "file order - no description of what this image shows (import prompts for meaning-based matching)"
        mode_note = "by file order (no image descriptions found)"
    free = [j for j in range(len(auto_pool)) if j not in set(x for x in assignment if x is not None)]
    allow_repeat = ov.get("repeat", bool(st.get("repeat_media", False)))
    for i in range(len(auto_scenes)):
        if assignment[i] is None:
            if free:
                assignment[i] = free.pop(0)
                status[i] = "fallback"
                why[i] = ("no visual matched this part of the script - showing an unused one as a FALLBACK "
                          "(replace it, or press 'Accept fallback')")
            elif allow_repeat and auto_pool:
                assignment[i] = i % len(auto_pool)
                status[i] = "fallback"
                why[i] = "no visual left - repeating one (repeats allowed)"
            else:
                status[i] = "missing"
                why[i] = "no visual available for this part - upload or choose one"
    # ---- build scenes
    rng = random.Random(int(st.get("seed", 7)))
    scenes = []
    for a, b, c in spans:
        k = len(c["media"])
        parts = [a + (b - a) * j / k for j in range(k)] + [b]
        for j, m in enumerate(c["media"]):
            sc = _scene(words, parts[j], parts[j + 1], m, rng, preset, scene_text, _weighted)
            sc.update(status="assigned", why=("instruction: " + c["line"][:80]) if c.get("line") else
                      (f"scene map {c.get('row')}" if c.get("source") == "map" else
                       f"explicit assignment [{c.get('ref')}]"))
            for key in ("fit", "motion", "locked", "clip_in", "clip_out", "subject", "action", "setting",
                        "mood", "continuity", "scene_id"):
                if c.get(key) not in (None, ""):
                    sc[key] = c[key]
            if c.get("scene_id"):
                sc["id"] = c["scene_id"] if j == 0 else f"{c['scene_id']}-{j + 1}"
            scenes.append(sc)
    for (a, b), j, s_, w_ in zip(auto_scenes, assignment, status, why):
        m = auto_pool[j] if j is not None else ""
        sc = _scene(words, a, b, m, rng, preset, scene_text, _weighted)
        sc.update(status=s_, why=w_)
        if j is not None and descs:
            d = descs[j]
            sc["asset_id"] = d["id"]
        scenes.append(sc)
    scenes = keep_locked(scenes, locked, dur)
    for s in scenes:
        s["text"] = scene_text(words, s["start"], s["end"])
    # ---- project instructions + automatic look (never on locked scenes)
    free_sc = [s for s in scenes if not s.get("locked")]
    apply_motion_level(free_sc, preset, ov.get("motion_level", st.get("motion_level", "Medium")),
                       seed=int(st.get("seed", 7)))
    keep_tr = {id(s): s.get("transition") for s in scenes if s.get("locked")}
    reassign_transitions(scenes, preset, words, seed=int(st.get("seed", 7)))
    for s in scenes:
        if id(s) in keep_tr:
            s["transition"] = keep_tr[id(s)]
    for s in free_sc:
        if ov.get("transition"):
            s["transition"] = ov["transition"]
        if ov.get("fit") and not s.get("fit"):
            s["fit"] = ov["fit"]
    apply_guidelines(project, scenes, topic_times=[words[w]["s"] for w in topic_cut_wis], overrides=ov)
    if scenes and not scenes[-1].get("locked"):
        scenes[-1]["transition"] = "cut"
    # ---- ids, warnings
    seen = set()
    for s in scenes:
        if not s.get("id") or s["id"] in seen:
            s["id"] = _new_id(project)
            while s["id"] in seen:
                s["id"] = _new_id(project)
        seen.add(s["id"])
        s["warnings"] = scene_warnings(s, project)
    n_by = {}
    for s in scenes:
        n_by[s["status"]] = n_by.get(s["status"], 0) + 1
    unused = [m for m in pool if os.path.normcase(m) not in {os.path.normcase(s.get("media") or "") for s in scenes}]
    report.append(f"{len(scenes)} scenes - visuals chosen {mode_note}. " +
                  ", ".join(f"{v} {k}" for k, v in sorted(n_by.items())) +
                  (f". {len(unused)} visual(s) not used." if unused else "."))
    report += notes + issues
    return scenes, report


# ============================================================================ scene map import
MAP_FIELDS = ("scene_id", "asset_filename", "script_excerpt", "start_seconds", "end_seconds", "fit_mode", "motion",
              "locked")
_FIT_ALIASES = {"fill": "crop", "crop": "crop", "fill/crop": "crop", "fit": "fit_blur", "blur": "fit_blur",
                "fit_blur": "fit_blur", "fit/background": "fit_blur", "color": "fit_color", "colour": "fit_color",
                "fit_color": "fit_color", "bars": "fit_bars", "black bars": "fit_bars", "fit_bars": "fit_bars"}


def _map_media(row, media):
    from .asset_info import asset_id
    name = os.path.basename(str(row.get("asset_filename") or "")).lower()
    for m in media:
        if os.path.basename(m).lower() == name:
            return [m]
    aid = row.get("asset_id")
    if aid:                                     # renamed since import: find it by its content ID
        for m in media:
            if asset_id(m) == aid:
                return [m]
    return []


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return C.parse_time(str(v))


def load_scene_map(path, media, duration=None):
    """CSV or JSON with columns scene_id, asset_filename, script_excerpt, start_seconds, end_seconds,
    fit_mode, motion, locked. -> (rows, problems). Every row is validated; nothing is silently guessed."""
    import csv
    import io
    import json
    from .asset_info import asset_id
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw = f.read()
    if path.lower().endswith(".json"):
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("scenes") or []
        rows = [r for r in data if isinstance(r, dict)]
    else:
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                for r in csv.DictReader(io.StringIO(raw))]
    out, problems, seen_ids, seen_assets = [], [], set(), {}
    if rows and not any(k in rows[0] for k in ("asset_filename", "start_seconds", "script_excerpt")):
        return [], ["The file has none of the expected columns: " + ", ".join(MAP_FIELDS)]
    for i, r in enumerate(rows, 1):
        tag = f"row {i}" + (f" ({r.get('scene_id')})" if r.get("scene_id") else "")
        sid = str(r.get("scene_id") or "").strip()
        if sid:
            if sid in seen_ids:
                problems.append(f"{tag}: duplicate scene_id - row skipped")
                continue
            seen_ids.add(sid)
        fn = str(r.get("asset_filename") or "").strip()
        if not fn:
            problems.append(f"{tag}: no asset_filename - row skipped")
            continue
        hit = _map_media({"asset_filename": fn}, media)
        if not hit:
            problems.append(f"{tag}: '{fn}' isn't in the project media - row kept but will be skipped until the "
                            "file is added")
        elif fn.lower() in seen_assets:
            problems.append(f"{tag}: '{fn}' is also used in {seen_assets[fn.lower()]} (allowed, but check)")
        seen_assets.setdefault(fn.lower(), tag)
        a, b = _num(r.get("start_seconds")), _num(r.get("end_seconds"))
        if a is not None and b is not None and b <= a:
            problems.append(f"{tag}: end_seconds must be after start_seconds - row skipped")
            continue
        if duration and a is not None and a >= duration:
            problems.append(f"{tag}: starts at {a:.1f}s, after the narration ends ({duration:.1f}s) - row skipped")
            continue
        if duration and b is not None and b > duration + 0.05:
            problems.append(f"{tag}: end_seconds {b:.1f} is past the narration end - clipped to {duration:.1f}s")
            b = duration
        if a is None and not (r.get("script_excerpt") or "").strip():
            problems.append(f"{tag}: needs start_seconds or script_excerpt to know WHEN - row skipped")
            continue
        fit = str(r.get("fit_mode") or "").strip().lower()
        if fit and fit not in _FIT_ALIASES:
            problems.append(f"{tag}: unknown fit_mode '{fit}' (use fill, fit, colour or bars) - project default used")
        lk = str(r.get("locked") or "").strip().lower() in ("1", "true", "yes", "y", "locked", "x")
        row = {"scene_id": sid, "asset_filename": fn, "ref": fn, "text": (r.get("script_excerpt") or "").strip(),
               "t": a, "t_end": b, "fit": _FIT_ALIASES.get(fit), "motion": (r.get("motion") or "").strip() or None,
               "locked": lk, "asset_id": asset_id(hit[0]) if hit else None, "source": "map", "row": tag}
        out.append(row)
    starts = sorted((r["t"], r["t_end"], r["scene_id"] or r["asset_filename"]) for r in out if r["t"] is not None)
    for (a1, b1, n1), (a2, _b2, n2) in zip(starts, starts[1:]):
        if b1 is not None and b1 > a2 + 0.05:
            problems.append(f"{n1} overlaps {n2} ({b1:.1f}s > {a2:.1f}s) - {n1} will end at {a2:.1f}s")
    return out, problems


PACING = {"relaxed": (4.0, 12.0), "balanced": (None, None), "brisk": (1.5, 5.0)}
SAFE_TRANSITIONS = {"whip": "crossfade", "zoom": "crossfade", "slide": "crossfade"}


def apply_guidelines(project, scenes, topic_times=None, overrides=None):
    """Project guidelines on scenes that aren't locked and have no instruction of their own for that field:
    default transition (+ how often), no flashing transitions, no hand-held drift."""
    st = project["settings"]
    ov = overrides or {}
    td = st.get("transition_default") or "auto"
    freq = st.get("transition_frequency") or "every_cut"
    if topic_times is None:
        topic_times = []
    for k, s in enumerate(scenes[:-1]):
        prov = s.get("provenance") or {}
        if s.get("locked") or "transition" in (s.get("locked_fields") or []) or \
                str(prov.get("transition", "")).startswith("instruction") or ov.get("transition"):
            continue
        if td != "auto":
            at_topic = any(abs(s["end"] - t) < 0.6 for t in topic_times)
            s["transition"] = td if (freq == "every_cut" or (freq == "topic_changes" and at_topic)) else "cut"
            prov["transition"] = "guidelines"
        if st.get("restrict_no_flash") and s.get("transition") in SAFE_TRANSITIONS:
            s["transition"] = SAFE_TRANSITIONS[s["transition"]]
            prov["transition"] = "guidelines (no flashing)"
        s["provenance"] = prov
    if st.get("restrict_no_shake"):
        for s in scenes:
            if not s.get("locked") and s.get("motion") == "subtle":
                s["motion"] = "zoom_in"
    return scenes


def keep_locked(scenes, locked, dur):
    """Locked scenes win: anything overlapping them is trimmed; the narration stays covered exactly once."""
    for L in sorted(locked, key=lambda s: s["start"]):
        nxt = []
        for s in scenes:
            if s.get("locked") or s["end"] <= L["start"] + 1e-6 or s["start"] >= L["end"] - 1e-6:
                nxt.append(s)
                continue
            if s["start"] < L["start"] - 0.3:
                nxt.append(dict(s, end=L["start"]))
            if s["end"] > L["end"] + 0.3:
                nxt.append(dict(s, start=L["end"]))
        scenes = nxt + [dict(L, status="locked")]
    scenes.sort(key=lambda s: s["start"])
    if scenes:
        scenes[0]["start"] = 0.0 if not scenes[0].get("locked") else scenes[0]["start"]
        for p, q in zip(scenes, scenes[1:]):
            if abs(p["end"] - q["start"]) > 1e-6:
                if q.get("locked") and not p.get("locked"):
                    p["end"] = q["start"]
                elif not q.get("locked"):
                    q["start"] = p["end"]
                else:                       # two locked scenes with a gap/overlap: leave them alone
                    pass
        if not scenes[-1].get("locked") and dur:
            scenes[-1]["end"] = dur
        scenes = [s for s in scenes if s["end"] - s["start"] > 0.05 or s.get("locked")]
    return scenes


def _order_key(d):
    sid = str(d.get("scene_id") or "")
    m = re.search(r"\d+", sid)
    if m:
        return (0, int(m.group(0)), "")
    n = C.media_number(d["path"])
    return (1, n if n is not None else 10 ** 9, os.path.basename(d["path"]).lower())


def _scene(words, a, b, m, rng, preset, scene_text, _weighted):
    return {"start": round(a, 6), "end": round(b, 6), "text": scene_text(words, a, b), "media": m,
            "motion": "none" if (not m or is_video(m)) else _weighted(rng, preset["motions"]),
            "transition": "cut", "zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0}


def scene_warnings(s, project):
    """Problems worth showing in Review (not errors that stop anything)."""
    w = []
    m = s.get("media")
    if not m:
        w.append("no visual")
        return w
    if not os.path.isfile(m):
        w.append("file missing")
        return w
    from .exportcfg import dims
    st = project["settings"]
    W, H = dims(st.get("aspect", "16:9"), st.get("resolution", "Full HD"))
    try:
        if is_video(m):
            from .mediainfo import probe
            info = probe(m) or {}
            d = info.get("duration") or 0
            avail = (s.get("clip_out") or d) - (s.get("clip_in") or 0)
            if d and avail < (s["end"] - s["start"]) - 0.05:
                w.append(f"clip is {avail:.1f}s but the scene is {s['end'] - s['start']:.1f}s - "
                         f"{s.get('short_clip') or st.get('short_clip', 'freeze')} (choose loop / slow / freeze, "
                         "trim the scene or use another clip)")
            sw, sh = info.get("display_w") or 0, info.get("display_h") or 0
        else:
            from PIL import Image
            sw, sh = Image.open(m).size
        if sw and sh and max(sw / W, sh / H) < 0.75 and min(sw, sh) < min(W, H):
            w.append(f"low resolution ({sw}x{sh}) for a {W}x{H} export - will look soft")
    except Exception:
        pass
    if s.get("status") == "fallback":
        w.append("fallback visual - not matched to the script")
    return w
