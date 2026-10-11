"""Render a project to MP4: visuals + motion + transitions + captions, then encode."""
import os
import re
import shutil
import subprocess
import time

from . import audio
from .engine import Compositor
from .overlays import effective_overlays
from .presets import SFX_LEVELS
from .util import ffmpeg_exe, popen_kwargs, run_ffmpeg


class Cancelled(Exception):
    pass


_hw_ok = {}


def _hw_works(kind, W=1280, H=720, q="Standard"):
    """Real 1-second test encode with the SAME options at the export size: the encoder may be listed
    by FFmpeg but still fail on this PC (no GPU, old driver, size limits)."""
    from .exportcfg import hw_args
    key = (kind, W, H, q)
    if key not in _hw_ok:
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               f"color=c=gray:s={W}x{H}:r=30:d=1"] + hw_args(kind, q) + ["-f", "null", "-"]
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30, **popen_kwargs())
            _hw_ok[key] = r.returncode == 0
        except Exception:
            _hw_ok[key] = False
    return _hw_ok[key]


def video_codec_args(choice, q="Standard", log=print, W=1280, H=720):
    """-> (ffmpeg args, kind) kind: 'cpu' | 'nvenc' | 'qsv' | 'amf'. Hardware is only used after a real
    test encode succeeds; otherwise the CPU (x264) is used."""
    from .exportcfg import HW_NAMES, hw_args, x264_args
    if isinstance(q, dict):            # very old callers passed a QUALITY dict
        q = "Standard"
    q = q if q in ("Draft", "Standard", "High") else "Standard"
    choice = choice or "Auto"
    want = []
    if choice.startswith("Auto"):
        want = ["nvenc", "qsv", "amf"]
    elif "NVENC" in choice or "NVIDIA" in choice:
        want = ["nvenc"]
    elif "QuickSync" in choice:
        want = ["qsv"]
    elif "AMF" in choice:
        want = ["amf"]
    for w in want:
        if _hw_works(w, W, H, q):
            log(f"Encoder: GPU ({HW_NAMES[w]}) - quality {q}")
            return hw_args(w, q), w
    if want and not choice.startswith("Auto"):
        log(f"{choice} isn't available on this PC (test encode failed) - using CPU (x264).")
    log(f"Encoder: CPU (x264) - quality {q}")
    return x264_args(q), "cpu"


def plan_project_sfx(project, log=print):
    """Plan AUTOMATIC effects for review and store them (editable) in project['sfx_events'].
    Effects from editing instructions (they have a 'source') are kept as they are."""
    st = project["settings"]
    explicit = [e for e in project.get("sfx_events") or [] if e.get("source")]
    mode = sfx_mode(st)
    if mode in ("none", "explicit_only"):
        log(f"Sound effects guideline: {mode.replace('_', ' ')} - no automatic effects are planned.")
        ev = []
    else:
        log(f"SFX level: {st.get('sfx_level', 'Normal')} | your folder: {project.get('sfx_dir') or '(none)'} | "
            f"built-in library: {'on' if st.get('builtin_sfx', True) else 'off'}")
        ev = audio.plan_sfx(project["scenes"], project["words"], project.get("sfx_dir"),
                            st.get("sfx_level", "Normal"), seed=st.get("seed", 7),
                            overlays=effective_overlays(project), builtin=st.get("builtin_sfx", True), log=log)
        ev = _drop_in_silent_scenes(project, ev, log)
    project["sfx_events"] = sorted(ev + explicit, key=lambda e: e["t"])
    project["sfx_planned"] = True
    return project["sfx_events"]


def sfx_mode(st):
    """Project guideline for sound effects: auto | explicit_only | none (older projects: auto, and the
    old 'SFX level: None' still means no automatic effects)."""
    m = st.get("sfx_mode") or "auto"
    return m if m in ("auto", "explicit_only", "none") else "auto"


def _drop_in_silent_scenes(project, events, log):
    quiet = [(s["start"], s["end"]) for s in project.get("scenes") or [] if s.get("sfx_mode") in ("none", "explicit")]
    if not quiet:
        return events
    keep = [e for e in events if not any(a <= e["t"] < b for a, b in quiet)]
    if len(keep) < len(events):
        log(f"{len(events) - len(keep)} automatic effect(s) removed from scenes whose instruction sets their own "
            "sound effects (or none).")
    return keep


def build_audio(project, work_dir, log=print):
    st = project["settings"]
    level = st.get("sfx_level", "Normal")
    explicit = [e for e in project.get("sfx_events") or [] if e.get("source")]
    mode = sfx_mode(st)
    if mode in ("none", "explicit_only") or level == "None" or not SFX_LEVELS.get(level):
        auto = []
        log("No automatic sound effects (" + (f"guideline: {mode.replace('_', ' ')}" if mode != "auto" else
                                                "SFX level is 'None'") + ").")
    elif project.get("sfx_planned") and isinstance(project.get("sfx_events"), list):
        auto = [e for e in project["sfx_events"] if not e.get("source")]
        log(f"Using your planned sound effects ({sum(1 for e in auto if e.get('enabled', True))} enabled).")
    else:
        auto = [e for e in plan_project_sfx(project, log) if not e.get("source")]
    auto = _drop_in_silent_scenes(project, auto, log)
    if explicit:
        log(f"Sound effects from editing instructions: {len(explicit)}"
            + (" (the project guideline says none, but these scenes ask for them explicitly)" if mode == "none" else ""))
    events = auto + explicit
    use = [dict(e) for e in events if e.get("enabled", True) and e.get("file") and os.path.isfile(e["file"])]
    gone = [e for e in events if e.get("enabled", True) and not (e.get("file") and os.path.isfile(e["file"]))]
    if gone:
        log(f"Warning: {len(gone)} planned effects point to missing files and were skipped "
            f"(e.g. {gone[0].get('file')}).")
    # B-roll clips with their own sound switched on (muted by default)
    from .mediainfo import probe
    for b in project.get("broll") or []:
        if b.get("audio_gain_db") is None or not b.get("media") or not os.path.isfile(b["media"]):
            continue
        info = probe(b["media"]) or {}
        if not info.get("acodec"):
            log(f"B-roll {os.path.basename(b['media'])}: sound is on, but the file has no audio track.")
            continue
        use.append({"t": b["start"], "file": b["media"], "trim_in": float(b.get("clip_in") or 0),
                    "dur": b["end"] - b["start"], "fade_in": 0.05, "fade_out": 0.1,
                    "vol_db": float(b["audio_gain_db"]), "cat": "b-roll audio", "reason": "B-roll source audio"})
    if use:
        vref = audio.voice_ref_db(project["voiceover"])
        rel = [e for e in use if e.get("rel_db") is not None]
        auto_use = [e for e in use if e.get("rel_db") is None and "vol_db" not in e]
        if auto_use:
            audio.event_volumes(auto_use, project["voiceover"], level if SFX_LEVELS.get(level) else "Normal")
        for e in rel:        # level RELATIVE TO THE NARRATION: effect's loud part sits rel_db under the voice's
            e["vol_db"] = round(max(-60.0, min(24.0, vref + float(e["rel_db"]) - audio.sfx_ref_db(e["file"]))), 2)
        sfx_db = float(st.get("sfx_db", 0.0) or 0.0)       # SFX track volume (Audio tab / A3 track)
        if sfx_db:
            for e in use:
                e["vol_db"] = round(max(-40.0, min(24.0, e["vol_db"] + sfx_db)), 1)
            log(f"SFX track volume {sfx_db:+.1f} dB applied to every effect.")
        lv = SFX_LEVELS.get(level)
        log(f"Sound effects in this render: {len(use)} (voice level {vref:.1f} dB"
            + (f", automatic effects set {lv['offset_db']:+.0f} dB relative to it" if lv else "") + "):")
        for e in use:
            log(f"  {e['t']:7.2f}s  {e['cat']:<14} {os.path.basename(e['file']):<22} vol {e['vol_db']:+.1f} dB  "
                f"<- {e.get('reason', '')}")
    lufs = st.get("loudness", -14.0)
    kw = dict(music=project.get("music") or None, music_db=st.get("music_db", -20),
              loudness=(None if lufs in (None, "", "off") else float(lufs)),
              voice_db=st.get("voice_db", 0.0), music_fade_in=st.get("music_fade_in", 1.5),
              music_fade_out=st.get("music_fade_out", 3.0), duck=st.get("music_duck", True),
              duck_ratio=max(2.0, min(10.0, float(st.get("duck_ratio", 6.0) or 6.0))),
              duck_release=max(250.0, min(1500.0, float(st.get("duck_release", 350.0) or 350.0))))
    # cache: identical audio inputs -> reuse the previous mix instead of re-mixing
    import hashlib
    import json as _json

    def stamp(pth):
        try:
            return [pth, os.path.getmtime(pth), os.path.getsize(pth)]
        except (OSError, TypeError):
            return [pth]
    key = _json.dumps({"voice": stamp(project["voiceover"]), "dur": project["duration"],
                       "music": stamp(kw["music"]) if kw["music"] else None,
                       "kw": {k: v for k, v in kw.items() if k != "music"},
                       "sfx": [[e["t"], stamp(e["file"]), e.get("vol_db"), e.get("dur"), e.get("trim_in"),
                                e.get("fade_in"), e.get("fade_out"), e.get("pan")] for e in use]},
                      sort_keys=True, default=str)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    out = os.path.join(work_dir, f"mix_{h}.wav")
    total = project["duration"] + 0.4
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        log("Audio unchanged since the last render - reusing the cached mix.")
        return out, total, use
    for old in [f for f in os.listdir(work_dir) if f.startswith("mix_") and f.endswith(".wav")]:
        try:
            os.remove(os.path.join(work_dir, old))
        except OSError:
            pass
    part = out[:-4] + ".part.wav"             # only a finished mix gets the cache name
    total = audio.mix(project["voiceover"], project["duration"], part, sfx_events=use,
                      log_path=os.path.join(work_dir, "ffmpeg_log.txt"), log=log, **kw)
    os.replace(part, out)
    return out, total, use


def _out_dir(project):
    return project.get("output_dir") or os.path.dirname(project["voiceover"])


def _encode(frame_fn, nframes, W, H, fps, audio_path, out_path, vcodec, log_path,
            progress=None, should_stop=None, label="", stats=None):
    """Pipe frames to FFmpeg. stats (dict) receives 'compose_s' (building frames) and 'write_s'
    (time blocked handing frames to the encoder = encoder is the bottleneck)."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if audio_path:
        cmd += ["-i", audio_path, "-map", "0:v", "-map", "1:a"]
    cmd += ["-vf", "setsar=1"] + vcodec + ["-r", str(fps)]
    if audio_path:
        # pad the sound so the VIDEO decides the length: every rendered frame is kept (no dropped last frame)
        cmd += ["-af", "apad", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", out_path]
    elog = open(log_path, "w", encoding="utf-8")
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=elog,
                           **popen_kwargs())
    t0 = time.time()
    tc = tw = 0.0
    try:
        for f in range(nframes):
            if should_stop and should_stop():
                raise Cancelled()
            a = time.time()
            data = frame_fn(f).tobytes()
            b = time.time()
            enc.stdin.write(data)
            tc += b - a
            tw += time.time() - b
            if progress and f % 10 == 0:
                el = time.time() - t0
                if f >= 30 and el > 2:
                    eta = el / (f + 1) * (nframes - f - 1)
                    msg = f"{label}frame {f + 1}/{nframes} - ~{int(eta // 60)}m{int(eta % 60):02d}s left"
                else:
                    msg = f"{label}frame {f + 1}/{nframes} - estimating time…"
                progress((f + 1) / nframes, msg)
        enc.stdin.close()
        rc = enc.wait()
        if rc != 0:
            raise RuntimeError(f"Encoder failed (exit {rc}) - see {log_path}")
    except BaseException as e:                 # any failure: stop the encoder and remove the partial file
        try:
            enc.kill()
        except Exception:
            pass
        enc.wait()
        try:
            os.remove(out_path)
        except OSError:
            pass
        if not isinstance(e, OSError):        # Cancelled, RuntimeError, or a bug while building frames
            raise
        raise RuntimeError(f"Encoder stopped: {e} - see {log_path}")
    finally:
        elog.close()
    if stats is not None:
        stats["compose_s"] = stats.get("compose_s", 0) + tc
        stats["write_s"] = stats.get("write_s", 0) + tw
    return time.time() - t0


def _safe_name(project):
    """The project name as a file name that works on Windows too (no : / \\ ? * etc.)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", project.get("name") or "").strip(" .") or "video"


def final_path(project, fmt, W, H, draft=False, overwrite=False):
    """Completed-video name. Never replaces an existing file unless overwrite=True ('name (2).mp4')."""
    tag = fmt.replace(":", "x")
    base = f"{_safe_name(project)}_{'DRAFT_' if draft else ''}{tag}_{W}x{H}"
    out = os.path.join(_out_dir(project), base + ".mp4")
    if overwrite or not os.path.exists(out):
        return out
    n = 2
    while os.path.exists(os.path.join(_out_dir(project), f"{base} ({n}).mp4")):
        n += 1
    return os.path.join(_out_dir(project), f"{base} ({n}).mp4")


def render(project, fmt="16:9", progress=None, should_stop=None, log=print, mix_path=None, total=None,
           draft=False, overwrite=False, stats=None):
    """Render ONE video in ONE aspect ratio. Written to a temporary file in _autoedit_work, verified
    (size, square pixels, frame rate, duration, audio), and only then moved to its final name."""
    from .exportcfg import dims
    from .mediainfo import check_export
    st = project["settings"]
    res = "Draft" if draft else st.get("resolution") or "Full HD"
    q = "Draft" if draft else st.get("enc_quality") or "Standard"
    W, H = dims(fmt, res)
    fps = int(st.get("fps", 30))
    stats = stats if stats is not None else {}
    out_dir = _out_dir(project)
    work = os.path.join(out_dir, "_autoedit_work")
    os.makedirs(work, exist_ok=True)
    if mix_path is None:
        log("Mixing audio...")
        ta = time.time()
        mix_path, total, _ = build_audio(project, work, log)
        stats["audio_s"] = stats.get("audio_s", 0) + time.time() - ta
    tag = fmt.replace(":", "x")
    out_path = final_path(project, fmt, W, H, draft, overwrite)
    tmp_path = os.path.join(work, f"rendering_{tag}_{os.getpid()}.mp4")
    nframes = int(round(total * fps))
    log(f"Rendering {'DRAFT ' if draft else ''}{fmt} {W}x{H} @ {fps}fps ({q}) - {nframes} frames, "
        f"{len(project['scenes'])} scenes")
    if draft:
        from .exportcfg import x264_args
        vcodec, kind = x264_args("Draft"), "cpu"
    else:
        vcodec, kind = video_codec_args(st.get("encoder"), q, log, W, H)
    comp = Compositor(project, W, H, fps, total=total, preview=False, log=log)
    try:
        try:
            el = _encode(lambda f: comp.frame_at(f / fps), nframes, W, H, fps, mix_path, tmp_path, vcodec,
                         os.path.join(work, f"encode_{tag}.log"), progress, should_stop, f"{fmt}: ", stats)
        except RuntimeError as e:
            if kind == "cpu":
                raise
            from .exportcfg import x264_args
            log(f"GPU encoder failed during the export ({e}) - retrying on the CPU (x264).")
            comp.close()
            comp = Compositor(project, W, H, fps, total=total, preview=False, log=log)
            stats.clear()
            el = _encode(lambda f: comp.frame_at(f / fps), nframes, W, H, fps, mix_path, tmp_path,
                         x264_args(q), os.path.join(work, f"encode_{tag}.log"), progress, should_stop,
                         f"{fmt}: ", stats)
            kind = "cpu"
    finally:
        comp.close()
    problems = check_export(tmp_path, W, H, fps, total)
    if problems:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise RuntimeError("The export didn't pass its checks: " + "; ".join(problems))
    os.replace(tmp_path, out_path)
    stats.update(encode_s=el, frames=nframes, encoder=kind, W=W, H=H, fps=fps)
    log(f"Done: {out_path}  ({el:.1f} s, {nframes / max(el, 1e-6):.1f} frames/s; building frames "
        f"{stats.get('compose_s', 0):.1f} s, waiting for the encoder {stats.get('write_s', 0):.1f} s)")
    log(f"Verified: {W}x{H}, square pixels, {fps} fps, H.264 + AAC, {total:.2f} s.")
    if progress:
        progress(1.0, f"{fmt}: done")
    return out_path


def preflight(project, log=print):
    """Stop BEFORE rendering if a visual file is gone (it would silently become black)."""
    gone = []
    for k, sc in enumerate(project.get("scenes") or []):
        m = sc.get("media")
        if m and not os.path.isfile(m):
            gone.append(f"scene {k + 1} ({sc.get('id') or ''}): {m}")
    for k, b in enumerate(project.get("broll") or []):
        if b.get("media") and not os.path.isfile(b["media"]):
            gone.append(f"V2 clip {k + 1}: {b['media']}")
    if gone:
        raise RuntimeError(f"{len(gone)} visual file(s) can't be found - nothing was exported. Relink or "
                           "replace them first:\n" + "\n".join(gone[:10]))
    empty = [k + 1 for k, sc in enumerate(project.get("scenes") or []) if not sc.get("media")]
    if empty:
        log(f"Note: {len(empty)} scene(s) have no visual and will be black: {empty[:12]}")
    if not project.get("voiceover") or not os.path.isfile(project["voiceover"]):
        raise RuntimeError("The voiceover file can't be found - nothing was exported.")
    from .instructions import validate
    fatal = [i for i in validate(project) if i["level"] == "fatal"]
    if fatal:
        raise RuntimeError(f"{len(fatal)} problem(s) must be fixed before export - nothing was exported:\n" +
                           "\n".join(f"{i.get('scene') or 'project'}: {i['msg']}" for i in fatal[:10]))


def render_all(project, progress=None, should_stop=None, log=print, draft=False, formats=None, overwrite=False,
               stats=None):
    """One export action = the chosen aspect ratio only (plus batch formats if batch export is on)."""
    from .exportcfg import export_formats
    from .timedplan import save_diagnostic_plan, timeline_revision
    st = project["settings"]
    preflight(project, log)
    rev = timeline_revision(project)
    log(f"Exporting timeline revision {rev} ({len(project.get('scenes') or [])} scenes)")
    try:                                       # what this export was made from - for checking afterwards
        work = os.path.join(_out_dir(project), "_autoedit_work")
        os.makedirs(work, exist_ok=True)
        save_diagnostic_plan(project, os.path.join(work, "last_export_plan.json"))
    except OSError as e:
        log(f"(Couldn't save the diagnostic plan: {e})")
    work = os.path.join(_out_dir(project), "_autoedit_work")
    os.makedirs(work, exist_ok=True)
    stats = stats if stats is not None else {}
    log("Mixing audio (voice + music + SFX)...")
    ta = time.time()
    mix_path, total, _ = build_audio(project, work, log)
    stats["audio_s"] = time.time() - ta
    outs = []
    fmts = formats or export_formats(st)
    t0 = time.time()
    for i, fmt in enumerate(fmts):
        def prog(p, msg, i=i):
            if progress:
                progress((i + p) / len(fmts), msg)
        s = {}
        outs.append(render(project, fmt, prog, should_stop, log, mix_path, total, draft=draft,
                           overwrite=overwrite, stats=s))
        stats.setdefault("per_format", []).append(s)
    stats["video_s"] = time.time() - t0
    log(f"{'Draft' if draft else 'Final'} export: {len(outs)} video(s) in {time.time() - t0:.1f} s "
        f"(+ audio {stats['audio_s']:.1f} s)")
    return outs


# ---------------------------------------------------------------- SRT + editor package

def _srt_time(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    ms = int(round((s - int(s)) * 1000))
    if ms == 1000:
        ms = 999
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def write_srt(words, path, max_words=7, max_chars=42):
    from .captions import make_chunks
    chunks = make_chunks(words, max_words, max_chars)
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(chunks, 1):
            text = " ".join(w["w"] for w in c["words"])
            f.write(f"{i}\n{_srt_time(c['start'])} --> {_srt_time(c['end'])}\n{text}\n\n")
    return path


PACKAGE_README = """AutoEditor - editor package
===========================
Use this to finish the video in CapCut, Premiere Pro, DaVinci Resolve or any editor.

clips/        one MP4 per scene, in order (motion + atmosphere already applied,
              no captions, no transitions). Exact scene lengths.
audio/        voiceover.wav  (your voice only)
              mix.wav        (voice + music + SFX, as used by AutoEditor)
captions.srt  captions with timings
scenes.csv    scene list: number, start, end, file, narration

CapCut (desktop):
 1. New project -> Import -> select ALL files in clips/ (they are numbered 001, 002...).
 2. Drag them to the timeline in order (select all -> drag; CapCut keeps the order
    they are selected/sorted in - check the first and last clip).
 3. Import audio/voiceover.wav (or mix.wav) and drop it on an audio track at 0:00.
 4. Captions: Text -> "Local captions" / "Import captions" -> captions.srt
    (menu names differ between CapCut versions).
Because every clip is exactly its scene length, the clips line up with the voice
when placed back to back from 0:00.
"""


def export_package(project, progress=None, should_stop=None, log=print):
    """Per-scene clips + audio + SRT for finishing in another editor."""
    st = project["settings"]
    from .exportcfg import dims, export_formats
    fmt = export_formats(st)[0]
    W, H = dims(fmt, st.get("resolution") or "Full HD")
    fps = int(st.get("fps", 30))
    preflight(project, log)
    out_dir = _out_dir(project)
    work = os.path.join(out_dir, "_autoedit_work")
    os.makedirs(work, exist_ok=True)
    pkg = os.path.join(out_dir, f"{_safe_name(project)}_edit_package_{fmt.replace(':', 'x')}")
    os.makedirs(os.path.join(pkg, "clips"), exist_ok=True)
    os.makedirs(os.path.join(pkg, "audio"), exist_ok=True)
    log(f"Exporting editor package to {pkg}")
    mix_path, total, _ = build_audio(project, work, log)
    shutil.copyfile(mix_path, os.path.join(pkg, "audio", "mix.wav"))  # (cached mix file name varies)
    run_ffmpeg(["-i", project["voiceover"], "-ar", "48000", "-ac", "2",
                os.path.join(pkg, "audio", "voiceover.wav")])
    write_srt(project["words"], os.path.join(pkg, "captions.srt"))
    with open(os.path.join(pkg, "README.txt"), "w", encoding="utf-8") as f:
        f.write(PACKAGE_README)
    scenes = project["scenes"]
    with open(os.path.join(pkg, "scenes.csv"), "w", encoding="utf-8-sig") as f:
        f.write("scene,start,end,duration,file,narration\n")
        for i, s in enumerate(scenes, 1):
            txt = s["text"].replace('"', "'")
            f.write(f'{i},{s["start"]:.3f},{s["end"]:.3f},{s["end"] - s["start"]:.3f},'
                    f'"{os.path.basename(s.get("media") or "")}","{txt}"\n')
    comp = Compositor(project, W, H, fps, total=total, preview=False, captions=False,
                      cuts_only=True, log=log)
    vcodec, _kind = video_codec_args(st.get("encoder"), st.get("enc_quality") or "Standard", log, W, H)
    # frame-exact cut points so clips add up to the full length
    bounds = [int(round(s["start"] * fps)) for s in scenes] + [int(total * fps)]
    bounds[0] = 0
    try:
        n = len(scenes)
        for k in range(n):
            f0, f1 = bounds[k], max(bounds[k] + 1, bounds[k + 1])
            out = os.path.join(pkg, "clips", f"{k + 1:03d}_scene.mp4")

            def prog(p, msg, k=k):
                if progress:
                    progress((k + p) / n, f"Clip {k + 1}/{n}")
            _encode(lambda f, f0=f0: comp.frame_at((f0 + f) / fps, captions=False), f1 - f0,
                    W, H, fps, None, out, vcodec, os.path.join(work, "encode_package.log"),
                    prog, should_stop)
    finally:
        comp.close()
    log(f"Package ready: {pkg}")
    if progress:
        progress(1.0, "Package done")
    return pkg
