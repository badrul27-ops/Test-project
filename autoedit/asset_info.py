"""What each image / video shows - from information that actually exists, never guessed.

Sources, in priority order (each description records where it came from):
  1. imported prompts / scene map (project['asset_meta'], keyed by a stable asset ID)
  2. a sidecar file next to the media: 001.txt / 001.json / 001.prompt
  3. text stored inside the file: PNG text chunks (prompt / parameters / Description / Title),
     JPEG EXIF ImageDescription / XPComment / XPTitle / UserComment
  4. nothing -> 'not described' (matching then falls back to file order and says so)

AutoEditor does NOT look at the pixels to recognise people, objects or places - that needs an AI
model that isn't included. Local checks only measure orientation, size, brightness and, for
videos, shot changes (usable time ranges).

Asset IDs come from the file's CONTENT (size + first/last 64 KB), so renaming a file keeps its ID,
its imported description and its scene assignment.
"""
import csv
import hashlib
import io
import json
import os
import re

from .util import APP_DIR, is_video

CACHE = os.path.join(APP_DIR, "cache", "assets")


def asset_id(path):
    try:
        size = os.path.getsize(path)
        h = hashlib.sha1(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(65536))
            if size > 131072:
                f.seek(-65536, 2)
                h.update(f.read(65536))
        return "a_" + h.hexdigest()[:14]
    except OSError:
        return None


def _sidecar(path):
    stem = os.path.splitext(path)[0]
    for ext in (".txt", ".prompt", ".json"):
        p = stem + ext
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    txt = f.read().strip()
            except OSError:
                continue
            if ext == ".json":
                try:
                    d = json.loads(txt)
                    if isinstance(d, dict):
                        return {k: d.get(k) for k in ("prompt", "description", "subject", "action", "setting",
                                                      "mood", "scene_id") if d.get(k)}, "sidecar " + os.path.basename(p)
                except ValueError:
                    pass
            return {"description": txt[:2000]}, "sidecar " + os.path.basename(p)
    return None, None


def _embedded(path):
    try:
        from PIL import Image
        im = Image.open(path)
        info = dict(im.info or {})
        for k in ("prompt", "parameters", "Description", "description", "Title", "Comment", "comment"):
            v = info.get(k)
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            if isinstance(v, str) and len(v.strip()) > 3:
                return {"description": v.strip()[:2000]}, f"text stored in the file ({k})"
        exif = im.getexif() if hasattr(im, "getexif") else None
        if exif:
            for tag in (270, 0x9C9C, 0x9C9B, 0x9286):     # ImageDescription, XPComment, XPTitle, UserComment
                v = exif.get(tag)
                if isinstance(v, bytes):
                    v = v.decode("utf-16-le" if tag in (0x9C9C, 0x9C9B) else "utf-8", "replace")
                    v = v.replace("\x00", "").replace("ASCII", "").strip()
                if isinstance(v, str) and len(v.strip()) > 3:
                    return {"description": v.strip()[:2000]}, "EXIF description"
    except Exception:
        pass
    return None, None


def _local_facts(path):
    facts = {"kind": "video" if is_video(path) else "image"}
    try:
        if is_video(path):
            from .mediainfo import probe
            i = probe(path) or {}
            facts.update(w=i.get("display_w"), h=i.get("display_h"), duration=i.get("duration"))
        else:
            from PIL import Image, ImageOps
            im = Image.open(path)
            w, h = im.size
            if (im.getexif() or {}).get(274) in (5, 6, 7, 8):
                w, h = h, w
            facts.update(w=w, h=h)
            small = ImageOps.exif_transpose(im).convert("L").resize((32, 32))
            facts["brightness"] = round(sum(small.getdata()) / 1024 / 255, 2)
        if facts.get("w") and facts.get("h"):
            r = facts["w"] / facts["h"]
            facts["orientation"] = "landscape" if r > 1.1 else ("portrait" if r < 0.9 else "square")
    except Exception:
        pass
    return facts


def shots(path, max_seconds=600, thresh=28.0):
    """Video: usable time ranges between shot changes (frame-difference cuts), sampled ~4x per second."""
    import cv2
    from .visuals import readable_video
    cap = cv2.VideoCapture(readable_video(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps / 4)))
    prev, t_last, cuts, i = None, 0.0, [0.0], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, fr = cap.retrieve()
            if not ok:
                break
            g = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (64, 36)).astype("float32")
            t = i / fps
            if prev is not None and float(abs(g - prev).mean()) > thresh and t - cuts[-1] > 0.6:
                cuts.append(round(t, 2))
            prev, t_last = g, t
            if t > max_seconds:
                break
        i += 1
    cap.release()
    end = round(max(t_last, cuts[-1]) + step / fps, 2)
    return [(a, b) for a, b in zip(cuts, cuts[1:] + [end]) if b - a > 0.2]


def describe(path, project=None):
    """-> {'id','path','text','source','uncertain', facts...}. Cached by asset ID."""
    aid = asset_id(path)
    meta = ((project or {}).get("asset_meta") or {}).get(aid) if aid else None
    d = {"id": aid, "path": path}
    d.update(_local_facts(path))
    if meta:
        d.update({k: v for k, v in meta.items() if k != "source"})
        d["source"] = meta.get("source", "imported prompts")
    else:
        found, src = _sidecar(path)          # sidecar files are read fresh every time (they can be edited)
        if found:
            d.update(found)
            d["source"] = src
        elif not is_video(path):
            cache = os.path.join(CACHE, f"{aid}.json") if aid else None   # keyed by content -> auto-invalidates
            c = None
            if cache and os.path.isfile(cache):
                try:
                    with open(cache, encoding="utf-8") as f:
                        c = json.load(f)
                except (OSError, ValueError):
                    c = None
            if c is None:
                found, src = _embedded(path)
                c = dict(found or {}, source=src if found else None)
                if cache:
                    try:
                        os.makedirs(CACHE, exist_ok=True)
                        with open(cache, "w", encoding="utf-8") as f:
                            json.dump(c, f)
                    except OSError:
                        pass
            d.update(c)
        else:
            d["source"] = None
    d["path"] = path
    text = " ".join(str(d.get(k) or "") for k in ("subject", "action", "setting", "mood", "prompt", "description"))
    d["text"] = " ".join(text.split())
    d["uncertain"] = not d["text"]
    return d


# ---------------------------------------------------------------------------- prompt import
def _rows_from_file(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw = f.read()
    if path.lower().endswith(".json"):
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("scenes") or data.get("assets") or data.get("items") or []
        return [r for r in data if isinstance(r, dict)]
    if path.lower().endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(raw)))
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]
    # plain text: one prompt per paragraph (or per line), in image order
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if len(paras) <= 1:
        paras = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return [{"order": i + 1, "prompt": re.sub(r"^\s*(?:scene|shot|image|img|prompt)?\s*#?\d+\s*[:.)\-–]\s*", "",
                                                p, flags=re.I)} for i, p in enumerate(paras)]


def import_prompts(project, path, media):
    """Attach image-generation prompts / descriptions / scene IDs to assets (by file name, by number,
    or by order for plain text). Stored by stable asset ID. -> (n_attached, problems)."""
    from .cues import media_index
    rows = _rows_from_file(path)
    by_num, by_name = media_index(media)
    ordered = [by_num[n][0] for n in sorted(by_num)] + [m for m in media if m not in
                                                      {x for v in by_num.values() for x in v}]
    meta = project.setdefault("asset_meta", {})
    problems, n = [], 0
    seen = set()
    for i, r in enumerate(rows):
        ref = str(r.get("asset_filename") or r.get("filename") or r.get("file") or r.get("image") or
                  r.get("asset") or "").strip()
        target = None
        if ref:
            target = by_name.get(os.path.basename(ref).lower()) or by_name.get(os.path.splitext(
                os.path.basename(ref).lower())[0])
            if target is None and ref.isdigit():
                hits = by_num.get(int(ref))
                target = hits[0] if hits else None
                if hits and len(hits) > 1:
                    problems.append(f"row {i + 1}: {len(hits)} files numbered {ref} - used {os.path.basename(hits[0])}")
            if target is None:
                problems.append(f"row {i + 1}: no media file '{ref}'")
                continue
        elif r.get("order"):
            k = int(r["order"]) - 1
            target = ordered[k] if 0 <= k < len(ordered) else None
            if target is None:
                problems.append(f"prompt {k + 1}: more prompts than media files")
                continue
        else:
            problems.append(f"row {i + 1}: no asset_filename")
            continue
        if target in seen:
            problems.append(f"row {i + 1}: {os.path.basename(target)} listed twice - later row used")
        seen.add(target)
        aid = asset_id(target)
        meta[aid] = {"prompt": r.get("prompt") or r.get("description") or "",
                     "subject": r.get("subject") or "", "action": r.get("action") or "",
                     "setting": r.get("setting") or "", "mood": r.get("mood") or "",
                     "scene_id": str(r.get("scene_id") or r.get("order") or ""),
                     "file_at_import": os.path.basename(target), "source": "imported prompts"}
        n += 1
    return n, problems
