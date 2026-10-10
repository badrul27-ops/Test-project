"""AutoEditor - automatic voiceover-driven video editor."""
import os as _os

# Quiet OpenCV's own console messages ("Could not find decoder for codec_id=..."): AutoEditor
# detects clips OpenCV can't decode and uses a preview copy made with the full FFmpeg instead.
_os.environ.setdefault("OPENCV_LOG_LEVEL", "FATAL")
_os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

__version__ = "7.3.0"
