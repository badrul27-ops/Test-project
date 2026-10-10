"""Custom widgets: preview player and timeline scrollbar.
(The multi-track timeline lives in timeline.py.)"""
import os
import subprocess

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
                               QSlider, QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from .util import ffmpeg_exe, fmt_time, popen_kwargs

def thumb(path, w=112, h=63):
    """Small QPixmap of an image or the first frame of a clip. Made in the background
    (see media_bin.loader); returns None until it's ready - callers repaint on
    loader().updated."""
    from .media_bin import loader
    return loader().thumb(path, w, h)


def audio_peaks(path, per_sec=100):
    """Waveform peaks (0..1) for the timeline, via FFmpeg."""
    sr = 8000
    try:
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", path, "-ac", "1",
               "-ar", str(sr), "-f", "s16le", "-"]
        raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=120, **popen_kwargs()).stdout
        a = np.abs(np.frombuffer(raw, np.int16).astype(np.float32))
        step = sr // per_sec
        n = len(a) // step
        if n == 0:
            return np.zeros(1, np.float32)
        pk = a[:n * step].reshape(n, step).max(axis=1)
        ref = float(np.percentile(pk, 99.5)) or float(pk.max())   # one click shouldn't flatten the rest
        return np.minimum(1.0, pk / (ref + 1e-6))
    except Exception:
        return np.zeros(1, np.float32)


def frame_to_pixmap(fr):
    h, w = fr.shape[:2]
    qi = QImage(fr.data, w, h, w * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qi)


# ====================================================================== player

class PreviewCanvas(QWidget):
    """The preview picture. It always uses the whole preview area and draws the frame:

      Fit   - largest size that shows the whole frame (letterbox bars only where the shapes differ)
      Fill  - covers the whole area (may crop the edges in the PREVIEW only)
      100%  - one preview pixel per screen pixel
      zoom  - x1.25 steps on top of Fit; drag to pan, Ctrl+wheel to zoom, double-click to reset

    Only the on-screen picture changes - never the project or the exported video."""
    MODES = ("Fit", "Fill", "100%")
    view_changed = Signal()

    def __init__(self, text=""):
        super().__init__()
        self.setObjectName("PreviewScreen")
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._img = None            # QImage of the current frame
        self._text = text
        self.mode = "Fit"
        self.zoom = 1.0             # multiplier on top of the mode
        self.pan = [0.0, 0.0]
        self._drag = None

    # -- QLabel-like API used by the player
    def setText(self, t):
        self._text, self._img = t, None
        self.update()

    def set_image(self, qimg):
        self._img = qimg
        self.update()

    def pixmap(self):
        return QPixmap.fromImage(self._img) if self._img is not None else None

    def set_mode(self, mode):
        self.mode = mode if mode in self.MODES else "Fit"
        self.zoom, self.pan = 1.0, [0.0, 0.0]
        self.update()
        self.view_changed.emit()

    def zoom_by(self, f):
        self.zoom = min(8.0, max(0.25, self.zoom * f))
        if abs(self.zoom - 1.0) < 0.02:
            self.zoom = 1.0
        self._clamp_pan()
        self.update()
        self.view_changed.emit()

    def reset_view(self):
        self.set_mode("Fit")

    def scale(self):
        if self._img is None:
            return 1.0
        iw, ih = self._img.width(), self._img.height()
        W, H = max(1, self.width() - 2), max(1, self.height() - 2)
        base = {"Fit": min(W / iw, H / ih), "Fill": max(W / iw, H / ih), "100%": 1.0}[self.mode]
        return base * self.zoom

    def frame_rect(self):
        """Where the frame is drawn (may extend past the canvas when zoomed / Fill)."""
        from PySide6.QtCore import QRectF
        if self._img is None:
            return QRectF()
        s = self.scale()
        w, h = self._img.width() * s, self._img.height() * s
        return QRectF((self.width() - w) / 2 + self.pan[0], (self.height() - h) / 2 + self.pan[1], w, h)

    def _clamp_pan(self):
        r = self.frame_rect()
        mx, my = max(0.0, (r.width() - self.width()) / 2), max(0.0, (r.height() - self.height()) / 2)
        self.pan = [min(mx, max(-mx, self.pan[0])), min(my, max(-my, self.pan[1]))]

    def zoom_percent(self):
        """Displayed size relative to the preview frame's own pixels (for the readout)."""
        return int(round(self.scale() * 100))

    def paintEvent(self, e):
        from PySide6.QtGui import QColor, QPainter, QPen
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0B0C10"))
        if self._img is None:
            p.setPen(QColor("#B4BACB"))
            p.drawText(self.rect().adjusted(12, 12, -12, -12), Qt.AlignCenter | Qt.TextWordWrap, self._text)
        else:
            p.setRenderHint(QPainter.SmoothPixmapTransform, self.scale() < 2.0)
            p.drawImage(self.frame_rect(), self._img)
        p.setPen(QPen(QColor("#2A2F3A"), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    def resizeEvent(self, e):
        self._clamp_pan()
        super().resizeEvent(e)

    def mousePressEvent(self, e):
        r = self.frame_rect()
        if e.button() == Qt.LeftButton and (r.width() > self.width() or r.height() > self.height()):
            self._drag = (e.position().x(), e.position().y(), list(self.pan))
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag:
            x0, y0, p0 = self._drag
            self.pan = [p0[0] + e.position().x() - x0, p0[1] + e.position().y() - y0]
            self._clamp_pan()
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, e):
        self.reset_view()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.25 if e.angleDelta().y() > 0 else 0.8)
            e.accept()
        else:
            e.ignore()


def _db(v):
    return 10 ** (float(v or 0) / 20.0)


class PlayerWidget(QWidget):
    """Preview screen + transport. Voice, music and (planned) sound effects play through
    QtMultimedia. The speaker button sets the PREVIEW volume only - it never changes the
    exported mix (track levels are in the Audio tab / track properties)."""
    time_changed = Signal(float)
    SFX_POOL = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QSettings
        self.empty_text = ("No preview yet\n\nAdd a voiceover and images in Media,\n"
                           "then choose Analyze voiceover.")
        self.screen = PreviewCanvas(self.empty_text)
        self.viewport = self.screen
        self.btn = QPushButton("▶ Play")
        self.btn.setFixedWidth(92)
        self.btn.setToolTip("Play / pause (Space)")
        self.btn.clicked.connect(self.toggle)
        # ---- volume (preview only)
        s = QSettings()
        self.volume = min(100, max(0, int(s.value("preview/volume", 80) or 80)))
        self.muted = str(s.value("preview/muted", "false")).lower() == "true"
        self.vol_btn = QToolButton()
        self.vol_btn.setObjectName("VolumeButton")
        self.vol_btn.setPopupMode(QToolButton.InstantPopup)
        self.vol_btn.setToolTip("Preview volume and mute (Ctrl+Up / Ctrl+Down, Ctrl+Shift+M). "
                                "Doesn't change the exported video.")
        menu = QMenu(self.vol_btn)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.addWidget(QLabel("Preview volume"))
        row = QHBoxLayout()
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setMinimumWidth(170)
        self.vol_slider.setValue(self.volume)
        self.vol_slider.valueChanged.connect(self.set_volume)
        self.vol_label = QLabel("")
        self.vol_label.setFixedWidth(38)
        row.addWidget(self.vol_slider, 1)
        row.addWidget(self.vol_label)
        bl.addLayout(row)
        self.mute_cb = QCheckBox("Mute preview")
        self.mute_cb.setChecked(self.muted)
        self.mute_cb.toggled.connect(self.set_muted)
        bl.addWidget(self.mute_cb)
        hint = QLabel("Preview only. Export levels: Audio tab\n(voice, music and SFX volume).")
        hint.setObjectName("Help")
        bl.addWidget(hint)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(box)
        menu.addAction(wa)
        self.vol_btn.setMenu(menu)
        self.time = QLabel("00:00.00 / 00:00.00")
        self.time.setMinimumWidth(self.time.fontMetrics().horizontalAdvance("00:00:00.00 / 00:00:00.00") // 1 - 20)
        self.aspect = QComboBox()
        self.aspect.addItems(["16:9", "9:16", "1:1"])
        self.aspect.setToolTip("Which export shape to preview (16:9, 9:16 or 1:1). Tick the shapes to export in "
                               "the Export tab - this menu doesn't change them.")
        self.caps = QCheckBox("Captions")
        self.caps.setChecked(True)
        self.fx = QCheckBox("Effects")
        self.fx.setChecked(True)
        self.fit = QComboBox()
        self.fit.addItems(list(PreviewCanvas.MODES))
        self.fit.setToolTip("Fit = whole frame · Fill = cover the preview (crops edges in the preview only) · "
                            "100% = one preview pixel per screen pixel. Never changes the export.")
        self.fit.activated.connect(lambda _i: self.screen.set_mode(self.fit.currentText()))
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("−")
        self.zoom_out_btn.setToolTip("Zoom the preview out")
        self.zoom_out_btn.clicked.connect(lambda: self.screen.zoom_by(0.8))
        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Zoom the preview in (drag the picture to pan, Ctrl+wheel also zooms)")
        self.zoom_in_btn.clicked.connect(lambda: self.screen.zoom_by(1.25))
        self.zoom_lbl = QToolButton()
        self.zoom_lbl.setText("Fit")
        self.zoom_lbl.setToolTip("Reset the preview to Fit (or double-click the picture)")
        self.zoom_lbl.clicked.connect(self.reset_view)
        self.screen.view_changed.connect(self._view_changed)
        self.theater_btn = QToolButton()
        self.theater_btn.setText("⛶ Theater")
        self.theater_btn.setCheckable(True)
        self.theater_btn.setToolTip("Give most of the window to the preview (F11). Click again to return to the "
                                    "editing layout.")
        self.music_on = QCheckBox("Music")
        self.music_on.setChecked(True)
        self.music_on.setToolTip("Play the background music with the preview (not ducked - the render ducks it)")
        self.music_on.toggled.connect(self._music_toggled)
        self.sfx_on = QCheckBox("SFX")
        self.sfx_on.setChecked(True)
        self.sfx_on.setToolTip("Play planned sound effects in the preview (levels are approximate - the render "
                               "matches them to your voice)")
        for c in (self.fit, self.aspect):
            c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(self.btn)
        row1.addWidget(self.vol_btn)
        row1.addWidget(self.time, 1)
        row1.addWidget(self.zoom_out_btn)
        row1.addWidget(self.zoom_lbl)
        row1.addWidget(self.zoom_in_btn)
        row1.addWidget(self.theater_btn)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.row1, self.row2 = row1, row2
        self._row2_items = (self.caps, self.fx, self.music_on, self.sfx_on, self.fit, self.aspect)
        self._one_row = None
        self._arrange(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.viewport, 1)
        lay.addLayout(row1)
        lay.addLayout(row2)

        self.frame_fn = None     # callable(t) -> RGB ndarray
        self.duration = 0.0
        self.t = 0.0
        self._t0 = 0.0
        self._sfx_t = 0.0
        self.playing = False
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.media = self.music = None
        self.audio_out = self.music_out = None
        self._music_path = None
        self.gain = {"voice": 1.0, "music": _db(-20), "sfx": 1.0}
        self.sfx_events = []
        self.sfx_players = []
        self._sfx_next = 0
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.media = QMediaPlayer(self)
            self.audio_out = QAudioOutput(self)
            self.media.setAudioOutput(self.audio_out)
            self.music = QMediaPlayer(self)
            self.music_out = QAudioOutput(self)
            self.music.setAudioOutput(self.music_out)
            for _ in range(self.SFX_POOL):
                pl, out = QMediaPlayer(self), QAudioOutput(self)
                pl.setAudioOutput(out)
                self.sfx_players.append((pl, out))
        except Exception:
            self.media = self.music = None
            self.sfx_players = []
            self.vol_btn.setEnabled(False)
            self.vol_btn.setToolTip("Audio preview isn't available on this PC")
        from PySide6.QtCore import QElapsedTimer
        self.clock = QElapsedTimer()
        self._apply_volume()

    # ---------------------------------------------------------------- toolbar layout
    ONE_ROW_WIDTH = 980

    def _arrange(self, one_row):
        """Wide preview: all controls in one row (more height for the picture). Narrow: two rows."""
        if one_row == self._one_row:
            return
        self._one_row = one_row
        for w in self._row2_items:
            self.row1.removeWidget(w)
            self.row2.removeWidget(w)
        while self.row2.count():
            self.row2.takeAt(0)
        if one_row:
            i = self.row1.indexOf(self.zoom_out_btn)
            for w in reversed(self._row2_items):
                self.row1.insertWidget(i, w)
        else:
            for w in self._row2_items[:4]:
                self.row2.addWidget(w)
            self.row2.addStretch(1)
            self.row2.addWidget(self.fit)
            self.row2.addWidget(self.aspect)
        for w in self._row2_items:
            w.show()

    # ---------------------------------------------------------------- view
    def aspect_name(self):
        return self.aspect.currentText()

    def reset_view(self):
        self.fit.setCurrentText("Fit")
        self.screen.reset_view()

    def _view_changed(self):
        cv = self.screen
        plain = abs(cv.zoom - 1.0) < 1e-3 and cv.mode != "100%"
        self.zoom_lbl.setText(cv.mode if (plain or cv._img is None) else f"{cv.zoom_percent()}%")
        with QSignalBlocker(self.fit):
            self.fit.setCurrentText(self.screen.mode)

    # ---------------------------------------------------------------- volume
    def master(self):
        return 0.0 if self.muted else self.volume / 100.0

    def _apply_volume(self):
        m = self.master()
        if self.audio_out is not None:
            self.audio_out.setVolume(min(1.0, m * self.gain["voice"]))
        if self.music_out is not None:
            self.music_out.setVolume(min(1.0, m * self.gain["music"]))
        self.vol_label.setText(f"{self.volume}%")
        icon_txt = "🔇" if self.muted or self.volume == 0 else ("🔈" if self.volume < 34 else
                                                                 ("🔉" if self.volume < 67 else "🔊"))
        self.vol_btn.setText(icon_txt + (" Muted" if self.muted else f" {self.volume}%"))
        self.vol_btn.setProperty("muted", self.muted)
        self.vol_btn.style().unpolish(self.vol_btn)
        self.vol_btn.style().polish(self.vol_btn)

    def _save_volume(self):
        from PySide6.QtCore import QSettings
        s = QSettings()
        s.setValue("preview/volume", self.volume)
        s.setValue("preview/muted", self.muted)

    def set_volume(self, v):
        self.volume = min(100, max(0, int(v)))
        if self.vol_slider.value() != self.volume:
            self.vol_slider.blockSignals(True)
            self.vol_slider.setValue(self.volume)
            self.vol_slider.blockSignals(False)
        if self.volume > 0 and self.muted:
            self.set_muted(False)
        self._apply_volume()
        self._save_volume()

    def set_muted(self, on):
        self.muted = bool(on)
        if self.mute_cb.isChecked() != self.muted:
            self.mute_cb.blockSignals(True)
            self.mute_cb.setChecked(self.muted)
            self.mute_cb.blockSignals(False)
        self._apply_volume()
        self._save_volume()

    def toggle_mute(self):
        self.set_muted(not self.muted)

    def step_volume(self, d):
        self.set_volume(self.volume + d)

    def set_levels(self, voice_db=0.0, music_db=-20.0, sfx_db=0.0):
        """Track levels from the project, so the preview balance follows the Audio settings."""
        self.gain = {"voice": _db(voice_db), "music": _db(music_db), "sfx": _db(sfx_db)}
        self._apply_volume()

    # ---------------------------------------------------------------- sources
    def set_audio(self, path):
        if self.media is not None and path and os.path.isfile(path):
            self.media.setSource(QUrl.fromLocalFile(path))

    def set_music(self, path, db=None):
        """Background music for the preview (looped)."""
        if self.music is None:
            return
        if db is not None:
            self.gain["music"] = _db(db)
            self._apply_volume()
        path = path if path and os.path.isfile(path) else None
        if path != self._music_path:
            self._music_path = path
            self.music.stop()
            self.music.setSource(QUrl.fromLocalFile(path) if path else QUrl())

    def set_sfx(self, events):
        """Planned effects [{'t','file','gain','enabled'}] to play while previewing."""
        self.sfx_events = sorted([e for e in (events or []) if e.get("enabled", True) and e.get("file")
                                  and os.path.isfile(e["file"])], key=lambda e: e["t"])

    def _music_pos(self):
        d = self.music.duration() if self.music is not None else 0
        ms = int(self.t * 1000)
        return ms % d if d and d > 0 else ms

    def _music_play(self):
        if self.music is not None and self._music_path and self.music_on.isChecked():
            self.music.setPosition(self._music_pos())
            self.music.play()

    def _music_toggled(self, on):
        if self.music is None:
            return
        if on and self.playing:
            self._music_play()
        elif not on:
            self.music.pause()

    def _fire_sfx(self, t0, t1):
        if not self.sfx_players or not self.sfx_on.isChecked() or t1 <= t0:
            return
        for e in self.sfx_events:
            if t0 < e["t"] <= t1:
                pl, out = self.sfx_players[self._sfx_next % len(self.sfx_players)]
                self._sfx_next += 1
                # approximate: the render matches effects to the voice; here ~6 dB under full scale
                lvl = float(e["vol_db"]) if e.get("vol_db") is not None else float(e.get("gain", 0) or 0) - 6
                out.setVolume(min(1.0, self.master() * self.gain["sfx"] * _db(lvl)))
                pl.stop()
                pl.setSource(QUrl.fromLocalFile(e["file"]))
                pl.play()

    # ---------------------------------------------------------------- transport
    def set_duration(self, d):
        self.duration = d
        self._update_label()

    def _update_label(self):
        self.time.setText(f"{fmt_time(self.t)} / {fmt_time(self.duration)}")

    def toggle(self):
        self.pause() if self.playing else self.play()

    def play(self):
        if self.duration <= 0:
            return
        if self.t >= self.duration - 0.05:
            self.t = 0.0
        self.playing = True
        self.btn.setText("❚❚ Pause")
        self._t0 = self.t
        self._sfx_t = self.t - 0.01
        self.clock.start()
        if self.media is not None:
            self.media.setPosition(int(self.t * 1000))
            self.media.play()
        self._music_play()
        self.timer.start()

    def pause(self):
        self.playing = False
        self.btn.setText("▶ Play")
        self.timer.stop()
        if self.media is not None:
            self.media.pause()
        if self.music is not None:
            self.music.pause()
        for pl, _ in self.sfx_players:
            pl.stop()

    def seek(self, t):
        self.t = min(max(0.0, t), self.duration)
        self._sfx_t = self.t
        if self.playing:
            self._t0 = self.t
            self.clock.restart()
            if self.media is not None:
                self.media.setPosition(int(self.t * 1000))
            if self.music is not None and self._music_path and self.music_on.isChecked():
                self.music.setPosition(self._music_pos())
        self.show_frame()

    def _now(self):
        # wall clock drives the picture; audio is re-synced if it drifts
        t = self._t0 + self.clock.elapsed() / 1000.0
        if self.media is not None and self.media.playbackState() == self.media.PlaybackState.PlayingState:
            at = self.media.position() / 1000.0
            if abs(at - t) > 0.25:
                self._t0, t = at, at
                self.clock.restart()
        return t

    def _tick(self):
        self.t = self._now()
        if self.t >= self.duration:
            self.t = self.duration
            self.pause()
        self._fire_sfx(self._sfx_t, self.t)
        self._sfx_t = self.t
        self.show_frame()

    def show_frame(self):
        self._update_label()
        self.time_changed.emit(self.t)
        if not self.frame_fn:
            return
        try:
            fr = self.frame_fn(self.t)
        except Exception as e:  # never crash the UI because of one bad frame
            self.screen.setText(f"Preview error: {e}")
            return
        if fr is None:
            self.screen.setText(self.empty_text)
            return
        h, w = fr.shape[:2]
        self.screen.set_image(QImage(fr.data, w, h, w * 3, QImage.Format_RGB888).copy())
        self._view_changed()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._arrange(self.width() >= self.ONE_ROW_WIDTH)
        if self.frame_fn and not self.playing:
            QTimer.singleShot(0, self.show_frame)


class TimelineScrollBar(QWidget):
    """Horizontal scrollbar under the timeline: the handle shows which part of the
    video is visible; drag it (or click the track, or use the wheel on it) to move.
    When the whole video already fits, it says how to zoom in instead of looking dead."""

    def __init__(self, timeline):
        from PySide6.QtWidgets import QScrollBar
        super().__init__()
        self.tl = timeline
        self.bar = QScrollBar(Qt.Horizontal)
        self.bar.setObjectName("TimelineBar")
        self.bar.setMinimumHeight(16)
        self.bar.setToolTip("Drag to move along the timeline")
        self.hint = QLabel("Whole video shown - zoom in with  +  or Ctrl + mouse wheel, then drag this bar")
        self.hint.setObjectName("Help")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(timeline.GUTTER, 2, 0, 2)
        lay.setSpacing(10)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.hint)
        self._sync = False
        self.bar.valueChanged.connect(self._moved)
        timeline.view_changed.connect(self.update_bar)
        self.update_bar()

    def update_bar(self):
        self._sync = True
        mx = self.tl.max_offset()
        vis = self.tl.visible_seconds()
        scrollable = mx > 0.01
        self.bar.setRange(0, int(round(mx * 100)))
        self.bar.setPageStep(max(1, int(round(vis * 100))))
        self.bar.setSingleStep(max(1, int(round(vis * 10))))
        self.bar.setValue(int(round(self.tl.offset * 100)))
        self.bar.setEnabled(scrollable)
        self.hint.setVisible(not scrollable and self.tl.duration > 1.0 and bool(self.tl.scenes))
        if scrollable:
            a = self.tl.offset
            self.bar.setToolTip(f"Showing {fmt_time(a)[:-3]}-{fmt_time(a + vis)[:-3]} - drag to move")
        self._sync = False

    def _moved(self, v):
        if not self._sync:
            self.tl.set_offset(v / 100.0)
