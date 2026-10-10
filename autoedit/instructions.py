"""Scene-by-scene editing instructions.

Two layers, kept apart on purpose:

  * INTENT  - project["edit_instructions"]: one record per narration scene (keyed by its stable scene ID)
              with the text you typed, the structured settings (what you asked for) and your choices for
              ambiguous references. Saved with the project, exported/imported as JSON/CSV.
  * RESULT  - project["scenes"] / ["broll"] / ["sfx_events"] / ["overlays"]: the one timeline that the
              preview AND the export draw. Applying an instruction writes resolved values there and
              records which instruction produced each value (scene["provenance"]).

Field modes: "inherit" (use project guidelines / defaults), "auto" (AutoEditor may decide),
"none" (explicitly off) and "explicit" (your value). Blank imported values mean inherit.

Precedence: locked explicit values > unlocked explicit scene values > project guidelines > category preset >
automatic defaults. Two explicit visuals on the same time range are a CONFLICT (nothing is applied), never
an arbitrary winner. Narration is never sped up, cut or extended to satisfy an instruction.

Everything here is deterministic (no language model, no network). Unknown phrases are kept and marked
unresolved - never silently dropped.
"""
import copy
import csv
import difflib
import hashlib
import io
import json
import os
import re

from . import cues as C
from .util import AUDIO_EXT, is_video

SCHEMA_VERSION = 1
FIELDS = ("primary_visual", "timing", "framing", "motion", "b_roll", "sfx", "effects", "transition", "captions")
FIELD_NAMES = {"primary_visual": "Visual", "timing": "Timing", "framing": "Framing", "motion": "Motion",
               "b_roll": "B-roll", "sfx": "Sound effects", "effects": "Effects", "transition": "Transition out",
               "captions": "Captions"}
MODES = ("inherit", "auto", "none", "explicit")
MODE_NAMES = {"inherit": "Inherit (project guidelines)", "auto": "Automatic", "none": "None",
              "explicit": "My setting"}
TIMING_MODES = {"follow_narration": "Follow narration", "absolute": "Absolute time",
                "scene_relative": "Relative to scene start"}
MOTION_TYPES = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "zoom_in+pan_left",
                "zoom_in+pan_right", "zoom_out+pan_left", "zoom_out+pan_right")
MOTION_STRENGTH = {"subtle": 0.06, "medium": 0.12, "strong": 0.2}
EASES = {"smooth": "Smooth (ease in/out)", "linear": "Linear", "ease_in": "Ease in (starts slow)",
         "ease_out": "Ease out (ends slow)"}
TRANSITIONS = {"cut": "Cut", "crossfade": "Dissolve (crossfade)", "dip_black": "Fade through black",
               "slide": "Slide", "whip": "Whip pan", "zoom": "Zoom through"}
POSITIONS = {"top_left": (0.04, 0.05), "top_right": (0.96, 0.05), "bottom_left": (0.04, 0.95),
             "bottom_right": (0.96, 0.95), "center": (0.5, 0.5), "top": (0.5, 0.05), "bottom": (0.5, 0.95)}
FOCUS_WORDS = {"top left": (0.2, 0.2), "top right": (0.8, 0.2), "bottom left": (0.2, 0.8),
               "bottom right": (0.8, 0.8), "top": (0.5, 0.15), "bottom": (0.5, 0.85), "left": (0.15, 0.5),
               "right": (0.85, 0.5), "center": (0.5, 0.5), "centre": (0.5, 0.5), "middle": (0.5, 0.5)}
# effects the renderer really implements (engine._scene_effects) - nothing else is offered
EFFECTS = {
    "brightness": {"label": "Brightness", "param": "amount", "min": -0.5, "max": 0.5, "default": 0.15},
    "contrast": {"label": "Contrast", "param": "amount", "min": 0.5, "max": 1.6, "default": 1.2},
    "saturation": {"label": "Saturation", "param": "amount", "min": 0.0, "max": 2.0, "default": 0.5},
    "black_white": {"label": "Black & white", "param": "amount", "min": 0.0, "max": 1.0, "default": 1.0},
    "sepia": {"label": "Sepia", "param": "amount", "min": 0.0, "max": 1.0, "default": 0.8},
    "warm": {"label": "Warm", "param": "amount", "min": 0.0, "max": 1.0, "default": 0.6},
    "cold": {"label": "Cold", "param": "amount", "min": 0.0, "max": 1.0, "default": 0.6},
    "blur": {"label": "Blur", "param": "amount", "min": 0.0, "max": 10.0, "default": 3.0},
    "vignette": {"label": "Vignette", "param": "amount", "min": 0.0, "max": 1.0, "default": 0.5},
    "text": {"label": "Text overlay (label)", "param": "text", "default": ""},
}
UNSUPPORTED_EFFECTS = ("flash", "strobe", "flicker", "shake", "highlight", "glitch", "lens flare", "glow",
                       "rgb split", "chromatic", "light leak", "particles", "speed ramp")
STATUS_NAMES = {"ready": "Ready", "review": "Needs review", "unresolved": "Unresolved instruction",
                "missing": "Missing asset", "conflict": "Timing conflict", "empty": "Automatic",
                "draft": "Not applied yet"}


# ============================================================================ records
def blank_settings():
    return {"timing_mode": "follow_narration", "narration_anchor": None, "end_anchor": None,
            "start_seconds": None, "end_seconds": None, "duration_seconds": None, "offset_seconds": 0.0,
            "primary_visual": None, "segments": [], "clip_in": None, "clip_out": None,
            "framing": {"mode": "inherit"},
            "motion": {"mode": "inherit"},
            "b_roll_mode": "inherit", "b_roll": [],
            "sfx_mode": "inherit", "sfx": [],
            "effects_mode": "inherit", "effects": [],
            "transition_out": {"mode": "inherit"},
            "captions": {"mode": "inherit"},
            "locked": False, "locked_fields": []}


def new_record(scene_id):
    return {"scene_id": scene_id, "instruction_text": "", "notes": "", "settings": blank_settings(),
            "interpretation": [], "unresolved": [], "issues": [], "choices": {}, "applied": False,
            "resolved": None, "accepted": [], "status": "empty"}


def records(project):
    return project.setdefault("edit_instructions", {})


def record(project, scene_id, create=True):
    recs = records(project)
    if scene_id not in recs and create:
        recs[scene_id] = new_record(scene_id)
    return recs.get(scene_id)


def has_content(rec):
    s = rec["settings"]
    b = blank_settings()
    return bool(rec.get("instruction_text", "").strip() or rec.get("notes", "").strip()) or any(
        s.get(k) != v for k, v in b.items())


def words_sig(words):
    h = hashlib.sha1()
    for w in words or []:
        h.update(f"{w['w']}|{w['s']:.2f}|{w['e']:.2f};".encode("utf-8"))
    return h.hexdigest()[:16]


# ============================================================================ assets
def _norm(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", "").replace("’", ""))


def sfx_files(project):
    """Local sound files: your SFX folder, the built-in library, audio files in the media folder."""
    from .audio import BUILTIN_SFX
    out, seen = [], set()
    roots = [project.get("sfx_dir"), BUILTIN_SFX if project["settings"].get("builtin_sfx", True) else None,
             project.get("media_dir")]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dp, _, files in os.walk(root):
            for f in sorted(files):
                if os.path.splitext(f)[1].lower() in AUDIO_EXT and not f.startswith("."):
                    p = os.path.join(dp, f)
                    k = os.path.normcase(os.path.abspath(p))
                    if k not in seen:
                        seen.add(k)
                        out.append(p)
    for p in project.get("extra_sfx") or []:
        if os.path.isfile(p) and os.path.normcase(os.path.abspath(p)) not in seen:
            out.append(p)
    return out


def find_asset(ref, pool, kind="visual"):
    """ref -> (candidates [paths], problem or None). Several candidates = the user must choose."""
    ref = (ref or "").strip().strip("\"'“”")
    if not ref:
        return [], "no file named"
    if os.path.isabs(ref) and os.path.isfile(ref):
        return [ref], None
    base = os.path.basename(ref).lower()
    hits = [p for p in pool if os.path.basename(p).lower() == base]
    if not hits and "." not in base:
        hits = [p for p in pool if os.path.splitext(os.path.basename(p))[0].lower() == base]
    if not hits and re.fullmatch(r"#?\d+", base) and kind != "sfx":
        n = int(base.lstrip("#"))
        hits = [p for p in pool if C.media_number(p) == n]
    if not hits:
        return [], f"{ref} is missing. Select a replacement."
    return hits, None


ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10, "last": -1}


# ============================================================================ phrases in the narration
def find_occurrences(words, phrase, lo=0, hi=None):
    """All places the phrase is spoken (word index of its first word, index after its last word, score).
    Exact token matches first; otherwise close matches (>= 0.8)."""
    pt = _norm(phrase)
    if not pt or not words:
        return []
    hi = len(words) if hi is None else hi
    wt = [(_norm(w["w"]) or [""])[0] for w in words]
    n = len(pt)
    out = []
    for i in range(lo, max(lo, hi - n + 1)):
        if wt[i:i + n] == pt:
            out.append((i, i + n, 1.0))
    if out:
        return out
    i = lo
    while i < max(lo, hi - n + 1):
        r = difflib.SequenceMatcher(None, pt, wt[i:i + n], autojunk=False).ratio()
        if r >= 0.8:
            out.append((i, i + n, r))
            i += n
        else:
            i += 1
    return out


def _ctx(words, i, j):
    a, b = max(0, i - 4), min(len(words), j + 4)
    return " ".join(w["w"] for w in words[a:b])


def cut_before(words, i):
    return C.cut_time(words, i)


def cut_after(words, j, dur):
    """Boundary right after word j-1 (between it and the next word)."""
    if j >= len(words):
        return dur
    return C.cut_time(words, j)


def snap(t, fps):
    """Seconds -> nearest output frame boundary, computed from the absolute time (no accumulated drift)."""
    return round(round(float(t) * fps) / fps, 6)


# ============================================================================ natural-language parsing
FILE_RE = r"[\w\-. ()]+?\.(?:png|jpe?g|webp|gif|bmp|tiff?|mp4|mov|mkv|webm|m4v|avi)"
AUDIO_RE = r"[\w\-. ()]+?\.(?:wav|mp3|ogg|flac|m4a|aac)"
TIME_RE = r"(\d+:\d{1,2}(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)?"
OCC_RE = re.compile(r"\((?:the\s+)?(first|second|third|fourth|fifth|last|\d+)(?:st|nd|rd|th)?\s*(?:time|occurrence)\)"
                    r"|\b(?:the\s+)?(first|second|third|fourth|fifth|last|\d+)(?:st|nd|rd|th)?\s+(?:time|occurrence)\b"
                    r"|\boccurrence\s+(\d+)\b", re.I)


def _protect_quotes(text):
    qs = []

    def sub(m):
        qs.append(m.group(1) or m.group(2) or m.group(3))
        return f"\u0001{len(qs) - 1}\u0002"
    t = re.sub(r"“([^”]*)”|\"([^\"]*)\"|‘([^’]*)’", sub, text)
    return t, qs


def _restore(s, qs):
    return re.sub("\u0001(\\d+)\u0002", lambda m: "“" + qs[int(m.group(1))] + "”", s)


def split_clauses(text):
    t, qs = _protect_quotes(text or "")
    # a quote that ends a sentence ("... says “She opened the letter.” Hold it ...") also ends the clause
    t = re.sub("(\u0001(\\d+)\u0002)\\s+(?=[A-Z])",
               lambda m: m.group(1) + ("\n" if re.search(r"[.!?]$", qs[int(m.group(2))].strip()) else " "), t)
    parts = re.split(r"(?<=[.;!?])\s+|\n+", t)
    out = []
    for p in parts:
        p = p.strip().rstrip(".;!").strip()
        if p:
            out.append(_restore(p, qs))
    return out


def _quote(s):
    m = re.search(r"“([^”]*)”", s)
    return m.group(1).strip().rstrip(",.") if m else None


def _occ(s):
    m = OCC_RE.search(s)
    if not m:
        return None
    v = next(g for g in m.groups() if g)
    return ORDINALS.get(v.lower(), None) if not v.isdigit() else int(v)


def _secs(s):
    s = s.strip().lower()
    s = re.sub(r"\s*(s|sec|secs|seconds?)$", "", s)
    return C.parse_time(s)


def _phrase_after(rest, verbs):
    q = _quote(rest)
    if q:
        return q
    m = re.search(r"(?:" + verbs + r")\s+(?:about\s+)?(?:the\s+)?(.+?)(?:$|,|;| and keep| for \d| until)", rest, re.I)
    return m.group(1).strip() if m else None


SAY_VERBS = r"says?|said|reads?|reaches|mentions?|mentioned|discuss(?:es)?|talks?\s+about|describes?|explains?|" \
            r"starts?\s+talking\s+about|introduces?|gets?\s+to"


def _level(c):
    """Words -> level in dB RELATIVE TO THE NARRATION (0 = as loud as the voice's loud parts)."""
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*db", c, re.I)
    if m:
        return float(m.group(1)), "as written"
    lc = c.lower()
    for words, v in ((("very quiet", "barely", "faint"), -20.0), (("quiet", "soft", "below the narration",
                                                                      "under the narration", "low"), -14.0),
                     (("loud", "strong"), -4.0)):
        if any(w in lc for w in words):
            return v, f"'{next(w for w in words if w in lc)}' = {v:+.0f} dB relative to the narration"
    return -10.0, "default level: -10 dB relative to the narration"


def parse(text):
    """Instruction text -> (settings dict, interpretation [(clause, meaning)], unresolved [clauses]).
    Only fills what the text says; everything else stays 'inherit'."""
    s = blank_settings()
    interp, unresolved = [], []
    last_visual = None
    for c in split_clauses(text):
        lc = c.lower()
        done = False
        # ---- notes
        if re.match(r"^(note|notes)\s*:", lc):
            interp.append((c, "kept as a scene note"))
            s.setdefault("_notes", []).append(c.split(":", 1)[1].strip())
            continue
        # ---- locks
        if re.match(r"^lock\b", lc):
            if re.search(r"\b(everything|all|this scene|the scene|scene)\b", lc) and not re.search(
                    r"visual|timing|motion|sfx|sound|effect|transition|caption|framing|b-?roll", lc):
                s["locked"] = True
                interp.append((c, "lock the whole scene"))
            else:
                got = []
                for key, pat in (("primary_visual", r"visual|image|picture|clip"), ("timing", r"timing|time|times"),
                                 ("motion", r"motion|zoom|pan"), ("framing", r"framing|crop"),
                                 ("sfx", r"sfx|sound"), ("effects", r"effect"), ("transition", r"transition"),
                                 ("captions", r"caption"), ("b_roll", r"b-?roll")):
                    if re.search(pat, lc):
                        got.append(key)
                if got:
                    s["locked_fields"] = sorted(set(s["locked_fields"]) | set(got))
                    interp.append((c, "lock " + ", ".join(FIELD_NAMES[g] for g in got)))
                else:
                    unresolved.append(c)
            continue
        # ---- B-roll off / on
        if re.search(r"\b(no|without|don'?t add|do not add)\s+(any\s+)?b-?roll\b|\badd no b-?roll\b", lc):
            s["b_roll_mode"], s["b_roll"] = "none", []
            interp.append((c, "B-roll: none in this scene"))
            continue
        m = re.match(r"^(insert|cut\s+to|add|overlay|show)\s+(?:the\s+)?(?:b-?roll\s+)?(?:clip|video|footage|image)?\s*"
                     r"(?P<f>" + FILE_RE + r")\b(?P<rest>.*)$", c, re.I)
        if m and (m.group(1).lower() in ("insert", "overlay") or re.match(r"cut\s+to", m.group(1), re.I)
                  or "b-roll" in lc or "broll" in lc):
            b = _parse_broll(m.group("f"), m.group("rest"), overlay=m.group(1).lower() == "overlay" or
                             re.search(r"\b(corner|small|picture.in.picture|overlay)\b", lc) is not None)
            s["b_roll_mode"] = "explicit"
            s["b_roll"].append(b)
            interp.append((c, _describe_broll(b)))
            continue
        # ---- primary visual
        m = re.match(r"^(?:then\s+)?(?:use|show|display|put|start with)\s+(?:the\s+)?(?:image|picture|photo|clip|video|"
                     r"file|asset)?\s*(?P<ref>" + FILE_RE + r"|#?\d+\b|(?:the\s+)?(?:first|second|third|fourth|fifth|"
                     r"sixth|seventh|eighth|ninth|tenth|last)\s+(?:image|picture|photo|clip))(?P<rest>.*)$", c, re.I)
        if m:
            seg = {"ref": m.group("ref").strip(), "anchor": None, "start_seconds": None, "end_seconds": None,
                   "duration_seconds": None, "end_anchor": None}
            rest = m.group("rest")
            _timing_from(rest, seg)
            if s["primary_visual"] is None:
                s["primary_visual"] = {"asset_filename": seg["ref"]}
                for k in ("start_seconds", "end_seconds", "duration_seconds"):
                    if seg[k] is not None:
                        s[k] = seg[k]
                if seg["anchor"]:
                    s["narration_anchor"] = seg["anchor"]
                if seg["end_anchor"]:
                    s["end_anchor"] = seg["end_anchor"]
                if seg["start_seconds"] is not None:
                    s["timing_mode"] = "absolute"
            else:
                s["segments"].append(seg)
            last_visual = seg
            interp.append((c, _describe_visual(seg)))
            done = True
        if done:
            continue
        # ---- hold / keep until
        m = re.match(r"^(?:and\s+)?(?:hold|keep)\s+(?:it|this|the\s+(?:image|shot|picture|visual))?\s*(?P<rest>.*)$", c, re.I)
        if m and re.search(r"until|till|for\s+\d|to the end", lc):
            tgt = last_visual if (last_visual is not None and s["segments"] and last_visual is s["segments"][-1]) \
                else None
            r = m.group("rest")
            if re.search(r"next\s+(topic|scene|section)", r, re.I):
                ea = {"next_topic": True}
            elif re.search(r"end of the (sentence|line)", r, re.I):
                ea = {"end_of_sentence": True}
            else:
                q = _quote(r)
                fm = re.search(r"for\s+" + TIME_RE, r, re.I)
                if q:
                    ea = {"text": q, "occurrence": _occ(r)}
                elif fm:
                    (tgt if tgt is not None else s)["duration_seconds"] = _secs(fm.group(1))
                    interp.append((c, f"lasts {_secs(fm.group(1)):.2f} s"))
                    continue
                else:
                    unresolved.append(c)
                    continue
            (tgt if tgt is not None else s)["end_anchor"] = ea
            interp.append((c, "ends " + _describe_end(ea)))
            continue
        # ---- motion
        mo = _parse_motion(c)
        if mo is not None:
            s["motion"] = mo
            interp.append((c, _describe_motion(mo)))
            continue
        # ---- SFX
        if re.search(r"\b(no|without)\s+(sfx|sound effects?|sounds?)\b", lc):
            s["sfx_mode"], s["sfx"] = "none", []
            interp.append((c, "sound effects: none in this scene"))
            continue
        if re.search(r"\b(automatic|auto)\s+(sfx|sound effects?)\b", lc):
            s["sfx_mode"] = "auto"
            interp.append((c, "sound effects: automatic suggestions (for review)"))
            continue
        m = re.match(r"^(?:play|add|use)\s+(?:the\s+)?(?:sound\s*(?:effect)?\s+|sfx\s+)?(?P<f>" + AUDIO_RE + r")\b(?P<rest>.*)$",
                     c, re.I)
        if m:
            e = _parse_sfx(m.group("f"), m.group("rest"))
            s["sfx_mode"] = "explicit"
            s["sfx"].append(e)
            interp.append((c, _describe_sfx(e)))
            continue
        # ---- transition
        tr = _parse_transition(c)
        if tr is not None:
            s["transition_out"] = tr
            interp.append((c, "transition out: " + (TRANSITIONS.get(tr.get("type"), "inherit") +
                                                    (f", {tr['duration_seconds']:.2f} s" if tr.get("duration_seconds")
                                                     else ""))))
            continue
        # ---- captions
        if re.search(r"\b(no|hide|without|turn off)\s+captions?\b|captions?\s+off\b", lc):
            s["captions"] = {"mode": "none"}
            interp.append((c, "captions: off in this scene"))
            continue
        m = re.search(r"captions?\s+(?:at|on|to)\s+(?:the\s+)?(top|middle|bottom)", lc)
        if m:
            s["captions"] = {"mode": "explicit", "position": m.group(1).capitalize()}
            interp.append((c, f"captions at the {m.group(1)}"))
            continue
        # ---- framing
        fr = _parse_framing(c)
        if fr is not None:
            s["framing"] = fr
            interp.append((c, "framing: " + _describe_framing(fr)))
            continue
        # ---- source clip range
        m = re.search(r"(?:use|play)\s+(?:only\s+)?" + TIME_RE + r"\s*(?:to|-|–)\s*" + TIME_RE + r"\s+of\s+the\s+(?:clip|video)",
                      c, re.I)
        if m:
            s["clip_in"], s["clip_out"] = _secs(m.group(1)), _secs(m.group(2))
            interp.append((c, f"source clip range {s['clip_in']:.2f}-{s['clip_out']:.2f} s"))
            continue
        # ---- effects
        if re.search(r"\b(no|without)\s+effects?\b", lc):
            s["effects_mode"], s["effects"] = "none", []
            interp.append((c, "effects: none in this scene"))
            continue
        eff = _parse_effect(c)
        if eff == "unsupported":
            unresolved.append(c + "  - this effect is unavailable in the current renderer")
            continue
        if eff is not None:
            s["effects_mode"] = "explicit"
            s["effects"].append(eff)
            interp.append((c, "effect: " + _describe_effect(eff)))
            continue
        # ---- speed changes are refused (narration timing is fixed)
        if re.search(r"\b(speed up|slow down|faster|slower)\b.*\b(voice|narration|voiceover)\b", lc):
            unresolved.append(c + "  - narration speed is never changed")
            continue
        unresolved.append(c)
    notes = s.pop("_notes", None)
    return s, interp, unresolved, notes


def _timing_from(rest, seg):
    r = rest or ""
    m = re.search(r"\bfrom\s+" + TIME_RE + r"\s*(?:to|-|–|until)\s*" + TIME_RE, r, re.I)
    if m:
        seg["start_seconds"], seg["end_seconds"] = _secs(m.group(1)), _secs(m.group(2))
        return
    m = re.search(r"\bat\s+(\d+:\d{1,2}(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:s|sec|seconds?))\b", r, re.I)
    if m and not _quote(r):
        seg["start_seconds"] = _secs(m.group(1))
    q = None
    m = re.search(r"\b(?:when|as|while|where|once)\s+(?:the\s+)?(?:narrator|voice(?:over)?|narration|speaker|she|he|they)?\s*"
                  r"(?:is\s+)?(?:" + SAY_VERBS + r")\b(?P<p>.*?)(?:$|;|\bhold\b|\bkeep\b|\buntil\b|\bfor\s+\d)", r, re.I)
    if m:
        q = _quote(m.group("p")) or m.group("p").strip(" ,.")
        q = re.sub(r"^(about\s+)?(the\s+)?", "", q, flags=re.I) if not _quote(m.group("p")) else q
    elif _quote(r) and re.search(r"\b(at|on|with)\s+“", r):
        q = _quote(r)
    if q:
        seg["anchor"] = {"text": q, "occurrence": _occ(r)}
    um = re.search(r"\buntil\s+(?:the\s+narrator\s+says\s+)?(?P<p>.*)$", r, re.I)
    if um:
        p = um.group("p")
        if re.search(r"next\s+(topic|scene|section)", p, re.I):
            seg["end_anchor"] = {"next_topic": True}
        elif _quote(p):
            seg["end_anchor"] = {"text": _quote(p), "occurrence": _occ(p)}
    fm = re.search(r"\bfor\s+" + TIME_RE, r, re.I)
    if fm:
        seg["duration_seconds"] = _secs(fm.group(1))


def _parse_motion(c):
    lc = c.lower()
    if re.search(r"\b(no|without)\s+(camera\s+)?(motion|movement|zoom|pan)\b|\bkeep it still\b|\bstatic\b|\bdon'?t move\b",
                 lc):
        return {"mode": "none"}
    if re.search(r"\b(automatic|auto)\s+motion\b", lc):
        return {"mode": "auto"}
    z = "zoom_in" if re.search(r"\b(zoom|push)(es|ing)?\s+(in|toward|towards|into|on)\b|\bpush in\b|\bzoom in\b", lc) else \
        "zoom_out" if re.search(r"\b(zoom|pull)(s|ing)?\s+(out|back|away)\b", lc) else None
    p = None
    pm = re.search(r"\bpan(?:s|ning)?\s+(?:to\s+the\s+)?(left|right|up|down)\b", lc)
    if pm:
        p = "pan_" + pm.group(1)
    if not z and not p:
        if re.search(r"\b(zoom|pan|motion|movement|ken burns)\b", lc) and not re.search(r"\btransition\b", lc):
            if re.search(r"\b(gentle|subtle|slow|slight)\b", lc):
                return {"mode": "explicit", "type": "zoom_in", "strength": "subtle", "ease": "smooth"}
        return None
    if z and p and p in ("pan_left", "pan_right"):
        typ = f"{z}+{p}"
    else:
        typ = z or p
    strength = "subtle" if re.search(r"\b(slow|slowly|gentle|gently|subtle|slight|slightly)\b", lc) else \
        "strong" if re.search(r"\b(fast|strong|dramatic|big)\b", lc) else "medium"
    ease = "linear" if "linear" in lc else "ease_in" if "ease in" in lc else "ease_out" if "ease out" in lc else "smooth"
    mo = {"mode": "explicit", "type": typ, "strength": strength, "ease": ease}
    tgt = re.search(r"\b(?:toward|towards|into|on)\s+(?:the\s+)?([a-z ]+?)(?:$|,|\.)", lc)
    if tgt:
        t = tgt.group(1).strip()
        if t in FOCUS_WORDS:
            mo["focus"] = list(FOCUS_WORDS[t])
        else:
            mo["target"] = t           # we can't see what's in the picture: you set the focal point
    return mo


def _parse_sfx(f, rest):
    e = {"asset_filename": f.strip(), "anchor": None, "start_seconds": None, "offset_seconds": 0.0,
         "gain_db": None, "fade_in": 0.0, "fade_out": 0.0, "trim_in": 0.0, "duration": None, "pan": 0.0}
    q = _quote(rest)
    if q:
        e["anchor"] = {"text": q, "occurrence_in_scene": _occ(rest) or None}
    else:
        m = re.search(r"\bat\s+" + TIME_RE, rest, re.I)
        if m:
            e["start_seconds"] = _secs(m.group(1))
        else:
            m = re.search(r"\bat\s+the\s+(start|beginning|end)\b", rest, re.I)
            if m:
                e["scene_point"] = "start" if m.group(1).lower() != "end" else "end"
    om = re.search(TIME_RE + r"\s+(after|before|later|earlier)", rest, re.I)
    if om:
        v = _secs(om.group(1))
        e["offset_seconds"] = v if om.group(2).lower() in ("after", "later") else -v
    gain, why = _level(rest)
    e["gain_db"], e["_why"] = gain, why
    fm = re.search(r"fade\s*in\s+" + TIME_RE, rest, re.I)
    if fm:
        e["fade_in"] = _secs(fm.group(1))
    fm = re.search(r"fade\s*out\s+" + TIME_RE, rest, re.I)
    if fm:
        e["fade_out"] = _secs(fm.group(1))
    dm = re.search(r"\bfor\s+" + TIME_RE, rest, re.I)
    if dm:
        e["duration"] = _secs(dm.group(1))
    if re.search(r"\b(on the )?left\b", rest, re.I):
        e["pan"] = -0.6
    elif re.search(r"\b(on the )?right\b", rest, re.I):
        e["pan"] = 0.6
    return e


def _parse_broll(f, rest, overlay=False):
    b = {"asset_filename": f.strip(), "mode": "overlay" if overlay else "insert", "anchor": None,
         "end_anchor": None, "start_seconds": None, "end_seconds": None, "duration_seconds": None,
         "clip_in": None, "clip_out": None, "size": 0.35, "position": "top_right", "audio_gain_db": None,
         "short": None}
    _timing_from(rest, b)
    pm = re.search(r"\b(top|bottom)[\s-]+(left|right)\b|\b(center|centre)\b", rest, re.I)
    if pm:
        b["position"] = (f"{pm.group(1)}_{pm.group(2)}".lower() if pm.group(1) else "center")
    sm = re.search(r"\b(\d{1,3})\s*%", rest)
    if sm:
        b["size"] = max(0.1, min(0.9, int(sm.group(1)) / 100.0))
    am = re.search(r"\bwith\s+(?:its\s+)?(?:own\s+)?(?:sound|audio)\b(?:\s+at\s+(-?\d+(?:\.\d+)?)\s*db)?", rest, re.I)
    if am:
        b["audio_gain_db"] = float(am.group(1)) if am.group(1) else -12.0
    if re.search(r"\bloop(ed|ing)?\b", rest, re.I):
        b["short"] = "loop"
    elif re.search(r"\bfreeze\b", rest, re.I):
        b["short"] = "freeze"
    m = re.search(r"\b(?:use|from)\s+" + TIME_RE + r"\s*(?:to|-|–)\s*" + TIME_RE + r"\s+of\s+the\s+(?:clip|video)", rest, re.I)
    if m:
        b["clip_in"], b["clip_out"] = _secs(m.group(1)), _secs(m.group(2))
    return b


def _parse_transition(c):
    lc = c.lower()
    if re.search(r"\b(hard\s+)?cut\s+(straight\s+)?(to|into)\s+the\s+next\b|\bjust\s+cut\b|\bno\s+transition\b|"
                 r"\btransition\s*[:=]?\s*(none|cut)\b|\bhard cut\b|\bdirect cut\b", lc):
        return {"mode": "explicit", "type": "cut", "duration_seconds": 0.0}
    for pat, typ in ((r"\b(dissolve|cross\s*-?fade)", "crossfade"), (r"\bfade\s+(to|through)\s+black|\bdip\s+to\s+black",
                                                                     "dip_black"),
                     (r"\bslide\b", "slide"), (r"\bwhip\b", "whip"), (r"\bzoom\s+(through|transition)\b", "zoom")):
        if re.search(pat, lc) and re.search(r"next|transition|into|to the|between|out|end|black", lc):
            d = re.search(r"(?:over|of|for|lasting)\s+" + TIME_RE, lc)
            return {"mode": "explicit", "type": typ, "duration_seconds": _secs(d.group(1)) if d else None}
    if re.search(r"\b(automatic|auto)\s+transition\b", lc):
        return {"mode": "auto"}
    return None


def _parse_framing(c):
    lc = c.lower()
    if re.search(r"\bfill the (frame|screen)\b|\bcrop to fill\b|\bno (black )?bars\b", lc):
        fr = {"mode": "explicit", "fit": "crop"}
    elif re.search(r"\bshow the whole (image|picture|clip)\b|\bfit (it|the image)\b|\bblurred background\b", lc):
        fr = {"mode": "explicit", "fit": "fit_blur"}
    elif re.search(r"\bblack bars\b|\bletterbox\b", lc):
        fr = {"mode": "explicit", "fit": "fit_bars"}
    else:
        fr = None
    fm = re.search(r"\b(?:focus|focal point|center|centre|keep)\s+(?:on|at)\s+(?:the\s+)?(top left|top right|bottom left|"
                   r"bottom right|top|bottom|left|right|center|centre|middle)\b", lc)
    if fm:
        fr = fr or {"mode": "explicit", "fit": "crop"}
        fr["focus"] = list(FOCUS_WORDS[fm.group(1)])
    return fr


def _parse_effect(c):
    lc = c.lower()
    if any(u in lc for u in UNSUPPORTED_EFFECTS):
        return "unsupported"
    m = re.search(r"\b(?:show|add|put)\s+(?:the\s+)?(?:text|label|caption card|title)\s*[:]?\s*“([^”]+)”", c, re.I) or \
        re.search(r"\btext\s*:\s*“([^”]+)”", c, re.I)
    if m:
        return {"type": "text", "params": {"text": m.group(1), "style": "label"}, "start_offset": 0.0,
                "end_offset": None}
    for pat, typ in ((r"black\s*(and|&)\s*white|greyscale|grayscale|monochrome", "black_white"), (r"sepia", "sepia"),
                     (r"vignette", "vignette"), (r"\bwarm(er)?\b", "warm"), (r"\bcold(er)?\b|\bcool(er)?\b", "cold"),
                     (r"\bblur(red|ry)?\b", "blur"), (r"desaturat|less colou?r|muted colou?rs", "saturation"),
                     (r"brighten|brighter", "brightness"), (r"darken|darker", "brightness"),
                     (r"more contrast|increase contrast|high contrast", "contrast")):
        if re.search(pat, lc):
            v = EFFECTS[typ]["default"]
            if typ == "brightness" and re.search(r"darken|darker", lc):
                v = -0.15
            am = re.search(r"(\d{1,3})\s*%", lc)
            if am and typ not in ("brightness", "contrast"):
                v = round(min(EFFECTS[typ]["max"], int(am.group(1)) / 100.0 * EFFECTS[typ]["max"]), 3)
            e = {"type": typ, "params": {"amount": v}, "start_offset": 0.0, "end_offset": None}
            fm = re.search(r"(?:for|during)\s+the\s+(first|last)\s+" + TIME_RE, lc)
            if fm:
                d = _secs(fm.group(2))
                if fm.group(1) == "first":
                    e["end_offset"] = d
                else:
                    e["start_offset"] = -d          # negative = counted back from the scene end
            return e
    return None


# ---------------------------------------------------------------------------- descriptions (for the UI)
def _describe_anchor(a):
    if not a:
        return ""
    occ = a.get("occurrence") or a.get("occurrence_in_scene")
    return f"“{a['text']}”" + (f" (occurrence {occ})" if occ else "")


def _describe_end(ea):
    if not ea:
        return "at the next scene"
    if ea.get("next_topic"):
        return "at the next topic change"
    if ea.get("end_of_sentence"):
        return "at the end of the sentence"
    return "after " + _describe_anchor(ea)


def _describe_visual(seg):
    t = f"visual {seg['ref']}"
    if seg.get("start_seconds") is not None:
        t += f" from {seg['start_seconds']:.2f} s" + (f" to {seg['end_seconds']:.2f} s" if seg.get("end_seconds")
                                                      is not None else "")
    elif seg.get("anchor"):
        t += " starting at " + _describe_anchor(seg["anchor"])
    if seg.get("end_anchor"):
        t += ", ends " + _describe_end(seg["end_anchor"])
    if seg.get("duration_seconds"):
        t += f", lasts {seg['duration_seconds']:.2f} s"
    return t


def _describe_motion(mo):
    if mo.get("mode") in ("none", "auto", "inherit"):
        return "motion: " + MODE_NAMES[mo["mode"]].lower()
    t = f"motion: {mo['type'].replace('_', ' ').replace('+', ' + ')}, {mo.get('strength', 'medium')}"
    if mo.get("focus"):
        t += f", toward {mo['focus']}"
    if mo.get("target"):
        t += f" (toward “{mo['target']}” - set the focal point; AutoEditor can't see what's in the picture)"
    return t


def _describe_sfx(e):
    t = f"sound {e['asset_filename']}"
    if e.get("anchor"):
        t += " at " + _describe_anchor(e["anchor"])
    elif e.get("start_seconds") is not None:
        t += f" at {e['start_seconds']:.2f} s"
    if e.get("offset_seconds"):
        t += f" {e['offset_seconds']:+.2f} s"
    t += f", level {e['gain_db']:+.0f} dB relative to the narration"
    if e.get("_why"):
        t += f" ({e['_why']})"
    return t


def _describe_broll(b):
    t = f"B-roll {b['asset_filename']} ({'full-frame insert' if b['mode'] == 'insert' else 'overlay ' + b['position']})"
    if b.get("anchor"):
        t += " at " + _describe_anchor(b["anchor"])
    elif b.get("start_seconds") is not None:
        t += f" {b['start_seconds']:.2f}-{(b.get('end_seconds') or 0):.2f} s"
    if b.get("duration_seconds"):
        t += f" for {b['duration_seconds']:.2f} s"
    t += ", source audio " + ("muted" if b.get("audio_gain_db") is None else f"{b['audio_gain_db']:+.0f} dB")
    return t


def _describe_framing(fr):
    from .framing import FIT_MODES
    t = FIT_MODES.get(fr.get("fit"), "inherit")
    if fr.get("focus"):
        t += f", focal point {fr['focus'][0]:.0%} / {fr['focus'][1]:.0%}"
    return t


def _describe_effect(e):
    if e["type"] == "text":
        return f"text “{e['params']['text']}”"
    return f"{EFFECTS[e['type']]['label']} {e['params'].get('amount')}"


# ============================================================================ resolving (intent -> times/files)
class Ctx:
    def __init__(self, project):
        from .project import project_media
        self.p = project
        self.words = project.get("words") or []
        self.dur = float(project.get("duration") or 0)
        self.fps = int(project["settings"].get("fps", 30))
        self.media = project_media(project)
        self.sfx = sfx_files(project)


def _choice(rec, key, options, msg):
    """Ambiguity: returns the chosen option or None (and adds an issue listing the candidates)."""
    k = rec.get("choices", {}).get(key)
    if k is not None and 0 <= int(k) < len(options):
        return options[int(k)]
    rec["issues"].append({"level": "choice", "key": key, "msg": msg,
                          "options": [o[1] for o in options]})
    return None


def _anchor_word(ctx, rec, key, a, lo=0, hi=None, label="phrase"):
    occ = find_occurrences(ctx.words, a["text"], lo, hi)
    if not occ:
        rec["issues"].append({"level": "error", "key": key,
                              "msg": f"Couldn't find {label} “{a['text']}” in the recording."})
        return None
    want = a.get("occurrence") or a.get("occurrence_in_scene")
    if want:
        if want == -1:
            return occ[-1]
        if want > len(occ):
            rec["issues"].append({"level": "error", "key": key, "msg": f"“{a['text']}” is spoken {len(occ)} time(s); "
                                                                       f"occurrence {want} doesn't exist."})
            return None
        return occ[want - 1]
    if len(occ) == 1:
        return occ[0]
    opts = [(o, f"{_fmt(ctx.words[o[0]]['s'])} - …{_ctx(ctx.words, o[0], o[1])}…") for o in occ]
    pick = _choice(rec, key, opts, f"The phrase “{a['text']}” appears {len(occ)} times. Choose which occurrence "
                                   "to use.")
    return pick[0] if pick else None


def _fmt(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:05.2f}"


def _asset(ctx, rec, key, ref, kind="visual"):
    pool = ctx.sfx if kind == "sfx" else ctx.media
    ref = (ref or "").strip()
    om = re.match(r"(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last)\s+(image|picture|"
                  r"photo|clip)", ref, re.I)
    if om:
        order = sorted(pool, key=lambda p: (C.media_number(p) if C.media_number(p) is not None else 10 ** 9,
                                            os.path.basename(p).lower()))
        n = ORDINALS[om.group(1).lower()]
        if not order:
            rec["issues"].append({"level": "error", "key": key, "msg": "No media files to choose from."})
            return None
        cand = order[n - 1] if n != -1 and n <= len(order) else order[-1]
        opts = [(cand, f"{os.path.basename(cand)} (#{n if n != -1 else len(order)} by number in the file name)")]
        pick = _choice(rec, key, opts, f"“{ref}” depends on how your files are ordered. Confirm the candidate.")
        return pick[0] if pick else None
    hits, problem = find_asset(ref, pool, kind)
    if problem:
        rec["issues"].append({"level": "missing", "key": key, "msg": problem})
        return None
    if len(hits) == 1:
        return hits[0]
    opts = [(h, os.path.relpath(h, ctx.p.get("media_dir") or os.path.dirname(h))) for h in hits]
    pick = _choice(rec, key, opts, f"{len(hits)} files are named {os.path.basename(ref)}. Choose one.")
    return pick[0] if pick else None


def _scene_by_id(project, sid):
    for k, s in enumerate(project.get("scenes") or []):
        if s.get("id") == sid:
            return k, s
    return None, None


def _topic_end(ctx, t):
    from .planner import boundary_scores, paragraph_starts, sentences
    sents = sentences(ctx.words)
    paras = paragraph_starts(ctx.p, ctx.words)
    sc = boundary_scores(ctx.words, sents, paras)
    cuts = sorted(paras) if paras else sorted(sents[j + 1][0] for j, v in enumerate(sc) if v >= 1.25)
    for wi in cuts:
        if ctx.words[wi]["s"] > t + 1.0:
            return C.cut_time(ctx.words, wi)
    return ctx.dur


def _sentence_end(ctx, wi):
    from .planner import sentences
    for a, b in sentences(ctx.words):
        if a <= wi <= b:
            return cut_after(ctx.words, b + 1, ctx.dur)
    return ctx.dur


def resolve(project, rec, ctx=None):
    """Turn a record's settings into concrete values. Fills rec['issues'] and returns the plan dict
    (or None when the timing can't be resolved)."""
    ctx = ctx or Ctx(project)
    rec["issues"] = [i for i in rec.get("issues") or [] if i.get("key") == "stale"]   # kept until reviewed
    if rec.get("aspect_review"):
        rec["issues"].append({"level": "warning", "key": "aspect", "accept": True,
                              "msg": f"The video shape changed to {rec['aspect_review']}: check this scene's manual "
                                     "framing (focal point / crop / zoom)."})
    s = rec["settings"]
    k, cur = _scene_by_id(project, rec["scene_id"])
    plan = {"scene_id": rec["scene_id"], "segments": [], "broll": [], "sfx": [], "effects": [], "overlays": []}
    words, dur = ctx.words, ctx.dur
    # ---- timing of the scene
    a = b = None
    explicit_timing = False
    tm = s.get("timing_mode") or "follow_narration"
    anchor_i = None
    if tm == "absolute" and s.get("start_seconds") is not None:
        a = float(s["start_seconds"])
        explicit_timing = True
    elif tm == "scene_relative" and cur is not None:
        a = cur["start"] + float(s.get("offset_seconds") or 0)
        explicit_timing = True
    elif s.get("narration_anchor"):
        oc = _anchor_word(ctx, rec, "narration_anchor", s["narration_anchor"])
        if oc:
            anchor_i = oc
            a = cut_before(words, oc[0]) + float(s.get("offset_seconds") or 0)
            explicit_timing = True
    elif cur is not None:
        a = cur["start"]
    if a is not None:
        ea = s.get("end_anchor")
        if tm == "absolute" and s.get("end_seconds") is not None:
            b = float(s["end_seconds"])
        elif s.get("duration_seconds"):
            b = a + float(s["duration_seconds"])
        elif ea and ea.get("text"):
            lo = anchor_i[0] if anchor_i else 0
            oc = _anchor_word(ctx, rec, "end_anchor", ea, lo=lo, label="end phrase")
            b = cut_after(words, oc[1], dur) if oc else None
        elif ea and ea.get("next_topic"):
            b = _topic_end(ctx, a)
        elif ea and ea.get("end_of_sentence") and anchor_i:
            b = _sentence_end(ctx, anchor_i[0])
        elif cur is not None and cur["end"] > a + 0.3 and not anchor_i:
            b = cur["end"]
        elif anchor_i:
            nxt = cur["end"] if cur is not None and cur["end"] > a + 0.3 else _sentence_end(ctx, anchor_i[0])
            b = nxt
        else:
            b = a + 3.0
        if b is not None and (ea or s.get("duration_seconds") or tm == "absolute"):
            explicit_timing = True
    if a is not None and b is not None:
        if a < -1e-6 or b > dur + 0.05:
            rec["issues"].append({"level": "conflict", "key": "timing",
                                  "msg": f"Times {_fmt(a)}-{_fmt(b)} are outside the narration (0-{_fmt(dur)}). "
                                         "The narration is never extended - shorten the scene."})
        if b - a < 1.0 / ctx.fps:
            rec["issues"].append({"level": "conflict", "key": "timing",
                                  "msg": f"The scene would be {max(0, b - a):.2f} s long - check the start/end."})
        a, b = max(0.0, snap(a, ctx.fps)), min(dur, snap(b, ctx.fps))
    # ---- visuals (one or several segments)
    vis_ref = (s.get("primary_visual") or {}).get("asset_filename")
    segs = []
    if vis_ref or s.get("segments"):
        v = _asset(ctx, rec, "primary_visual", vis_ref) if vis_ref else (cur.get("media") if cur else None)
        segs.append({"media": v, "start": a, "end": b, "ref": vis_ref})
        for j, sg in enumerate(s.get("segments") or []):
            key = f"segment{j + 1}"
            m = _asset(ctx, rec, key, sg.get("ref"))
            st = None
            if sg.get("start_seconds") is not None:
                st = float(sg["start_seconds"])
            elif sg.get("anchor"):
                lo = anchor_i[0] if anchor_i else 0
                oc = _anchor_word(ctx, rec, key + "_anchor", sg["anchor"], lo=lo)
                st = cut_before(words, oc[0]) if oc else None
            if st is None:
                rec["issues"].append({"level": "error", "key": key,
                                      "msg": f"Visual {sg.get('ref')} has no start - say when it starts "
                                             "(when the narrator says “…”, or at a time)."})
                continue
            segs.append({"media": m, "start": snap(st, ctx.fps), "end": None, "ref": sg.get("ref")})
            if sg.get("end_anchor") and sg["end_anchor"].get("text"):
                oc = _anchor_word(ctx, rec, key + "_end", sg["end_anchor"], label="end phrase")
                if oc:
                    b = max(b or 0, snap(cut_after(words, oc[1], dur), ctx.fps))
            elif sg.get("duration_seconds"):
                b = max(b or 0, snap(st + float(sg["duration_seconds"]), ctx.fps))
        segs.sort(key=lambda x: x["start"] if x["start"] is not None else 0)
        for x, y in zip(segs, segs[1:]):
            x["end"] = y["start"]
        if segs:
            segs[-1]["end"] = b
            segs[0]["start"] = a
        for sg in segs:
            if sg["start"] is not None and sg["end"] is not None and sg["end"] - sg["start"] < 0.2:
                rec["issues"].append({"level": "conflict", "key": "segments",
                                      "msg": f"Visual {sg['ref']} would last {sg['end'] - sg['start']:.2f} s - "
                                             "its start is at/after the next visual's or the scene's end."})
    elif a is not None:
        segs.append({"media": cur.get("media") if cur else None, "start": a, "end": b, "ref": None})
    plan["segments"] = segs
    plan["explicit_timing"] = explicit_timing
    plan["explicit_visual"] = bool(vis_ref)
    plan["start"], plan["end"] = a, b
    if a is None:
        if not any(i.get("key") == "narration_anchor" for i in rec["issues"]):
            rec["issues"].append({"level": "error", "key": "timing",
                                  "msg": "No time for this scene yet (the scene isn't on the timeline and no anchor "
                                         "or time is given)."})
        return plan
    # ---- clip range of the primary visual
    if s.get("clip_in") is not None or s.get("clip_out") is not None:
        m0 = segs[0]["media"] if segs else None
        if m0 and is_video(m0):
            from .visuals import probe_duration
            d = probe_duration(m0) or 0
            ci, co = float(s.get("clip_in") or 0), float(s.get("clip_out") or d)
            if co <= ci or (d and ci >= d):
                rec["issues"].append({"level": "conflict", "key": "clip",
                                      "msg": f"Source range {ci:.2f}-{co:.2f} s is invalid for a {d:.2f} s clip."})
        elif m0:
            rec["issues"].append({"level": "warning", "key": "clip", "msg": "Source in/out only applies to video clips."})
    # ---- B-roll
    if s.get("b_roll_mode") == "explicit":
        for j, br in enumerate(s.get("b_roll") or []):
            key = f"b_roll{j + 1}"
            m = _asset(ctx, rec, key, br.get("asset_filename"))
            if not m:
                continue
            bs = be = None
            if br.get("start_seconds") is not None:
                bs = float(br["start_seconds"])
                be = float(br["end_seconds"]) if br.get("end_seconds") is not None else None
            elif br.get("anchor"):
                lo = _word_at(words, a)
                hi = _word_at(words, b) + 1
                ac = dict(br["anchor"])
                if find_occurrences(words, ac["text"], lo, hi):
                    oc = _anchor_word(ctx, rec, key, ac, lo, hi, "B-roll phrase")
                else:
                    oc = _anchor_word(ctx, rec, key, ac, label="B-roll phrase")
                if oc:
                    bs = cut_before(words, oc[0])
                    if br.get("end_anchor") and br["end_anchor"].get("text"):
                        oe = _anchor_word(ctx, rec, key + "_end", br["end_anchor"], lo=oc[0], label="end phrase")
                        be = cut_after(words, oe[1], dur) if oe else None
                    elif not br.get("duration_seconds"):
                        be = _sentence_end(ctx, oc[0])
            elif br.get("scene_relative") is not None:
                bs = a + float(br["scene_relative"])
            else:
                bs = a
            if bs is None:
                continue
            if be is None:
                be = bs + float(br.get("duration_seconds") or (b - bs))
            bs, be = snap(bs, ctx.fps), snap(min(be, dur), ctx.fps)
            if be - bs < 0.1:
                rec["issues"].append({"level": "conflict", "key": key, "msg": f"B-roll {br['asset_filename']} has no "
                                                                             "time left (check its start/end)."})
                continue
            if bs < a - 1e-6 or be > b + 1e-6:
                rec["issues"].append({"level": "warning", "key": key,
                                      "msg": f"B-roll {br['asset_filename']} ({_fmt(bs)}-{_fmt(be)}) runs outside "
                                             f"this scene ({_fmt(a)}-{_fmt(b)})."})
            item = {"media": m, "start": bs, "end": be, "mode": br.get("mode", "insert"),
                    "size": float(br.get("size") or 0.35), "position": br.get("position") or "top_right",
                    "clip_in": br.get("clip_in"), "clip_out": br.get("clip_out"),
                    "audio_gain_db": br.get("audio_gain_db"), "short_clip": br.get("short") or None,
                    "fade": 0.0 if br.get("mode", "insert") == "insert" else 0.2}
            if is_video(m):
                from .visuals import probe_duration
                d = probe_duration(m) or 0
                ci = float(br.get("clip_in") or 0)
                co = float(br.get("clip_out") or d)
                if d and (co <= ci or ci >= d):
                    rec["issues"].append({"level": "conflict", "key": key,
                                          "msg": f"Source range {ci:.2f}-{co:.2f} s is invalid for {os.path.basename(m)} "
                                                 f"({d:.2f} s)."})
                    continue
                avail = co - ci
                need = be - bs
                if avail < need - 0.05:
                    if not br.get("short"):
                        rec["issues"].append({"level": "warning", "key": key + "_short", "accept": True,
                                              "msg": f"This clip provides {avail:.1f} seconds, but the insert needs "
                                                     f"{need:.1f} seconds. Choose another source range, another "
                                                     "clip, approved looping, or a freeze frame (accepting this "
                                                     "freezes the last frame)."})
                    item["short_clip"] = br.get("short") or "freeze"
            plan["broll"].append(item)
        # overlapping full-frame inserts: the later one would hide the earlier - a conflict, not a guess
        ins = sorted([x for x in plan["broll"] if x["mode"] == "insert"], key=lambda x: x["start"])
        for x, y in zip(ins, ins[1:]):
            if y["start"] < x["end"] - 1e-6:
                rec["issues"].append({"level": "conflict", "key": "b_roll",
                                      "msg": f"Inserts {os.path.basename(x['media'])} and {os.path.basename(y['media'])} "
                                             f"overlap ({_fmt(y['start'])}-{_fmt(min(x['end'], y['end']))}). Shorten "
                                             "one or make one an overlay."})
    # ---- SFX
    if s.get("sfx_mode") == "explicit":
        for j, e in enumerate(s.get("sfx") or []):
            key = f"sfx{j + 1}"
            f = _asset(ctx, rec, key, e.get("asset_filename"), kind="sfx")
            if not f:
                continue
            t = None
            if e.get("anchor"):
                lo, hi = _word_at(words, a), _word_at(words, b) + 1
                ac = dict(e["anchor"])
                ac["occurrence"] = ac.get("occurrence_in_scene") or ac.get("occurrence")
                if find_occurrences(words, ac["text"], lo, hi):
                    oc = _anchor_word(ctx, rec, key, ac, lo, hi, "sound cue")
                else:
                    oc = _anchor_word(ctx, rec, key, ac, label="sound cue")
                    if oc:
                        rec["issues"].append({"level": "warning", "key": key, "accept": True,
                                              "msg": f"“{ac['text']}” isn't spoken inside this scene - the sound uses "
                                                     f"the occurrence at {_fmt(words[oc[0]]['s'])}."})
                    if oc:
                        rec["issues"].append({"level": "warning", "key": key,
                                              "msg": f"“{ac['text']}” isn't spoken inside this scene - used the "
                                                     f"occurrence at {_fmt(words[oc[0]]['s'])}."})
                t = words[oc[0]]["s"] if oc else None       # sound starts with the word itself
            elif e.get("start_seconds") is not None:
                t = float(e["start_seconds"])
            elif e.get("scene_point") == "end":
                t = b
            else:
                t = a
            if t is None:
                continue
            t = max(0.0, t + float(e.get("offset_seconds") or 0))
            if t > dur:
                rec["issues"].append({"level": "conflict", "key": key, "msg": f"Sound at {_fmt(t)} is after the end."})
                continue
            plan["sfx"].append({"t": round(t, 4), "file": f, "rel_db": float(e["gain_db"] if e.get("gain_db")
                                                                           is not None else -10.0),
                                "fade_in": float(e.get("fade_in") or 0), "fade_out": float(e.get("fade_out") or 0),
                                "trim_in": float(e.get("trim_in") or 0),
                                "dur": float(e["duration"]) if e.get("duration") else None,
                                "pan": float(e.get("pan") or 0), "enabled": True, "cat": "explicit",
                                "reason": f"instruction {rec['scene_id']}"})
    # ---- effects
    if s.get("effects_mode") == "explicit":
        for j, e in enumerate(s.get("effects") or []):
            key = f"effect{j + 1}"
            if e.get("type") not in EFFECTS:
                rec["issues"].append({"level": "conflict", "key": key, "msg": f"Effect '{e.get('type')}' is unavailable "
                                                                             "in the current renderer."})
                continue
            so = float(e.get("start_offset") or 0)
            eo = e.get("end_offset")
            L = b - a
            st = L + so if so < 0 else so
            en = L if eo in (None, "") else (L + float(eo) if float(eo) < 0 else float(eo))
            if st >= L or en <= st:
                rec["issues"].append({"level": "warning", "key": key,
                                      "msg": f"Effect {EFFECTS[e['type']]['label']} ({st:.2f}-{en:.2f} s) is outside "
                                             f"the scene ({L:.2f} s) - it was clamped."})
            st, en = max(0.0, min(st, L)), max(0.0, min(en, L))
            if en - st < 1.0 / ctx.fps:
                continue
            if e["type"] == "text":
                txt = (e.get("params") or {}).get("text", "").strip()
                if not txt:
                    rec["issues"].append({"level": "warning", "key": key, "msg": "Text overlay has no text."})
                    continue
                plan["overlays"].append({"type": (e.get("params") or {}).get("style", "label"), "start": a + st,
                                         "end": a + en, "text": txt, "sub": ""})
            else:
                rng = EFFECTS[e["type"]]
                v = float((e.get("params") or {}).get("amount", rng["default"]))
                v = min(rng["max"], max(rng["min"], v))
                plan["effects"].append({"type": e["type"], "amount": v, "start": round(st, 4), "end": round(en, 4)})
    # ---- motion / framing / transition / captions (resolved values, or None = leave to guidelines)
    plan["motion"] = _resolve_motion(rec, s.get("motion") or {"mode": "inherit"})
    fr = s.get("framing") or {"mode": "inherit"}
    plan["framing"] = fr if fr.get("mode") == "explicit" else None
    tr = s.get("transition_out") or {"mode": "inherit"}
    plan["transition"] = tr if tr.get("mode") in ("explicit", "none") else None
    if tr.get("mode") == "explicit" and tr.get("duration_seconds") and tr.get("type") != "cut":
        nxt = (project.get("scenes") or [])[k + 1] if k is not None and k + 1 < len(project.get("scenes") or []) else None
        L = b - a
        L2 = (nxt["end"] - nxt["start"]) if nxt else L
        d = float(tr["duration_seconds"])
        if d > 0.9 * min(L, L2):
            rec["issues"].append({"level": "warning", "key": "transition",
                                  "msg": f"Transition {d:.2f} s is too long for these scenes - it will be clamped "
                                         f"to {0.45 * min(L, L2) * 2:.2f} s."})
    cp = s.get("captions") or {"mode": "inherit"}
    plan["captions"] = cp if cp.get("mode") in ("none", "explicit") else None
    plan["sfx_mode"] = s.get("sfx_mode", "inherit")
    plan["b_roll_mode"] = s.get("b_roll_mode", "inherit")
    plan["effects_mode"] = s.get("effects_mode", "inherit")
    return plan


def _resolve_motion(rec, mo):
    if mo.get("mode") == "explicit":
        out = dict(mo)
        if mo.get("target") and not mo.get("focus"):
            rec["issues"].append({"level": "warning", "key": "motion", "accept": True,
                                  "msg": f"Zoom toward “{mo['target']}”: AutoEditor can't see where that is in the "
                                         "picture. Set the focal point (Framing) - until then it zooms to the centre."})
        return out
    if mo.get("mode") in ("none", "auto"):
        return dict(mo)
    return None


def _word_at(words, t):
    for i, w in enumerate(words):
        if w["e"] > t:
            return i
    return max(0, len(words) - 1)


# ============================================================================ applying (intent -> timeline)
def _explicit_timing_ids(project):
    out = set()
    for sid, r in records(project).items():
        if r.get("applied") and (r.get("resolved") or {}).get("explicit_timing"):
            out.add(sid)
            for j in range(len((r.get("resolved") or {}).get("segments") or []) - 1):
                out.add(f"{sid}-{j + 2}")
    return out


def _timing_locked(s):
    return bool(s.get("locked")) or "timing" in (s.get("locked_fields") or [])


def place(project, sid, segs, fps, own_ids):
    """Put the scene's segments on the timeline. Other scenes are trimmed / stretched; a locked or
    explicitly-timed scene in the way is a conflict (returns a message, changes nothing)."""
    scenes = [dict(s) for s in project.get("scenes") or []]
    dur = float(project.get("duration") or 0)
    mine = [s for s in scenes if s.get("id") in own_ids]
    rest = [s for s in scenes if s.get("id") not in own_ids]
    fixed = _explicit_timing_ids(project) - set(own_ids)
    a, b = segs[0]["start"], segs[-1]["end"]
    for s in rest:
        if s["end"] <= a + 1e-6 or s["start"] >= b - 1e-6:
            continue
        if _timing_locked(s) or s.get("id") in fixed:
            why = "locked" if _timing_locked(s) else "has its own instruction timing"
            return (f"Overlaps scene {s.get('id')} ({_fmt(s['start'])}-{_fmt(s['end'])}), which {why}. Change one "
                    "of the two scenes, or unlock it.")
    base = dict(mine[0]) if mine else {"motion": "zoom_in", "transition": "cut", "zoom": 1.0, "x": 0.0, "y": 0.0,
                                       "rot": 0.0}
    out = []
    for s in rest:
        if s["end"] <= a + 1e-6 or s["start"] >= b - 1e-6:
            out.append(s)
            continue
        if s["start"] < a - 0.05:
            out.append(dict(s, end=a))
        if s["end"] > b + 0.05:
            out.append(dict(s, start=b, id=s.get("id") if s["start"] >= a - 0.05 else _fresh_id(project)))
    new = []
    for j, sg in enumerate(segs):
        sc = dict(base)
        old = next((m for m in mine if m.get("id") == (sid if j == 0 else f"{sid}-{j + 1}")), None)
        if old:
            sc = dict(old)
        sc.update(start=sg["start"], end=sg["end"], id=sid if j == 0 else f"{sid}-{j + 1}")
        if j:
            sc["segment_of"] = sid
        if sg.get("media") is not None:
            sc["media"] = sg["media"]
        new.append(sc)
    out = sorted(out + new, key=lambda s: s["start"])
    # cover the narration exactly once: stretch movable neighbours into gaps
    gaps = []
    for x, y in zip(out, out[1:]):
        if y["start"] - x["end"] > 1e-6:
            if not (_timing_locked(x) or x.get("id") in fixed or x.get("id") in own_ids_set(sid, segs)):
                x["end"] = y["start"]
            elif not (_timing_locked(y) or y.get("id") in fixed or y.get("id") in own_ids_set(sid, segs)):
                y["start"] = x["end"]
            else:
                gaps.append((x, y))
    if out and out[0]["start"] > 1e-6 and not (_timing_locked(out[0]) or out[0].get("id") in fixed):
        out[0]["start"] = 0.0
    if out and dur and out[-1]["end"] < dur - 1e-6 and not (_timing_locked(out[-1]) or out[-1].get("id") in fixed):
        out[-1]["end"] = dur
    if gaps:
        x, y = gaps[0]
        return (f"Nothing would cover {_fmt(x['end'])}-{_fmt(y['start'])} (between {x.get('id')} and {y.get('id')}, "
                "both fixed). Extend one of them or remove a lock.")
    out = [s for s in out if s["end"] - s["start"] > 0.02 or s.get("id") in own_ids_set(sid, segs)]
    project["scenes"] = out
    return None


def own_ids_set(sid, segs):
    return {sid} | {f"{sid}-{j + 1}" for j in range(1, len(segs))}


def _fresh_id(project):
    n = int(project.get("next_scene_id", 1))
    ids = {s.get("id") for s in project.get("scenes") or []} | set(records(project))
    while f"S{n:03d}" in ids:
        n += 1
    project["next_scene_id"] = n + 1
    return f"S{n:03d}"


def _status(rec):
    lv = [i["level"] for i in rec.get("issues") or [] if not (i.get("accept") and i["msg"] in rec.get("accepted", []))]
    if rec.get("unresolved"):
        return "unresolved"
    if "missing" in lv:
        return "missing"
    if "conflict" in lv or "error" in lv:
        return "conflict"
    if "choice" in lv or "warning" in lv or rec.get("stale"):
        return "review"
    if not has_content(rec):
        return "empty"
    return "ready" if rec.get("applied") else "draft"


def blocking(rec):
    return any(i["level"] in ("missing", "conflict", "error", "choice") for i in rec.get("issues") or [])


def apply(project, sid, log=print, ctx=None, reviewed=False, placed=False):
    """Resolve + write one scene's instruction into the timeline. -> (ok, message).
    reviewed=True (you pressed Apply): clears 'narration changed' review flags."""
    rec = record(project, sid)
    if reviewed:
        rec["issues"] = [i for i in rec.get("issues") or [] if i.get("key") != "stale"]
        rec["stale"] = False
    ctx = ctx or Ctx(project)
    plan = resolve(project, rec, ctx)
    if blocking(rec):
        rec["status"] = _status(rec)
        return False, "; ".join(i["msg"] for i in rec["issues"] if i["level"] in ("missing", "conflict", "error",
                                                                                     "choice"))
    if rec.get("unresolved"):
        rec["issues"].append({"level": "warning", "key": "unresolved",
                              "msg": "Some phrases weren't understood (listed) - they were NOT applied."})
    segs = [sg for sg in plan["segments"] if sg["start"] is not None and sg["end"] is not None]
    if not segs:
        return False, "nothing to place"
    _, cur = _scene_by_id(project, sid)
    old_ids = {s.get("id") for s in project.get("scenes") or [] if s.get("segment_of") == sid} | {sid}
    moved = cur is None or abs(cur["start"] - segs[0]["start"]) > 1e-6 or abs(cur["end"] - segs[-1]["end"]) > 1e-6 \
        or len(segs) > 1 or len(old_ids) > 1
    if placed:                 # the caller already put this scene exactly at its times (timed plan)
        moved = False
    if moved or (plan["explicit_visual"] and not placed):
        locked_t = (cur is not None and _timing_locked(cur)) or rec["settings"].get("locked") or \
            "timing" in (rec["settings"].get("locked_fields") or [])
        if cur is not None and locked_t and moved and rec.get("applied"):
            rec["issues"].append({"level": "warning", "key": "timing",
                                  "msg": f"Scene timing is locked at {_fmt(cur['start'])}-{_fmt(cur['end'])}; the "
                                         f"instruction now resolves to {_fmt(segs[0]['start'])}-{_fmt(segs[-1]['end'])}."
                                         " The new voiceover changed this scene's timing. Review its locked times."})
            segs = [dict(segs[0], start=cur["start"], end=cur["end"])] if len(segs) == 1 else segs
            if len(segs) > 1:
                rec["status"] = _status(rec)
                return False, "locked timing"
        msg = place(project, sid, segs, ctx.fps, old_ids | own_ids_set(sid, segs))
        if msg:
            rec["issues"].append({"level": "conflict", "key": "timing", "msg": msg})
            rec["status"] = _status(rec)
            return False, msg
    if not placed:
        from .scenes import scene_text
        for x in project["scenes"]:          # narration excerpt follows the (possibly moved) times
            x["text"] = scene_text(ctx.words, x["start"], x["end"])
    else:
        _, cur = _scene_by_id(project, sid)
        if cur is not None and plan["explicit_visual"] and segs[0].get("media"):
            cur["media"] = segs[0]["media"]
    _write_fields(project, rec, plan)
    rec["applied"] = True
    rec["resolved"] = {"start": segs[0]["start"], "end": segs[-1]["end"], "explicit_timing": plan["explicit_timing"],
                       "segments": [{"media": sg["media"], "start": sg["start"], "end": sg["end"]} for sg in segs],
                       "words_sig": words_sig(ctx.words)}
    if not any(i.get("key") == "stale" for i in rec["issues"]):
        rec["stale"] = False
    rec["status"] = _status(rec)
    return True, "applied"


def _write_fields(project, rec, plan):
    sid = rec["scene_id"]
    s = rec["settings"]
    scenes = project["scenes"]
    mine = [x for x in scenes if x.get("id") == sid or x.get("segment_of") == sid]
    prov_src = f"instruction {sid}"
    for sc in mine:
        prov = dict(sc.get("provenance") or {})
        if plan["explicit_visual"]:
            prov["visual"] = prov_src
            sc["status"], sc["why"] = "assigned", "your editing instruction"
            sc.pop("accepted", None)
        if plan["explicit_timing"]:
            prov["timing"] = prov_src
        mo = plan["motion"]
        if mo is not None and sc.get("media") and not is_video(sc.get("media", "")):
            if mo["mode"] == "none":
                sc["motion"], sc["motion_amount"] = "none", None
            elif mo["mode"] == "explicit":
                sc["motion"] = mo["type"]
                sc["motion_amount"] = MOTION_STRENGTH.get(mo.get("strength", "medium"), 0.12)
                sc["motion_ease"] = mo.get("ease", "smooth")
                if mo.get("focus"):
                    sc["focus"] = list(mo["focus"])
            prov["motion"] = prov_src if mo["mode"] != "auto" else "automatic"
        fr = plan["framing"]
        if fr is not None:
            if fr.get("fit"):
                sc["fit"] = fr["fit"]
            if fr.get("focus"):
                sc["focus"] = list(fr["focus"])
            prov["framing"] = prov_src
        if s.get("clip_in") is not None:
            sc["clip_in"] = float(s["clip_in"])
        if s.get("clip_out") is not None:
            sc["clip_out"] = float(s["clip_out"])
        cp = plan["captions"]
        if cp is not None:
            sc["captions"] = "off" if cp["mode"] == "none" else "on"
            if cp.get("position"):
                sc["caption_position"] = cp["position"]
            prov["captions"] = prov_src
        elif "captions" in sc and prov.get("captions") == prov_src:
            sc.pop("captions", None)
            sc.pop("caption_position", None)
        sc["sfx_mode"] = plan["sfx_mode"]
        sc["b_roll_mode"] = plan["b_roll_mode"]
        if plan["effects_mode"] == "explicit":
            sc["effects"] = [e for e in plan["effects"]] if sc is mine[0] else []
            prov["effects"] = prov_src
        elif plan["effects_mode"] == "none":
            sc["effects"] = []
            sc.pop("adjust", None)
            prov["effects"] = prov_src
        sc["locked"] = bool(s.get("locked")) or bool(sc.get("locked") and prov.get("lock") != prov_src)
        if s.get("locked"):
            prov["lock"] = prov_src
        sc["locked_fields"] = sorted(set(s.get("locked_fields") or []))
        sc["provenance"] = prov
        sc["instruction"] = sid
    # transition belongs to the boundary after the LAST segment of this scene; segments in between cut
    for sc in mine[:-1]:
        sc["transition"] = "cut"
    if mine and plan["transition"] is not None:
        tr = plan["transition"]
        last = mine[-1]
        if tr.get("mode") == "none":
            last["transition"] = "cut"
            last.pop("transition_s", None)
        else:
            last["transition"] = tr.get("type", "cut")
            if tr.get("duration_seconds"):
                last["transition_s"] = float(tr["duration_seconds"])
            else:
                last.pop("transition_s", None)
        last.setdefault("provenance", {})["transition"] = prov_src
    # B-roll / SFX / overlays produced by this instruction: replaced, never duplicated
    project["broll"] = [x for x in project.get("broll") or [] if x.get("source") != sid]
    for x in plan["broll"]:
        project["broll"].append(dict(x, source=sid))
    project["broll"].sort(key=lambda x: x["start"])
    project["sfx_events"] = [x for x in project.get("sfx_events") or [] if x.get("source") != sid]
    for e in plan["sfx"]:
        project["sfx_events"].append(dict(e, source=sid))
    project["sfx_events"].sort(key=lambda e: e["t"])
    project["overlays"] = [x for x in project.get("overlays") or [] if x.get("source") != sid]
    for o in plan["overlays"]:
        project["overlays"].append(dict(o, source=sid))
    if rec.get("notes"):
        for sc in mine:
            sc["notes"] = rec["notes"]


def parse_record(rec):
    """Parse the record's instruction text into its settings (replacing what the text controls)."""
    s, interp, unresolved, notes = parse(rec.get("instruction_text") or "")
    keep = rec["settings"]
    # fields the text didn't mention keep their current (e.g. typed-in) values
    blank = blank_settings()
    for k, v in s.items():
        if v != blank.get(k):
            keep[k] = v
    rec["interpretation"] = [[c, m] for c, m in interp]
    rec["unresolved"] = unresolved
    if notes:
        rec["notes"] = "\n".join(([rec["notes"]] if rec.get("notes") else []) + notes)
    rec["choices"] = {}
    rec["applied"] = False
    return rec


def apply_all(project, log=print, only_valid=True):
    """Apply every instruction that resolves without blocking problems. -> (applied, skipped [(sid, why)])."""
    ctx = Ctx(project)
    done, skipped = [], []
    recs = records(project)
    order = sorted(recs, key=lambda sid: (0 if (_scene_by_id(project, sid)[1] or {}).get("locked") else 1, sid))
    for sid in order:
        rec = recs[sid]
        if not has_content(rec):
            continue
        ok, msg = apply(project, sid, log, ctx)
        (done if ok else skipped).append(sid if ok else (sid, msg))
    for s in project.get("scenes") or []:
        if not s.get("id"):
            s["id"] = _fresh_id(project)
    return done, skipped


def reapply_after_replan(project, log=print):
    """After the planner rebuilt the scenes: re-apply applied instructions (field values, B-roll, SFX)."""
    recs = records(project)
    if not any(r.get("applied") for r in recs.values()):
        return
    ctx = Ctx(project)
    for sid, rec in sorted(recs.items()):
        if rec.get("applied"):
            ok, msg = apply(project, sid, log, ctx)
            if not ok:
                rec["applied"] = False
                log(f"Instruction {sid} could not be re-applied: {msg}")


def explicit_cues(project):
    """Applied instructions with their own timing + visual, for the planner (so their pictures and times
    are reserved before automatic matching)."""
    out = []
    for sid, rec in records(project).items():
        r = rec.get("resolved") or {}
        if not rec.get("applied") or not r.get("explicit_timing"):
            continue
        for j, sg in enumerate(r.get("segments") or []):
            if sg.get("media"):
                out.append({"ref": sid, "media": [sg["media"]], "t": sg["start"], "t_end": sg["end"],
                            "explicit": True, "line": f"editing instruction {sid}",
                            "scene_id": sid if j == 0 else f"{sid}-{j + 1}"})
    return out


def refresh_resolved(project, log=print):
    """The narration (words) changed since instructions were applied: re-resolve their narration anchors
    BEFORE re-planning. Absolute times stay; locked times stay (with a warning); anything that moved or
    can't be found any more is marked for review. -> number of instructions needing review."""
    sig = words_sig(project.get("words") or [])
    ctx = Ctx(project)
    n = 0
    for sid, rec in records(project).items():
        r = rec.get("resolved") or {}
        if not rec.get("applied") or r.get("words_sig") == sig:
            continue
        old = (r.get("start"), r.get("end"))
        plan = resolve(project, rec, ctx)
        segs = [sg for sg in plan["segments"] if sg["start"] is not None and sg["end"] is not None]
        locked = "timing" in (rec["settings"].get("locked_fields") or []) or rec["settings"].get("locked")
        if blocking(rec) or not segs:
            rec["applied"] = False
            rec["stale"] = True
            rec["issues"].append({"level": "warning", "key": "stale",
                                  "msg": "The new voiceover changed this scene's timing and the instruction no "
                                         "longer resolves - review it."})
            n += 1
        else:
            new = (segs[0]["start"], segs[-1]["end"])
            moved = old[0] is None or abs(new[0] - old[0]) > 0.05 or abs(new[1] - (old[1] or 0)) > 0.05
            if moved and locked:
                rec["stale"] = True
                rec["issues"].append({"level": "warning", "key": "stale", "accept": True,
                                      "msg": f"The new voiceover changed this scene's timing (now "
                                             f"{_fmt(new[0])}-{_fmt(new[1])}). Review its locked times "
                                             f"({_fmt(old[0] or 0)}-{_fmt(old[1] or 0)})."})
                n += 1
                r["words_sig"] = sig
            else:
                if moved:
                    rec["stale"] = True
                    rec["issues"].append({"level": "warning", "key": "stale", "accept": True,
                                          "msg": f"Re-anchored to the new narration: {_fmt(old[0] or 0)}-"
                                                 f"{_fmt(old[1] or 0)} → {_fmt(new[0])}-{_fmt(new[1])}. Check it."})
                    n += 1
                rec["resolved"] = dict(r, start=new[0], end=new[1], words_sig=sig,
                                       segments=[{"media": sg["media"], "start": sg["start"], "end": sg["end"]}
                                                 for sg in segs])
        rec["status"] = _status(rec)
    if n:
        log(f"{n} editing instruction(s) need review because the narration changed.")
    return n


# ============================================================================ validation before export
def validate(project):
    """-> list of {'level': fatal|warning, 'scene', 'msg', 'key'}. Fatal problems block the export."""
    out = []
    dur = float(project.get("duration") or 0)
    scenes = project.get("scenes") or []
    for sid, rec in records(project).items():
        if not has_content(rec):
            continue
        for i in rec.get("issues") or []:
            if (i.get("accept") and i["msg"] in rec.get("accepted", [])) or i.get("key") == "unresolved":
                continue
            lvl = "fatal" if i["level"] in ("missing", "conflict", "error") and rec.get("applied") else "warning"
            if i["level"] == "missing" and _requires(rec, i.get("key")):
                lvl = "fatal"
            out.append({"level": lvl, "scene": sid, "msg": i["msg"], "key": i.get("key")})
        if rec.get("unresolved") and "unresolved" not in rec.get("accepted", []):
            out.append({"level": "warning", "scene": sid, "key": "unresolved",
                        "msg": f"{len(rec['unresolved'])} phrase(s) not understood and NOT applied: "
                               + " | ".join(rec["unresolved"][:3])})
        if has_content(rec) and not rec.get("applied") and "not_applied" not in rec.get("accepted", []):
            out.append({"level": "warning", "scene": sid, "key": "not_applied",
                        "msg": "This instruction hasn't been applied to the timeline."})
    for k, s in enumerate(scenes):
        if s["end"] - s["start"] <= 0:
            out.append({"level": "fatal", "scene": s.get("id"), "msg": "Scene has no length.", "key": "timing"})
        if k and abs(scenes[k - 1]["end"] - s["start"]) > 0.02:
            gap = s["start"] - scenes[k - 1]["end"]
            out.append({"level": "fatal", "scene": s.get("id"), "key": "timing",
                        "msg": (f"Uncovered gap of {gap:.2f} s before this scene." if gap > 0 else
                                f"Overlaps the previous scene by {-gap:.2f} s.")})
        for e in s.get("effects") or []:
            if e.get("type") not in EFFECTS or e["type"] == "text":
                out.append({"level": "fatal", "scene": s.get("id"), "key": "effects",
                            "msg": f"This effect is unavailable in the current renderer: {e.get('type')}."})
        m = s.get("media")
        if m and is_video(m) and (s.get("clip_in") or s.get("clip_out")):
            from .visuals import probe_duration
            d = probe_duration(m) or 0
            ci, co = float(s.get("clip_in") or 0), float(s.get("clip_out") or d)
            if d and (co <= ci or ci >= d):
                out.append({"level": "fatal", "scene": s.get("id"), "key": "clip",
                            "msg": f"Source range {ci:.2f}-{co:.2f} s is outside the clip ({d:.2f} s)."})
    from .timedplan import problems_for_export
    out += problems_for_export(project)
    if project["settings"].get("scene_mode") == "timed" and not (project.get("timed_plan") or "").strip():
        out.append({"level": "fatal", "scene": None, "key": "timed_plan",
                    "msg": "The timed edit plan is required: say which image runs from which second to which second "
                           "(Instructions tab → Timed edit plan)."})
    if scenes and dur and (scenes[0]["start"] > 0.02 or abs(scenes[-1]["end"] - dur) > 0.05):
        out.append({"level": "fatal", "scene": None, "key": "timing",
                    "msg": "The scenes don't cover the whole narration."})
    for b in project.get("broll") or []:
        if not os.path.isfile(b.get("media") or ""):
            out.append({"level": "fatal", "scene": b.get("source"), "key": "b_roll",
                        "msg": f"{os.path.basename(b.get('media') or '?')} is missing. Select a replacement."})
        elif b.get("mode") == "overlay" and not (0.05 <= float(b.get("size") or 0) <= 1.0):
            out.append({"level": "fatal", "scene": b.get("source"), "key": "b_roll",
                        "msg": f"Overlay size {b.get('size')} is impossible (use 5-100 %)."})
    for e in project.get("sfx_events") or []:
        if e.get("source") and not os.path.isfile(e.get("file") or ""):
            out.append({"level": "fatal", "scene": e.get("source"), "key": "sfx",
                        "msg": f"{os.path.basename(e.get('file') or '?')} is missing. Select a replacement."})
        if e.get("source") and dur and e.get("t", 0) > dur:
            out.append({"level": "fatal", "scene": e.get("source"), "key": "sfx", "msg": "Sound starts after the end."})
    return out


def _requires(rec, key):
    return bool(key) and (key == "primary_visual" or key.startswith(("b_roll", "sfx", "segment")))


# ============================================================================ import / export
CSV_COLUMNS = ["scene_id", "asset_filename", "script_excerpt", "phrase_occurrence", "timing_mode", "start_seconds",
               "end_seconds", "motion_mode", "fit_mode", "transition_type", "transition_seconds", "sfx_filename",
               "sfx_anchor_text", "sfx_offset_seconds", "sfx_gain_db", "locked"]
_FIT = {"fill": "crop", "crop": "crop", "fit": "fit_blur", "fit_blur": "fit_blur", "blur": "fit_blur",
        "color": "fit_color", "colour": "fit_color", "fit_color": "fit_color", "bars": "fit_bars",
        "fit_bars": "fit_bars", "black bars": "fit_bars"}
_TR = {"cut": "cut", "none": "cut", "fade": "dip_black", "dip_black": "dip_black", "dissolve": "crossfade",
       "crossfade": "crossfade", "slide": "slide", "whip": "whip", "zoom": "zoom"}


def export_json(project):
    data = {"schema_version": SCHEMA_VERSION, "project_revision": int(project.get("revision", 0) or 0),
            "project_guidelines": project["settings"].get("guidelines") or {}, "scenes": []}
    for sid in sorted(records(project)):
        rec = records(project)[sid]
        if not has_content(rec):
            continue
        s = copy.deepcopy(rec["settings"])
        d = {"scene_id": sid, "instruction_text": rec.get("instruction_text", ""), "notes": rec.get("notes", "")}
        d.update(s)
        _, sc = _scene_by_id(project, sid)
        if sc is not None:
            d["resolved"] = {"start_seconds": sc["start"], "end_seconds": sc["end"],
                             "asset_filename": os.path.basename(sc.get("media") or "")}
        data["scenes"].append(d)
    return data


def template_csv(project):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for s in project.get("scenes") or []:
        rec = records(project).get(s.get("id")) or {}
        st = (rec.get("settings") or {})
        tr = (st.get("transition_out") or {})
        w.writerow([s.get("id", ""), os.path.basename(s.get("media") or ""), (s.get("text") or "")[:120], "",
                    st.get("timing_mode", "follow_narration"), f"{s['start']:.3f}", f"{s['end']:.3f}",
                    (st.get("motion") or {}).get("mode", "inherit"),
                    (st.get("framing") or {}).get("fit", ""), tr.get("type", ""), tr.get("duration_seconds") or "",
                    "", "", "", "", "yes" if s.get("locked") else ""])
    return buf.getvalue()


def _num(row, col, errs, i, lo=None, hi=None):
    v = (row.get(col) or "").strip()
    if v == "":
        return None
    t = C.parse_time(v)
    if t is None:
        errs.append(f"row {i}, {col}: '{v}' is not a number")
        return None
    if (lo is not None and t < lo) or (hi is not None and t > hi):
        errs.append(f"row {i}, {col}: {t} is out of range")
        return None
    return t


def _rows_to_settings(rows, errs):
    """CSV rows -> {scene_id: settings}. Blank = inherit; 'none' = explicitly off."""
    out = {}
    for i, r in enumerate(rows, 2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        sid = r.get("scene_id", "")
        if not re.fullmatch(r"[A-Za-z0-9_\-.]{1,24}", sid or ""):
            errs.append(f"row {i}, scene_id: '{sid}' is missing or invalid")
            continue
        if sid in out:
            errs.append(f"row {i}, scene_id: {sid} appears twice")
            continue
        s = blank_settings()
        tm = r.get("timing_mode") or "follow_narration"
        if tm not in TIMING_MODES:
            errs.append(f"row {i}, timing_mode: '{tm}' (use {', '.join(TIMING_MODES)})")
            continue
        s["timing_mode"] = tm
        if r.get("asset_filename"):
            s["primary_visual"] = {"asset_filename": r["asset_filename"]}
        if r.get("script_excerpt"):
            occ = _num(r, "phrase_occurrence", errs, i, 1, 999)
            s["narration_anchor"] = {"text": r["script_excerpt"], "occurrence": int(occ) if occ else None}
            ex = r["script_excerpt"].split()
            if len(ex) > 3:
                s["end_anchor"] = {"text": " ".join(ex[-4:]), "occurrence": None}
        a, b = _num(r, "start_seconds", errs, i, 0), _num(r, "end_seconds", errs, i, 0)
        if a is not None and b is not None and b <= a:
            errs.append(f"row {i}, end_seconds: must be after start_seconds")
            continue
        s["start_seconds"], s["end_seconds"] = a, b
        if tm == "absolute" and a is None:
            errs.append(f"row {i}, start_seconds: needed for timing_mode absolute")
            continue
        mm = (r.get("motion_mode") or "").lower()
        if mm in ("", "inherit"):
            pass
        elif mm in ("none", "auto"):
            s["motion"] = {"mode": mm}
        elif mm in MOTION_TYPES or mm in ("zoom_in", "zoom_out"):
            s["motion"] = {"mode": "explicit", "type": mm, "strength": "medium", "ease": "smooth"}
        else:
            errs.append(f"row {i}, motion_mode: '{mm}' (use inherit, none, auto or {', '.join(MOTION_TYPES[:6])})")
        fm = (r.get("fit_mode") or "").lower()
        if fm:
            if fm in _FIT:
                s["framing"] = {"mode": "explicit", "fit": _FIT[fm]}
            elif fm != "inherit":
                errs.append(f"row {i}, fit_mode: '{fm}' (use fill, fit, colour or bars)")
        tt = (r.get("transition_type") or "").lower()
        if tt:
            if tt in _TR:
                ts = _num(r, "transition_seconds", errs, i, 0, 5)
                s["transition_out"] = {"mode": "explicit", "type": _TR[tt], "duration_seconds": ts}
            elif tt != "inherit":
                errs.append(f"row {i}, transition_type: '{tt}' (use cut, dissolve, fade, slide, whip, zoom)")
        sf = r.get("sfx_filename", "")
        if sf.lower() == "none":
            s["sfx_mode"] = "none"
        elif sf:
            g = _num(r, "sfx_gain_db", errs, i, -60, 12)
            off = r.get("sfx_offset_seconds", "")
            try:
                off = float(off) if off else 0.0
            except ValueError:
                errs.append(f"row {i}, sfx_offset_seconds: '{off}' is not a number")
                off = 0.0
            s["sfx_mode"] = "explicit"
            s["sfx"] = [{"asset_filename": sf, "anchor": {"text": r["sfx_anchor_text"], "occurrence_in_scene": None}
                         if r.get("sfx_anchor_text") else None, "start_seconds": None, "offset_seconds": off,
                         "gain_db": g if g is not None else -10.0, "fade_in": 0.0, "fade_out": 0.0, "trim_in": 0.0,
                         "duration": None, "pan": 0.0}]
        lk = (r.get("locked") or "").lower()
        if lk in ("1", "yes", "true", "y", "x", "locked"):
            s["locked"] = True
        elif lk and lk not in ("0", "no", "false", "n"):
            s["locked_fields"] = [f.strip() for f in re.split(r"[;|,]", lk) if f.strip() in FIELDS]
        out[sid] = {"settings": s, "instruction_text": "", "notes": ""}
    return out


def _json_to_settings(data, errs):
    if not isinstance(data, dict):
        errs.append("top level must be an object with schema_version and scenes")
        return {}, {}
    v = data.get("schema_version")
    if v != SCHEMA_VERSION:
        errs.append(f"schema_version {v!r} isn't supported (this version reads {SCHEMA_VERSION})")
        return {}, {}
    out = {}
    allowed = set(blank_settings())
    for i, sc in enumerate(data.get("scenes") or [], 1):
        if not isinstance(sc, dict):
            errs.append(f"scenes[{i}]: not an object")
            continue
        sid = str(sc.get("scene_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_\-.]{1,24}", sid):
            errs.append(f"scenes[{i}].scene_id: '{sid}' is missing or invalid")
            continue
        if sid in out:
            errs.append(f"scenes[{i}].scene_id: {sid} appears twice")
            continue
        s = blank_settings()
        for k, val in sc.items():
            if k in ("scene_id", "instruction_text", "notes", "resolved"):
                continue
            if k not in allowed:
                errs.append(f"scenes[{i}].{k}: unknown field (ignored)")
                continue
            s[k] = val
        bad = _check_settings(s)
        for b in bad:
            errs.append(f"scenes[{i}] ({sid}): {b}")
        if bad:
            continue
        out[sid] = {"settings": s, "instruction_text": str(sc.get("instruction_text") or ""),
                    "notes": str(sc.get("notes") or "")}
    return out, data.get("project_guidelines") or {}


def _check_settings(s):
    bad = []
    if s.get("timing_mode") not in TIMING_MODES:
        bad.append(f"timing_mode '{s.get('timing_mode')}'")
    for k in ("start_seconds", "end_seconds", "duration_seconds", "offset_seconds", "clip_in", "clip_out"):
        v = s.get(k)
        if v is not None and not isinstance(v, (int, float)):
            bad.append(f"{k} must be a number")
    for k in ("b_roll_mode", "sfx_mode", "effects_mode"):
        if s.get(k) not in MODES:
            bad.append(f"{k} '{s.get(k)}' (use {', '.join(MODES)})")
    for k in ("motion", "framing", "transition_out", "captions"):
        v = s.get(k)
        if not isinstance(v, dict) or v.get("mode", "inherit") not in MODES:
            bad.append(f"{k} needs a 'mode' of {', '.join(MODES)}")
    mo = s.get("motion") or {}
    if mo.get("mode") == "explicit" and mo.get("type") not in MOTION_TYPES:
        bad.append(f"motion.type '{mo.get('type')}'")
    tr = s.get("transition_out") or {}
    if tr.get("mode") == "explicit" and tr.get("type") not in TRANSITIONS:
        bad.append(f"transition_out.type '{tr.get('type')}'")
    for k, lst in (("b_roll", s.get("b_roll")), ("sfx", s.get("sfx")), ("effects", s.get("effects"))):
        if not isinstance(lst, list):
            bad.append(f"{k} must be a list")
            continue
        for j, x in enumerate(lst):
            if not isinstance(x, dict):
                bad.append(f"{k}[{j}] must be an object")
            elif k == "effects" and x.get("type") not in EFFECTS:
                bad.append(f"effects[{j}].type '{x.get('type')}' is unavailable in the current renderer")
            elif k != "effects" and not x.get("asset_filename"):
                bad.append(f"{k}[{j}].asset_filename is required")
            elif k == "sfx" and x.get("gain_db") is not None and not isinstance(x.get("gain_db"), (int, float)):
                bad.append(f"sfx[{j}].gain_db must be a number (dB)")
    if s.get("effects_mode") == "explicit" and not s.get("effects"):
        bad.append("effects_mode 'explicit' needs at least one effect (use 'none' to switch effects off)")
    if not isinstance(s.get("locked_fields"), list) or any(f not in FIELDS for f in s.get("locked_fields") or []):
        bad.append(f"locked_fields must list some of: {', '.join(FIELDS)}")
    return bad


def load_import(project, path):
    """-> (incoming {sid: {...}}, errors, preview lines). Nothing is changed yet."""
    errs = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = f.read()
    except OSError as e:
        return {}, [f"can't read the file: {e}"], []
    guide = {}
    if path.lower().endswith(".json"):
        try:
            data = json.loads(raw)
        except ValueError as e:
            return {}, [f"not valid JSON: {e}"], []
        incoming, guide = _json_to_settings(data, errs)
    else:
        rows = list(csv.DictReader(io.StringIO(raw)))
        if rows and "scene_id" not in {(k or "").strip().lower() for k in rows[0]}:
            return {}, ["the CSV has no scene_id column. Expected: " + ",".join(CSV_COLUMNS)], []
        incoming = _rows_to_settings(rows, errs)
    preview = []
    for sid, inc in incoming.items():
        cur = records(project).get(sid)
        _, sc = _scene_by_id(project, sid)
        where = "existing scene" if sc is not None else "NEW scene (needs an anchor or times)"
        changes = []
        old = (cur or {}).get("settings") or blank_settings()
        for k, v in inc["settings"].items():
            if v != old.get(k) and v != blank_settings().get(k):
                changes.append(f"{k} → {json.dumps(v, ensure_ascii=False)[:70]}")
        preview.append(f"{sid} ({where}): " + ("; ".join(changes) if changes else "no changes"))
    if guide:
        preview.append("Project guidelines: " + ", ".join(f"{k}={v}" for k, v in guide.items()))
    return {"scenes": incoming, "guidelines": guide}, errs, preview


def commit_import(project, incoming):
    """Write imported records (replacing those scenes' settings entirely). Apply separately."""
    for sid, inc in incoming.get("scenes", {}).items():
        rec = new_record(sid)
        rec.update(instruction_text=inc.get("instruction_text", ""), notes=inc.get("notes", ""))
        rec["settings"] = inc["settings"]
        if rec["instruction_text"]:
            _, interp, unresolved, _ = parse(rec["instruction_text"])
            rec["interpretation"] = [[c, m] for c, m in interp]
            rec["unresolved"] = unresolved
        records(project)[sid] = rec
    if incoming.get("guidelines"):
        project["settings"].setdefault("guidelines", {}).update(incoming["guidelines"])
    return list(incoming.get("scenes", {}))


# ============================================================================ locks from the UI
def lock_field(project, sid, field, on=True):
    """Lock one field of a scene. Locking keeps the scene's CURRENT value as an explicit, locked setting
    (and ties the scene to its narration, so regeneration keeps it)."""
    k, sc = _scene_by_id(project, sid)
    if sc is None:
        return
    rec = record(project, sid)
    s = rec["settings"]
    lf = set(s.get("locked_fields") or [])
    (lf.add if on else lf.discard)(field)
    s["locked_fields"] = sorted(lf)
    if on:
        words = project.get("words") or []
        if not s.get("narration_anchor") and s.get("timing_mode") == "follow_narration" and words:
            i, j = _word_at(words, sc["start"] + 0.01), _word_at(words, sc["end"] - 0.01)
            first = " ".join(w["w"] for w in words[i:i + 4])
            last = " ".join(w["w"] for w in words[max(i, j - 3):j + 1])
            occ = find_occurrences(words, first)
            s["narration_anchor"] = {"text": first, "occurrence": next((n + 1 for n, o in enumerate(occ) if o[0] == i),
                                                                     None)}
            occ2 = find_occurrences(words, last, i)
            s["end_anchor"] = {"text": last, "occurrence": 1 if occ2 else None}
        if field == "primary_visual" and sc.get("media") and not s.get("primary_visual"):
            s["primary_visual"] = {"asset_filename": os.path.basename(sc["media"])}
            mp = rec.setdefault("choices", {})
            pool = Ctx(project).media
            hits = [p for p in pool if os.path.basename(p).lower() == os.path.basename(sc["media"]).lower()]
            if len(hits) > 1:
                mp["primary_visual"] = hits.index(sc["media"]) if sc["media"] in hits else 0
        if field == "timing" and s.get("timing_mode") == "follow_narration" and not s.get("narration_anchor"):
            s["timing_mode"], s["start_seconds"], s["end_seconds"] = "absolute", sc["start"], sc["end"]
    sc_l = project["scenes"][k]
    sc_l["locked_fields"] = sorted(lf)
    rec["applied"] = True
    rec["resolved"] = {"start": sc["start"], "end": sc["end"], "explicit_timing": bool(s.get("narration_anchor") or
                                                                                         s.get("start_seconds")
                                                                                         is not None),
                       "segments": [{"media": sc.get("media"), "start": sc["start"], "end": sc["end"]}],
                       "words_sig": words_sig(project.get("words") or [])}
    rec["status"] = _status(rec)


def scene_status(project, sc):
    """Status shown in the instruction table for a timeline scene."""
    sid = sc.get("id")
    rec = records(project).get(sid) or records(project).get(sc.get("segment_of") or "")
    m = sc.get("media")
    if m and not os.path.isfile(m):
        return "missing"
    if rec is None:
        return "empty"
    return rec.get("status") or _status(rec)


def flag_framing_review(project, aspect):
    """Video shape changed: every scene with MANUAL framing (focal point, border trim, zoom/position,
    explicit framing instruction) is marked for review. -> number flagged."""
    n = 0
    for sc in project.get("scenes") or []:
        manual = sc.get("focus") or sc.get("trim") or abs(float(sc.get("zoom", 1) or 1) - 1) > 1e-3 or \
            abs(float(sc.get("x", 0) or 0)) > 1e-3 or abs(float(sc.get("y", 0) or 0)) > 1e-3 or sc.get("keyframes")
        rec = records(project).get(sc.get("id"))
        if rec is not None and (rec["settings"].get("framing") or {}).get("mode") == "explicit":
            manual = True
        if not manual or not sc.get("id"):
            continue
        rec = record(project, sc["id"])
        rec["aspect_review"] = aspect
        msg = (f"The video shape changed to {aspect}: check this scene's manual framing (focal point / crop / "
               "zoom).")
        rec["accepted"] = [a for a in rec.get("accepted", []) if a != msg]
        rec["issues"] = [i for i in rec.get("issues") or [] if i.get("key") != "aspect"] + [
            {"level": "warning", "key": "aspect", "accept": True, "msg": msg}]
        rec["status"] = _status(rec)
        n += 1
    return n
