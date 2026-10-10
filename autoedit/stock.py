"""Stock B-roll search (Pexels + Pixabay videos) - no Qt here, so it can be tested on its own.

Retrieves EXISTING stock footage with the user's own API keys; it never generates video.

Provider rules followed here (from the providers' API docs, checked Oct 2026 - they can change):
  Pexels  - Authorization header with the key; 200 requests/hour, 20,000/month (X-Ratelimit-*
            headers); show a prominent link to Pexels and credit the creator with a link to the
            video page.  https://www.pexels.com/api/documentation/
  Pixabay - key as a URL parameter; 100 requests / 60 s; "requests must be cached for 24 hours";
            show where results are from; no systematic mass downloads.  https://pixabay.com/api/docs/
So: every search is cached on disk for 24 h, requests are rate-limited on our side too, nothing is
downloaded unless the user picks one clip and asks for it, and every downloaded clip keeps its
provider, creator, page link and license link in the project.

API keys are stored with the `keyring` package (Windows Credential Manager on Windows) - never in
the project file, settings or source code.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .util import APP_DIR

PROVIDERS = ("Pexels", "Pixabay")
API = {  # overridable for tests
    "Pexels": "https://api.pexels.com/v1/videos/search",
    "Pixabay": "https://pixabay.com/api/videos/",
}
SIGNUP = {"Pexels": "https://www.pexels.com/api/", "Pixabay": "https://pixabay.com/api/docs/"}
HOME = {"Pexels": "https://www.pexels.com", "Pixabay": "https://pixabay.com"}
LICENSE = {"Pexels": "https://www.pexels.com/license/", "Pixabay": "https://pixabay.com/service/license-summary/"}
CACHE_DIR = os.path.join(APP_DIR, "cache", "stock")
CACHE_TTL = 24 * 3600
UA = "AutoEditor (local desktop video editor)"
KEYRING_SERVICE = "AutoEditor stock footage"


class StockError(Exception):
    """kind: no_key | bad_key | rate | network | http | empty | bad_file | cancelled"""

    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind


# ============================================================================ API keys
_SESSION_KEYS = {}


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def key_storage_name():
    kr = _keyring()
    if kr is None:
        return None
    name = type(kr.get_keyring()).__name__
    return "Windows Credential Manager" if "Win" in name else name


def get_key(provider):
    if _SESSION_KEYS.get(provider):
        return _SESSION_KEYS[provider]
    kr = _keyring()
    if kr is not None:
        try:
            return kr.get_password(KEYRING_SERVICE, provider) or ""
        except Exception:
            return ""
    return ""


def set_key(provider, key):
    """Save (or with an empty key: remove) a provider key. Returns 'saved' | 'removed' | 'session'.
    'session' = no secure store available: the key is kept in memory until the app closes."""
    key = (key or "").strip()
    kr = _keyring()
    _SESSION_KEYS.pop(provider, None)
    if kr is None:
        if key:
            _SESSION_KEYS[provider] = key
            return "session"
        return "removed"
    try:
        if key:
            kr.set_password(KEYRING_SERVICE, provider, key)
            return "saved"
        try:
            kr.delete_password(KEYRING_SERVICE, provider)
        except Exception:
            pass
        return "removed"
    except Exception:
        if key:
            _SESSION_KEYS[provider] = key
            return "session"
        return "removed"


# ============================================================================ rate limits + cache
class _Limiter:
    def __init__(self):
        self.times = {p: [] for p in PROVIDERS}
        self.remaining = {}         # from provider headers
        self.reset = {}

    def check(self, provider):
        now = time.time()
        win, cap = (3600, 190) if provider == "Pexels" else (60, 90)   # a little under the published limits
        self.times[provider] = [t for t in self.times[provider] if now - t < win]
        if len(self.times[provider]) >= cap:
            wait = int(win - (now - self.times[provider][0])) + 1
            raise StockError("rate", f"{provider}: too many searches - wait about {wait} s (provider limit).")
        rem = self.remaining.get(provider)
        if rem is not None and rem <= 0 and self.reset.get(provider, 0) > now:
            raise StockError("rate", f"{provider}: request limit reached until "
                                     f"{time.strftime('%H:%M', time.localtime(self.reset[provider]))}.")
        self.times[provider].append(now)

    def update(self, provider, headers):
        try:
            if headers.get("X-Ratelimit-Remaining") is not None:
                self.remaining[provider] = int(headers.get("X-Ratelimit-Remaining"))
            r = headers.get("X-Ratelimit-Reset")
            if r is not None:
                r = int(r)
                self.reset[provider] = r if r > 10 ** 9 else time.time() + r
        except (TypeError, ValueError):
            pass


LIMITER = _Limiter()


def _cache_file(provider, params):
    k = json.dumps([provider, API[provider], {k: v for k, v in params.items() if k != "key"}], sort_keys=True)
    return os.path.join(CACHE_DIR, hashlib.sha1(k.encode("utf-8")).hexdigest() + ".json")


def _cache_get(path):
    try:
        if time.time() - os.path.getmtime(path) < CACHE_TTL:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return None


def _cache_put(path, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def _get_json(provider, url, headers, timeout=20):
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": UA}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            LIMITER.update(provider, r.headers)
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        LIMITER.update(provider, e.headers or {})
        if e.code in (401, 403) or "invalid" in body.lower() and "key" in body.lower():
            raise StockError("bad_key", f"{provider} rejected the API key (HTTP {e.code}). Check it in "
                                        "Stock → API keys.")
        if e.code == 429:
            raise StockError("rate", f"{provider}: request limit reached (HTTP 429). Try again later.")
        raise StockError("http", f"{provider} error HTTP {e.code}: {body or e.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise StockError("network", f"Can't reach {provider} ({getattr(e, 'reason', e)}). Check your internet "
                                    "connection - local media import still works.")
    except ValueError:
        raise StockError("http", f"{provider} sent a response AutoEditor couldn't read.")


# ============================================================================ search
def _norm_pexels(v):
    files = [{"url": f.get("link"), "w": f.get("width") or 0, "h": f.get("height") or 0,
              "quality": f.get("quality") or "", "type": f.get("file_type") or ""}
             for f in v.get("video_files") or [] if f.get("link")]
    user = v.get("user") or {}
    return {"provider": "Pexels", "id": str(v.get("id")), "page_url": v.get("url") or "",
            "thumb": v.get("image") or "", "duration": float(v.get("duration") or 0),
            "width": int(v.get("width") or 0), "height": int(v.get("height") or 0),
            "creator": user.get("name") or "unknown", "creator_url": user.get("url") or "",
            "files": files, "tags": ""}


def _norm_pixabay(v):
    vids = v.get("videos") or {}
    files, thumb = [], ""
    for q in ("large", "medium", "small", "tiny"):
        f = vids.get(q) or {}
        if f.get("url"):
            files.append({"url": f["url"], "w": f.get("width") or 0, "h": f.get("height") or 0,
                          "quality": q, "type": "video/mp4", "size": f.get("size") or 0})
        if not thumb and f.get("thumbnail"):
            thumb = f["thumbnail"]
    big = max(files, key=lambda f: f["w"] * f["h"]) if files else {"w": 0, "h": 0}
    uid = v.get("user_id")
    user = v.get("user") or "unknown"
    return {"provider": "Pixabay", "id": str(v.get("id")), "page_url": v.get("pageURL") or "",
            "thumb": thumb, "duration": float(v.get("duration") or 0), "width": big["w"], "height": big["h"],
            "creator": user, "creator_url": f"https://pixabay.com/users/{user}-{uid}/" if uid else "",
            "files": files, "tags": v.get("tags") or ""}


def search(provider, query, orientation="landscape", page=1, per_page=20, key=None, use_cache=True):
    """-> (results, info). orientation: landscape | portrait | square | any."""
    query = " ".join((query or "").split())
    if not query:
        raise StockError("empty", "Type something to search for (e.g. 'storm clouds' or 'empty street at night').")
    if provider not in PROVIDERS:
        raise StockError("http", f"Unknown provider {provider}")
    key = key if key is not None else get_key(provider)
    if not key:
        raise StockError("no_key", f"No {provider} API key yet - add your free key in Stock → API keys "
                                   f"({SIGNUP[provider]}).")
    if provider == "Pexels":
        params = {"query": query, "page": page, "per_page": min(80, per_page)}
        if orientation in ("landscape", "portrait", "square"):
            params["orientation"] = orientation
        url = API["Pexels"] + "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": key}
    else:
        params = {"key": key, "q": query[:100], "page": page, "per_page": max(3, min(200, per_page)),
                  "safesearch": "true", "video_type": "all"}
        url = API["Pixabay"] + "?" + urllib.parse.urlencode(params)
        headers = {}
    cf = _cache_file(provider, params)
    data = _cache_get(cf) if use_cache else None
    cached = data is not None
    if data is None:
        LIMITER.check(provider)
        data = _get_json(provider, url, headers)
        _cache_put(cf, data)
    if provider == "Pexels":
        res = [_norm_pexels(v) for v in data.get("videos") or []]
        total = int(data.get("total_results") or len(res))
    else:
        res = [_norm_pixabay(v) for v in data.get("hits") or []]
        total = int(data.get("totalHits") or len(res))
    res = [r for r in res if r["files"]]
    left = LIMITER.remaining.get(provider)
    return res, {"total": total, "cached": cached, "remaining": left, "page": page}


def pick_file(result, max_h=1080, fmt="16:9"):
    """Best file to download: the largest that fits within 1080p (for the chosen shape), mp4 preferred."""
    files = [f for f in result["files"] if "mp4" in (f.get("type") or "mp4") or f["url"].lower().endswith(".mp4")]
    files = files or result["files"]
    limit = 1920 * 1080 if fmt != "4k" else 3840 * 2160
    ok = [f for f in files if f["w"] * f["h"] <= limit and min(f["w"], f["h"]) <= max_h] or files
    return max(ok, key=lambda f: f["w"] * f["h"])


def preview_file(result):
    """Smallest file - for streaming a quick preview (not saved)."""
    files = [f for f in result["files"] if f["w"]] or result["files"]
    return min(files, key=lambda f: f["w"] * f["h"] or 10 ** 9)


# ============================================================================ download
def stock_folder(project):
    """Downloads go into the project's media folder, in a sub-folder the scene planner ignores
    (so stock clips never change the order of your own media); falls back to the project /
    voiceover folder."""
    base = project.get("media_dir") or ""
    if not base or not os.path.isdir(base):
        base = os.path.dirname(project.get("voiceover") or "") or ""
    if not base:
        return None
    return os.path.join(base, "_autoedit_stock")


def safe_name(result, f):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (result.get("tags") or result.get("page_url", "").rstrip("/")
                                          .split("/")[-1] or "clip"))[:40].strip("-").lower() or "clip"
    return f"{result['provider'].lower()}_{result['id']}_{slug}_{f['h'] or 'x'}p.mp4"


def download(result, dest_dir, fmt="16:9", progress=None, should_stop=None, timeout=30):
    """Download ONE chosen clip. Writes to a .part file and renames when complete; a failed or
    cancelled download leaves nothing behind. Returns (path, asset_info)."""
    f = pick_file(result, fmt=fmt)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, safe_name(result, f))
    info = asset_info(result, f)
    if os.path.isfile(path) and os.path.getsize(path) > 10_000:
        return path, info                      # already downloaded - reuse, no second download
    part = path + ".part"
    req = urllib.request.Request(f["url"], headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if ctype and not (ctype.startswith("video/") or "octet-stream" in ctype or "mp4" in ctype):
                raise StockError("bad_file", f"The provider sent '{ctype}' instead of a video.")
            got = 0
            while True:
                if should_stop and should_stop():
                    raise StockError("cancelled", "Download cancelled.")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got / total if total else 0.0, got)
        if total and got < total:
            raise StockError("network", f"Download stopped early ({got // 1024} of {total // 1024} KB).")
        if got < 10_000:
            raise StockError("bad_file", "The downloaded file is too small to be a video.")
        os.replace(part, path)
    except StockError:
        _rm(part)
        raise
    except urllib.error.HTTPError as e:
        _rm(part)
        raise StockError("http", f"Download failed: HTTP {e.code} from {result['provider']}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _rm(part)
        raise StockError("network", f"Download failed ({getattr(e, 'reason', e)}). Nothing was saved.")
    return path, info


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def asset_info(result, f=None):
    """Source + license record kept in the project (project['stock_assets'][path])."""
    p = result["provider"]
    return {"provider": p, "id": result["id"], "page_url": result["page_url"],
            "creator": result["creator"], "creator_url": result["creator_url"],
            "license": f"{p} License", "license_url": LICENSE[p],
            "width": (f or {}).get("w") or result["width"], "height": (f or {}).get("h") or result["height"],
            "duration": result["duration"], "downloaded": time.strftime("%Y-%m-%d %H:%M")}


def credit_line(info):
    return f"Video by {info['creator']} on {info['provider']}: {info['page_url']}"


def credits_text(project, used_only=True):
    """Credits block for the video description (Pexels asks for creator credit where possible)."""
    assets = project.get("stock_assets") or {}
    if not assets:
        return ""
    used = {os.path.normcase(os.path.abspath(c["media"])) for c in (project.get("scenes") or []) +
            (project.get("broll") or []) if c.get("media")}
    lines = [credit_line(i) for p, i in assets.items()
             if not used_only or os.path.normcase(os.path.abspath(p)) in used]
    return ("Stock footage:\n" + "\n".join(lines)) if lines else ""


# ============================================================================ search ideas
STOP = set("""a an the and or but if then than so of to in on at by for with from into onto over under
about after before during while as is was were be been being are am it its it's this that these those
there here he she they them his her their our we you your i me my mine not no yes very just also only
can could would should will shall may might must do does did done have has had having up down out
off again further once more most other some such own same too s t don now when where why how all any
both each few many much one two three first last new old said says say like get got make made""".split())
PEOPLE = set("""man men woman women boy girl child children kid kids people person crowd face faces portrait
politician president soldier soldiers worker workers banker bankers family families police officer
doctor nurse teacher student students king queen leader businessman businesswoman
businessmen businesswomen couple selfie""".split())
CONCEPTS = {  # narration word -> footage idea (broad, people-free where possible)
    "storm": "storm clouds timelapse", "rain": "rain on window", "war": "smoke over battlefield",
    "battle": "smoke over battlefield", "money": "old banknotes close up", "bank": "bank building exterior",
    "banks": "bank building exterior", "financial": "stock market ticker", "market": "stock market ticker",
    "gold": "gold bars", "night": "city at night", "city": "city skyline aerial", "river": "river aerial",
    "sea": "ocean waves", "ocean": "ocean waves", "fire": "fire flames close up", "secret": "dark hallway",
    "meeting": "empty conference room", "island": "island aerial", "train": "old train station",
    "railway": "railway tracks", "ship": "ship at sea", "letter": "old letter writing", "law": "courthouse columns",
    "government": "capitol building", "congress": "capitol building", "election": "ballot box",
    "church": "old church interior", "forest": "dark forest fog", "fear": "dark corridor",
    "death": "candle flame dark", "time": "clock ticking", "history": "old documents",
    "newspaper": "old newspaper printing", "map": "old map", "road": "empty road",
    "night,": "city at night", "winter": "snow falling", "snow": "snow falling", "desert": "desert dunes",
    "mountain": "mountain fog", "factory": "industrial factory", "oil": "oil field pump",
    "power": "power lines sunset", "crash": "falling papers", "crisis": "empty street",
}
IDEAS = {
    "Locations": ["city skyline aerial", "empty street at night", "old town alley", "countryside road",
                  "harbor at dawn", "abandoned building", "government building exterior", "train station"],
    "Objects": ["old documents", "vintage typewriter", "pocket watch", "old map", "candle flame",
                "banknotes close up", "telephone vintage", "chess pieces"],
    "Weather & nature": ["storm clouds timelapse", "fog over forest", "rain on window", "lightning",
                         "sunset clouds", "snow falling", "ocean waves", "wind in grass"],
    "Silhouettes & moods": ["silhouette walking", "shadow on wall", "dark corridor", "figure in fog",
                            "hands close up", "backlit window", "lonely road at night", "flickering light"],
    "Textures & backgrounds": ["paper texture", "film grain", "dust particles", "smoke background",
                               "bokeh lights", "ink in water", "abstract dark background"],
}


def suggest_terms(text, category="", max_n=6):
    """Editable search ideas from a scene's narration. Skips names of people and prefers places,
    objects, weather and moods (safer than footage of identifiable people)."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", text or "")
    out = []
    lower = [w.lower().strip("'") for w in words]
    for w in lower:
        if w in CONCEPTS and CONCEPTS[w] not in out:
            out.append(CONCEPTS[w])
    # plain content words (not names, not people words)
    names = {w for i, w in enumerate(words) if w[0].isupper() and i > 0 and words[i - 1][-1] not in ".!?"}
    for w, lw in zip(words, lower):
        if len(out) >= max_n:
            break
        if lw in STOP or lw in PEOPLE or len(lw) < 4 or w in names or lw.endswith(("ed", "ing", "ly")):
            continue
        if lw not in " ".join(out):
            out.append(lw)
    mood = {"Horror": "dark fog", "Long Story History": "old documents", "Vox Style": "map close up",
            "Documentary": "archival film texture"}.get(category)
    if mood and mood not in out:
        out.append(mood)
    return out[:max_n]


def people_warning(query):
    """Non-empty when the search will likely return identifiable people."""
    q = (query or "").lower().split()
    caps = [w for w in (query or "").split() if w[:1].isupper() and len(w) > 2]
    if any(w in PEOPLE for w in q) or len(caps) >= 2:
        return ("This search may return identifiable people. Don't use real people's footage in a way that "
                "suggests they took part in, or are connected to, events in your story - it can be misleading "
                "or defamatory and the providers' licenses forbid it. Prefer places, objects, silhouettes or "
                "hands.")
    return ""
