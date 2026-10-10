"""Video categories (editing-style profiles).

A category only sets STARTING DEFAULTS - preset (pacing, motion mix, transitions), motion level,
captions, look and sound. Every setting stays editable afterwards, and the chosen category is
saved in the project file. AutoEditor never draws missing artwork: the notes below say which
assets a category expects you to import.
"""
import os

from .util import is_video

CATEGORIES = {
    "Documentary": {
        "about": "Restrained camera motion, clean captions, pacing for factual storytelling.",
        "defaults": {"preset": "Documentary", "motion_level": "Subtle", "caption_style": "Documentary",
                     "atmosphere": "None", "atmo_strength": 1.0, "sfx_level": "Minimal", "music_db": -22,
                     "hook_fast_cuts": False},
        "needs": [],
    },
    "Vox Style": {
        "about": "Clear visual explanations: maps, labels, callouts and graphics with purposeful cuts. "
                 "Motion is used selectively.",
        "defaults": {"preset": "Vox Style", "motion_level": "Subtle", "caption_style": "Modern (word highlight)",
                     "atmosphere": "Vintage Document", "atmo_strength": 0.5, "sfx_level": "Normal",
                     "music_db": -22, "hook_fast_cuts": True},
        "needs": ["maps, charts and labelled graphics as images or clips (AutoEditor doesn't create maps)",
                  "labels and callouts: add them as text cards in the Text tab (place/date labels, lower thirds)"],
    },
    "Long Story History": {
        "about": "Measured pacing, archival-style look, restrained pans and zooms, longer transitions.",
        "defaults": {"preset": "Long Story History", "motion_level": "Subtle", "caption_style": "Documentary",
                     "atmosphere": "Old Film", "atmo_strength": 0.7, "sfx_level": "Minimal", "music_db": -23,
                     "hook_fast_cuts": False},
        "needs": ["archival photos, documents or footage (your own or properly licensed)"],
    },
    "Horror": {
        "about": "Darker treatment, suspenseful pacing, slow creeping push-ins, controlled transitions, "
                 "tension sound effects.",
        "defaults": {"preset": "Horror", "motion_level": "Medium", "caption_style": "Minimal",
                     "atmosphere": "Dark Noir", "atmo_strength": 1.0, "sfx_level": "Cinematic", "music_db": -20,
                     "hook_fast_cuts": False},
        "needs": ["an ambience or drone music track if you want a continuous bed (add it as Music) - the "
                  "built-in library has short tension, heartbeat, rumble and riser effects only"],
    },
    "2D Animation": {
        "about": "For imported 2D illustrated scenes and assets: lively but readable moves, slides and cuts.",
        "defaults": {"preset": "2D Animation", "motion_level": "Medium", "caption_style": "Boxed word (TikTok)",
                     "atmosphere": "None", "atmo_strength": 1.0, "sfx_level": "Normal", "music_db": -20,
                     "hook_fast_cuts": True, "fit": "fit_blur"},
        "needs": ["your 2D scenes as images or clips - AutoEditor moves and times them but doesn't draw or "
                  "animate characters; use keyframes (Properties → Animation) for precise movement"],
        "illustrated": True,
    },
    "Stickman": {
        "about": "For imported stickman scenes: simple, readable movement and clean cuts.",
        "defaults": {"preset": "Stickman", "motion_level": "Subtle", "caption_style": "Modern (word highlight)",
                     "atmosphere": "None", "atmo_strength": 1.0, "sfx_level": "Normal", "music_db": -21,
                     "hook_fast_cuts": True, "fit": "fit_blur"},
        "needs": ["your stickman scenes as images or clips - AutoEditor doesn't draw stickmen"],
        "illustrated": True,
    },
}
DEFAULT_CATEGORY = "Documentary"


def apply_category(settings, name):
    """Set the category and its starting defaults. Returns the list of changed keys."""
    cat = CATEGORIES.get(name)
    if not cat:
        return []
    changed = [k for k, v in cat["defaults"].items() if settings.get(k) != v]
    settings.update(cat["defaults"])
    settings["category"] = name
    return changed


def category_notes(project, media=None):
    """[(level, text)] level: 'ok' | 'info' | 'warn' - what this category expects vs what's imported."""
    from .project import project_media
    name = project.get("settings", {}).get("category") or DEFAULT_CATEGORY
    cat = CATEGORIES.get(name, {})
    media = project_media(project) if media is None else media
    out = []
    if cat.get("illustrated") and not media:
        out.append(("warn", f"{name} needs your own illustrated scenes - none imported yet."))
    elif not media and project.get("voiceover"):
        out.append(("warn", "No images or clips imported yet."))
    for n in cat.get("needs", []):
        out.append(("info", "Expects " + n + "."))
    if name == "Vox Style" and project.get("scenes") and not project.get("overlays"):
        out.append(("info", "Tip: no text cards yet - Vox-style videos usually label places, dates and figures."))
    if name == "Horror" and not project.get("music"):
        out.append(("info", "No music/ambience track yet (optional)."))
    if media:
        nv = sum(1 for m in media if is_video(m))
        ni = len(media) - nv
        out.append(("ok", f"{ni} image{'s' if ni != 1 else ''} and {nv} clip{'s' if nv != 1 else ''} imported."))
    return out


def notes_text(notes):
    icon = {"ok": "✓", "info": "•", "warn": "⚠"}
    return "\n".join(f"{icon[k]} {t}" for k, t in notes)


__all__ = ["CATEGORIES", "DEFAULT_CATEGORY", "apply_category", "category_notes", "notes_text", "os"]
