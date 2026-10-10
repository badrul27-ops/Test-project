import os
import re
import shutil
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IMAGE_EXT = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

_FFMPEG = None


def ffmpeg_exe():
    """ffmpeg on PATH, else the copy bundled with the imageio-ffmpeg package."""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            exe = None
    if not exe:
        raise RuntimeError("FFmpeg not found. Run setup.bat again, or install FFmpeg "
                           "(winget install Gyan.FFmpeg).")
    _FFMPEG = exe
    return exe


def popen_kwargs():
    """Hide console windows for ffmpeg on Windows."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def run_ffmpeg(args, log_path=None):
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"] + args
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs())
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", "replace")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(" ".join(cmd) + "\n" + err + "\n")
        raise RuntimeError("FFmpeg failed:\n" + err[-1500:])
    return res


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def clean_path(p):
    """Accept pasted paths with quotes / trailing slashes, and file paths (-> their folder)."""
    p = (p or "").strip().strip('"').strip("'").strip()
    if p and os.path.isfile(p):
        p = os.path.dirname(p)
    return os.path.normpath(p) if p else ""


def list_media(folder):
    """Images/clips in the folder AND its sub-folders, in natural file-name order."""
    folder = clean_path(folder)
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted([d for d in dirs if not d.startswith((".", "_autoedit"))
                          and "edit_package" not in d], key=natural_key)
        for f in files:
            if os.path.splitext(f)[1].lower() in IMAGE_EXT | VIDEO_EXT and not f.startswith("."):
                out.append(os.path.join(root, f))
    out.sort(key=lambda p: natural_key(os.path.relpath(p, folder).replace("\\", "/")))
    return out


def folder_report(folder):
    """Human explanation when no media is found."""
    folder = clean_path(folder)
    if not folder:
        return "No folder chosen."
    if not os.path.isdir(folder):
        return f"Folder not found: {folder}"
    exts = {}
    n = 0
    for root, _, files in os.walk(folder):
        for f in files:
            n += 1
            e = os.path.splitext(f)[1].lower() or "(no extension)"
            exts[e] = exts.get(e, 0) + 1
    if n == 0:
        return "This folder is empty."
    top = ", ".join(f"{k} x{v}" for k, v in sorted(exts.items(), key=lambda x: -x[1])[:5])
    return (f"No supported images/clips found. Files here: {top}. "
            f"Supported: {' '.join(sorted(IMAGE_EXT | VIDEO_EXT))}")


def is_video(path):
    return os.path.splitext(path or "")[1].lower() in VIDEO_EXT


FONT_DIRS = [
    os.path.join(APP_DIR, "fonts"),
    r"C:\Windows\Fonts",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype",
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
]


def find_font(candidates):
    for name in candidates:
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    # last resort: any ttf in the app fonts folder
    d = os.path.join(APP_DIR, "fonts")
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.lower().endswith((".ttf", ".otf")):
                return os.path.join(d, f)
    return None


def fmt_time(t):
    t = max(0.0, float(t))
    m = int(t // 60)
    return f"{m:02d}:{t - m * 60:05.2f}"
