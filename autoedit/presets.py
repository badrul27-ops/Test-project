"""Editing style presets.

Each preset controls scene pacing, camera motion, transitions, SFX and the
overall look. Edit the numbers here to tune a channel's style.

motions / transitions are weights (relative probabilities).
"""

MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
TRANSITIONS = ["cut", "crossfade", "dip_black", "slide", "whip", "zoom"]

PRESETS = {
    "Documentary": {
        "min_scene": 3.0,        # seconds
        "max_scene": 8.0,
        "section_pause": 1.0,    # a pause this long (s) = new section -> section transition
        "zoom_amount": 0.12,     # 0.12 = zoom 12% over the scene
        "motions": {"zoom_in": 40, "zoom_out": 30, "pan_left": 10, "pan_right": 10,
                    "pan_up": 3, "pan_down": 2, "static": 5},
        "transitions": {"crossfade": 55, "cut": 40, "dip_black": 5},
        "section_transition": "dip_black",
        "transition_duration": 0.6,
        "vignette": 0.15,
        "saturation": 1.0,
    },
    "Dark Psychology": {
        "min_scene": 3.5,
        "max_scene": 9.0,
        "section_pause": 1.0,
        "zoom_amount": 0.10,
        "motions": {"zoom_in": 55, "zoom_out": 25, "pan_left": 8, "pan_right": 8, "static": 4},
        "transitions": {"crossfade": 60, "cut": 25, "dip_black": 15},
        "section_transition": "dip_black",
        "transition_duration": 0.8,
        "vignette": 0.45,
        "saturation": 0.8,
    },
    "Energetic (Cars)": {
        "min_scene": 1.8,
        "max_scene": 4.5,
        "section_pause": 0.9,
        "zoom_amount": 0.20,
        "motions": {"zoom_in": 50, "zoom_out": 15, "pan_left": 15, "pan_right": 15, "static": 5},
        "transitions": {"cut": 50, "whip": 20, "slide": 15, "zoom": 15},
        "section_transition": "zoom",
        "transition_duration": 0.35,
        "vignette": 0.10,
        "saturation": 1.1,
    },
    "Shorts / Reels (fast)": {
        "min_scene": 1.5,
        "max_scene": 3.5,
        "section_pause": 0.8,
        "zoom_amount": 0.18,
        "motions": {"zoom_in": 50, "zoom_out": 20, "pan_left": 10, "pan_right": 10,
                    "pan_up": 5, "pan_down": 5},
        "transitions": {"cut": 55, "whip": 20, "zoom": 15, "crossfade": 10},
        "section_transition": "whip",
        "transition_duration": 0.3,
        "vignette": 0.10,
        "saturation": 1.05,
    },
}

# ---- presets used by the video categories (see profiles.py)
PRESETS.update({
    "Vox Style": {             # purposeful cuts, motion used selectively
        "min_scene": 2.2, "max_scene": 5.5, "section_pause": 0.9, "zoom_amount": 0.08,
        "motions": {"static": 30, "zoom_in": 25, "pan_left": 10, "pan_right": 10, "zoom_out": 10, "subtle": 15},
        "transitions": {"cut": 60, "slide": 15, "crossfade": 15, "zoom": 10},
        "section_transition": "slide", "transition_duration": 0.4, "vignette": 0.0, "saturation": 1.0,
    },
    "Long Story History": {    # measured pacing, restrained moves, long crossfades
        "min_scene": 4.5, "max_scene": 11.0, "section_pause": 1.1, "zoom_amount": 0.09,
        "motions": {"zoom_in": 30, "zoom_out": 25, "pan_left": 15, "pan_right": 15, "subtle": 15},
        "transitions": {"crossfade": 65, "cut": 25, "dip_black": 10},
        "section_transition": "dip_black", "transition_duration": 0.9, "vignette": 0.25, "saturation": 0.9,
    },
    "Horror": {                # slow creeping push-ins, dark look, controlled transitions
        "min_scene": 3.5, "max_scene": 9.0, "section_pause": 1.0, "zoom_amount": 0.12,
        "motions": {"zoom_in": 55, "subtle": 20, "zoom_out": 10, "pan_left": 8, "pan_right": 7},
        "transitions": {"crossfade": 45, "dip_black": 30, "cut": 25},
        "section_transition": "dip_black", "transition_duration": 1.0, "vignette": 0.55, "saturation": 0.65,
    },
    "2D Animation": {          # illustrated scenes: lively but readable
        "min_scene": 2.5, "max_scene": 6.0, "section_pause": 0.9, "zoom_amount": 0.10,
        "motions": {"zoom_in": 25, "pan_left": 20, "pan_right": 20, "zoom_out": 15, "pan_up": 5, "pan_down": 5,
                    "subtle": 10},
        "transitions": {"cut": 45, "slide": 25, "crossfade": 20, "zoom": 10},
        "section_transition": "slide", "transition_duration": 0.4, "vignette": 0.0, "saturation": 1.05,
    },
    "Stickman": {              # simple, readable: mostly still with gentle pushes, clean cuts
        "min_scene": 2.0, "max_scene": 5.0, "section_pause": 0.9, "zoom_amount": 0.06,
        "motions": {"static": 45, "zoom_in": 25, "subtle": 15, "zoom_out": 10, "pan_right": 5},
        "transitions": {"cut": 65, "slide": 20, "crossfade": 15},
        "section_transition": "slide", "transition_duration": 0.35, "vignette": 0.0, "saturation": 1.0,
    },
})

# motion level -> multiplier on the preset's zoom/pan amount (None = still images)
MOTION_LEVELS = {"None": 0.0, "Subtle": 0.55, "Medium": 1.0, "Dynamic": 1.6}

CAPTION_STYLES = ["Modern (word highlight)", "Boxed word (TikTok)", "Documentary", "Minimal", "None"]

SFX_LEVELS = {
    # keywords: use narration keyword rules; spacing: min seconds between effects;
    # offset_db: effect loudness relative to the voice (negative = quieter than the voice);
    # per_min: at most this many effects per minute; volume_db: legacy fixed level (unused now)
    "None": None,
    "Minimal": {"keywords": False, "spacing": 10.0, "offset_db": -10.0, "per_min": 2, "repeat_gap": 20.0, "volume_db": -16},
    "Normal": {"keywords": True, "spacing": 5.0, "offset_db": -7.0, "per_min": 5, "repeat_gap": 10.0, "volume_db": -13},
    "Cinematic": {"keywords": True, "spacing": 2.5, "offset_db": -4.0, "per_min": 9, "repeat_gap": 5.0, "volume_db": -10},
}

QUALITY = {
    "1080p": {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "x264": "fast", "crf": 20},
    "720p": {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720), "x264": "veryfast", "crf": 24},
}


ENCODERS = ["Auto (use GPU if available)", "CPU (x264)", "Intel GPU (QuickSync)", "AMD GPU (AMF)"]


def transition_duration(kind, preset):
    if kind == "cut":
        return 0.0
    d = float(preset.get("transition_duration", 0.5))
    if kind == "whip":
        d = min(d, 0.35)
    if kind == "dip_black":
        d = max(d, 0.6)
    return d
