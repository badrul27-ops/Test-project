"""Project guidelines: full-video defaults that every scene inherits unless its own instruction says
otherwise. Style presets only FILL these fields (shown before applying); they promise nothing about
accuracy, quality or monetisation."""
from .presets import PRESETS

STYLES = {
    "custom": ("Custom", {}),
    "documentary": ("Documentary", {"preset": "Documentary", "motion_level": "Subtle", "pacing": "balanced",
                                    "transition_default": "crossfade", "transition_frequency": "topic_changes",
                                    "fit": "crop", "sfx_mode": "explicit_only", "caption_style": "Documentary"}),
    "narrative": ("Narrative story", {"preset": "Long Story History", "motion_level": "Subtle", "pacing": "relaxed",
                                      "transition_default": "crossfade", "transition_frequency": "topic_changes",
                                      "fit": "crop", "sfx_mode": "explicit_only",
                                      "caption_style": "Modern (word highlight)"}),
    "cars": ("Car explainer", {"preset": "Energetic (Cars)", "motion_level": "Dynamic", "pacing": "brisk",
                               "transition_default": "auto", "fit": "crop", "sfx_mode": "auto",
                               "caption_style": "Boxed word (TikTok)"}),
    "psychology": ("Psychology explainer", {"preset": "Dark Psychology", "motion_level": "Subtle",
                                            "pacing": "balanced", "transition_default": "cut", "fit": "crop",
                                            "sfx_mode": "explicit_only", "caption_style": "Modern (word highlight)"}),
    "animation": ("Animation-based narration", {"preset": "2D Animation", "motion_level": "Subtle",
                                                "pacing": "balanced", "transition_default": "cut",
                                                "fit": "fit_blur", "sfx_mode": "explicit_only",
                                                "caption_style": "Modern (word highlight)"}),
}
LABELS = {"preset": "Editing preset", "motion_level": "Motion", "pacing": "Pacing", "transition_default": "Transition",
          "transition_frequency": "Transitions at", "fit": "Default framing", "sfx_mode": "Sound effects",
          "caption_style": "Caption style"}


def style_changes(style, settings):
    """[(label, old, new)] that applying the style would change (nothing changes until you apply)."""
    vals = dict(STYLES.get(style, ("", {}))[1])
    if vals.get("preset") and vals["preset"] not in PRESETS:
        vals.pop("preset")
    return [(LABELS.get(k, k), settings.get(k), v) for k, v in vals.items() if settings.get(k) != v]


def apply_style(style, settings):
    vals = dict(STYLES.get(style, ("", {}))[1])
    if vals.get("preset") and vals["preset"] not in PRESETS:
        vals.pop("preset")
    settings.update(vals)
    settings["style"] = style
    return settings
