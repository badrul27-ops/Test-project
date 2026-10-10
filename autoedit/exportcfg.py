"""Export settings: ONE aspect ratio per export (optional batch), resolution presets, quality presets
mapped to each real encoder, and safe migration of older projects."""

ASPECTS = {"16:9": "16:9 Landscape", "9:16": "9:16 Portrait", "1:1": "1:1 Square"}
RESOLUTIONS = {  # preset -> {aspect: (W, H)}
    "Draft": {"16:9": (854, 480), "9:16": (480, 854), "1:1": (480, 480)},
    "HD": {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)},
    "Full HD": {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)},
    "1440": {"16:9": (2560, 1440), "9:16": (1440, 2560), "1:1": (1440, 1440)},
    "4K-class": {"16:9": (3840, 2160), "9:16": (2160, 3840), "1:1": (2160, 2160)},
}
FPS_CHOICES = (24, 25, 30, 60)
ENC_QUALITY = ("Draft", "Standard", "High")
ENC_QUALITY_HELP = {"Draft": "fastest, larger files / visible compression - for checking",
                    "Standard": "good quality, quick - recommended",
                    "High": "best compression for the same quality, slower"}
ENCODERS = ["Auto (use GPU if available)", "CPU (x264)", "NVIDIA GPU (NVENC)", "Intel GPU (QuickSync)",
            "AMD GPU (AMF)"]
LEGACY_QUALITY = {"1080p": "Full HD", "720p": "HD", "720p draft (fast)": "HD"}

NEW_DEFAULTS = {"aspect": "16:9", "resolution": "Full HD", "fps": 30, "enc_quality": "Standard",
                "batch_export": False, "batch_formats": [], "fit": "crop", "bg_color": "#000000",
                "short_clip": "freeze", "encoder": "Auto (use GPU if available)"}


def dims(aspect, resolution):
    return RESOLUTIONS.get(resolution, RESOLUTIONS["Full HD"]).get(aspect, (1920, 1080))


def export_formats(st):
    """The aspect ratios ONE export action renders: the chosen one, plus batch extras only if enabled."""
    a = st.get("aspect") or ((st.get("formats") or ["16:9"])[0])
    out = [a]
    if st.get("batch_export"):
        out += [f for f in st.get("batch_formats") or [] if f in ASPECTS and f not in out]
    return out


def summary(st):
    fm = export_formats(st)
    W, H = dims(fm[0], st.get("resolution", "Full HD"))
    s = (f"{len(fm)} video{'s' if len(fm) > 1 else ''} · {' + '.join(fm)} · {W} × {H}"
         if len(fm) == 1 else f"{len(fm)} videos · {' + '.join(fm)} · {st.get('resolution', 'Full HD')}")
    return f"{s} · {int(st.get('fps', 30))} fps · {st.get('enc_quality', 'Standard')}"


def migrate(saved, settings):
    """`saved` = settings exactly as stored in an older project file (may lack new keys);
    `settings` = defaults merged with saved. Keeps every explicit old choice."""
    if "aspect" not in saved:
        old = saved.get("formats")
        if isinstance(old, list) and old:
            settings["aspect"] = old[0] if old[0] in ASPECTS else "16:9"
            extra = [f for f in old[1:] if f in ASPECTS]
            settings["batch_formats"] = extra
            settings["batch_export"] = bool(extra)       # the project used to export several formats
    if "resolution" not in saved and saved.get("quality"):
        settings["resolution"] = LEGACY_QUALITY.get(saved["quality"], "Full HD")
    if "enc_quality" not in saved and saved:
        settings["enc_quality"] = "Standard"
    if "fit" in saved:
        settings["fit"] = saved["fit"]                    # 'blur' (old default) keeps its old look
    if "short_clip" not in saved and saved:
        settings["short_clip"] = "auto"                   # old projects: previous clip-fitting behaviour
    if "scene_mode" not in saved and saved:
        settings["scene_mode"] = "auto"                   # old projects keep their pause-based planning
    if settings.get("fps") not in FPS_CHOICES:
        settings["fps"] = 30
    return settings


# ---------------------------------------------------------------------------- encoder arguments
def x264_args(q):
    """Measured on the fixed benchmark (tools/bench_render.py, 2-core CPU, vs a crf-10 reference):
    Standard (veryfast/18) matched the old default (fast/20) in SSIM/PSNR at about half the time;
    Draft is visibly more compressed; High is slower and best."""
    preset, crf = {"Draft": ("ultrafast", 26), "Standard": ("veryfast", 18), "High": ("medium", 18)}[q]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high"]


def hw_args(kind, q):
    """Each GPU encoder gets ITS OWN quality options (CPU x264 options aren't reused)."""
    i = ENC_QUALITY.index(q)
    if kind == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", ("p1", "p4", "p6")[i], "-rc", "vbr",
                "-cq", str((28, 21, 19)[i]), "-b:v", "0", "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if kind == "qsv":
        return ["-c:v", "h264_qsv", "-preset", ("veryfast", "medium", "slow")[i],
                "-global_quality", str((28, 22, 19)[i]), "-pix_fmt", "nv12"]
    if kind == "amf":
        qp = (28, 21, 19)[i]
        return ["-c:v", "h264_amf", "-quality", ("speed", "balanced", "quality")[i], "-rc", "cqp",
                "-qp_i", str(qp), "-qp_p", str(qp + 2), "-pix_fmt", "nv12"]
    raise ValueError(kind)


HW_NAMES = {"nvenc": "NVIDIA NVENC", "qsv": "Intel Quick Sync", "amf": "AMD AMF"}
