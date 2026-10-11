"""Project file (JSON): inputs, settings, transcript and the scene list."""
import json
import os

from . import scenes as scn
from .presets import PRESETS
from .transcribe import apply_script, transcribe
from .util import list_media

DEFAULT_SETTINGS = {
    "preset": "Documentary",
    "caption_style": "Modern (word highlight)",
    "captions": True,
    "formats": ["16:9"],          # legacy list (older versions); exports use aspect/batch below
    "quality": "1080p",
    "fps": 30,
    "scene_mode": "timed",         # v7.2 default: YOUR timed edit plan (required). v7: "smart"         # v7 default: script topics + meaning. Older: "auto" (pauses),
                                   # "match_media" (one per file), "timestamps" (0-03.png), "script_cues"
    "whisper_model": "small",
    "language": "",
    "sfx_level": "Normal",
    "music_db": -20,
    "seed": 7,
    "encoder": "Auto (use GPU if available)",
    "atmosphere": "None",
    "atmo_strength": 1.0,
    "overlay": "",               # optional overlay video (dust, light leaks...) - screen blended
    "overlay_opacity": 0.5,
    "fit": "crop",               # framing.py: crop | fit_blur | fit_color | fit_bars | blur (legacy)
    "snap_timestamps": True,     # timestamp mode: move cuts to the nearest pause (within 0.6s)
    "builtin_sfx": True,         # use the built-in SFX library for categories your folder lacks
    "loudness": -14.0,           # final mix loudness in LUFS (None = off)
    # captions
    "caption_font": "Auto (by style)",
    "caption_color": "#FFFFFF",
    "highlight_color": "#FFD600",
    "caption_position": "Bottom",
    "caption_size": 100,         # percent
    "caption_anim": "Pop-in",    # None / Pop-in / Word bounce
    # variety + hook
    "auto_vary": True,           # different motion/transition mix for every video
    "hook_fast_cuts": True,      # faster cuts in the first ~10 s
    "hook_title": "",            # big title over the first seconds (empty = none)
    "shuffle": 0,                # bump to get a different style mix for the same video
    "title_font": "",            # text cards font ("" = same as captions)
    # audio tracks
    "voice_db": 0.0,             # voiceover level adjustment
    "music_fade_in": 1.5,        # seconds
    "music_fade_out": 3.0,
    "music_duck": True,          # lower the music under the voice
    "autosave": True,            # keep a recovery copy while you work
    # v6.1
    "category": "",              # video category / editing style (profiles.py); "" = not chosen
    "motion_level": "Medium",    # None / Subtle / Medium / Dynamic (automatic image motion)
    "sfx_db": 0.0,               # sound-effects volume adjustment in the export mix
    # v6.6
    "repeat_media": False,       # auto mode: allow an image to appear more than once
    # v7 export + framing (see exportcfg.py / framing.py)
    "aspect": "16:9", "resolution": "Full HD", "enc_quality": "Standard",
    "batch_export": False, "batch_formats": [],
    "bg_color": "#000000", "short_clip": "freeze", "overwrite": False,
    "project_instructions": "",
    # v7.1 project guidelines (editing-instructions brief)
    "style": "custom", "pacing": "balanced", "shot_min": None, "shot_max": None,
    "transition_default": "auto", "transition_seconds": None, "transition_frequency": "every_cut",
    "sfx_mode": "auto", "duck_ratio": 6.0, "duck_release": 350.0, "caption_margin": None,
    "continuity": "", "restrict_no_shake": False, "restrict_no_flash": False,
    "plan_roots": {},           # editing-plan folder name → real folder (Map folder…), for this project only
}


def new_project():
    return {"version": 1, "name": "video", "voiceover": "", "script": "", "media_dir": "",
            "music": "", "sfx_dir": "", "output_dir": "", "settings": dict(DEFAULT_SETTINGS),
            "words": [], "duration": 0.0, "scenes": [], "overlays": [], "chapters": [],
            "sfx_events": [], "sfx_planned": False, "broll": [], "extra_media": [], "excluded_media": [], "stock_assets": {}, "directions": "",
            # v7: exact script text, imported scene map, asset descriptions (by stable asset ID)
            "script_text": "", "scene_map": [], "asset_meta": {}, "next_scene_id": 1,
            "script_check": [], "alignment": {},
            # v7.1: scene-by-scene editing instructions (intent; see instructions.py)
            "edit_instructions": {},
            # v7.2: timed edit plan - which image from which second to which second (timedplan.py)
            "timed_plan": ""}


def save(project, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(project, f, indent=1, ensure_ascii=False)


def load(path):
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    base = new_project()
    base.update(p)
    s = dict(DEFAULT_SETTINGS)
    saved = p.get("settings", {}) or {}
    s.update(saved)
    from .exportcfg import migrate
    migrate(saved, s)
    if s.get("quality") == "720p draft (fast)":      # renamed in v6 (Draft preview is its own button now)
        s["quality"] = "720p"
    base["settings"] = s
    for sc in base["scenes"] + base.get("broll", []):
        for k, v in (("zoom", 1.0), ("x", 0.0), ("y", 0.0), ("rot", 0.0)):
            sc.setdefault(k, v)
    return base


def project_media(project):
    """All media available to the project: the folder (+ sub-folders) plus files added one by
    one, minus files you removed from the project (they stay on disk)."""
    excl = {os.path.normcase(os.path.abspath(p)) for p in project.get("excluded_media", [])}
    out = [m for m in list_media(project.get("media_dir"))
           if os.path.normcase(os.path.abspath(m)) not in excl]
    seen = {os.path.normcase(os.path.abspath(m)) for m in out}
    for m in project.get("extra_media", []):
        k = os.path.normcase(os.path.abspath(m))
        if k not in seen and k not in excl:
            out.append(m)
            seen.add(k)
    return out


def _cache_path(project):
    vo = project["voiceover"]
    st = project["settings"]
    return f"{vo}.{st.get('whisper_model', 'small')}.words.json"


def estimate_words(script, duration):
    """Fallback when speech recognition isn't available: spread the script's words over the audio in
    proportion to their length. NOT real alignment - every caller marks the result as estimated."""
    toks = script.split()
    if not toks or duration <= 0:
        return []
    weights = [len(t) + 2 + (6 if t.endswith((".", "!", "?")) else 0) for t in toks]
    lead = min(0.2, duration / 10)              # small silence at both ends, never more than the clip allows
    k = (duration - 2 * lead) / sum(weights)
    out, t = [], lead
    for tok, wgt in zip(toks, weights):
        d = wgt * k
        out.append({"w": tok, "s": round(t, 3), "e": round(t + d * 0.85, 3)})
        t += d
    return out


def analyze(project, progress=None, log=print, force=False):
    """Transcribe (cached next to the voiceover), compare with the exact script, build the scene plan."""
    from .planner import check_summary, script_check, script_text
    vo = project["voiceover"]
    if not vo or not os.path.isfile(vo):
        raise FileNotFoundError("Choose a voiceover file first.")
    st = project["settings"]
    cache = _cache_path(project)
    script = script_text(project)
    estimated = False
    if os.path.isfile(cache) and not force:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        words, duration = data["words"], data["duration"]
        log("Using saved transcript (delete the .words.json file to re-transcribe).")
    else:
        try:
            words, duration = transcribe(vo, st.get("whisper_model", "small"),
                                         st.get("language") or None, progress, log)
        except Exception as e:
            if not script:
                raise
            from .visuals import probe_duration
            duration = float(probe_duration(vo) or 0)
            words = estimate_words(script, duration)
            if not words:
                raise
            estimated = True
            log(f"Speech recognition failed ({e}). Using an ESTIMATED timing from your script - cuts may be "
                "off by several seconds. Check scenes in Review, or fix the transcription and analyze again.")
        if not estimated:
            try:
                with open(cache, "w", encoding="utf-8") as f:
                    json.dump({"words": words, "duration": duration}, f)
            except OSError:
                pass
    if script and not estimated:
        issues = script_check(script, words)
        project["script_check"] = issues
        log("Script vs recording: " + check_summary(issues))
        words = apply_script(words, script)
        log("Caption text corrected from your script.")
    else:
        project["script_check"] = []
    project["alignment"] = {"status": "estimated" if estimated else ("script+audio" if script else "audio only"),
                            "note": ("timings estimated from the script length - not aligned to the voice"
                                     if estimated else "")}
    project["words"] = words
    project["duration"] = duration
    replan(project, log)
    return project


def replan(project, log=print):
    from .editorial import video_seed, vary_preset
    st = project["settings"]
    preset = PRESETS.get(st["preset"], PRESETS["Documentary"])
    media = project_media(project)
    if not media:
        from .util import folder_report
        log("No images/videos found - scenes will be blank. " + folder_report(project.get("media_dir")))
    seed = int(st.get("seed", 7))
    if st.get("auto_vary", True):
        seed = video_seed(project) + int(st.get("shuffle", 0)) * 7919
        preset = vary_preset(preset, seed)
    locked = [dict(x) for x in project.get("scenes") or [] if x.get("locked")]
    previous = [dict(x) for x in project.get("scenes") or []]
    from . import instructions as ins
    ins.refresh_resolved(project, log)            # narration changed? re-anchor instructions first
    if st.get("scene_mode") == "timed":
        from . import timedplan
        if (project.get("timed_plan") or "").strip():
            try:
                timedplan.build(project, log)
                return project
            except ValueError as e:
                # strict plan: never replace it with pictures in file order. The timeline stays as it was
                # (empty if there was none) and every problem is listed; export stays blocked.
                log(str(e))
                project["plan_report"] = (["Editing plan NOT applied - fix the problems below. The timeline was "
                                           "left unchanged (no automatic picture order is used)."]
                                          + str(e).splitlines()[1:])
                for line in project["plan_report"][:1]:
                    log(line)
                return project
        else:
            log("No timed edit plan yet - showing an automatic DRAFT so you can preview. Export stays blocked until "
                "the plan covers the whole narration (Instructions tab → Timed edit plan → Fill template).")
        from .planner import plan
        project["scenes"], rep = plan(project, log)
        project["plan_report"] = ["DRAFT - write the timed edit plan (required before export)."] + rep
        for s_ in project["scenes"]:
            s_["status"], s_["why"] = "fallback", "automatic draft - not from your plan"
        return project
    if st.get("scene_mode", "smart") == "smart":
        from .planner import plan
        project["scenes"], rep = plan(project, log)
        project["plan_report"] = rep
        for line in rep:
            log(line)
    else:
        _replan_classic(project, log, preset, media, seed)
        if locked:
            from .planner import keep_locked
            project["scenes"] = keep_locked(project["scenes"], locked, project.get("duration") or 0)
            log(f"{len(locked)} locked scene(s) kept as they were.")
    keep_ids(previous, project["scenes"], project.get("edit_instructions") or {})
    ins.reapply_after_replan(project, log)        # your instructions win over automatic choices
    return project


def keep_ids(previous, scenes, recs):
    """Stable scene IDs across re-planning: a new scene that mostly covers the same narration as an old
    one keeps the old ID (so its editing instruction still finds it)."""
    taken = {s.get("id") for s in scenes if s.get("id") in recs and (recs[s["id"]].get("resolved") or {}).get(
        "explicit_timing")}
    for s in scenes:
        if s.get("id") in taken:
            continue
        L = s["end"] - s["start"]
        best, bid = 0.0, None
        for o in previous:
            ov = min(s["end"], o["end"]) - max(s["start"], o["start"])
            if ov > best and o.get("id") and o["id"] not in taken:
                best, bid = ov, o["id"]
        if bid and best >= 0.5 * L and best >= 0.5 * (next(o for o in previous if o.get("id") == bid)["end"] -
                                                     next(o for o in previous if o.get("id") == bid)["start"]):
            if any(x is not s and x.get("id") == bid for x in scenes):
                other = next(x for x in scenes if x is not s and x.get("id") == bid)
                other["id"] = s.get("id")          # swap: the ids stay unique
            s["id"] = bid
            taken.add(bid)


def _replan_classic(project, log, preset, media, seed):
    from .editorial import apply_hook_cuts, jitter_motion_amounts
    st = project["settings"]
    if st.get("scene_mode") == "script_cues":
        from . import cues as cue_mod
        sp = project.get("script")
        if (project.get("directions") or "").strip():
            cl, note = cue_mod.parse_directions(project["directions"], media)
            if note:
                log("Directions: " + note)
        elif sp and os.path.isfile(sp):
            cl = cue_mod.load_cues(sp)
        else:
            raise ValueError("Scene mode 'Follow my directions' needs directions (paste your scene list in the "
                             "Directions box) or a script file with markers like [1], [2] (Media tab -> Script).")
        if not cl:
            raise ValueError("No directions found. Paste one paragraph per image (blank line between), or put "
                             "[1], [2], ... (or [03.png]) before the part of the narration each image covers.")
        project["scenes"], rep = cue_mod.plan_from_cues(project, cl, log)
        for line in rep:
            log("Cues: " + line)
        lens = [s["end"] - s["start"] for s in project["scenes"]]
        log(f"{len(lens)} scenes from image markers, {min(lens):.1f}-{max(lens):.1f}s each.")
        return project
    project["scenes"] = scn.plan(project["words"], project["duration"], preset, media,
                                 mode=st.get("scene_mode", "auto"), seed=seed, log=log,
                                 snap=st.get("snap_timestamps", True),
                                 allow_repeat=bool(st.get("repeat_media", False)))
    if st.get("auto_vary", True):
        jitter_motion_amounts(project["scenes"], preset, seed)
    scn.apply_motion_level(project["scenes"], preset, st.get("motion_level", "Medium"), seed)
    if st.get("hook_fast_cuts", True):
        n0 = len(project["scenes"])
        apply_hook_cuts(project)
        if len(project["scenes"]) > n0:
            log(f"Hook: opening split into {len(project['scenes']) - n0} extra quick shots.")
        if st.get("motion_level") == "None":        # hook shots stay still too
            scn.apply_motion_level(project["scenes"], preset, "None", seed)
    from .planner import apply_guidelines
    apply_guidelines(project, project["scenes"])
    lens = [s["end"] - s["start"] for s in project["scenes"]]
    if lens:
        log(f"{len(lens)} scenes, {min(lens):.1f}-{max(lens):.1f}s each "
            f"(avg {sum(lens) / len(lens):.1f}s), {len(media)} media files.")
    return project
