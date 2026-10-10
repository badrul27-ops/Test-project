"""Editorial tools: hook builder, chapters + YouTube description, style variety,
and the pre-render quality check."""
import copy
import os
import random
import re
import zlib
from collections import Counter

from .overlays import effective_overlays
from .scenes import scene_text
from .util import fmt_time, is_video


# ------------------------------------------------------------------ variety

def video_seed(project):
    """Stable per-video seed so every video gets its own motion/transition mix."""
    key = (os.path.basename(project.get("voiceover") or "") + "|" + (project.get("name") or "")).encode("utf-8")
    return zlib.crc32(key) % 1_000_000


def vary_preset(preset, seed):
    """Copy of a preset with its weights and motion strength nudged for this video."""
    rng = random.Random(seed * 31 + 7)
    p = copy.deepcopy(preset)
    for key in ("motions", "transitions"):
        p[key] = {k: max(1.0, v * rng.uniform(0.55, 1.5)) for k, v in p[key].items()}
    p["zoom_amount"] = preset["zoom_amount"] * rng.uniform(0.85, 1.2)
    p["transition_duration"] = preset["transition_duration"] * rng.uniform(0.85, 1.15)
    return p


def jitter_motion_amounts(scenes, preset, seed):
    rng = random.Random(seed * 17 + 3)
    for s in scenes:
        if not is_video(s.get("media", "")) and s.get("motion_amount") in (None, ""):
            s["motion_amount"] = round(preset["zoom_amount"] * rng.uniform(0.75, 1.3), 3)


# ------------------------------------------------------------------ hook

def apply_hook_cuts(project, window=10.0, max_len=2.6):
    """Faster cutting in the opening seconds: long image scenes are split at word
    gaps into shorter shots with alternating, punchier camera moves."""
    words = project["words"]
    out = []
    moves = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
    mi = 0
    for s in project["scenes"]:
        if s["start"] >= window or is_video(s.get("media", "")) or (s["end"] - s["start"]) <= max_len:
            out.append(s)
            continue
        gaps = [(words[i]["e"] + words[i + 1]["s"]) / 2 for i in range(len(words) - 1)]
        gaps = [g for g in gaps if s["start"] + 1.0 < g < min(s["end"], window + 2) - 1.0]
        cuts, last = [], s["start"]
        for g in gaps:
            if g - last >= max_len * 0.75:
                cuts.append(g)
                last = g
        if not cuts:
            out.append(s)
            continue
        bounds = [s["start"]] + cuts + [s["end"]]
        for j in range(len(bounds) - 1):
            ns = dict(s)
            ns["start"], ns["end"] = round(bounds[j], 3), round(bounds[j + 1], 3)
            ns["text"] = scene_text(words, ns["start"], ns["end"])
            if j < len(bounds) - 2:
                ns["transition"] = "cut"
            ns["motion"] = moves[mi % len(moves)]
            ns["motion_amount"] = 0.22
            ns["zoom"] = [1.0, 1.12, 1.05, 1.18][mi % 4]
            mi += 1
            out.append(ns)
    project["scenes"] = out
    return project


# ------------------------------------------------------------------ chapters

def _title_from(text, n=6):
    w = re.sub(r"[^\w\s'-]", "", text).split()
    t = " ".join(w[:n])
    return t[:1].upper() + t[1:] if t else "Chapter"


def auto_chapters(project, min_gap=None, max_n=12):
    """Pick chapter starts at section breaks (long pauses / dip-to-black)."""
    scenes, words = project["scenes"], project["words"]
    dur = project["duration"] or (scenes[-1]["end"] if scenes else 0)
    if not scenes or dur < 40:
        return []
    min_gap = min_gap or max(30.0, dur / (max_n + 1))
    gaps = {}
    for i in range(len(words) - 1):
        gaps[round((words[i]["e"] + words[i + 1]["s"]) / 2, 2)] = words[i + 1]["s"] - words[i]["e"]
    cands = []
    for k in range(1, len(scenes)):
        t = scenes[k]["start"]
        pause = max([g for m, g in gaps.items() if abs(m - t) < 0.15] or [0])
        score = pause + (1.0 if scenes[k - 1].get("transition") == "dip_black" else 0)
        cands.append((score, t, k))
    cands.sort(reverse=True)
    chosen = [0.0]
    for score, t, k in cands:
        if len(chosen) >= max_n:
            break
        if all(abs(t - c) >= min_gap for c in chosen) and dur - t >= 10:
            chosen.append(t)
    chosen.sort()
    out = []
    for i, t in enumerate(chosen):
        end = chosen[i + 1] if i + 1 < len(chosen) else dur
        txt = scene_text(words, t, min(end, t + 6))
        out.append({"start": round(t, 2), "title": "Intro" if i == 0 else _title_from(txt)})
    return out


def chapter_cards(chapters, dur=2.5):
    """Overlay entries for chapter cards (skips the 0:00 intro)."""
    ovs = []
    for i, c in enumerate(chapters):
        if c["start"] <= 0.5:
            continue
        ovs.append({"type": "chapter", "start": c["start"], "end": c["start"] + dur,
                    "text": c["title"], "sub": f"Chapter {i}"})
    return ovs


def yt_time(t):
    t = int(t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def description_text(project, chapters):
    lines = []
    warn = []
    if chapters:
        if chapters[0]["start"] > 0.5:
            chapters = [{"start": 0.0, "title": "Intro"}] + chapters
        lines.append("Chapters:")
        for c in chapters:
            lines.append(f"{yt_time(c['start'])} {c['title']}")
        if len(chapters) < 3:
            warn.append("YouTube needs at least 3 chapters for them to show up.")
        for a, b in zip(chapters, chapters[1:] + [{"start": project["duration"]}]):
            if b["start"] - a["start"] < 10:
                warn.append(f"Chapter '{a['title']}' is shorter than 10 s - YouTube may ignore the chapters.")
    return "\n".join(lines), warn


# ------------------------------------------------------------------ quality check

def quality_report(project):
    """List of (level, message, scene_index or None) + a 0-100 score.
    level: 'fix' (should fix), 'warn', 'tip', 'good'."""
    st = project.get("settings", {})
    scenes, words = project.get("scenes", []), project.get("words", [])
    dur = project.get("duration") or 0
    items = []

    def add(level, msg, k=None):
        items.append((level, msg, k))

    if not scenes:
        return [("fix", "Analyze the voiceover first.", None)], 0

    missing = [k for k, s in enumerate(scenes) if not s.get("media") or not os.path.isfile(s["media"])]
    for k in missing[:5]:
        add("fix", f"Scene {k + 1} has no visual (it will be black).", k)
    if len(missing) > 5:
        add("fix", f"...and {len(missing) - 5} more scenes without visuals.")

    # v7: matching status + per-scene warnings from the planner
    fb = [k for k, s in enumerate(scenes) if s.get("status") == "fallback" and not s.get("accepted")]
    if fb:
        add("warn", f"{len(fb)} scene(s) use a FALLBACK visual that wasn't matched to the script "
                    f"(first: scene {fb[0] + 1}). Replace them or press 'Accept fallback' in Review scenes.", fb[0])
    short = [k for k, s in enumerate(scenes) if any("clip is" in w for w in s.get("warnings") or [])]
    if short:
        add("warn", f"{len(short)} video clip(s) are shorter than their scene (first: scene {short[0] + 1}) - "
                    "choose freeze / loop / slow, trim the scene or pick another clip.", short[0])
    lowres = [k for k, s in enumerate(scenes) if any("low resolution" in w for w in s.get("warnings") or [])]
    if lowres:
        add("tip", f"{len(lowres)} visual(s) are low resolution for the chosen export size (first: scene "
                   f"{lowres[0] + 1}) - they'll look soft.", lowres[0])
    if (project.get("alignment") or {}).get("status") == "estimated":
        add("fix", "Scene timing is ESTIMATED from the script (speech recognition failed) - cuts may not match "
                   "the voice. Check every scene or re-run Analyze.")
    chk = [i for i in project.get("script_check") or [] if i.get("kind") in ("missing", "extra", "changed")]
    if chk:
        add("tip", f"{len(chk)} passage(s) differ between your script and the recording - see the Script tab.")

    # v7.1 editing instructions: fatal problems block the export, warnings need a decision
    from .instructions import validate
    for i in validate(project)[:12]:
        k = next((n for n, s in enumerate(scenes) if s.get("id") == i.get("scene")), None)
        add("fix" if i["level"] == "fatal" else "warn",
            f"Instruction {i.get('scene') or ''}: {i['msg']}" if i.get("scene") else i["msg"], k)

    long_static = [(k, s["end"] - s["start"]) for k, s in enumerate(scenes)
                   if not is_video(s.get("media", "")) and s["end"] - s["start"] > 9.0]
    for k, d in long_static[:6]:
        add("warn", f"Scene {k + 1} holds one image for {d:.1f}s - split it or add another visual "
                    f"(long static shots feel slow).", k)
    if len(long_static) > 6:
        add("warn", f"...and {len(long_static) - 6} more long static scenes.")

    uses = Counter(s.get("media") for s in scenes if s.get("media"))
    reused = [m for m, n in uses.items() if n > 1]
    # adjacent re-use (hook splits) is fine; count non-adjacent
    bad = []
    for m in reused:
        idx = [k for k, s in enumerate(scenes) if s.get("media") == m]
        if any(b - a > 1 for a, b in zip(idx, idx[1:])):
            bad.append((m, idx))
    if bad:
        m, idx = bad[0]
        add("warn", f"{len(bad)} visuals are repeated in different parts of the video "
                    f"(e.g. {os.path.basename(m)} in scenes {', '.join(str(i + 1) for i in idx[:4])}). "
                    f"Repeats read as low-effort - add more visuals.", idx[0])

    motions = [s.get("motion") for s in scenes if not is_video(s.get("media", ""))]
    if motions:
        run, best, prev = 0, 0, None
        for m in motions:
            run = run + 1 if m == prev else 1
            best, prev = max(best, run), m
        if best >= 4:
            add("warn", f"The same camera move repeats {best} times in a row - vary it in the Clip panel.")
        top, n = Counter(motions).most_common(1)[0]
        if len(motions) >= 8 and n / len(motions) > 0.55:
            add("warn", f"{n / len(motions):.0%} of images use '{top}' - the edit may feel templated.")
    trans = [s.get("transition") for s in scenes[:-1]]
    nc = [t for t in trans if t != "cut"]
    if len(nc) >= 6:
        t, n = Counter(nc).most_common(1)[0]
        if n / len(nc) > 0.8:
            add("tip", f"Almost every transition is '{t}'. Mix in a few others, or more straight cuts.")

    lens = [s["end"] - s["start"] for s in scenes]
    avg = sum(lens) / len(lens)
    if avg > 7.0:
        add("warn", f"Average shot length is {avg:.1f}s - that is slow for YouTube. More visuals or "
                    f"'Auto - split at pauses' mode will tighten the pacing.")
    elif dur > 120 and avg < 1.6:
        add("warn", f"Average shot length is {avg:.1f}s - very fast for a long video; it can feel frantic.")
    else:
        add("good", f"Pacing: average shot {avg:.1f}s.")

    opening = [s for s in scenes if s["start"] < 7]
    if opening and (max(s["end"] - s["start"] for s in opening) > 4.5):
        add("warn", "The first 10 seconds have a long shot - turn on 'Hook: faster cuts' to grab attention.", 0)
    ovs = effective_overlays(project)
    if not any(o["type"] == "hook" for o in ovs):
        add("tip", "No hook title. A short, bold question or promise on screen in the first seconds helps retention.")
    else:
        add("good", "Hook title set.")

    if not st.get("captions", True):
        add("tip", "Captions are off. Many viewers watch without sound - captions keep them watching.")
    else:
        add("good", "Captions on.")
    if not project.get("script") and not (project.get("script_text") or "").strip():
        add("tip", "No script file - names and numbers in captions may be misspelled. Add your script .txt.")

    if not any(is_video(s.get("media", "")) for s in scenes) and dur > 180:
        add("tip", "Only still images. A few real video clips (B-roll, archive footage, screen recordings) "
                   "make the video feel less like a slideshow.")
    if not [o for o in ovs if o["type"] != "hook"]:
        add("tip", "No text cards yet. Place/date labels, lower thirds and chapter cards add your own "
                   "editorial layer (Text & Chapters tab).")
    else:
        add("good", f"{len(ovs)} text cards / labels.")
    if dur > 360 and not project.get("chapters"):
        add("tip", "Long video without chapters - use 'Auto chapters' and paste them into your description.")

    if float(st.get("music_db", -20)) > -15 and project.get("music"):
        add("warn", "Music is loud (above -15 dB) - it may fight the voice.")

    # dead air
    for i in range(len(words) - 1):
        g = words[i + 1]["s"] - words[i]["e"]
        if g > 2.5:
            add("warn", f"{g:.1f}s of silence at {fmt_time(words[i]['e'])} - trim the voiceover or add a "
                        f"title card there.")
            break

    penalty = {"fix": 15, "warn": 6, "tip": 2, "good": 0}
    score = max(0, 100 - sum(penalty[l] for l, _, _ in items))
    order = {"fix": 0, "warn": 1, "tip": 2, "good": 3}
    items.sort(key=lambda x: order[x[0]])
    return items, score
