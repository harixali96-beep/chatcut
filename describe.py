#!/usr/bin/env python3
"""
Descript-style descriptions from a transcript.

Extractive and deterministic on purpose: no LLM, no API key, no cost, and the
output can never claim something the speaker did not say. Good enough for a
title, a summary, hashtags and chapters; not a replacement for a writer.
"""
import re

STOP = set("""a about above after again against all am an and any are aren't as at be
because been before being below between both but by can can't cannot could couldn't did
didn't do does doesn't doing don't down during each few for from further had hadn't has
hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his
how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most
mustn't my myself no nor not of off on once only or other ought our ours ourselves out over
own same shan't she she'd she'll she's should shouldn't so some such than that that's the
their theirs them themselves then there there's these they they'd they'll they're they've
this those through to too under until up very was wasn't we we'd we'll we're we've were
weren't what what's when when's where where's which while who who's whom why why's with
won't would wouldn't you you'd you'll you're you've your yours yourself yourselves gonna
wanna kinda really just like know think yeah okay right well actually basically literally
""".split())

SPLIT = re.compile(r"(?<=[.!?])\s+")


def _tokens(text):
    return [w.lower().strip(".,!?;:\"'()") for w in re.findall(r"[A-Za-z][A-Za-z'-]+", text)]


def keywords(text, limit=8):
    toks = [t for t in _tokens(text) if t and t not in STOP and len(t) > 3]
    freq = {}
    for t in toks:
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [w for w, _ in ranked[:limit]]


def sentences(text):
    parts = [s.strip() for s in SPLIT.split(text) if s.strip()]
    return [p for p in parts if len(p.split()) > 3]


def title(text, max_words=11):
    """Most keyword-dense short sentence, falling back to the opening words."""
    cands = sentences(text)
    if not cands:
        words = text.split()[:max_words]
        return " ".join(words).strip(" ,.") or "Untitled clip"
    kw = set(keywords(text, 12))
    best, best_score = cands[0], -1
    for s in cands[:12]:
        words = [w.lower().strip(".,!?") for w in s.split()]
        if not (3 <= len(words) <= 18):
            continue
        score = sum(1 for w in words if w in kw) / (len(words) ** 0.5)
        if score > best_score:
            best, best_score = s, score
    words = best.split()
    if len(words) > max_words:
        words = words[:max_words]
    out = " ".join(words).strip(" ,.;:-")
    return (out[:1].upper() + out[1:]) if out else "Untitled clip"


def summary(text, max_sentences=3):
    cands = sentences(text)
    if not cands:
        return text.strip()[:280]
    kw = set(keywords(text, 15))
    def score(s):
        w = [x.lower().strip(".,!?") for x in s.split()]
        return sum(1 for x in w if x in kw) / (len(w) ** 0.6)
    ranked = sorted(cands, key=score, reverse=True)[:max_sentences]
    ordered = [s for s in cands if s in ranked]
    return " ".join(ordered)[:600]


def hashtags(text, limit=8):
    tags = []
    for w in keywords(text, limit * 2):
        clean = re.sub(r"[^a-z0-9]", "", w)
        if len(clean) < 4:
            continue
        tags.append("#" + clean)
        if len(tags) >= limit:
            break
    return tags


def chapters(segments, target=5):
    """Split the transcript into roughly equal timed chapters."""
    if not segments:
        return []
    n = min(target, max(1, len(segments) // 3 or 1))
    per = max(1, len(segments) // n)
    out = []
    for i in range(0, len(segments), per):
        block = segments[i:i + per]
        if not block:
            continue
        txt = " ".join(b["text"] for b in block)
        out.append({
            "start": block[0]["start"],
            "end": block[-1]["end"],
            "label": title(txt, max_words=7),
        })
    return out[:n]


def _tc(sec):
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


def describe(text, segments=None, duration=None, platform="shorts"):
    """Full description block: title, summary, hashtags, chapters, caption text."""
    t = title(text)
    s = summary(text)
    tags = hashtags(text)
    ch = chapters(segments or [])
    hook = (sentences(text) or [text])[0][:120]
    lines = [t, "", s, ""]
    if ch:
        lines.append("Chapters:")
        for c in ch:
            lines.append(f"{_tc(c['start'])} {c['label']}")
        lines.append("")
    lines.append(" ".join(tags))
    return {
        "method": "extractive (no LLM)",
        "platform": platform,
        "title": t,
        "hook": hook,
        "summary": s,
        "hashtags": tags,
        "chapters": ch,
        "description": "\n".join(lines).strip(),
    }


if __name__ == "__main__":
    import argparse
    import json
    import sys
    sys.path.insert(0, ".")
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    import transcribe as tr
    r = tr.transcribe(a.media, a.model)
    d = describe(r["text"], r["segments"], r.get("duration"))
    print(json.dumps(d, indent=2, ensure_ascii=False))
