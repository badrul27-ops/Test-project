"""Generate AutoEditor's built-in sound-effect library.

All sounds are synthesized from scratch (noise, oscillators, filters), so they
are original and free to use in your videos. Re-run to regenerate:
    python tools/make_sfx.py            (needs numpy + scipy)
"""
import os
import sys
import wave

import numpy as np
from scipy import signal

SR = 48000
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sfx_library")


def t_axis(d):
    return np.arange(int(SR * d)) / SR


def env(n, a=0.01, r=0.2, shape=1.0):
    """attack/release envelope (seconds)."""
    e = np.ones(n)
    na, nr = max(1, int(a * SR)), max(1, int(r * SR))
    na, nr = min(na, n), min(nr, n)
    e[:na] = np.linspace(0, 1, na) ** shape
    e[-nr:] *= np.linspace(1, 0, nr) ** shape
    return e


def expdec(n, tau):
    return np.exp(-np.arange(n) / (tau * SR))


def bp(x, lo, hi, order=2):
    sos = signal.butter(order, [max(20, lo), min(hi, SR / 2 - 100)], "bandpass", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def lp(x, f, order=2):
    sos = signal.butter(order, min(f, SR / 2 - 100), "lowpass", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def hp(x, f, order=2):
    sos = signal.butter(order, max(20, f), "highpass", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def sweep_noise(d, f0, f1, q=1.5, rng=None, curve=1.0):
    """Band-passed noise whose centre frequency glides f0 -> f1 (block processing)."""
    rng = rng or np.random.default_rng()
    n = int(SR * d)
    x = rng.standard_normal(n)
    out = np.zeros(n)
    blk = 1024
    win = np.hanning(blk * 2)
    for s in range(0, n, blk):
        seg = x[max(0, s - blk):s + blk]
        p = (s / n) ** curve
        fc = f0 * (f1 / f0) ** p
        bw = fc / q
        y = bp(seg, fc - bw / 2, fc + bw / 2)
        w = win[:len(y)] if len(y) == len(win) else np.hanning(len(y))
        a = max(0, s - blk)
        out[a:a + len(y)] += y * w
    return out


def norm(x, peak_db=-1.0):
    m = np.max(np.abs(x)) + 1e-9
    return x / m * (10 ** (peak_db / 20))


def fade(x, fi=0.003, fo=0.01):
    a, b = int(fi * SR), int(fo * SR)
    if a:
        x[:a] *= np.linspace(0, 1, a)
    if b:
        x[-b:] *= np.linspace(1, 0, b)
    return x


def save(cat, name, x, peak_db=-1.0):
    d = os.path.join(OUT, cat)
    os.makedirs(d, exist_ok=True)
    x = fade(norm(np.asarray(x, np.float64), peak_db))
    rms = np.sqrt(np.mean(x ** 2)) + 1e-9
    cap = 10 ** (-17 / 20)          # keep sustained sounds from being much louder than clicks
    if rms > cap:
        x *= cap / rms
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(os.path.join(d, name + ".wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def click(rng, dur=0.02, f=3000, body=180, tone=0.3):
    n = int(SR * dur)
    nz = bp(rng.standard_normal(n), f * 0.5, min(f * 2.5, 18000)) * expdec(n, dur / 6)
    t = np.arange(n) / SR
    tb = np.sin(2 * np.pi * body * t) * expdec(n, dur / 4) * tone
    return nz + tb


def place(buf, x, at):
    i = int(at * SR)
    j = min(len(buf), i + len(x))
    if j > i:
        buf[i:j] += x[:j - i]
    return buf


def bell(d, freqs, decays, amps):
    t = t_axis(d)
    return sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t / dk) for f, dk, a in zip(freqs, decays, amps))


# ------------------------------------------------------------------ sounds

def whoosh(rng, d=0.9, f0=300, f1=3000, peak=0.55):
    x = sweep_noise(d, f0, f1, q=1.2, rng=rng)
    n = len(x)
    t = np.linspace(0, 1, n)
    e = np.exp(-((t - peak) ** 2) / (2 * 0.16 ** 2))
    return x * e


def make_all(variants=3):
    for v in range(variants):
        rng = np.random.default_rng(1000 + v)
        j = lambda a, b: rng.uniform(a, b)  # noqa: E731

        # transitions
        save("whoosh", f"whoosh_{v + 1}", whoosh(rng, j(0.7, 1.1), j(200, 400), j(2500, 5000), j(0.45, 0.6)))
        save("swoosh", f"swoosh_{v + 1}", whoosh(rng, j(0.3, 0.45), j(800, 1500), j(5000, 9000), j(0.4, 0.55)))
        save("swipe", f"swipe_{v + 1}", whoosh(rng, j(0.18, 0.28), j(2000, 3000), j(8000, 12000), 0.5))
        rw = whoosh(rng, j(0.8, 1.2), j(3000, 5000), j(300, 500), 0.8)
        save("reverse_whoosh", f"reverse_whoosh_{v + 1}", rw)

        # risers (end on the cut)
        d = j(1.6, 2.6)
        t = t_axis(d)
        nz = sweep_noise(d, 400, j(6000, 9000), q=0.8, rng=rng, curve=1.5)
        f = 150 * (j(8, 12)) ** (t / d)
        tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35
        save("riser", f"riser_{v + 1}", (nz + tone) * (t / d) ** 2.2)

        # impacts / booms
        d = j(0.9, 1.4)
        t = t_axis(d)
        f = j(45, 60) * np.exp(-t * 3) + 30
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / j(0.18, 0.3))
        trans = lp(rng.standard_normal(len(t)), 4000) * np.exp(-t / 0.015) * 0.6
        save("impact", f"impact_{v + 1}", np.tanh(1.6 * (body + trans)))

        d = j(2.2, 3.2)
        t = t_axis(d)
        f = j(40, 52) * np.exp(-t * 2) + 26
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.5)
        rum = lp(rng.standard_normal(len(t)), 180, 4) * np.exp(-t / 0.9) * 2.5
        trans = lp(rng.standard_normal(len(t)), 2500) * np.exp(-t / 0.03)
        save("boom", f"boom_{v + 1}", np.tanh(1.3 * (body + rum + trans)))

        d = j(1.3, 1.8)
        t = t_axis(d)
        f = 140 * np.exp(-t * j(2.5, 3.5)) + 32
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), 0.005, d * 0.5)
        save("bass_drop", f"bass_drop_{v + 1}", np.tanh(2.2 * x))

        imp = np.tanh(1.5 * (np.sin(2 * np.pi * np.cumsum(50 * np.exp(-t_axis(2.2) * 3) + 32) / SR)
                             * np.exp(-t_axis(2.2) / 0.3)))
        ring = bell(2.2, [j(310, 360), j(520, 610), j(870, 990), j(1450, 1600)], [0.9, 0.7, 0.5, 0.35],
                    [0.25, 0.2, 0.15, 0.08])
        save("hit_cinematic", f"hit_cinematic_{v + 1}", imp + ring + lp(rng.standard_normal(len(imp)), 3000)
             * np.exp(-t_axis(2.2) / 0.02) * 0.5)

        d = 0.25
        t = t_axis(d)
        x = np.sin(2 * np.pi * np.cumsum(90 * np.exp(-t * 18) + 45) / SR) * np.exp(-t / 0.07)
        ck = click(rng, 0.02, 3000)
        save("punch", f"punch_{v + 1}", np.tanh(2 * x) + np.pad(ck, (0, len(x) - len(ck))) * 0.6)

        # keyboard typing (modern keyboard)
        d = j(1.8, 3.0)
        buf = np.zeros(int(SR * d))
        tt = 0.05
        while tt < d - 0.1:
            k = click(rng, j(0.015, 0.03), j(2200, 4200), j(150, 260), j(0.2, 0.5)) * j(0.5, 1.0)
            place(buf, k, tt)
            if rng.random() < 0.12:  # space bar / enter: lower thump
                place(buf, click(rng, 0.05, 900, 110, 0.6) * 0.9, tt + 0.03)
                tt += j(0.18, 0.3)
            tt += j(0.055, 0.16)
        save("typing", f"typing_{v + 1}", buf)

        # single key clicks & mouse clicks
        save("key_click", f"key_click_{v + 1}", click(rng, 0.04, j(2500, 4000), j(160, 240), 0.4))
        buf = np.zeros(int(SR * 0.25))
        place(buf, click(rng, 0.02, 5000, 300, 0.2), 0.01)
        place(buf, click(rng, 0.02, 4200, 280, 0.2) * 0.7, 0.09)
        save("mouse_click", f"mouse_click_{v + 1}", buf)

        # typewriter (heavier strikes + bell)
        d = j(2.0, 2.8)
        buf = np.zeros(int(SR * (d + 1.2)))
        tt = 0.05
        while tt < d:
            place(buf, click(rng, 0.05, j(1200, 2000), j(90, 140), 0.8) * j(0.7, 1.0), tt)
            place(buf, click(rng, 0.02, 5000, 300, 0.1) * 0.3, tt + 0.012)
            tt += j(0.11, 0.22)
        place(buf, bell(1.1, [2093, 4186, 6300], [0.5, 0.25, 0.12], [0.5, 0.2, 0.08]), d + 0.05)
        save("typewriter", f"typewriter_{v + 1}", buf)

        # camera shutter
        buf = np.zeros(int(SR * 0.35))
        place(buf, click(rng, 0.03, 3500, 200, 0.3), 0.0)
        place(buf, hp(rng.standard_normal(int(SR * 0.06)), 2000) * expdec(int(SR * 0.06), 0.015) * 0.5, 0.02)
        place(buf, click(rng, 0.035, 2600, 170, 0.35) * 0.9, j(0.09, 0.14))
        save("camera", f"camera_{v + 1}", buf)

        # pop / bubble
        d = 0.12
        t = t_axis(d)
        f = j(300, 500) * (j(4, 7)) ** (t / d)
        save("pop", f"pop_{v + 1}", np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), 0.002, 0.08))

        # notification / ding
        base = [880, 988, 1047][v]
        x = np.concatenate([bell(0.18, [base, base * 2.01], [0.12, 0.06], [0.6, 0.2]),
                            bell(0.9, [base * 1.5, base * 3.02], [0.35, 0.15], [0.6, 0.2])])
        save("ding", f"ding_{v + 1}", x)

        # coin / cash register style
        buf = np.zeros(int(SR * 1.3))
        place(buf, click(rng, 0.06, 1500, 120, 0.6), 0.0)
        place(buf, bell(1.2, [j(2400, 2700), j(3900, 4300), j(5600, 6100), j(7900, 8400)],
                        [0.45, 0.3, 0.2, 0.12], [0.5, 0.35, 0.2, 0.1]), 0.08)
        save("cash", f"cash_{v + 1}", buf)

        # clock ticking
        buf = np.zeros(int(SR * 3.2))
        for i in range(6):
            place(buf, click(rng, 0.03, 3500 if i % 2 == 0 else 2800, 900, 0.2) * 0.9, 0.1 + i * 0.5)
        save("clock", f"clock_{v + 1}", buf)

        # heartbeat
        buf = np.zeros(int(SR * 2.6))
        for i in range(2):
            for off, a in ((0.0, 1.0), (0.22, 0.7)):
                n = int(SR * 0.2)
                tt_ = np.arange(n) / SR
                x = np.sin(2 * np.pi * (55 + 25 * np.exp(-tt_ * 30)) * tt_) * np.exp(-tt_ / 0.05) * a
                place(buf, lp(x, 200), 0.1 + i * 1.1 + off)
        save("heartbeat", f"heartbeat_{v + 1}", np.tanh(2 * buf))

        # glitch
        d = j(0.4, 0.8)
        n = int(SR * d)
        buf = np.zeros(n)
        pos = 0
        while pos < n:
            ln = int(SR * j(0.01, 0.06))
            kind = rng.integers(0, 3)
            tt_ = np.arange(ln) / SR
            if kind == 0:
                seg = np.sign(np.sin(2 * np.pi * j(80, 1200) * tt_)) * 0.6
            elif kind == 1:
                seg = np.round(rng.standard_normal(ln) * 3) / 3 * 0.5
            else:
                seg = np.zeros(ln)
            buf[pos:pos + ln] = seg[:max(0, min(ln, n - pos))]
            pos += ln
        save("glitch", f"glitch_{v + 1}", hp(buf, 120))

        # error buzz
        t = t_axis(0.45)
        x = signal.square(2 * np.pi * [110, 98, 123][v] * t) * env(len(t), 0.005, 0.05)
        save("error", f"error_{v + 1}", lp(x, 2500) * 0.8)

        # page turn / paper
        d = j(0.5, 0.8)
        n = int(SR * d)
        nz = bp(rng.standard_normal(n), 1500, 9000)
        amp = np.abs(signal.resample(rng.standard_normal(40), n)) ** 1.5
        save("page_turn", f"page_turn_{v + 1}", nz * amp * env(n, 0.05, 0.2))

        # rumble / tension drone
        d = 4.0
        t = t_axis(d)
        save("rumble", f"rumble_{v + 1}", lp(rng.standard_normal(len(t)), 120, 4) * env(len(t), 0.8, 1.5))
        f0 = [55, 49, 62][v]
        dr = sum(signal.sawtooth(2 * np.pi * f0 * m * t + rng.uniform(0, 6)) for m in (1, 1.005, 1.498, 2.003))
        save("tension", f"tension_{v + 1}", lp(dr, 600) * env(len(t), 1.5, 1.5) * (1 + 0.2 * np.sin(2 * np.pi * 0.3 * t)))

        # sparkle / shimmer (reveals)
        buf = np.zeros(int(SR * 1.4))
        for _ in range(18):
            f = j(2500, 7000)
            place(buf, bell(0.3, [f], [0.08], [j(0.2, 0.6)]), j(0.0, 1.0))
        save("sparkle", f"sparkle_{v + 1}", buf)

        # engine rev (for car videos)
        d = 2.8
        t = t_axis(d)
        rpm = 900 + 4500 * np.clip(np.sin(np.pi * t / d * 0.9) ** 1.5, 0, 1)
        fz = rpm / 60 * 4
        ph = 2 * np.pi * np.cumsum(fz) / SR
        x = signal.sawtooth(ph) + 0.5 * signal.sawtooth(ph * 0.5) + 0.3 * rng.standard_normal(len(t))
        save("engine_rev", f"engine_rev_{v + 1}", np.tanh(1.5 * lp(x, 1800, 3)) * env(len(t), 0.1, 0.6))


if __name__ == "__main__":
    make_all(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
    cats = sorted(d for d in os.listdir(OUT) if os.path.isdir(os.path.join(OUT, d)))
    print(f"Generated {len(cats)} categories in {OUT}: {', '.join(cats)}")
