"""Stock B-roll tab: search Pexels / Pixabay with your own API keys, preview, then explicitly
download one clip and add it to a scene, to V2 or just to the media bin."""
import os
import urllib.request

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from . import stock as S
from .app_parts_base import form, label


def _emit(sig, *a):
    try:
        sig.emit(*a)
    except RuntimeError:
        pass


class _Sig(QObject):
    done = Signal(object, object)       # tag, result
    failed = Signal(object, str, str)   # tag, kind, message
    progress = Signal(float, int)


class _Job(QRunnable):
    def __init__(self, sig, tag, fn):
        super().__init__()
        self.sig, self.tag, self.fn = sig, tag, fn

    def run(self):
        try:
            _emit(self.sig.done, self.tag, self.fn())
        except S.StockError as e:
            _emit(self.sig.failed, self.tag, e.kind, str(e))
        except Exception as e:  # never crash the app because of a network problem
            _emit(self.sig.failed, self.tag, "http", f"Unexpected error: {e}")


def fmt_dur(s):
    s = int(round(s or 0))
    return f"{s // 60}:{s % 60:02d}"


# ============================================================================ API keys dialog
class ApiKeysDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stock footage API keys")
        lay = QVBoxLayout(self)
        store = S.key_storage_name()
        lay.addWidget(label("Your own free API keys. They're stored securely " +
                            (f"in {store}" if store else "for this session only - the secure key store isn't installed "
                                                          "yet: run setup.bat once (it adds the 'keyring' package), "
                                                          "then keys are kept in Windows Credential Manager") +
                            " - never in project files, settings or the app's code.", "Help"))
        f = form()
        self.edits = {}
        for p in S.PROVIDERS:
            e = QLineEdit(S.get_key(p))
            e.setEchoMode(QLineEdit.Password)
            e.setPlaceholderText(f"Paste your {p} API key")
            show = QToolButton()
            show.setText("Show")
            show.setCheckable(True)
            show.toggled.connect(lambda on, e=e: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
            link = QLabel(f'<a href="{S.SIGNUP[p]}">Get a free {p} key</a>')
            link.setOpenExternalLinks(True)
            row = QHBoxLayout()
            row.addWidget(e, 1)
            row.addWidget(show)
            f.addRow(p, row)
            f.addRow("", link)
            self.edits[p] = e
        lay.addLayout(f)
        lay.addWidget(label("Leave a field empty and press Save to remove that key.", "Help"))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(520, 300)

    def save(self):
        return {p: S.set_key(p, e.text()) for p, e in self.edits.items()}


# ============================================================================ preview dialog
class PreviewDialog(QDialog):
    """Streams the smallest version of a clip (nothing is saved)."""

    def __init__(self, parent, result):
        super().__init__(parent)
        self.setWindowTitle(f"Preview - {result['provider']} #{result['id']}")
        lay = QVBoxLayout(self)
        self.player = None
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
            vw = QVideoWidget()
            vw.setMinimumSize(480, 270)
            lay.addWidget(vw, 1)
            self.player = QMediaPlayer(self)
            self.player.setVideoOutput(vw)
            self.player.setLoops(-1) if hasattr(self.player, "setLoops") else None
            self.player.setSource(QUrl(S.preview_file(result)["url"]))
            self.player.play()
        except Exception as e:
            lay.addWidget(label(f"Video preview isn't available here ({e}). Use 'Open source page'.", "Err"))
        info = QLabel(f'Video by <a href="{result["creator_url"] or result["page_url"]}">{result["creator"]}</a> on '
                      f'<a href="{result["page_url"]}">{result["provider"]}</a> · {fmt_dur(result["duration"])} · '
                      f'{result["width"]}×{result["height"]} · streamed preview (not saved)')
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(720, 480)

    def done(self, r):
        if self.player is not None:
            self.player.stop()
        super().done(r)


# ============================================================================ panel
class StockPanel(QWidget):
    """Search -> pick -> preview -> Download (+ add to scene / V2 / bin). Emits `downloaded`."""
    downloaded = Signal(str, dict, str)     # path, asset info, action: scene | v2 | bin
    log = Signal(str)
    TW, TH = 128, 72

    def __init__(self, get_project, get_scene_text, get_category, get_fmt):
        super().__init__()
        self.get_project, self.get_scene_text = get_project, get_scene_text
        self.get_category, self.get_fmt = get_category, get_fmt
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(4)
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        self.sig.failed.connect(self._failed)
        self.sig.progress.connect(self._dl_progress)
        self.results, self.thumbs = [], {}
        self.page, self.last_query = 1, None
        self._search_id = 0
        self._dl = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        credit = QLabel('Free stock videos from <a href="https://www.pexels.com">Pexels</a> and '
                        '<a href="https://pixabay.com">Pixabay</a> · '
                        '<a href="keys">API keys…</a>')
        credit.setObjectName("StockCredit")
        credit.setTextFormat(Qt.RichText)
        credit.setWordWrap(True)
        credit.linkActivated.connect(self._link)
        lay.addWidget(credit)
        lay.addWidget(label("Finds existing footage (it doesn't generate video). You choose what to download; "
                            "use depends on each clip's license.", "Help"))
        # search row
        f = form()
        top = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItems(list(S.PROVIDERS))
        self.provider.setToolTip("Which library to search")
        self.orient = QComboBox()
        for t, v in (("Landscape", "landscape"), ("Portrait", "portrait"), ("Square", "square"), ("Any shape", "any")):
            self.orient.addItem(t, v)
        top.addWidget(self.provider, 1)
        top.addWidget(self.orient, 1)
        f.addRow("Library", top)
        self.query = QLineEdit()
        self.query.setPlaceholderText("e.g. storm clouds, empty street at night, old documents")
        self.query.returnPressed.connect(lambda: self.search(new=True))
        self.query.textChanged.connect(self._people_check)
        self.btn_search = QPushButton("Search")
        self.btn_search.setProperty("primary", True)
        self.btn_search.clicked.connect(lambda: self.search(new=True))
        f.addRow("Search", self.query)
        lay.addLayout(f)
        self.people = label("", "Err")
        self.people.setVisible(False)
        lay.addWidget(self.people)
        # suggestions + ideas
        srow = QHBoxLayout()
        b = QPushButton("Suggest from scene")
        b.setToolTip("Search ideas from the selected scene's narration (you can edit them before searching)")
        b.clicked.connect(self.suggest)
        self.ideas = QToolButton()
        self.ideas.setText("Ideas ▾")
        self.ideas.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(self.ideas)
        for cat, items in S.IDEAS.items():
            sm = m.addMenu(cat)
            for it in items:
                sm.addAction(it, lambda it=it: self._use_term(it))
        self.ideas.setMenu(m)
        srow.addWidget(self.btn_search)
        srow.addWidget(b)
        srow.addWidget(self.ideas)
        srow.addStretch(1)
        lay.addLayout(srow)
        self.sugg = QListWidget()
        self.sugg.setFlow(QListWidget.LeftToRight)
        self.sugg.setWrapping(True)
        self.sugg.setResizeMode(QListWidget.Adjust)
        self.sugg.setMaximumHeight(58)
        self.sugg.setVisible(False)
        self.sugg.setToolTip("Click a suggestion to put it in the search box (edit it, then Search)")
        self.sugg.itemClicked.connect(lambda it: self._use_term(it.text()))
        lay.addWidget(self.sugg)
        # results
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(self.TW, self.TH))
        self.list.setGridSize(QSize(self.TW + 16, self.TH + 46))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setMinimumHeight(200)
        self.list.currentRowChanged.connect(self._select)
        self.list.itemDoubleClicked.connect(lambda _it: self.preview())
        lay.addWidget(self.list, 1)
        self.more = QPushButton("Load more results")
        self.more.setVisible(False)
        self.more.clicked.connect(lambda: self.search(new=False))
        lay.addWidget(self.more)
        # details + actions
        self.detail = QLabel("Search, then select a clip to see its details.")
        self.detail.setObjectName("Help")
        self.detail.setWordWrap(True)
        self.detail.setOpenExternalLinks(True)
        self.detail.setTextFormat(Qt.RichText)
        lay.addWidget(self.detail)
        r1 = QHBoxLayout()
        self.b_prev = QPushButton("▶ Preview")
        self.b_prev.clicked.connect(self.preview)
        self.b_page = QPushButton("Source page")
        self.b_page.setToolTip("Open this clip's page on the provider's site (license and details)")
        self.b_page.clicked.connect(self.open_page)
        r1.addWidget(self.b_prev)
        r1.addWidget(self.b_page)
        lay.addLayout(r1)
        r2 = QHBoxLayout()
        self.b_scene = QPushButton("Download → scene")
        self.b_scene.setToolTip("Download this clip and put it on the selected scene (asks before replacing "
                                "an existing visual)")
        self.b_scene.clicked.connect(lambda: self.download("scene"))
        self.b_v2 = QPushButton("Download → V2")
        self.b_v2.setToolTip("Download this clip and lay it over the main scenes at the playhead (B-roll track)")
        self.b_v2.clicked.connect(lambda: self.download("v2"))
        r2.addWidget(self.b_scene)
        r2.addWidget(self.b_v2)
        lay.addLayout(r2)
        self.b_bin = QPushButton("Download to media bin only")
        for b in (self.b_scene, self.b_v2, self.b_bin):
            b.setMinimumWidth(0)
        self.b_bin.clicked.connect(lambda: self.download("bin"))
        lay.addWidget(self.b_bin)
        prow = QHBoxLayout()
        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setVisible(False)
        self.b_cancel = QPushButton("Cancel download")
        self.b_cancel.setVisible(False)
        self.b_cancel.clicked.connect(self.cancel_download)
        prow.addWidget(self.prog, 1)
        prow.addWidget(self.b_cancel)
        lay.addLayout(prow)
        self.status = label("", "Help")
        lay.addWidget(self.status)
        b = QPushButton("Copy credits for the description")
        b.setToolTip("'Video by … on Pexels/Pixabay' lines for the stock clips used in this video")
        b.clicked.connect(self.copy_credits)
        lay.addWidget(b)
        self._select(-1)

    # ---------------------------------------------------------------- helpers
    def _link(self, href):
        if href == "keys":
            self.edit_keys()
        else:
            QDesktopServices.openUrl(QUrl(href))

    def edit_keys(self):
        dlg = ApiKeysDialog(self)
        if dlg.exec():
            res = dlg.save()
            msg = ", ".join(f"{p}: {'saved securely' if r == 'saved' else 'kept for this session' if r == 'session' else 'removed'}"
                            for p, r in res.items())
            self.set_status(msg, "Ok")

    def set_status(self, msg, kind="Help"):
        self.status.setObjectName(kind)
        self.status.setText(msg)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _people_check(self, *_):
        w = S.people_warning(self.query.text())
        self.people.setText("⚠ " + w if w else "")
        self.people.setVisible(bool(w))

    def _use_term(self, t):
        self.query.setText(t)
        self.query.setFocus()
        self.query.selectAll()

    def suggest(self):
        txt = self.get_scene_text() or ""
        terms = S.suggest_terms(txt, self.get_category() or "")
        self.sugg.clear()
        for t in terms:
            QListWidgetItem(t, self.sugg)
        self.sugg.setVisible(bool(terms))
        if not txt:
            self.set_status("Select a scene with narration first - or use Ideas ▾.")
        elif terms:
            self.set_status("Suggestions from the scene's narration - click one, edit it if you like, then Search.")
            self.query.setText(terms[0])
        else:
            self.set_status("No good search words in this scene - try Ideas ▾ (places, objects, weather, moods).")

    # ---------------------------------------------------------------- search
    def search(self, new=True):
        q = self.query.text().strip()
        if not q:
            self.set_status("Type something to search for, or use Suggest from scene / Ideas ▾.", "Err")
            return False
        prov = self.provider.currentText()
        if new:
            self.page, self.results = 1, []
            self.list.clear()
        else:
            self.page += 1
        self.last_query = (prov, q, self.orient.currentData())
        self._search_id += 1
        sid = self._search_id
        self.btn_search.setEnabled(False)
        self.set_status(f"Searching {prov} for '{q}'…")
        o, page = self.orient.currentData(), self.page
        self.pool.start(_Job(self.sig, ("search", sid), lambda: S.search(prov, q, o, page)))
        return True

    def _done(self, tag, res):
        kind = tag[0]
        if kind == "search":
            if tag[1] != self._search_id:
                return
            self.btn_search.setEnabled(True)
            results, info = res
            prov, q, _ = self.last_query
            start = len(self.results)
            self.results += results
            for r in results:
                it = QListWidgetItem(f"{fmt_dur(r['duration'])} · {r['width']}×{r['height']}\n"
                                     f"{r['creator'][:16]} · {r['provider']}")
                it.setToolTip(f"Video by {r['creator']} on {r['provider']}\n{r['page_url']}")
                it.setIcon(self._placeholder())
                self.list.addItem(it)
                self._fetch_thumb(r["thumb"])
            n = len(self.results)
            more = n < info["total"] and results
            self.more.setVisible(bool(more))
            if not n:
                self.set_status(f"No {prov} results for '{q}'. Try broader words (a place, object, weather or mood).")
            else:
                rem = info.get("remaining")
                self.set_status(f"{n} of {info['total']} {prov} results for '{q}'" +
                                (" (from the 24-hour cache)" if info["cached"] else "") +
                                (f" · {rem} requests left" if rem is not None else "") +
                                ". Select one for details, ▶ to preview.", "Ok")
            if start == 0 and n:
                self.list.setCurrentRow(0)
        elif kind == "thumb":
            url, img = tag[1], res
            if img is not None and not img.isNull():
                self.thumbs[url] = QPixmap.fromImage(img.scaled(self.TW, self.TH, Qt.KeepAspectRatio,
                                                                Qt.SmoothTransformation))
                for i, r in enumerate(self.results):
                    if r["thumb"] == url and self.list.item(i):
                        self.list.item(i).setIcon(QIcon(self._boxed(self.thumbs[url])))
        elif kind == "download":
            path, info = res
            action = tag[1]
            self._end_download()
            self.set_status(f"Downloaded {os.path.basename(path)} - {S.credit_line(info)}", "Ok")
            self.log.emit(f"Stock clip downloaded: {path} ({S.credit_line(info)})")
            self.downloaded.emit(path, info, action)

    def _failed(self, tag, kind, msg):
        if tag[0] == "search":
            if tag[1] != self._search_id:
                return
            self.btn_search.setEnabled(True)
            if self.page > 1:
                self.page -= 1
            self.set_status(("🔑 " if kind in ("no_key", "bad_key") else "⚠ ") + msg, "Err")
            self.log.emit("Stock search: " + msg)
            if kind in ("no_key", "bad_key"):
                self.detail.setText(f'<a href="{S.SIGNUP[self.provider.currentText()]}">Get a free '
                                    f'{self.provider.currentText()} key</a>, then click <b>API keys…</b> above.')
        elif tag[0] == "download":
            self._end_download()
            self.set_status("⚠ " + msg, "Err" if kind != "cancelled" else "Help")
            self.log.emit("Stock download: " + msg)
        # thumbnail failures are silent (the placeholder stays)

    # ---------------------------------------------------------------- thumbnails
    def _placeholder(self):
        pm = QPixmap(self.TW, self.TH)
        pm.fill(QColor(40, 44, 54))
        return QIcon(pm)

    def _boxed(self, pm):
        box = QPixmap(self.TW, self.TH)
        box.fill(QColor(12, 13, 17))
        from PySide6.QtGui import QPainter
        p = QPainter(box)
        p.drawPixmap((self.TW - pm.width()) // 2, (self.TH - pm.height()) // 2, pm)
        p.end()
        return box

    def _fetch_thumb(self, url):
        if not url:
            return
        if url in self.thumbs:
            return

        def get():
            req = urllib.request.Request(url, headers={"User-Agent": S.UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(3_000_000)
            img = QImage()
            img.loadFromData(data)
            return img
        self.pool.start(_Job(self.sig, ("thumb", url), get))

    # ---------------------------------------------------------------- selection / actions
    def current(self):
        i = self.list.currentRow()
        return self.results[i] if 0 <= i < len(self.results) else None

    def _select(self, _i):
        r = self.current()
        for b in (self.b_prev, self.b_page, self.b_scene, self.b_v2, self.b_bin):
            b.setEnabled(r is not None and self._dl is None)
        if r is None:
            return
        f = S.pick_file(r)
        cu = r["creator_url"] or r["page_url"]
        self.detail.setText(
            f'<b>{fmt_dur(r["duration"])}</b> · {r["width"]}×{r["height"]} (downloads {f["w"]}×{f["h"]})<br>'
            f'Video by <a href="{cu}">{r["creator"]}</a> on <a href="{r["page_url"]}">{r["provider"]}</a> · '
            f'<a href="{S.LICENSE[r["provider"]]}">{r["provider"]} License</a>')

    def preview(self):
        r = self.current()
        if r:
            PreviewDialog(self, r).exec()

    def open_page(self):
        r = self.current()
        if r and r["page_url"]:
            QDesktopServices.openUrl(QUrl(r["page_url"]))

    def download(self, action):
        r = self.current()
        if r is None or self._dl is not None:
            return False
        dest = S.stock_folder(self.get_project())
        if not dest:
            self.set_status("Choose a voiceover or an images folder first - downloads go into the project's media "
                            "folder.", "Err")
            return False
        self._dl = {"stop": False}
        flag = self._dl
        fmt = self.get_fmt()
        for b in (self.b_scene, self.b_v2, self.b_bin, self.btn_search):
            b.setEnabled(False)
        self.prog.setValue(0)
        self.prog.setVisible(True)
        self.b_cancel.setVisible(True)
        self.set_status(f"Downloading {r['provider']} clip #{r['id']}…")
        sig = self.sig
        self.pool.start(_Job(self.sig, ("download", action), lambda: S.download(
            r, dest, fmt=fmt, progress=lambda p, n: _emit(sig.progress, p, n), should_stop=lambda: flag["stop"])))
        return True

    def _dl_progress(self, p, n):
        self.prog.setValue(int(p * 1000))
        self.prog.setFormat(f"{n / 1e6:.1f} MB")

    def cancel_download(self):
        if self._dl is not None:
            self._dl["stop"] = True

    def _end_download(self):
        self._dl = None
        self.prog.setVisible(False)
        self.b_cancel.setVisible(False)
        self.btn_search.setEnabled(True)
        self._select(self.list.currentRow())

    def copy_credits(self):
        from PySide6.QtGui import QGuiApplication
        txt = S.credits_text(self.get_project())
        if txt:
            QGuiApplication.clipboard().setText(txt)
            self.set_status("Credits copied - paste them into your video description.", "Ok")
        else:
            self.set_status("No downloaded stock clips are used in this video yet.")
