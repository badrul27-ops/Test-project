"""Voiceover -> word timestamps (Faster-Whisper, runs on CPU)."""
import difflib
import re


def transcribe(audio_path, model_size="small", language=None, progress=None, log=print):
    from faster_whisper import WhisperModel

    log(f"Loading Whisper model '{model_size}' (first run downloads it)...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    log("Transcribing voiceover...")
    segments, info = model.transcribe(
        audio_path,
        language=(language or None),
        word_timestamps=True,
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    duration = float(info.duration)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append({"w": text, "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
        if progress and duration > 0:
            progress(min(1.0, seg.end / duration))
    log(f"Transcribed {len(words)} words, {duration:.1f}s, language={info.language}")
    return words, duration


_norm_re = re.compile(r"[^\w']+", re.UNICODE)


def _norm(t):
    return _norm_re.sub("", t.lower())


def apply_script(words, script_text):
    """Replace Whisper's words with the exact words from your script, keeping timings.

    Useful for names and spellings Whisper gets wrong. Uses sequence alignment;
    where the script and audio differ, timings are spread evenly.
    """
    script = script_text.split()
    if not script or not words:
        return words
    a = [_norm(w["w"]) for w in words]
    b = [_norm(t) for t in script]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                w = dict(words[i1 + k])
                w["w"] = script[j1 + k]
                out.append(w)
        elif tag == "replace":
            s, e = words[i1]["s"], words[i2 - 1]["e"]
            n = j2 - j1
            for k in range(n):
                out.append({"w": script[j1 + k],
                            "s": round(s + (e - s) * k / n, 3),
                            "e": round(s + (e - s) * (k + 1) / n, 3)})
        elif tag == "delete":
            # spoken but not in script (ad-lib) -> keep Whisper's words
            out.extend(dict(w) for w in words[i1:i2])
        elif tag == "insert":
            # in script but not heard -> skip (no audio to time it to)
            pass
    return out
