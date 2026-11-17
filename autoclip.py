#!/usr/bin/env python3
"""
Long video in, short vertical clips out. The Opus-style part, with no API key.

How the moments are chosen, stated plainly so the result can be trusted:

- The whole video is transcribed once with whisper.
- Candidate windows are built on sentence boundaries, between min_len and max_len.
- Each window is scored on things that can actually be measured in a transcript:
  speech density (words per second), hook vocabulary, question marks, numbers,
  and how close the length is to the sweet spot. No model guesses, no vibes
  dressed up as analysis.
- Windows are picked highest score first, skipping overlaps, until n clips.
- Each clip is cut, framed (facecam layout when the source has one, otherwise
  9:16), captioned and described.

If the footage has no usable speech the run reports that instead of cutting
silence into random pieces.
"""
import json
import sys
import os
import shutil
import tempfile

import describe
import engine
import transcribe

HOOK_WORDS = set("""
how why what when where who never always secret truth mistake mistakes biggest
best worst crazy insane unbelievable listen watch stop imagine free money fast
easy hard nobody everyone actually really finally literally almost suddenly
because but so if then guess look here this that you your i me we they
""".split())

PUNCT = ".,!?;:\"'()[]"


def _clean(word):
    return word.lower().strip(PUNCT)


def _srt_ts(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def build_candidates(segments, min_len=15.0, max_len=45.0):
    """Windows that start and end on sentence boundaries."""
    out = []
    for i, seg in enumerate(segments):
        start = seg["start"]
        text, words = [], 0
        for j in range(i, len(segments)):
            dur = segments[j]["end"] - start
            if dur > max_len:
                break
            text.append(segments[j]["text"])
            words += len(segments[j]["text"].split())
            dur = segments[j]["end"] - start
            if dur >= min_len:
                out.append({
                    "start": round(start, 3),
                    "end": round(segments[j]["end"], 3),
                    "duration": round(dur, 3),
                    "text": " ".join(text).strip(),
                    "words": words,
                })
    # drop near-duplicates that share the same end
    seen, uniq = set(), []
    for c in out:
        key = (round(c["start"], 1), round(c["end"], 1))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def score_window(cand, sweet=26.0):
    dur = max(1.0, cand["duration"])
    words = [w for w in cand["text"].split() if w.strip(PUNCT)]
    wps = len(words) / dur
    hooks = sum(1 for w in words if _clean(w) in HOOK_WORDS)
    questions = cand["text"].count("?")
    numbers = sum(1 for w in words if _clean(w).isdigit())
    length_fit = max(0.0, 1.0 - abs(dur - sweet) / sweet)
    score = (2.2 * min(wps, 4.5) / 4.5
             + 0.9 * min(hooks, 6) / 6
             + 0.4 * min(questions, 3) / 3
             + 0.3 * min(numbers, 3) / 3
             + 0.6 * length_fit)
    return round(score, 4), {
        "words_per_sec": round(wps, 2),
        "hook_hits": hooks,
        "questions": questions,
        "numbers": numbers,
        "length_fit": round(length_fit, 2),
    }


def pick(cands, n=3, min_gap=1.0):
    ranked = sorted(cands, key=lambda c: c["score"], reverse=True)
    chosen = []
    for c in ranked:
        if all(c["end"] <= o["start"] - min_gap or c["start"] >= o["end"] + min_gap
               for o in chosen):
            chosen.append(c)
        if len(chosen) >= n:
            break
    return sorted(chosen, key=lambda c: c["start"])


def window_srt(segments, start, end, path):
    lines = []
    i = 1
    for s in segments:
        if s["end"] <= start or s["start"] >= end:
            continue
        a = max(0.0, s["start"] - start)
        b = max(a + 0.2, s["end"] - start)
        lines.append(f"{i}\n{_srt_ts(a)} --> {_srt_ts(b)}\n{s['text']}")
        i += 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(lines) + "\n")
    return path


def auto_clip(src, outdir=None, n=3, min_len=15.0, max_len=45.0, model=None,
              style=None, want_facecam=True, progress=None, extra=""):
    outdir = outdir or tempfile.mkdtemp(prefix="chatcut-clips-")
    os.makedirs(outdir, exist_ok=True)
    def say(msg):
        if progress:
            progress(msg)

    say("transcribing the source")
    tr = transcribe.transcribe(src, model_size=model)
    segments = tr["segments"]
    if not segments:
        return {"ok": False, "reason": "no speech found, nothing to cut around",
                "clips": []}

    cands = build_candidates(segments, min_len, max_len)
    if not cands:
        dur = engine.probe(src)["duration"]
        return {"ok": False,
                "reason": f"only {dur:.0f}s of usable speech, shorter than the "
                          f"{min_len:.0f}s minimum clip",
                "clips": []}
    for c in cands:
        c["score"], c["signals"] = score_window(c)
    chosen = pick(cands, n)

    clips = []
    for idx, c in enumerate(chosen, 1):
        say(f"rendering clip {idx}/{len(chosen)}")
        wd = tempfile.mkdtemp(prefix="chatcut-clip-", dir=outdir)
        cut = os.path.join(wd, "cut.mp4")
        engine.render_timeline(src, [{"start": c["start"], "end": c["end"]}], cut)
        srt = window_srt(segments, c["start"], c["end"],
                         os.path.join(wd, "clip.srt"))

        base = ["captions lagao",
                "facecam upar rakho, gameplay neechay" if want_facecam
                else "shorts banao"]
        merged = {o["op"]: o for o in engine.parse_instruction(", ".join(base))}
        for o in engine.parse_instruction(extra or ""):
            merged.setdefault(o["op"], o)
        framing = {"streamer", "podcast", "track", "vertical"}
        present = [k for k in merged if k in framing]
        keep = ("streamer" if (want_facecam and "streamer" in merged) else
                "podcast" if "podcast" in merged else
                "track" if "track" in merged else
                present[0] if present else None)
        for k in present:
            if k != keep:
                merged.pop(k, None)
        clip_ops = engine.order_ops(list(merged.values()))

        out = os.path.join(outdir, f"clip_{idx:02d}.mp4")
        try:
            result = engine.edit(cut, "", out, srt=srt, style=style, ops=clip_ops)
        except Exception as exc:
            clips.append({"index": idx, "error": str(exc), "window": c})
            continue

        desc = describe.describe(c["text"], [
            {"start": s["start"] - c["start"], "end": s["end"] - c["start"],
             "text": s["text"]}
            for s in segments
            if s["end"] > c["start"] and s["start"] < c["end"]
        ])
        layout = next((s for s in result.get("steps", [])
                       if s["op"] in ("streamer", "vertical", "track", "podcast")), {})
        clips.append({
            "index": idx,
            "file": out,
            "window": {"start": c["start"], "end": c["end"],
                       "duration": c["duration"]},
            "score": c["score"],
            "signals": c["signals"],
            "layout": layout.get("op"),
            "layout_detail": layout.get("layout"),
            "title": desc["title"],
            "summary": desc["summary"],
            "hashtags": desc["hashtags"],
            "result": result.get("result"),
        })

    produced = len([c for c in clips if c.get("file")])
    note = None
    if produced < n:
        note = (f"asked for {n}, produced {produced}: the free gaps left in the "
                f"source were shorter than the {min_len:.0f}s minimum clip length")
    manifest = {
        "ok": True,
        "requested": n,
        "produced": produced,
        "note": note,
        "source": os.path.abspath(src),
        "source_duration": round(engine.probe(src)["duration"], 3),
        "language": tr.get("language"),
        "candidates_considered": len(cands),
        "clips": clips,
        "outdir": outdir,
    }
    with open(os.path.join(outdir, "clips.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, default=float)
    say(f"done, {produced} clips" + (f" ({note})" if note else ""))
    return manifest


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="long video in, vertical clips out")
    ap.add_argument("video")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("-n", "--count", type=int, default=3)
    ap.add_argument("--min-len", type=float, default=15.0)
    ap.add_argument("--max-len", type=float, default=45.0)
    ap.add_argument("--model", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--no-facecam", action="store_true")
    a = ap.parse_args()
    res = auto_clip(a.video, a.outdir, a.count, a.min_len, a.max_len, a.model,
                    a.style, not a.no_facecam,
                    progress=lambda m: print("  ...", m, file=sys.stderr))
    print(json.dumps(res, indent=2, ensure_ascii=False, default=float))
