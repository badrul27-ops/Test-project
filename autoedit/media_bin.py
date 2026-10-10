"""Media bin + background preview loading.

Thumbnails and waveforms are produced on a thread pool (never on the UI thread) and
cached on disk (cache/thumbs) so reopening a project is instant. The UI asks for a
thumbnail, gets None the first time, and repaints when `updated` fires.
"""
import hashlib
import os

import numpy as np
from PySide6.QtCore import QMimeData, QObject, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget)

from .util import APP_DIR, IMAGE_EXT, VIDEO_EXT, is_video

CACHE_DIR = os.path.join(APP_DIR, "cache", "thumbs")


def _cache_file(path, w, h):
    try:
        st = os.stat(path)
        key = f"{path}|{st.st_mtime}|{st.st_size}|{w}x{h}"
    except OSError:
        return None
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".jpg")


def make_thumb(path, w, h):
    """Small RGB QImage of an image or a clip's first frame (runs on a worker thread).
    Uses the disk cache and JPEG draft decoding, so big photos decode fast."""
    cf = _cache_file(path, w, h)
    if cf and os.path.isfile(cf):
        img = QImage(cf)
        if not img.isNull():
            return img
    from PIL import Image, ImageOps
    if is_video(path):
        import cv2
        from .visuals import readable_video
        cap = cv2.VideoCapture(readable_video(path))      # proxy if this OpenCV can't decode the codec
        ok, fr = cap.read()
        cap.release()
        if not ok:
            raise ValueError("can't read video (damaged or unsupported codec)")
        im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
    else:
        im = Image.open(path)
        im.draft("RGB", (w * 2, h * 2))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("P", "LA", "RGBA", "PA"):          # transparency -> composite on black
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
            bg.alpha_composite(im)
            im = bg
        im = im.convert("RGB")
    im.thumbnail((w, h))
    if cf:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            im.save(cf, "JPEG", quality=85)
        except OSError:
            pass
    return QImage(im.tobytes(), im.width, im.height, im.width * 3, QImage.Format_RGB888).copy()


class _Signals(QObject):
    thumb_done = Signal(str, int, int, QImage)
    thumb_failed = Signal(str, str)
    peaks_done = Signal(str, object)


def _emit(sig, *args):
    try:
        sig.emit(*args)
    except RuntimeError:        # app is shutting down - nobody is listening any more
        pass


class _ThumbJob(QRunnable):
    def __init__(self, sig, path, w, h):
        super().__init__()
        self.sig, self.path, self.w, self.h = sig, path, w, h

    def run(self):
        try:
            img = make_thumb(self.path, self.w, self.h)
        except Exception as e:
            _emit(self.sig.thumb_failed, self.path, str(e)[:160])
            return
        _emit(self.sig.thumb_done, self.path, self.w, self.h, img)


class _PeaksJob(QRunnable):
    def __init__(self, sig, path):
        super().__init__()
        self.sig, self.path = sig, path

    def run(self):
        from .gui_widgets import audio_peaks
        _emit(self.sig.peaks_done, self.path, audio_peaks(self.path))


class PreviewLoader(QObject):
    """Shared background loader. thumb()/peaks() return what's ready (or None) and schedule
    the rest; `updated` fires when something new is ready."""
    updated = Signal()
    progress = Signal(int)          # jobs still pending

    def __init__(self):
        super().__init__()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 2) - 1)))
        self.sig = _Signals()
        self.sig.thumb_done.connect(self._thumb_done)
        self.sig.thumb_failed.connect(self._thumb_failed)
        self.sig.peaks_done.connect(self._peaks_done)
        self.pix = {}
        self.errors = {}
        self.pending = set()
        self.peaks_cache = {}
        self.peaks_pending = set()

    def thumb(self, path, w=112, h=63):
        if not path:
            return None
        key = (path, w, h)
        if key in self.pix:
            return self.pix[key]
        if path in self.errors:
            return None
        if key not in self.pending and os.path.isfile(path):
            self.pending.add(key)
            self.pool.start(_ThumbJob(self.sig, path, w, h))
            self.progress.emit(len(self.pending))
        return None

    def _thumb_done(self, path, w, h, img):
        self.pending.discard((path, w, h))
        self.pix[(path, w, h)] = QPixmap.fromImage(img)
        self.progress.emit(len(self.pending))
        self.updated.emit()

    def _thumb_failed(self, path, msg):
        self.pending = {k for k in self.pending if k[0] != path}
        self.errors[path] = msg
        self.progress.emit(len(self.pending))
        self.updated.emit()

    def peaks(self, path):
        if not path or not os.path.isfile(path):
            return None
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return None
        if key in self.peaks_cache:
            return self.peaks_cache[key]
        if key not in self.peaks_pending:
            self.peaks_pending.add(key)
            self.pool.start(_PeaksJob(self.sig, path))
        return None

    def _peaks_done(self, path, pk):
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return
        self.peaks_pending.discard(key)
        self.peaks_cache[key] = pk
        self.updated.emit()

    def cancel_pending(self):
        """Drop queued (not yet started) jobs - e.g. when a project is closed."""
        self.pool.clear()
        self.pending.clear()
        self.progress.emit(0)


_LOADER = None


def loader():
    global _LOADER
    if _LOADER is None:
        _LOADER = PreviewLoader()
    return _LOADER


# ============================================================================ media bin widget

class _BinList(QListWidget):
    """Grid of media; items can be dragged onto the timeline."""

    def startDrag(self, actions):
        it = self.currentItem()
        if not it:
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(it.data(Qt.UserRole))])
        md.setData("application/x-autoedit-media", it.data(Qt.UserRole).encode("utf-8"))
        d = QDrag(self)
        d.setMimeData(md)
        if not it.icon().isNull():
            d.setPixmap(it.icon().pixmap(96, 54))
        d.exec(Qt.CopyAction)


class MediaBin(QWidget):
    """Thumbnails + names of the project's media. Double-click = use for the selected scene."""
    use_for_scene = Signal(str)
    add_to_v2 = Signal(str)
    remove_from_project = Signal(list)
    add_files = Signal()
    add_folder = Signal()

    TW, TH = 104, 58

    def __init__(self, get_media, get_used):
        super().__init__()
        self.get_media, self.get_used = get_media, get_used
        self.filter = QComboBox()
        self.filter.addItems(["All", "Images", "Clips", "Unused"])
        self.filter.currentIndexChanged.connect(self.refresh)
        b_files = QPushButton("+ Files")
        b_files.setToolTip("Add images or clips (several at once)")
        b_files.clicked.connect(self.add_files.emit)
        b_folder = QPushButton("+ Folder")
        b_folder.setToolTip("Use a folder of images/clips (sub-folders included)")
        b_folder.clicked.connect(self.add_folder.emit)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(b_files)
        top.addWidget(b_folder)
        top.addStretch(1)
        top.addWidget(self.filter)
        self.list = _BinList()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(self.TW, self.TH))
        self.list.setGridSize(QSize(self.TW + 14, self.TH + 34))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragEnabled(True)
        self.list.setMinimumHeight(170)
        self.list.itemDoubleClicked.connect(lambda it: self.use_for_scene.emit(it.data(Qt.UserRole)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.status = QLabel("")
        self.status.setObjectName("Help")
        self.status.setWordWrap(True)
        row = QHBoxLayout()
        b_use = QPushButton("Use for scene")
        b_use.setToolTip("Put the selected file on the selected scene (V1)")
        b_use.clicked.connect(lambda: self._emit_sel(self.use_for_scene))
        b_v2 = QPushButton("Add to V2")
        b_v2.setToolTip("Place the selected file on the V2 (B-roll) track at the playhead")
        b_v2.clicked.connect(lambda: self._emit_sel(self.add_to_v2))
        b_rm = QPushButton("Remove")
        b_rm.setToolTip("Remove from this project (the file stays on your PC)")
        b_rm.clicked.connect(lambda: self.remove_from_project.emit(self.selected()))
        for b in (b_use, b_v2, b_rm):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)
        lay.addWidget(self.status)
        self._items = {}
        loader().updated.connect(self._update_icons)
        loader().progress.connect(self._progress)

    def selected(self):
        return [it.data(Qt.UserRole) for it in self.list.selectedItems()]

    def _placeholder(self):
        if getattr(self, "_ph", None) is None:
            pm = QPixmap(self.TW, self.TH)
            pm.fill(QColor(40, 44, 54))
            p = QPainter(pm)
            p.setPen(QColor(120, 126, 142))
            p.drawText(pm.rect(), Qt.AlignCenter, "loading…")
            p.end()
            self._ph = QIcon(pm)
        return self._ph

    def _emit_sel(self, sig):
        s = self.selected()
        if s:
            sig.emit(s[0])

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        path = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Use for selected scene", lambda: self.use_for_scene.emit(path))
        m.addAction("Add to V2 track at playhead", lambda: self.add_to_v2.emit(path))
        m.addSeparator()
        m.addAction("Remove from project (keeps the file)", lambda: self.remove_from_project.emit(self.selected()))
        m.addAction("Show in folder", lambda: _reveal(path))
        m.exec(self.list.mapToGlobal(pos))

    def refresh(self):
        media = self.get_media()
        used = self.get_used()
        f = self.filter.currentText()
        if f == "Images":
            media = [m for m in media if not is_video(m)]
        elif f == "Clips":
            media = [m for m in media if is_video(m)]
        elif f == "Unused":
            media = [m for m in media if os.path.normcase(os.path.abspath(m)) not in used]
        cur = set(self.selected())
        self.list.clear()
        self._items = {}
        for m in media:
            it = QListWidgetItem(os.path.basename(m))
            it.setData(Qt.UserRole, m)
            it.setIcon(self._placeholder())      # full-size from the start, so the grid lays out right
            n = sum(1 for u in [os.path.normcase(os.path.abspath(m))] if u in used)
            it.setToolTip(m + ("\n(used in the timeline)" if n else "\n(not used yet)"))
            if not n:
                it.setForeground(QColor("#B4BACB"))
            self.list.addItem(it)
            self._items[m] = it
            if m in cur:
                it.setSelected(True)
        self._update_icons()
        n_img = sum(1 for m in media if not is_video(m))
        self._summary = f"{n_img} images, {len(media) - n_img} clips" if media else \
            "No media yet - use + Files / + Folder, or drag files onto the window."
        self._progress(len(loader().pending))

    def _update_icons(self):
        L = loader()
        for m, it in self._items.items():
            if m in L.errors:
                it.setIcon(_error_icon(self.TW, self.TH))
                it.setToolTip(f"{m}\n⚠ {L.errors[m]}")
                continue
            pm = L.thumb(m, self.TW, self.TH)
            if pm is not None and not it.data(Qt.UserRole + 1):
                box = QPixmap(self.TW, self.TH)
                box.fill(QColor(12, 13, 17))
                p = QPainter(box)
                p.drawPixmap((self.TW - pm.width()) // 2, (self.TH - pm.height()) // 2, pm)
                p.end()
                it.setIcon(QIcon(box))
                it.setData(Qt.UserRole + 1, True)

    def _progress(self, n):
        errs = [m for m in self._items if m in loader().errors]
        txt = getattr(self, "_summary", "")
        if n:
            txt += f" · generating previews… {n} left"
        if errs:
            txt += f" · ⚠ {len(errs)} file(s) can't be read (damaged or unsupported) - hover for details"
        self.status.setText(txt)


def _error_icon(w, h):
    pm = QPixmap(w, h)
    pm.fill(QColor(70, 30, 34))
    p = QPainter(pm)
    p.setPen(QColor(242, 109, 109))
    p.drawText(pm.rect(), Qt.AlignCenter, "can't read")
    p.end()
    return QIcon(pm)


def _reveal(path):
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


MEDIA_FILTER = "Images and clips (" + " ".join("*" + e for e in sorted(IMAGE_EXT | VIDEO_EXT)) + ")"

__all__ = ["loader", "MediaBin", "MEDIA_FILTER", "make_thumb", "np"]
