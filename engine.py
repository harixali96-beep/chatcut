#!/usr/bin/env python3
"""
chatcut engine v2 - chat to edit, fully free and open source (MIT).

Natural language in, an edit plan out, a real rendered video on disk.

Design rule (learned from Descript/Opus/ChatCut): the LLM is only a router.
Every actual edit is a small deterministic ffmpeg operation. That keeps it
free, repeatable and debuggable.

Ops: trim, cut_silence, vertical, track (face-following reframe), loudnorm,
speed, fade, effects, zoom, captions (120 ASS styles), music, sfx.
Outputs: rendered media, an edit plan, a timeline (JSON / EDL / FCPXML).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

try:
    import styles as _styles
except Exception:  # pragma: no cover
    sys.path.insert(0, HERE)
    import styles as _styles


# ---------------------------------------------------------------- probe
def probe(path):
    cmd = [FFPROBE, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    out = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in out["streams"] if s["codec_type"] == "audio"), None)
    fps = v.get("r_frame_rate", "25/1") if v else "25/1"
    try:
        num, den = fps.split("/")
        fps_f = float(num) / float(den or 1)
    except Exception:
        fps_f = 25.0
    return {
        "duration": float(out["format"].get("duration", 0) or 0),
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": round(fps_f, 3),
        "has_audio": a is not None,
    }


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        tail = [l for l in p.stderr.strip().splitlines() if l.strip()]
        raise RuntimeError(tail[-1] if tail else "ffmpeg failed")
    return p


# ------------------------------------------------------------- effects
# name -> ffmpeg video filter chain. All built in, no extra binaries.
EFFECTS = {
    "cinematic":   "eq=contrast=1.08:saturation=1.05,unsharp=5:5:0.6,vignette=PI/8",
    "blockbuster": "eq=contrast=1.25:saturation=0.9,colorbalance=bs=0.08:rs=-0.03,vignette=PI/8",
    "punch":       "eq=contrast=1.18:saturation=1.15,unsharp=5:5:1.0",
    "vintage":     "curves=vintage,eq=saturation=0.85,noise=alls=8:allf=t+u",
    "noir":        "hue=s=0,eq=contrast=1.2:brightness=-0.02,vignette=PI/8",
    "bw":          "hue=s=0,eq=contrast=1.15",
    "warm":        "colorbalance=rs=0.06:gs=0.02:bs=-0.05,eq=saturation=1.06",
    "cool":        "colorbalance=rs=-0.05:gs=0.01:bs=0.08,eq=saturation=1.02",
    "dream":       "gblur=sigma=1.2,eq=brightness=0.04:saturation=1.1",
    "glow":        "eq=brightness=0.06:saturation=1.08,gblur=sigma=2,unsharp=5:5:0.8",
    "grain":       "noise=alls=14:allf=t",
    "sharp":       "unsharp=7:7:1.2",
    "soft":        "gblur=sigma=0.8",
    "chroma":      "rgbashift=rh=3:bh=-3,eq=saturation=1.1",
    "glitch":      "rgbashift=rh=6:bh=-6,noise=alls=6:allf=t",
    "vhs":         "eq=saturation=1.2:contrast=0.95,noise=alls=10:allf=t,rgbashift=rh=2:bh=-2",
    "leak":        "colorbalance=rs=0.10:gs=0.04:bs=-0.10,eq=brightness=0.06:saturation=1.1",
    "anime":       "hqdn3d=2:2:3:3,eq=saturation=1.5:contrast=1.1,unsharp=5:5:1.2",
    "faded":       "curves=all='0/0.06 0.5/0.5 1/0.94',eq=saturation=0.9:contrast=0.92",
}

EFFECT_ALIASES = {
    "hdr": "blockbuster", "film": "cinematic", "old": "vintage", "retro": "vintage",
    "black and white": "bw", "b&w": "bw", "mono": "bw", "vhs": "vhs", "glitchy": "glitch",
    "smooth": "soft", "crisp": "sharp", "blur": "soft", "warm tone": "warm",
    "cold": "cool", "cinematic look": "cinematic",
}

# ------------------------------------------------------- intent parsing
RULES = [
    ("cut_silence", r"\b(silence|silent|pause|pauses|chup|chupchupati|khamoshi|rukawat|dead air|gaps?)\b.*\b(hata|hatao|remove|cut|nikal|delete)\b|\b(remove|cut)\b.*\b(silence|pauses?|dead air)\b"),
    ("streamer",    r"\b(facecam|face cam|face-cam|webcam|gameplay|streamer|stream|gta|gaming)\b.*\b(upar|upper|top|neechay|neeche|bottom|rakho|layout|split)\b|\b(facecam|webcam)\b|\bgameplay\b"),
    ("podcast",     r"\b(podcast|split screen|split karo|split kar|upar neechay|upar niche|top bottom|stack|do log|do bande|do shakhs|2 log|2 bande|2 characters?|two (?:people|persons|speakers|guests|characters))\b"),
    ("track",       r"\b(face|chehra|tracking|track|follow|subject|speaker|insaan|person|body|jism|character)\b.*\b(track|follow|reframe|rakho|centre|center)\b|\b(face tracking|auto reframe|smart reframe|follow the face|chehra track|body track)\b"),
    ("vertical",    r"\b(vertical|shorts?|reels?|9:16|portrait|tiktok)\b"),
    ("zoom",        r"\b(zoom|punch[- ]?in|ken burns|slow zoom|camera movement|movement|camera move)\b"),
    ("loudnorm",    r"\b(normalize|normalise|loud|awaz|awaaz|volume|audio)\b.*\b(theek|thik|fix|balance|normalize|barabar|set)\b|\b(normalize|normalise|audio fix|loudness)\b"),
    ("captions",    r"\b(caption|captions|subtitle|subtitles|text|likhai)\b"),
    ("sfx",         r"\b(sfx|sound effect|sound effects|whoosh|impact|effect sound)\b"),
    ("music",       r"\b(music|background music|bgm|soundtrack|song|gana)\b"),
    ("fade",        r"\b(fade|dheela|smooth)\b"),
]

SPEED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x|times|guna)", re.I)
FIRST_N_RE = re.compile(r"\b(?:first|pehle|pehla)\s+(\d+)\s*(?:sec|secs|second|seconds|s)\b", re.I)
FIRST_N_MIN_RE = re.compile(r"\b(?:first|pehle)\s+(\d+)\s*(?:min|mins|minute|minutes)\b", re.I)
ANY_SEC_RE = re.compile(r"\b(\d+)\s*(?:sec|secs|second|seconds)\b", re.I)
NEG_RE = re.compile(r"\b(hata|hatao|remove|nikal|band|off|mat|bina|without|no|nahi|nahin|na)\b", re.I)


EXEC_ORDER = {
    "trim": 1, "cut_silence": 2, "speed": 3, "podcast": 4, "streamer": 4,
    "track": 4, "vertical": 4,
    "zoom": 5, "effects": 6, "loudnorm": 7,
    "captions": 8, "sfx": 9, "music": 10, "fade": 11,
}


def order_ops(ops):
    """Stable sort into a sane pipeline. Burning text before a grade destroys it."""
    return sorted(ops, key=lambda o: EXEC_ORDER.get(o.get("op"), 99))


def _negated_near(text, pattern):
    """True when the verb attached to THIS keyword is a removal.

    "captions lagao" adds captions. "captions hata do" removes them. Only the
    words right around the keyword decide, so an unrelated "silence hatao"
    earlier in the sentence cannot switch off captions.
    """
    m = re.search(pattern, text)
    if not m:
        return False
    toks = re.findall(r"[a-z']+", text)
    kw = re.findall(r"[a-z']+", m.group(0))
    if not kw:
        return False
    try:
        i = toks.index(kw[0])
    except ValueError:
        return m.start() >= 0 and False
    NEG_AFTER = {"hata", "hatao", "hatado", "remove", "nikal", "band", "off",
                 "mat", "nahi", "nahin", "na", "delete", "without", "no", "bina"}
    NEG_BEFORE = {"no", "without", "bina", "sivaye", "sirf"}
    if any(t in NEG_AFTER for t in toks[i + 1:i + 3]):
        return True
    if any(t in NEG_BEFORE for t in toks[max(0, i - 2):i]):
        return True
    return False


def parse_instruction(text):
    """Turn a chat sentence into an ordered list of edit ops."""
    t = text.lower()
    ops = []

    m = FIRST_N_MIN_RE.search(t)
    if m:
        ops.append({"op": "trim", "start": 0, "end": int(m.group(1)) * 60})
    else:
        m = FIRST_N_RE.search(t) or ANY_SEC_RE.search(t)
        if m:
            secs = int(m.group(1))
            if 1 <= secs <= 7200:
                ops.append({"op": "trim", "start": 0, "end": secs})

    m = SPEED_RE.search(t)
    if m:
        f = float(m.group(1))
        if 0.25 <= f <= 4 and f != 1.0:
            ops.append({"op": "speed", "factor": f})

    # named effect wins if the user said one
    effect = None
    for token in list(EFFECTS) + list(EFFECT_ALIASES):
        if re.search(r"(^|[^a-z])" + re.escape(token) + r"([^a-z]|$)", t):
            effect = EFFECT_ALIASES.get(token, token)
            break
    for op, pattern in RULES:
        if re.search(pattern, t):
            if op == "cut_silence":
                ops.append({"op": "cut_silence", "min_ms": 500})
            elif op == "podcast":
                ops.append({"op": "podcast", "width": 1080, "height": 1920})
            elif op == "track":
                subject = "body" if re.search(
                    r"\b(body|jism|torso|character|insaan|poora|full body)\b", t) else "face"
                ops.append({"op": "track", "width": 1080, "height": 1920,
                            "subject": subject})
            elif op == "vertical":
                ops.append({"op": "vertical", "width": 1080, "height": 1920})
            elif op == "zoom":
                ops.append({"op": "zoom", "amount": 0.12})
            elif op == "loudnorm":
                ops.append({"op": "loudnorm"})
            elif op == "fade":
                ops.append({"op": "fade", "seconds": 0.4})
            else:
                if op in ("music", "captions", "sfx") and _negated_near(t, pattern):
                    continue
                ops.append({"op": op})

    # the grade lands last, on the final framing
    if effect:
        ops.append({"op": "effects", "preset": effect})

    # a split layout supersedes single-speaker framing
    kinds = [o["op"] for o in ops]
    if "podcast" in kinds:
        ops = [o for o in ops if o["op"] not in ("track", "vertical", "streamer")]
    if "streamer" in kinds:
        ops = [o for o in ops if o["op"] not in ("track", "vertical")]
    if "track" in kinds and "vertical" in kinds:
        ops = [o for o in ops if o["op"] != "vertical"]
    return ops


# --------------------------------------------------------- primitives
def detect_silences(path, min_ms=500, noise_db=-32):
    p = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", path, "-af",
         f"silencedetect=noise={noise_db}dB:d={min_ms/1000}", "-f", "null", "-"],
        capture_output=True, text=True)
    out = p.stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", out)]
    return list(zip(starts, ends))


def keep_ranges(duration, silences, pad=0.08):
    cuts = []
    for s, e in silences:
        cuts.append((max(0.0, s + pad), min(duration, e - pad)))
    cuts.sort()
    kept, cursor = [], 0.0
    for s, e in cuts:
        if s > cursor:
            kept.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        kept.append((cursor, duration))
    return [(s, e) for s, e in kept if e - s > 0.15]


def make_sfx(workdir):
    """Synthesise whoosh and impact with ffmpeg. No sample packs, no licences."""
    whoosh = os.path.join(workdir, "whoosh.wav")
    impact = os.path.join(workdir, "impact.wav")
    run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "anoisesrc=d=0.38:c=pink:a=0.55",
         "-af", "highpass=f=700,lowpass=f=6500,afade=t=in:d=0.05,"
                "afade=t=out:st=0.18:d=0.2,volume=0.9", whoosh])
    run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=f=68:d=0.28",
         "-af", "afade=t=in:d=0.01,afade=t=out:st=0.05:d=0.22,volume=1.2", impact])
    return whoosh, impact


def remap_srt(srt_text, kept):
    """Pull caption timings onto the cut timeline after silences are removed."""
    def remap(t):
        acc = 0.0
        for s0, e0 in kept:
            if t < s0:
                return acc
            if t <= e0:
                return acc + (t - s0)
            acc += e0 - s0
        return acc

    def fmt(t):
        h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    out, changed = [], 0
    for block in srt_text.split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) < 2 or "-->" not in block:
            continue
        idx = lines[0]
        times = [x.strip() for x in re.search(r"([\d:,]+)\s*-->\s*([\d:,]+)", block).groups()]
        def parse(x):
            hh, mm, rest = x.replace(".", ",").split(":")
            ss, ms = rest.split(",")
            return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000
        a, b = remap(parse(times[0])), remap(parse(times[1]))
        if b - a <= 0.05:
            continue
        text = "\n".join(lines[2:]) if lines[2:] else ""
        if not text.strip():
            continue
        out.append(f"{idx}\n{fmt(a)} --> {fmt(b)}\n{text}")
        changed += 1
    return "\n\n".join(out) + "\n", changed


def splice_layouts(split_p, single_p, intervals, dst, workdir):
    """Cut between the split layout and the single layout on the decided times.

    Each interval is cut to its own file and joined with the concat demuxer.
    A single mega filtergraph choked on stream ends; this does not.
    """
    parts = []
    for i, (a, b, mode) in enumerate(intervals):
        src = split_p if mode == "split" else single_p
        seg = os.path.join(workdir, f"layout_{i}_{mode}.mp4")
        run([FFMPEG, "-y", "-v", "error", "-i", src,
             "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-avoid_negative_ts", "make_zero", seg])
        parts.append(seg)
    listing = os.path.join(workdir, "layout_concat.txt")
    with open(listing, "w") as fh:
        for p_ in parts:
            fh.write("file '%s'\n" % p_.replace("'", "'\\''"))
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", listing, "-c", "copy", dst])
    return parts


def burn_captions(src, dst, srt_path, style_name, workdir, size, kept=None):
    with open(srt_path, "r", encoding="utf-8", errors="ignore") as fh:
        srt = fh.read()
    remapped = None
    if kept:
        srt, remapped = remap_srt(srt, kept)
    ass = _styles.build_ass(srt, style_name, play_res=size)
    ass_path = os.path.join(workdir, "captions.ass")
    with open(ass_path, "w", encoding="utf-8") as fh:
        fh.write(ass)
    esc = ass_path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    run([FFMPEG, "-y", "-v", "error", "-i", src,
         "-vf", f"subtitles='{esc}'",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "copy", dst])
    return len(ass.splitlines())


# ------------------------------------------------------------------ render
def render(src, ops, out_path, workdir, srt=None, music=None, style=None,
           dry=False):
    info = probe(src)
    current = src
    steps = []
    state = {"cuts": [], "size": [info["width"], info["height"]]}
    idx = 0

    def nxt():
        nonlocal idx
        idx += 1
        return os.path.join(workdir, f"step{idx}.mp4")

    for op in ops:
        kind = op["op"]

        if kind == "trim":
            dst = nxt()
            start = op.get("start", 0)
            end = min(op.get("end", info["duration"]), info["duration"])
            run([FFMPEG, "-y", "-v", "error", "-i", current, "-ss", str(start),
                 "-to", str(end), "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "20", "-c:a", "aac", "-b:a", "160k", dst])
            current = dst
            steps.append({"op": kind, "start": start, "end": end})

        elif kind == "cut_silence":
            if not info["has_audio"]:
                steps.append({"op": kind, "skipped": "no audio stream"})
                continue
            sil = detect_silences(current, op.get("min_ms", 500))
            cur_dur = probe(current)["duration"]
            kept = keep_ranges(cur_dur, sil)
            if not kept:
                kept = [(0, cur_dur)]
            dst = nxt()
            parts, labels = [], []
            for i, (s, e) in enumerate(kept):
                parts.append(f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS[v{i}]")
                parts.append(f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[a{i}]")
                labels.append(f"[v{i}][a{i}]")
            graph = ";".join(parts) + ";" + "".join(labels) + \
                    f"concat=n={len(kept)}:v=1:a=1[vo][ao]"
            run([FFMPEG, "-y", "-v", "error", "-i", current,
                 "-filter_complex", graph, "-map", "[vo]", "-map", "[ao]",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "160k", dst])
            current = dst
            # cumulative join times in the new timeline, for SFX placement
            acc, joins = 0.0, []
            for s, e in kept[1:]:
                acc += kept[kept.index((s, e)) - 1][1] - kept[kept.index((s, e)) - 1][0]
            t, cuts = 0.0, []
            for s, e in kept[:-1]:
                t += e - s
                cuts.append(round(t, 3))
            state["cuts"] = cuts
            state["kept_ranges"] = kept
            state["remap_base"] = "source"
            removed = round(cur_dur - probe(dst)["duration"], 2)
            steps.append({"op": kind, "silences_found": len(sil),
                          "segments_kept": len(kept), "seconds_removed": removed,
                          "cut_points": cuts[:12]})

        elif kind in ("vertical", "track"):
            w, h = op.get("width", 1080), op.get("height", 1920)
            dst = nxt()
            if kind == "track":
                try:
                    import track as _track
                    res = _track.analyse(current, out_w=w, out_h=h)
                    vf = res["filter"]
                    tracker_info = res["info"]
                except Exception as exc:
                    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
                    tracker_info = {"fallback": f"tracker unavailable: {exc}"}
                run([FFMPEG, "-y", "-v", "error", "-i", current, "-vf", vf,
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-c:a", "copy", dst])
                steps.append({"op": "track", "size": f"{w}x{h}",
                              "faces": tracker_info.get("faces_found"),
                              "path_points": tracker_info.get("points"),
                              "note": tracker_info.get("fallback")})
            else:
                vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                      f"crop={w}:{h}")
                run([FFMPEG, "-y", "-v", "error", "-i", current, "-vf", vf,
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-c:a", "copy", dst])
                steps.append({"op": kind, "size": f"{w}x{h}"})
            current = dst
            state["size"] = [w, h]

        elif kind == "streamer":
            w, h = op.get("width", 1080), op.get("height", 1920)
            ratio = op.get("top_ratio", 0.30)
            dst = nxt()
            try:
                import track as _track
                plan = _track.build_streamer_plan(current, out_w=w, out_h=h,
                                                  top_ratio=ratio)
            except Exception as exc:
                plan = {"ok": False, "reason": f"tracker unavailable: {exc}"}
            if not plan.get("ok"):
                steps.append({"op": kind, "skipped": plan.get("reason",
                              "no facecam found")})
                continue
            run([FFMPEG, "-y", "-v", "error", "-i", current,
                 "-filter_complex", plan["filter"], "-map", "[v]", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "copy", dst])
            current = dst
            state["size"] = [w, h]
            steps.append({"op": kind, "layout": "facecam top / gameplay bottom",
                          "panel": plan["panel"], "corner": plan.get("corner"),
                          "coverage": plan.get("coverage"),
                          "facecam": plan.get("facecam"),
                          "gameplay": plan.get("gameplay")})

        elif kind == "podcast":
            w, h = op.get("width", 1080), op.get("height", 1920)
            dst = nxt()
            try:
                import track as _track
                data = _track.detect_faces(current)
                plan = _track.build_two_speaker_plan(current, out_w=w, out_h=h)
            except Exception as exc:
                plan = {"ok": False, "reason": f"tracker unavailable: {exc}"}
            if not plan.get("ok"):
                steps.append({"op": kind, "skipped": plan.get("reason"),
                              "hint": "needs two faces on screen at the same time"})
                continue
            # each sample decides the layout: two faces split, one face normal
            try:
                dur = probe(current)["duration"]
                intervals = _track.mode_intervals(data["samples"], dur)
            except Exception:
                intervals = [(0.0, probe(current)["duration"], "split")]
            split_p = os.path.join(workdir, "podcast_split.mp4")
            run([FFMPEG, "-y", "-v", "error", "-i", current,
                 "-filter_complex", plan["filter"], "-map", "[v]", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "copy", split_p])

            if any(m == "single" for _, _, m in intervals):
                single_p = os.path.join(workdir, "podcast_single.mp4")
                one = _track.analyse(current, out_w=w, out_h=h)
                run([FFMPEG, "-y", "-v", "error", "-i", current, "-vf", one["filter"],
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-c:a", "copy", single_p])
                splice_layouts(split_p, single_p, intervals, dst, workdir)
                layout_note = "dynamic: split when two, normal when one"
            else:
                shutil.copyfile(split_p, dst)
                layout_note = "static split, two speakers throughout"
            current = dst
            state["size"] = [w, h]
            steps.append({"op": kind, "layout": "top/bottom",
                          "panel": plan["panel"], "mode": layout_note,
                          "intervals": [{"from": a, "to": b, "layout": m}
                                        for a, b, m in intervals],
                          "speakers": [sp["x_range"] for sp in plan["speakers"]]})

        elif kind == "zoom":
            cur = probe(current)
            amt = op.get("amount", 0.12)
            frames = max(1, int(cur["duration"] * cur["fps"]))
            dst = nxt()
            # zoompan resets timestamps; without the setpts fix libass sees wrong
            # times and burned captions silently disappear.
            vf = (f"zoompan=z='min(1+{amt}*on/{frames},1+{amt})'"
                  f":d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":s={cur['width']}x{cur['height']}:fps={cur['fps']},"
                  f"settb=AVTB,setpts=N/({cur['fps']}*TB)")
            run([FFMPEG, "-y", "-v", "error", "-i", current, "-vf", vf,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "copy", dst])
            current = dst
            steps.append({"op": kind, "amount": amt, "frames": frames})
            state["size"] = [cur["width"], cur["height"]]

        elif kind == "effects":
            preset = op.get("preset", "cinematic")
            chain = EFFECTS.get(preset, EFFECTS["cinematic"])
            dst = nxt()
            run([FFMPEG, "-y", "-v", "error", "-i", current,
                 "-vf", chain, "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "20", "-c:a", "copy", dst])
            current = dst
            steps.append({"op": kind, "preset": preset})

        elif kind == "loudnorm":
            if not info["has_audio"]:
                steps.append({"op": kind, "skipped": "no audio stream"})
                continue
            dst = nxt()
            run([FFMPEG, "-y", "-v", "error", "-i", current,
                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", dst])
            current = dst
            steps.append({"op": kind, "target_lufs": -16})

        elif kind == "speed":
            f = op["factor"]
            dst = nxt()
            run([FFMPEG, "-y", "-v", "error", "-i", current,
                 "-filter_complex",
                 f"[0:v]setpts=PTS/{f}[v];[0:a]atempo={min(max(f,0.5),2.0)}[a]",
                 "-map", "[v]", "-map", "[a]",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "160k", dst])
            current = dst
            steps.append({"op": kind, "factor": f})

        elif kind == "fade":
            sec = op.get("seconds", 0.4)
            dur = probe(current)["duration"]
            dst = nxt()
            run([FFMPEG, "-y", "-v", "error", "-i", current,
                 "-vf", f"fade=t=in:st=0:d={sec},fade=t=out:st={max(0, dur-sec):.2f}:d={sec}",
                 "-af", f"afade=t=in:st=0:d={sec},afade=t=out:st={max(0, dur-sec):.2f}:d={sec}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "160k", dst])
            current = dst
            steps.append({"op": kind, "seconds": sec})

        elif kind == "captions":
            if not srt or not os.path.exists(srt):
                steps.append({"op": kind, "skipped": "no transcript supplied (--srt)"})
                continue
            chosen = style or _styles.pick_style(op.get("instruction", ""))
            dst = nxt()
            n = burn_captions(current, dst, srt, chosen, workdir,
                              tuple(state["size"]),
                              kept=state.get("kept_ranges"))
            current = dst
            step = {"op": kind, "style": chosen, "ass_lines": n}
            if state.get("kept_ranges"):
                step["retimed"] = "captions pulled onto the cut timeline"
            steps.append(step)

        elif kind == "sfx":
            if not info["has_audio"]:
                steps.append({"op": kind, "skipped": "no audio stream"})
                continue
            cuts = state["cuts"][:24]
            if not cuts:
                steps.append({"op": kind, "skipped": "no cut points detected yet"})
                continue
            whoosh, impact = make_sfx(workdir)
            dst = nxt()
            inputs = [FFMPEG, "-y", "-v", "error", "-i", current]
            filters, mixes = [], ["[0:a]"]
            for i, t in enumerate(cuts):
                inputs += ["-i", whoosh]
                ms = int(max(0, t - 0.10) * 1000)
                filters.append(f"[{i+1}:a]adelay={ms}|{ms},volume=0.30[s{i}]")
                mixes.append(f"[s{i}]")
            graph = ";".join(filters) + ";" + "".join(mixes) + \
                    f"amix=inputs={len(mixes)}:duration=first:dropout_transition=0[a]"
            inputs += ["-filter_complex", graph, "-map", "0:v", "-map", "[a]",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", dst]
            run(inputs)
            current = dst
            steps.append({"op": kind, "placements": len(cuts), "source": "synth-whoosh"})

        elif kind == "music":
            if not music or not os.path.exists(music):
                steps.append({"op": kind, "skipped": "no music file supplied (--music)"})
                continue
            dst = nxt()
            # real ducking: the bed dips whenever the speaker talks
            run([FFMPEG, "-y", "-v", "error", "-i", current, "-stream_loop", "-1",
                 "-i", music, "-filter_complex",
                 "[0:a]asplit=2[d1][d2];"
                 "[1:a]volume=0.22,highpass=f=120[bed];"
                 "[bed][d1]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[duck];"
                 "[d2][duck]amix=inputs=2:duration=first:normalize=0[a]",
                 "-map", "0:v", "-map", "[a]", "-shortest",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", dst])
            current = dst
            steps.append({"op": kind, "mode": "sidechain ducked",
                          "bed_gain": "0.22", "duck": "8:1 @ 15/350ms"})

        else:
            steps.append({"op": kind, "skipped": "unknown op"})

    if current != out_path:
        shutil.copyfile(current, out_path)

    fi = probe(out_path)
    return {
        "output": out_path,
        "steps": steps,
        "source": info,
        "result": {"duration": round(fi["duration"], 2),
                   "size": f'{fi["width"]}x{fi["height"]}',
                   "fps": fi["fps"],
                   "has_audio": fi["has_audio"]},
    }


# --------------------------------------------------------------- timeline
def concat_files(parts, dst, workdir):
    """Join already-encoded clips with the concat demuxer."""
    if not parts:
        raise RuntimeError("nothing to join")
    if len(parts) == 1:
        shutil.copyfile(parts[0], dst)
        return dst
    listing = os.path.join(workdir, "concat_list.txt")
    with open(listing, "w") as fh:
        for p_ in parts:
            fh.write("file '%s'\n" % p_.replace("'", "'\\''"))
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", listing, "-c", "copy", dst])
    return dst


def render_timeline(src, segments, out_path, workdir=None):
    """Render an explicit cut list. This is what the draggable timeline sends.

    segments: [{"start": 0.0, "end": 3.2}, ...] in source seconds, in play order.
    Reordering the list reorders the video; trimming the numbers trims it.
    """
    own = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="chatcut-tl-")
    try:
        info = probe(src)
        clean = []
        for seg in segments:
            a = max(0.0, float(seg.get("start", 0)))
            b = min(info["duration"], float(seg.get("end", info["duration"])))
            if b - a > 0.05:
                clean.append((a, b))
        if not clean:
            raise RuntimeError("every segment is empty after clamping")
        parts = []
        for i, (a, b) in enumerate(clean):
            p_ = os.path.join(workdir, f"tl_{i:03d}.mp4")
            run([FFMPEG, "-y", "-v", "error", "-i", src,
                 "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "160k",
                 "-avoid_negative_ts", "make_zero", p_])
            parts.append(p_)
        concat_files(parts, out_path, workdir)
        out_info = probe(out_path)
        return {
            "output": out_path,
            "segments": [{"start": round(a, 3), "end": round(b, 3)} for a, b in clean],
            "source_duration": round(info["duration"], 3),
            "result": {"duration": round(out_info["duration"], 3),
                       "size": f'{out_info["width"]}x{out_info["height"]}',
                       "has_audio": out_info["has_audio"]},
        }
    finally:
        if own:
            shutil.rmtree(workdir, ignore_errors=True)


def timeline_json(plan, result, src):
    clips = []
    cursor = 0.0
    for s in result.get("steps", []):
        if s["op"] == "cut_silence":
            for i, t in enumerate(s.get("cut_points", [])):
                clips.append({"index": i, "type": "segment", "out_point": t})
    return {
        "version": 1,
        "source": src,
        "instruction": plan.get("instruction"),
        "ops": plan.get("ops", []),
        "steps": result.get("steps", []),
        "result": result.get("result"),
        "tracks": [{"kind": "video", "clips": clips or
                    [{"index": 0, "type": "single", "duration": (result.get("result") or {}).get("duration")}]}],
        "audio": {"dialogue": 1, "music": "bed" if any(s["op"] == "music" and not s.get("skipped") for s in result.get("steps", [])) else None,
                  "sfx": any(s["op"] == "sfx" and not s.get("skipped") for s in result.get("steps", []))},
    }


def timeline_edl(result, out_path, fps=25.0):
    """CMX3600 EDL, opens in Premiere, Resolve, Final Cut."""
    steps = result.get("steps", [])
    cuts = next((s.get("cut_points") for s in steps if s.get("cut_points")), None)
    dur = (result.get("result") or {}).get("duration", 0)
    f = int(round(fps))

    def tc(sec):
        t = int(round(sec * f))
        hh, rem = divmod(t, 3600 * f)
        mm, rem = divmod(rem, 60 * f)
        ss, ff = divmod(rem, f)
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    lines = ["TITLE: CHATCUT EXPORT", "FCM: NON-DROP FRAME", ""]
    if cuts:
        prev, n = 0.0, 1
        for c in cuts + [dur]:
            lines.append(f"{n:03d}  AX       V     C        {tc(prev)} {tc(c)} {tc(prev)} {tc(prev + (c - prev))}")
            lines.append(f"* FROM CLIP NAME: {os.path.basename(result.get('output', 'clip'))}")
            lines.append("")
            prev, n = c, n + 1
    else:
        lines.append(f"001  AX       V     C        {tc(0)} {tc(dur)} {tc(0)} {tc(dur)}")
        lines.append(f"* FROM CLIP NAME: {os.path.basename(result.get('output', 'clip'))}")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out_path


def timeline_fcpxml(result, out_path, fps=25.0):
    """Minimal FCPXML 1.9 for Final Cut / Resolve import."""
    dur = (result.get("result") or {}).get("duration", 0)
    r = (result.get("result") or {}).get("size", "1080x1920").split("x")
    w, h = int(r[0]), int(r[1])

    def ft(sec):
        t = int(round(sec * fps))
        return f"{t}/{int(fps)}s"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="{1}/{int(fps)}s" width="{w}" height="{h}"/>
  </resources>
  <library>
    <event name="ChatCut">
      <project name="ChatCut Export">
        <sequence format="r1" duration="{ft(dur)}" tcStart="0s" tcFormat="NDF">
          <spine>
            <clip offset="0s" duration="{ft(dur)}" name="{os.path.basename(result.get('output','clip'))}"/>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return out_path


# ------------------------------------------------------------------- api
def edit(src, instruction, out_path, srt=None, music=None, style=None,
         plan_only=False, tl_dir=None, auto_transcribe=True, model=None,
         want_description=False, ops=None):
    ops = order_ops(ops if ops is not None else parse_instruction(instruction))
    plan = {"instruction": instruction, "ops": ops}
    if plan_only or not ops:
        return {"plan": plan, "steps": [], "note": "no renderable ops parsed"}
    for o in ops:
        if o["op"] in ("captions", "music", "sfx"):
            o["instruction"] = instruction
    workdir = tempfile.mkdtemp(prefix="chatcut-")
    try:
        transcription = None
        need_caps = any(o["op"] == "captions" for o in ops)
        if need_caps and not srt and auto_transcribe:
            try:
                import transcribe as _tr
                auto = os.path.join(workdir, "auto.srt")
                tr = _tr.transcribe_to_file(src, auto, model_size=model)
                srt = auto
                transcription = {k: tr[k] for k in
                                 ("text", "language", "word_count", "model",
                                  "language_probability")}
                transcription["segments"] = tr["segments"]
                transcription["auto"] = True
            except Exception as exc:
                transcription = {"auto": False, "error": f"transcription failed: {exc}"}
        result = render(src, ops, out_path, workdir, srt, music, style)
        if transcription:
            result["transcript"] = transcription
        if want_description and transcription and transcription.get("text"):
            try:
                import describe as _desc
                result["description"] = _desc.describe(
                    transcription["text"], transcription.get("segments"))
            except Exception as exc:
                result["description"] = {"error": str(exc)}
        result["plan"] = plan
        tl = timeline_json(plan, result, src)
        result["timeline"] = tl
        if tl_dir:
            os.makedirs(tl_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(out_path))[0]
            with open(os.path.join(tl_dir, base + ".timeline.json"), "w") as fh:
                json.dump(tl, fh, indent=2)
            result["exports"] = [
                timeline_edl(result, os.path.join(tl_dir, base + ".edl")),
                timeline_fcpxml(result, os.path.join(tl_dir, base + ".fcpxml")),
            ]
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def list_styles():
    return _styles.style_names()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="chat to edit, free and local")
    ap.add_argument("video", nargs="?")
    ap.add_argument("instruction", nargs="?")
    ap.add_argument("-o", "--out", default="out.mp4")
    ap.add_argument("--srt")
    ap.add_argument("--music")
    ap.add_argument("--style", help="caption style name, see --list-styles")
    ap.add_argument("--timeline-dir", default=None,
                    help="write timeline.json, .edl and .fcpxml here")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--list-styles", action="store_true")
    ap.add_argument("--list-effects", action="store_true")
    ap.add_argument("--timeline-json", dest="tl_json", default=None,
                    help="JSON cut list for a draggable-timeline render")
    a = ap.parse_args()

    if a.list_styles:
        for n in list_styles():
            print(n)
        sys.exit(0)
    if a.list_effects:
        for n in sorted(EFFECTS):
            print(n)
        sys.exit(0)
    if a.tl_json:
        if not a.video:
            ap.error("video is required with --timeline-json")
        segs = json.loads(open(a.tl_json).read() if os.path.exists(a.tl_json)
                          else a.tl_json)
        if isinstance(segs, dict):
            segs = segs.get("segments", [])
        print(json.dumps(render_timeline(a.video, segs, a.out), indent=2, default=float))
        sys.exit(0)
    if not a.video or not a.instruction:
        ap.error("video and instruction are required")

    print(json.dumps(edit(a.video, a.instruction, a.out, a.srt, a.music,
                          a.style, a.plan_only, a.timeline_dir), indent=2,
                     default=float))
