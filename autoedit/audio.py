"""Sound design: SFX discovery + planning, music ducked under the voice, mixed with
FFmpeg, then loudness-normalised (two-pass EBU R128) to a YouTube-friendly level.

SFX volume is matched to the voice: each effect's short-term peak loudness is
measured and set a fixed number of dB below the narration (per SFX level), so a
quiet typing clip and a loud boom both end up audible but under the voice.
"""
import json
import os
import random
import re
import subprocess
import wave

import numpy as np

from .presets import SFX_LEVELS
from .util import APP_DIR, AUDIO_EXT, ffmpeg_exe, popen_kwargs, run_ffmpeg

RULES_PATH = os.path.join(APP_DIR, "sfx_rules.json")
BUILTIN_SFX = os.path.join(APP_DIR, "sfx_library")

_DEFAULT_RULES = {
    "transitions": {"whip": "swoosh", "slide": "swipe", "zoom": "whoosh", "dip_black": "impact"},
    "overlays": {"hook": "bass_drop", "title": "hit_cinematic", "chapter": "impact", "label": "typing",
                 "lower_third": "swipe"},
    "pre_roll": ["riser", "reverse_whoosh"],
    "max_seconds": {"typing": 1.6},
    "gain_db": {},
    "keywords": {"impact": ["suddenly", "but then"], "typing": ["typed", "message"]},
}


def load_rules(path=None):
    """(rules, note). Always resolved from the app folder; malformed/missing files fall back
    to built-in defaults and say so."""
    path = path or RULES_PATH
    note = f"SFX rules: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        if not isinstance(r, dict):
            raise ValueError("top level is not an object")
    except FileNotFoundError:
        r, note = dict(_DEFAULT_RULES), f"SFX rules file not found ({path}) - using built-in defaults."
    except (ValueError, OSError) as e:
        r, note = dict(_DEFAULT_RULES), f"SFX rules file is invalid ({e}) - using built-in defaults."
    # v1 compatibility
    if "transitions" not in r:
        r["transitions"] = {"whip": r.get("transition", "whoosh"), "slide": r.get("transition", "whoosh"),
                            "zoom": r.get("transition", "whoosh"), "dip_black": r.get("section", "impact")}
    for k, v in _DEFAULT_RULES.items():
        r.setdefault(k, type(v)())
    return r, note


def _scan(folder):
    lib = {}
    if not folder or not os.path.isdir(folder):
        return lib
    for root, _, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() not in AUDIO_EXT or f.startswith("."):
                continue
            p = os.path.join(root, f)
            if os.path.normpath(root) != os.path.normpath(folder):
                cat = os.path.basename(root).lower()
            else:
                cat = re.split(r"[_\-\s\d]+", os.path.splitext(f)[0].lower())[0]
            if cat:
                lib.setdefault(cat, []).append(p)
    for v in lib.values():
        v.sort()
    return lib


def scan_sfx(sfx_dir, builtin=True):
    """{category: [files]}. Your folder first; missing categories come from the built-in library."""
    lib = _scan(BUILTIN_SFX) if builtin else {}
    lib.update(_scan(sfx_dir))
    return lib


def library_report(sfx_dir, builtin=True):
    """(ok, [lines]) describing where effects come from - shown in the Audio panel and log."""
    lines = []
    user = _scan(sfx_dir) if sfx_dir else {}
    if sfx_dir:
        if not os.path.isdir(sfx_dir):
            lines.append(f"Your SFX folder does not exist: {sfx_dir}")
        elif not user:
            lines.append(f"Your SFX folder has no usable audio ({', '.join(sorted(AUDIO_EXT))}) "
                         f"in category sub-folders: {sfx_dir}")
        else:
            lines.append(f"Your SFX folder: {len(user)} categories, {sum(map(len, user.values()))} files")
    bi = _scan(BUILTIN_SFX) if builtin else {}
    if builtin:
        lines.append(f"Built-in library: {len(bi)} categories, {sum(map(len, bi.values()))} files"
                     if bi else f"Built-in library missing or empty: {BUILTIN_SFX}")
    lib = dict(bi)
    lib.update(user)
    if not lib:
        lines.append("No sound effects available - choose an SFX folder or turn on the built-in library.")
    return bool(lib), lines, lib


def _norm(t):
    return re.sub(r"[^\w']", "", t.lower())


def _decode_mono(path, sr=16000):
    try:
        raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
                              "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=120, **popen_kwargs()).stdout
        return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    except Exception:
        return np.zeros(0, np.float32)


def _window_db(x, sr=16000, win=0.05):
    n = int(sr * win)
    if len(x) < n:
        return np.array([-90.0])
    k = len(x) // n
    frames = x[:k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)


_REF_CACHE = {}


def sfx_ref_db(path):
    """Short-term peak loudness of an effect (max 50 ms RMS, dBFS)."""
    key = (path, os.path.getmtime(path) if os.path.exists(path) else 0)
    if key not in _REF_CACHE:
        w = _window_db(_decode_mono(path))
        _REF_CACHE[key] = float(w.max()) if len(w) else -90.0
    return _REF_CACHE[key]


def voice_ref_db(path):
    """Typical loud level of the narration (90th percentile of 50 ms RMS over speech)."""
    key = ("voice", path, os.path.getmtime(path) if os.path.exists(path) else 0)
    if key not in _REF_CACHE:
        w = _window_db(_decode_mono(path))
        w = w[w > -50]
        _REF_CACHE[key] = float(np.percentile(w, 90)) if len(w) else -18.0
    return _REF_CACHE[key]


def _seconds(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        x = _decode_mono(path, 8000)
        return len(x) / 8000.0 if len(x) else None


def plan_sfx(scenes, words, sfx_dir, level, seed=7, overlays=(), builtin=True, log=None):
    """Plan effects. Returns [{'t','file','cat','gain','rule_gain','dur','enabled','reason'}] sorted by time.
    gain = your manual adjustment (dB, starts at 0)."""
    say = log or (lambda m: None)
    cfg = SFX_LEVELS.get(level)
    if not cfg:
        say("SFX level is 'None' - no sound effects.")
        return []
    ok, lines, lib = library_report(sfx_dir, builtin)
    for ln in lines:
        say(ln)
    if not ok:
        return []
    rules, note = load_rules()
    say(note)
    rng = random.Random(seed)
    cand = []  # (time, category, priority, reason)
    tmap = rules["transitions"]
    for k in range(len(scenes) - 1):
        tr = scenes[k].get("transition", "cut")
        cat = tmap.get(tr) or ""
        if cat:
            t = scenes[k]["end"]
            cand.append((t - (0.05 if tr == "dip_black" else 0.2), cat, 3 if tr == "dip_black" else 2,
                         f"{tr} transition after scene {k + 1}"))
    omap = rules["overlays"]
    for ov in overlays or ():
        cat = omap.get(ov.get("type", ""), "")
        if cat:
            cand.append((float(ov["start"]), cat, 4, f"{ov.get('type')} card \"{ov.get('text', '')[:24]}\""))
    if cfg["keywords"]:
        seq = [_norm(w["w"]) for w in words]
        for cat, phrases in rules["keywords"].items():
            for ph in phrases:
                toks = [_norm(x) for x in str(ph).split()]
                n = len(toks)
                if not n or not all(toks):
                    continue
                last_hit = -1e9
                for i in range(len(seq) - n + 1):
                    if seq[i:i + n] == toks and words[i]["s"] - last_hit >= 30.0:  # same phrase: once per 30 s
                        cand.append((words[i]["s"], cat, 1, f'keyword "{ph}"'))
                        last_hit = words[i]["s"]
    missing = sorted({c[1] for c in cand if c[1] not in lib})
    if missing:
        say(f"No sounds for categories: {', '.join(missing)} (add folders with these names to use them).")
    cand = [c for c in cand if c[1] in lib]
    cand.sort(key=lambda c: (-c[2], c[0]))
    chosen = []
    per_cat = {}
    cap = cfg.get("per_min", 6)
    for t, cat, pr, why in cand:
        spacing = cfg["spacing"] if pr < 4 else 1.0   # text cards always get their sound
        if any(abs(t - c["t"]) < spacing for c in chosen):
            continue
        if pr == 1 and any(abs(t - u) < cfg.get("repeat_gap", 8.0) for u in per_cat.get(cat, ())):  # same sound too soon
            continue
        if pr < 4 and sum(1 for c in chosen if abs(c["t"] - t) < 30.0) >= cap / 2.0 + 0.5:
            continue
        chosen.append({"t": t, "cat": cat, "pr": pr, "reason": why})
        per_cat.setdefault(cat, []).append(t)
    last = {}
    out = []
    for c in sorted(chosen, key=lambda c: c["t"]):
        files = lib[c["cat"]]
        opts = [f for f in files if f != last.get(c["cat"])] or files
        f = rng.choice(opts)
        last[c["cat"]] = f
        t = c["t"]
        if c["cat"] in rules["pre_roll"]:     # risers end ON the moment
            d = _seconds(f)
            if d:
                t -= d
        if t < 0:
            continue
        out.append({"t": round(t, 3), "file": f, "cat": c["cat"], "gain": 0.0,
                    "rule_gain": float(rules["gain_db"].get(c["cat"], 0.0)),
                    "dur": rules["max_seconds"].get(c["cat"]), "enabled": True, "reason": c["reason"]})
    if not out:
        say("No sound effects matched (no transition/text-card/keyword triggers in this video). "
            "Try SFX level 'Cinematic', whip/slide/zoom transitions, text cards, or add keywords "
            "to sfx_rules.json.")
    return out


def event_volumes(events, voice_path, level):
    """Absolute FFmpeg volume (dB) per event so each effect sits offset_db under the voice."""
    cfg = SFX_LEVELS.get(level) or {"offset_db": -7.0}
    vref = voice_ref_db(voice_path)
    for e in events:
        target = vref + cfg.get("offset_db", -7.0) + float(e.get("rule_gain", 0) or 0) + float(e.get("gain", 0) or 0)
        e["vol_db"] = round(max(-40.0, min(24.0, target - sfx_ref_db(e["file"]))), 1)
    return events


def _measure_loudness(path, target):
    """First loudnorm pass -> measured values dict (or None)."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostats", "-i", path, "-af",
           f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs())
    txt = r.stderr.decode("utf-8", "replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def event_filters(e):
    """Source trim, length, fades and pan of one event (filters before volume + delay).
    trim_in: seconds skipped at the start of the file; dur: seconds used; fade_in / fade_out: seconds;
    pan: -1 (left) .. 0 (centre) .. +1 (right), constant power."""
    f = ""
    ti = float(e.get("trim_in") or 0)
    d = float(e["dur"]) if e.get("dur") else None
    if ti > 0 or d:
        f += f"atrim=start={ti:.3f}" + (f":end={ti + d:.3f}" if d else "") + ",asetpts=PTS-STARTPTS,"
    fi = float(e.get("fade_in") or 0)
    fo = e.get("fade_out")
    if fo is None and d:
        fo = 0.25                       # older planned effects: short fade at the cut-off point
    fo = float(fo or 0)
    if fi > 0:
        f += f"afade=t=in:st=0:d={fi:.3f},"
    if fo > 0:
        length = d if d else max(0.0, (_seconds(e["file"]) or 0) - ti)
        if length > 0:
            f += f"afade=t=out:st={max(0.0, length - fo):.3f}:d={min(fo, length):.3f},"
    p = max(-1.0, min(1.0, float(e.get("pan") or 0)))
    if abs(p) > 1e-3:
        import math
        a = (p + 1) * math.pi / 4
        gl, gr = math.cos(a) * math.sqrt(2), math.sin(a) * math.sqrt(2)
        f += f"pan=stereo|c0={gl:.4f}*c0|c1={gr:.4f}*c1,"
    return f


def peak_db(path):
    """Highest sample level (dBFS) of a file, via FFmpeg volumedetect."""
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostats", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs())
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr.decode("utf-8", "replace"))
    return float(m.group(1)) if m and m.group(1) != "-inf" else -90.0


def mix(voice, duration, out_wav, music=None, music_db=-20, sfx_events=(), sfx_db=-13, log_path=None,
        loudness=-14.0, log=print, voice_db=0.0, music_fade_in=1.5, music_fade_out=3.0, duck=True,
        duck_ratio=6.0, duck_release=350.0):
    total = duration + 0.4
    args = ["-i", voice]
    idx = 1
    music_i = None
    if music and os.path.isfile(music):
        args += ["-stream_loop", "-1", "-i", music]
        music_i = idx
        idx += 1
    files = []
    for e in sfx_events:
        if e["file"] not in files:
            files.append(e["file"])
    file_i = {}
    for f in files:
        args += ["-i", f]
        file_i[f] = idx
        idx += 1

    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    vvol = f",volume={float(voice_db):.1f}dB" if abs(float(voice_db)) > 0.05 else ""
    chains = [f"[0:a]{fmt}{vvol},apad=whole_dur={total:.3f}[vo]"]
    mix_in = []
    if music_i is not None:
        fi, fo = max(0.0, float(music_fade_in)), max(0.0, float(music_fade_out))
        fades = (f",afade=t=in:d={fi:.2f}" if fi > 0 else "") + \
                (f",afade=t=out:st={max(0.0, total - fo):.3f}:d={fo:.2f}" if fo > 0 else "")
        chains.append(f"[{music_i}:a]{fmt},atrim=0:{total:.3f},volume={music_db}dB{fades}[m0]")
        if duck:
            chains.append("[vo]asplit=2[v1][vkey]")
            chains.append(f"[m0][vkey]sidechaincompress=threshold=0.02:ratio={duck_ratio:g}:attack=20:"
                          f"release={duck_release:g}[mus]")
            mix_in += ["[v1]", "[mus]"]
        else:
            mix_in += ["[vo]", "[m0]"]
    else:
        mix_in.append("[vo]")
    for f in files:
        evs = [e for e in sfx_events if e["file"] == f]
        i = file_i[f]
        n = len(evs)
        if n == 1:
            chains.append(f"[{i}:a]{fmt}[sf{i}_0]")
        else:
            outs = "".join(f"[sf{i}_{j}]" for j in range(n))
            chains.append(f"[{i}:a]{fmt},asplit={n}{outs}")
        for j, e in enumerate(evs):
            ms = int(round(e["t"] * 1000))
            vol = e["vol_db"] if "vol_db" in e else sfx_db + float(e.get("gain", 0) or 0)
            chains.append(f"[sf{i}_{j}]{event_filters(e)}volume={vol:.2f}dB,adelay={ms}|{ms}[d{i}_{j}]")
            mix_in.append(f"[d{i}_{j}]")
    if len(mix_in) > 1:
        chains.append("".join(mix_in) + f"amix=inputs={len(mix_in)}:duration=first:"
                      f"dropout_transition=0:normalize=0,atrim=0:{total:.3f}[out]")
    else:
        chains.append(f"[vo]atrim=0:{total:.3f}[out]")
    raw = out_wav if loudness is None else out_wav + ".raw.wav"
    args += ["-filter_complex", ";".join(chains), "-map", "[out]",
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", raw]
    run_ffmpeg(args, log_path)
    if loudness is not None:
        m = _measure_loudness(raw, loudness)
        if m and m.get("input_i") not in (None, "-inf"):
            af = (f"loudnorm=I={loudness}:TP=-1.5:LRA=11:measured_I={m['input_i']}:"
                  f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
                  f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
            log(f"Loudness: {float(m['input_i']):.1f} LUFS -> {loudness:.0f} LUFS")
        else:
            af = "alimiter=limit=0.95"
        run_ffmpeg(["-i", raw, "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", out_wav], log_path)
        try:
            os.remove(raw)
        except OSError:
            pass
    else:
        pk = peak_db(out_wav)           # loudness normalisation off: still never deliver clipped audio
        if pk > -0.1:
            tmp = out_wav + ".lim.wav"
            run_ffmpeg(["-i", out_wav, "-af", "alimiter=limit=0.95:level=disabled", "-ar", "48000", "-ac", "2",
                        "-c:a", "pcm_s16le", tmp], log_path)
            os.replace(tmp, out_wav)
            log(f"The mix peaked at {pk:+.1f} dBFS (clipping) - a limiter was applied. Lower music / SFX levels "
                "to avoid it.")
    return total
