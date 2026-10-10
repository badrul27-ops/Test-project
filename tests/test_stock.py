"""Stock B-roll: search, keys, errors, download and adding a clip to a scene - against a LOCAL fake
Pexels / Pixabay server (no internet, no real keys needed).

    python -m tests.test_stock
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autoedit import stock as S  # noqa: E402
from autoedit.util import ffmpeg_exe, list_media  # noqa: E402

HITS = []
CLIP = b""


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", length=None, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length if length is not None else len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        HITS.append(u.path)
        base = f"http://127.0.0.1:{self.server.server_port}"
        if u.path == "/pexels":
            if self.headers.get("Authorization") != "good-pexels":
                return self._send(401, b'{"error":"unauthorized"}')
            vids = [{"id": 1000 + i, "width": 1920, "height": 1080, "duration": 8 + i,
                     "url": f"https://www.pexels.com/video/fog-{1000 + i}/", "image": base + "/thumb.jpg",
                     "user": {"name": f"Creator {i}", "url": "https://www.pexels.com/@creator"},
                     "video_files": [{"link": base + "/clip.mp4", "quality": "hd", "file_type": "video/mp4",
                                      "width": 1280, "height": 720},
                                     {"link": base + "/clip.mp4", "quality": "sd", "file_type": "video/mp4",
                                      "width": 640, "height": 360}]} for i in range(3)]
            return self._send(200, json.dumps({"videos": vids, "total_results": 3}).encode(),
                              headers={"X-Ratelimit-Remaining": "199", "X-Ratelimit-Reset": str(int(time.time()) + 3600)})
        if u.path == "/pixabay":
            if q.get("key", [""])[0] != "good-pixabay":
                return self._send(400, b'[ERROR 400] "key" is invalid', ctype="text/plain")
            mk = {"url": base + "/broken.mp4", "width": 1280, "height": 720, "size": 1, "thumbnail": base + "/thumb.jpg"}
            hits = [{"id": 77, "pageURL": "https://pixabay.com/videos/rain-77/", "tags": "rain, window",
                     "duration": 12, "user": "rainmaker", "user_id": 5,
                     "videos": {"medium": mk, "small": dict(mk, url=base + "/short.mp4", width=640, height=360)}}]
            return self._send(200, json.dumps({"hits": hits, "totalHits": 1}).encode())
        if u.path == "/clip.mp4":
            return self._send(200, CLIP, ctype="video/mp4")
        if u.path == "/short.mp4":
            return self._send(200, CLIP[:20000], ctype="video/mp4", length=len(CLIP))   # connection ends early
        if u.path == "/thumb.jpg":
            return self._send(404, b"")
        return self._send(500, b"server error", ctype="text/plain")


def expect(kind, fn):
    try:
        fn()
    except S.StockError as e:
        assert e.kind == kind, (kind, e.kind, str(e))
        return str(e)
    raise AssertionError(f"expected StockError {kind}")


def main():
    global CLIP
    tmp = tempfile.mkdtemp()
    clip = os.path.join(tmp, "src.mp4")
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", clip], check=True)
    CLIP = open(clip, "rb").read()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    S.API["Pexels"], S.API["Pixabay"] = base + "/pexels", base + "/pixabay"
    S.CACHE_DIR = os.path.join(tmp, "cache")

    # ---- keys: missing / invalid / secure storage
    for p in S.PROVIDERS:
        S.set_key(p, "")
    msg = expect("no_key", lambda: S.search("Pexels", "fog"))
    assert not HITS, "no request may be sent without a key"
    expect("bad_key", lambda: S.search("Pexels", "fog", key="wrong"))
    expect("bad_key", lambda: S.search("Pixabay", "rain", key="wrong"))
    how = S.set_key("Pexels", "good-pexels")
    assert how in ("saved", "session") and S.get_key("Pexels") == "good-pexels"
    print(f"  missing key -> '{msg[:60]}…'; invalid keys rejected (Pexels 401, Pixabay 400); key stored: {how}"
          f" ({S.key_storage_name() or 'no secure store here -> session only'})")

    # ---- empty search: no request
    n = len(HITS)
    expect("empty", lambda: S.search("Pexels", "   "))
    assert len(HITS) == n
    # ---- search + 24 h cache
    res, info = S.search("Pexels", "fog forest")
    assert len(res) == 3 and res[0]["creator"] == "Creator 0" and res[0]["page_url"].startswith("https://www.pexels")
    assert info["remaining"] == 199 and not info["cached"]
    n = len(HITS)
    res2, info2 = S.search("Pexels", "fog forest")
    assert info2["cached"] and len(HITS) == n, "repeat search must come from the cache"
    S.set_key("Pixabay", "good-pixabay")
    px, _ = S.search("Pixabay", "rain")
    assert px[0]["creator"] == "rainmaker" and px[0]["width"] == 1280
    print("  empty search sends nothing; results normalised (thumbnail, duration, size, creator, link); repeat "
          "search served from the 24 h cache")

    # ---- provider unavailable
    S.API["Pexels"] = "http://127.0.0.1:9/pexels"
    expect("network", lambda: S.search("Pexels", "unreachable test", use_cache=False))
    S.API["Pexels"] = base + "/pexels"

    # ---- downloads: failed / truncated / ok
    media = os.path.join(tmp, "media")
    os.makedirs(media)
    from PIL import Image
    for i in range(3):
        Image.new("RGB", (640, 360), (60 * i, 90, 120)).save(os.path.join(media, f"{i + 1:02d}.jpg"))
    dest = os.path.join(media, "_autoedit_stock")
    expect("http", lambda: S.download(px[0], dest))                       # 500
    r_short = dict(px[0], files=[dict(px[0]["files"][1])])
    expect("network", lambda: S.download(r_short, dest))                  # connection ends early
    left = os.listdir(dest) if os.path.isdir(dest) else []
    assert not left, f"failed downloads left files behind: {left}"
    path, ainfo = S.download(res[0], dest)
    assert os.path.getsize(path) == len(CLIP) and ainfo["creator"] == "Creator 0" and ainfo["license_url"]
    assert path not in list_media(media), "stock clips must not join the folder's scene order"
    print("  failed (HTTP 500) and interrupted downloads leave nothing behind; good download saved to "
          "media/_autoedit_stock with source + license info")

    # ---- GUI: panel + adding to a scene
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.setOrganizationName("AEStockTest")
    QApplication.setApplicationName("AEStockTest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    from autoedit.theme import apply_theme
    apply_theme(app)
    import app as A
    from autoedit import project as prj
    A.ask_unsaved = lambda *a, **k: "discard"
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    p = prj.new_project()
    words, t = [], 0.3
    for i, w in enumerate("The storm reached the old bank at night and nobody saw it coming".split()):
        words.append({"w": w, "s": round(t, 2), "e": round(t + 0.3, 2)})
        t += 0.45
    p.update(name="stock", media_dir=media, voiceover="", words=words, duration=round(t + 0.5, 2),
             output_dir=tmp)
    p["settings"]["scene_mode"] = "match_media"
    prj.replan(p, log=lambda m: None)
    w = A.MainWindow()
    w.resize(1366, 740)
    w.show()
    w.project = p
    w.reset_state_tracking(saved=False)
    w.refresh_all()
    w.show_editor()
    app.processEvents()
    sp = w.stock
    w.tools.setCurrentIndex([b.text() for b in w.tools.buttons].index("Stock"))        # Stock tab
    app.processEvents()

    def wait(cond, secs=15):
        t0 = time.time()
        while not cond() and time.time() - t0 < secs:
            app.processEvents()
            time.sleep(0.02)
        return cond()
    # suggestions from the selected scene (editable, no person names)
    w.select_scene(0)
    sp.suggest()
    sugg = [sp.sugg.item(i).text() for i in range(sp.sugg.count())]
    assert sugg and "storm clouds timelapse" in sugg and sp.query.text() == sugg[0], sugg
    sp.query.setText("old bank building at night")              # editable
    # empty search in the panel
    sp.query.setText("  ")
    assert sp.search() is False and "Type something" in sp.status.text()
    # missing key in the panel -> clear message, no crash
    S.set_key("Pexels", "")
    sp.query.setText("fog")
    sp.search()
    assert wait(lambda: "key" in sp.status.text().lower()), sp.status.text()
    S.set_key("Pexels", "good-pexels")
    sp.query.setText("fog forest")
    sp.search()
    assert wait(lambda: sp.list.count() == 3), sp.status.text()
    assert "Creator 0" in sp.detail.text() and "Pexels License" in sp.detail.text()
    if os.environ.get("AE_SHOT"):
        w.grab().save(os.environ["AE_SHOT"])
        sa = w.pages.currentWidget()
        sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
        app.processEvents()
        w.left.grab().save(os.environ["AE_SHOT"].replace(".png", "_results.png"))
    # people warning
    sp.query.setText("businessman portrait")
    assert sp.people.isVisible() and "identifiable" in sp.people.text()
    sp.query.setText("fog forest")
    assert not sp.people.isVisible()
    # download + use for scene: scene already has a visual -> asks; 'bin' keeps the scene unchanged
    before = w.project["scenes"][0]["media"]
    w._ask_replace = lambda s, pth: "bin"
    sp.list.setCurrentRow(1)
    assert sp.download("scene")
    assert wait(lambda: sp._dl is None), sp.status.text()
    assert w.project["scenes"][0]["media"] == before, "scene visual replaced without permission!"
    clip_path = [k for k in w.project["stock_assets"]][0]
    assert os.path.isfile(clip_path) and clip_path in [os.path.normpath(m) for m in A.project_media(w.project)]
    # now confirm 'replace'
    w._ask_replace = lambda s, pth: "replace"
    sp.list.setCurrentRow(1)
    sp.download("scene")
    assert wait(lambda: sp._dl is None)
    assert os.path.normpath(w.project["scenes"][0]["media"]) == clip_path and w.project["scenes"][0]["motion"] == "none"
    # add to V2
    w.select_scene(1)
    sp.list.setCurrentRow(2)
    sp.download("v2")
    assert wait(lambda: sp._dl is None)
    assert len(w.project["broll"]) == 1 and w.project["broll"][0]["media"].endswith(".mp4")
    # failed download in the panel: Pixabay result points at a broken URL
    S.set_key("Pixabay", "good-pixabay")
    sp.provider.setCurrentText("Pixabay")
    sp.query.setText("rain")
    sp.search()
    assert wait(lambda: sp.list.count() == 1)
    sp.list.setCurrentRow(0)
    n_assets = len(w.project["stock_assets"])
    sp.download("scene")
    assert wait(lambda: sp._dl is None) and "failed" in sp.status.text().lower(), sp.status.text()
    assert len(w.project["stock_assets"]) == n_assets
    # local import still works while a provider is down
    extra = os.path.join(tmp, "local.png")
    Image.new("RGB", (320, 180), (200, 0, 0)).save(extra)
    assert w.import_media([extra]) == [extra]
    # undo removes the stock clip from the scene; files stay
    w.do_undo()
    w.do_undo()
    w.do_undo()
    assert os.path.isfile(clip_path)
    # credits + project file keeps source/license, never the keys
    out = os.path.join(tmp, "stock.autoedit.json")
    w.do_redo()
    w.do_redo()
    w.do_redo()
    prj.save(w.project, out)
    txt = open(out, encoding="utf-8").read()
    assert "good-pexels" not in txt and "good-pixabay" not in txt, "API key leaked into the project file"
    assert "Creator 1" in txt and "pexels.com/license" in txt
    cred = S.credits_text(w.project)
    assert "Video by Creator 1 on Pexels" in cred, cred
    print("  panel: suggestions from narration (editable), empty/missing-key messages, results with credit + "
          "license, people warning; Download + scene asks before replacing (kept / replaced), V2 add, failed "
          "download reported with nothing recorded, local import still works; keys never in the project; credits")
    srv.shutdown()
    for p_ in S.PROVIDERS:
        S.set_key(p_, "")
    print("STOCK OK")


if __name__ == "__main__":
    main()
