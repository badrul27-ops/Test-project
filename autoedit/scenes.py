"""Split the voiceover into scenes and plan visuals / motion / transitions."""
import os
import random
import re

from .presets import MOTION_LEVELS, MOTIONS
from .util import is_video

SENT_END = (".", "!", "?", "…", '."', '?"', '!"')
CLAUSE_END = (",", ";", ":", "—", "-")


def _candidates(words, duration):
    """Possible cut points between words: (time, score, gap)."""
    cands = []
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        gap = max(0.0, b["s"] - a["e"])
        t = a["e"] + gap / 2
        score = min(gap, 1.5) * 1.6 - 0.15
        if a["w"].endswith(SENT_END):
            score += 0.45
        elif a["w"].endswith(CLAUSE_END):
            score += 0.12
        cands.append((t, score, gap))
    return cands


def split_scenes(words, duration, preset, n_scenes=None):
    """Return list of (start, end) covering 0..duration.

    auto (n_scenes=None): choose cuts at pauses / sentence ends, each scene
    between min_scene and max_scene seconds.
    fixed (n_scenes=N): exactly N scenes, as even as possible, at the best pauses.
    """
    if not words or duration <= 0:
        return [(0.0, max(duration, 0.1))]
    cands = _candidates(words, duration)
    # keep only real pauses / punctuation when there are plenty (much faster)
    need = (n_scenes or duration / max(preset["min_scene"], 0.5)) * 3
    strong = [c for c in cands if c[2] >= 0.12 or c[1] > 0.2]
    if len(strong) >= need:
        cands = strong
    pts = [(0.0, 0.0)] + [(t, s) for t, s, _ in cands] + [(duration, 0.0)]
    P = len(pts)
    NEG = -1e18

    if n_scenes is None:
        lo, hi = preset["min_scene"], preset["max_scene"]
        if duration <= lo * 1.5:
            return [(0.0, duration)]
        best = [NEG] * P
        prev = [-1] * P
        best[0] = 0.0
        for j in range(1, P):
            tj = pts[j][0]
            for i in range(j - 1, -1, -1):
                d = tj - pts[i][0]
                if d > hi * 1.6:
                    break
                if best[i] == NEG:
                    continue
                pen = 0.0
                if d < lo:
                    pen = 3.0 * (lo - d)
                elif d > hi:
                    pen = 2.0 * (d - hi)
                v = best[i] + pts[j][1] - pen
                if v > best[j]:
                    best[j], prev[j] = v, i
        if best[P - 1] == NEG:  # no feasible path (very long silence) -> even split
            n = max(1, round(duration / ((lo + hi) / 2)))
            return [(duration * k / n, duration * (k + 1) / n) for k in range(n)]
        cuts, j = [], P - 1
        while j > 0:
            cuts.append(j)
            j = prev[j]
        idx = [0] + cuts[::-1]
    else:
        n = max(1, int(n_scenes))
        if n == 1:
            return [(0.0, duration)]
        if P - 2 < n - 1:  # not enough word gaps -> even split
            return [(duration * k / n, duration * (k + 1) / n) for k in range(n)]
        avg = duration / n
        win = max(avg * 3.0, preset["max_scene"] * 1.5)
        # dp[k][j]: best score with k scenes ending at point j
        dp = [[NEG] * P for _ in range(n + 1)]
        bk = [[-1] * P for _ in range(n + 1)]
        dp[0][0] = 0.0
        for k in range(1, n + 1):
            for j in range(1, P):
                if k == n and j != P - 1:
                    continue
                if k < n and j == P - 1:
                    continue
                tj = pts[j][0]
                row = dp[k - 1]
                for i in range(j - 1, -1, -1):
                    d = tj - pts[i][0]
                    if d > win:
                        break
                    if row[i] == NEG:
                        continue
                    dev = (d - avg) / avg
                    v = row[i] + pts[j][1] - 1.2 * dev * dev
                    if v > dp[k][j]:
                        dp[k][j], bk[k][j] = v, i
        if dp[n][P - 1] == NEG:
            return [(duration * k / n, duration * (k + 1) / n) for k in range(n)]
        idx, j = [], P - 1
        for k in range(n, 0, -1):
            idx.append(j)
            j = bk[k][j]
        idx = [0] + idx[::-1]

    times = [pts[i][0] for i in idx]
    return [(round(times[k], 3), round(times[k + 1], 3)) for k in range(len(times) - 1)]


def _pause_before(words, t):
    """Length of the silence around cut time t."""
    best = 0.0
    for i in range(len(words) - 1):
        if words[i]["e"] <= t <= words[i + 1]["s"]:
            return words[i + 1]["s"] - words[i]["e"]
    return best


def _weighted(rng, weights, avoid=None):
    items = [(k, v) for k, v in weights.items() if v > 0 and k != avoid] or list(weights.items())
    total = sum(v for _, v in items)
    r = rng.uniform(0, total)
    acc = 0
    for k, v in items:
        acc += v
        if r <= acc:
            return k
    return items[-1][0]


_TS_RE = re.compile(r"^\s*(\d{1,2})[-_.:](\d{1,2})(?:[-_:](\d{1,2}))?(?:[,.](\d{1,3}))?(?=$|[^\d])")


def parse_timestamp(path):
    """'0-03.png' -> 3.0, '1-15.jpg' -> 75.0, '01-02-03.png' -> 3723.0, '0-03.5.png' -> 3.5."""
    name = os.path.splitext(os.path.basename(path))[0]
    m = _TS_RE.match(name)
    if not m:
        return None
    a, b, c, frac = m.groups()
    if c is not None:
        t = int(a) * 3600 + int(b) * 60 + int(c)
    else:
        t = int(a) * 60 + int(b)
    if frac:
        t += float("0." + frac)
    return float(t)


def timestamp_spans(words, duration, media, snap=True, log=print):
    """Scenes from timestamp-named files. Returns (spans, media_in_order)."""
    items = []
    bad = []
    for m in media:
        t = parse_timestamp(m)
        if t is None:
            bad.append(os.path.basename(m))
        else:
            items.append((t, m))
    if bad:
        log(f"Skipped {len(bad)} files without a timestamp name (e.g. {bad[0]}). Expected names like 0-03.png")
    if not items:
        raise ValueError("No timestamp-named images found (expected names like 0-00.png, 0-03.png, 1-15.png).")
    items.sort(key=lambda x: x[0])
    times = [t for t, _ in items]
    files = [m for _, m in items]
    over = [t for t in times if t >= duration]
    if over:
        log(f"Warning: {len(over)} image timestamps are after the end of the voiceover ({duration:.1f}s) and are ignored.")
        keep = [i for i, t in enumerate(times) if t < duration]
        times, files = [times[i] for i in keep], [files[i] for i in keep]
    if not times:
        raise ValueError(f"All image timestamps are after the end of the voiceover ({duration:.1f}s) - "
                         "check the file names (expected names like 0-00.png, 0-03.png, 1-15.png).")
    times[0] = 0.0
    gaps = [(words[i]["e"] + words[i + 1]["s"]) / 2 for i in range(len(words) - 1)] if snap and words else []
    kept_t, kept_f, dropped = [0.0], [files[0]], []
    for t, f in zip(times[1:], files[1:]):
        near = min(gaps, key=lambda g: abs(g - t)) if gaps else t
        nt = near if abs(near - t) <= 0.6 else t
        if nt <= kept_t[-1] + 0.2:
            nt = t
        if nt <= kept_t[-1] + 0.05:             # same time as (or before) the previous cut: no room on screen
            dropped.append(os.path.basename(f))
            continue
        kept_t.append(round(nt, 3))
        kept_f.append(f)
    if dropped:
        log(f"Warning: {len(dropped)} image(s) NOT used because their timestamp is the same as (or too close to) "
            f"the previous image's: {', '.join(dropped[:5])}")
    times, files = kept_t, kept_f
    spans = [(times[i], times[i + 1] if i + 1 < len(times) else duration) for i in range(len(times))]
    lens = sorted(b - a for a, b in spans)
    med = lens[len(lens) // 2] if lens else 0
    last = spans[-1][1] - spans[-1][0]
    if len(spans) > 3 and med > 0 and last > max(6.0, med * 4):
        log(f"Warning: the last image stays on screen for {last:.1f}s while most images last ~{med:.1f}s. "
            f"Your timestamps probably don't cover the whole voiceover - check the file names, or use "
            f"'One scene per media file' mode instead.")
    return spans, files


def plan(words, duration, preset, media, mode="auto", seed=7, log=print, snap=True, allow_repeat=False):
    """Create the scene list for a project.
    allow_repeat=False (default): never show an image twice - if the narration has more natural
    pauses than you have images, the scenes are made longer so each image is used once, in order."""
    rng = random.Random(seed)
    if mode == "timestamps":
        spans, media = timestamp_spans(words, duration, media, snap=snap, log=log)
    else:
        n = len(media) if (mode == "match_media" and media) else None
        spans = split_scenes(words, duration, preset, n_scenes=n)
    if media and mode == "auto" and len(media) < len(spans) and not allow_repeat:
        n0 = len(spans)
        spans = split_scenes(words, duration, preset, n_scenes=len(media))
        log(f"Only {len(media)} images for {n0} natural pauses: each image is used ONCE, in file order "
            f"(scenes ~{duration / max(1, len(spans)):.1f}s). Tick 'Allow images to repeat' to repeat instead, "
            f"or use Match images to narration / directions to say exactly where each image goes.")
    if media and mode == "auto":
        if len(media) < len(spans):
            log(f"Note: {len(spans)} scenes but only {len(media)} media files - images repeat (you allowed "
                f"repeating). Untick 'Allow images to repeat' to use each image once.")
        elif len(media) > len(spans):
            log(f"Note: {len(media)} media files but {len(spans)} scenes - the last "
                f"{len(media) - len(spans)} files are unused.")
    scenes = []
    last_motion = None
    for k, (s, e) in enumerate(spans):
        text = " ".join(w["w"] for w in words if s <= (w["s"] + w["e"]) / 2 < e)
        m = media[k % len(media)] if media else ""
        motion = "none" if is_video(m) else _weighted(rng, preset["motions"], avoid=last_motion)
        if motion != "none":
            last_motion = motion
        scenes.append({"start": s, "end": e, "text": text, "media": m,
                       "motion": motion, "transition": "cut",
                       "zoom": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0})
    # transitions (out of each scene); long pause = section change
    for k in range(len(scenes) - 1):
        pause = _pause_before(words, scenes[k]["end"])
        if pause >= preset["section_pause"]:
            scenes[k]["transition"] = preset["section_transition"]
        else:
            scenes[k]["transition"] = _weighted(rng, preset["transitions"])
    return scenes


def reassign_motion(scene, preset, rng=None):
    rng = rng or random.Random()
    if is_video(scene["media"]):
        scene["motion"] = "none"
    elif scene.get("motion") in (None, "none"):
        scene["motion"] = _weighted(rng, preset["motions"])
    return scene


def media_label(path):
    return os.path.basename(path) if path else "(none)"


ALL_MOTIONS = MOTIONS + ["subtle", "none"]


def scene_text(words, s, e):
    return " ".join(w["w"] for w in words if s <= (w["s"] + w["e"]) / 2 < e)


def snap_points(words):
    return [round((words[i]["e"] + words[i + 1]["s"]) / 2, 3) for i in range(len(words) - 1)]


def reassign_transitions(scenes, preset, words, seed=7):
    """Re-roll every scene's transition from the preset mix (section breaks keep the
    preset's section transition)."""
    rng = random.Random(seed)
    for k in range(len(scenes) - 1):
        if _pause_before(words, scenes[k]["end"]) >= preset["section_pause"]:
            scenes[k]["transition"] = preset["section_transition"]
        else:
            scenes[k]["transition"] = _weighted(rng, preset["transitions"])
    if scenes:
        scenes[-1]["transition"] = "cut"
    return scenes


PANS = ("pan_left", "pan_right", "pan_up", "pan_down")


def apply_motion_level(scenes, preset, level="Medium", seed=7):
    """Automatic motion for still images, varied per scene.

    level: None (still) / Subtle / Medium / Dynamic - scales the preset's movement.
    Each image gets its own amount (random +-, and longer scenes move a bit further but
    no faster), long scenes prefer gentle pans over big zooms, and video clips are left
    alone (they play as recorded unless you pick a motion for them yourself)."""
    factor = MOTION_LEVELS.get(level, 1.0)
    rng = random.Random(seed * 13 + 5)
    for s in scenes:
        m0 = s.get("media", "")
        if not m0 or is_video(m0):
            continue
        if factor <= 0:
            s["motion"], s["motion_amount"] = "none", None
            continue
        L = max(0.1, s["end"] - s["start"])
        m = s.get("motion")
        if m in (None, "none"):
            m = _weighted(rng, preset["motions"])
        if level == "Subtle" and (m in PANS or m == "zoom_out") and rng.random() < 0.35:
            m = "subtle"
        elif level == "Dynamic" and m in ("static", "subtle"):
            m = rng.choice(["zoom_in", "zoom_out", "pan_left", "pan_right"])
        if L > 8.0 and m in ("zoom_in", "zoom_out") and rng.random() < 0.4:
            # Dynamic never falls back to near-still motion
            m = rng.choice(["pan_left", "pan_right"] + ([] if level == "Dynamic" else ["subtle"]))
        base = float(preset["zoom_amount"]) * rng.uniform(0.75, 1.3)
        lf = min(1.35, max(0.7, (L / 4.0) ** 0.5))
        s["motion"] = m
        s["motion_amount"] = round(min(0.45, max(0.02, base * factor * lf)), 3)
    return scenes
