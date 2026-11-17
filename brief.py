"""Read a content brief and check the finished clip against it.

A reward brief is prose ("30-60 seconds, 9:16, captions, no swearing, mention
Acme in the first 10 seconds"). This module turns that prose into numbers the
renderer can obey, then checks the output against the same numbers so the owner
gets a pass/fail list instead of a promise.

Only measurable things are checked. Nothing here judges whether a clip is
"good", because that is not something this code can know.
"""
import re

PROFANITY = ("fuck", "fucking", "shit", "bitch", "asshole", "bastard", "damn",
             "cunt", "dick", "pussy", "whore", "slut")

_SEC = r"(?:s\b|sec\b|secs\b|second|seconds)"


def _norm(text):
    return (text or "").lower().replace("–", "-").replace("—", "-")


def parse_brief(text):
    """Return the constraints a brief asks for. Missing keys mean 'no opinion'."""
    t = _norm(text)
    b = {}

    m = re.search(r"(\d{1,3})\s*(?:-|to|se|say|and|aur|&)\s*(\d{1,3})\s*" + _SEC, t)
    if m:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        b["duration_min"], b["duration_max"] = lo, hi
    else:
        m = re.search(r"(?:under|below|max(?:imum)?|up to|less than|zyada nahi)\s*(\d{1,3})\s*" + _SEC, t)
        if m:
            b["duration_max"] = int(m.group(1))
        m = re.search(r"(?:at least|min(?:imum)?|over|more than|se kam nahi)\s*(\d{1,3})\s*" + _SEC, t)
        if m:
            b["duration_min"] = int(m.group(1))
        if "duration_min" not in b and "duration_max" not in b:
            m = re.search(r"(\d{1,3})\s*" + _SEC, t)
            if m and "hook" not in t[max(0, m.start() - 12):m.start()]:
                n = int(m.group(1))
                b["duration_min"], b["duration_max"] = max(3, n - 4), n + 4

    if re.search(r"9\s*[:x]\s*16|\bvertical\b|\bportrait\b|\bshorts?\b|\breels?\b|\btiktok\b", t):
        b["aspect"] = "9:16"
    elif re.search(r"16\s*[:x]\s*9|\bhorizontal\b|\blandscape\b|\byoutube\b", t):
        b["aspect"] = "16:9"
    elif re.search(r"\b1\s*[:x]\s*1\b|\bsquare\b", t):
        b["aspect"] = "1:1"

    if re.search(r"(?:no|without|bina|skip)\s+(?:captions?|subtitles?)|captions?\s+(?:off|nahi)", t):
        b["captions"] = False
    elif re.search(r"captions?|subtitles?|caption\s+style", t):
        b["captions"] = True

    m = re.search(r"hook[^.]{0,25}?(\d{1,2})\s*" + _SEC, t) or \
        re.search(r"first\s+(\d{1,2})\s*" + _SEC, t)
    if m:
        b["hook_seconds"] = int(m.group(1))

    if re.search(r"(?:no|without|bina)\s+(?:music|bgm)|music\s+(?:off|nahi)", t):
        b["music"] = False
    elif re.search(r"background music|\bbgm\b|\bmusic\b", t):
        b["music"] = True

    if re.search(r"\bsfx\b|sound effects?|sound design", t):
        b["sfx"] = True

    terms = []
    for pat in (r"(?:must|should|need to)?\s*(?:mention|say|include|call out|name)\s+"
                r"[\"']?([A-Za-z0-9][A-Za-z0-9 .&+_'-]{1,28})[\"']?",
                r"(?:brand|product|app|company)\s+(?:name\s+)?(?:is\s+)?[\"']?([A-Za-z0-9][A-Za-z0-9 .&+_'-]{1,28})"):
        for hit in re.findall(pat, t):
            term = hit.strip(" .,;:'\"")
            if len(term) > 1 and term not in terms:
                terms.append(term)
    if terms:
        b["brand_terms"] = terms[:5]

    banned = []
    for hit in re.findall(r"(?:don'?t|do not|no|avoid|never|mat)\s+(?:say|mention|use)\s+"
                          r"[\"']?([A-Za-z0-9][A-Za-z0-9 ._-]{1,28})[\"']?", t):
        term = hit.strip(" .,;:'\"")
        if len(term) > 1:
            banned.append(term)
    if re.search(r"no\s+(?:profanity|swearing|swear words?|gaali)|clean\s+language|family\s+friendly", t):
        banned.extend(PROFANITY[:6])
        b["clean_language"] = True
    if banned:
        b["banned_terms"] = sorted(set(banned))

    for plat in ("tiktok", "reels", "shorts", "youtube", "instagram", "twitch"):
        if plat in t:
            b["platform"] = plat
            break

    return b


def describe_brief(b):
    """One line the owner can read back to confirm the tool understood."""
    if not b:
        return "no measurable constraints found"
    bits = []
    if "duration_min" in b or "duration_max" in b:
        lo = b.get("duration_min", 0)
        hi = b.get("duration_max", "any")
        bits.append(f"{lo}-{hi}s")
    if b.get("aspect"):
        bits.append(b["aspect"])
    if b.get("captions") is True:
        bits.append("captions on")
    elif b.get("captions") is False:
        bits.append("captions off")
    if b.get("hook_seconds"):
        bits.append(f"hook in first {b['hook_seconds']}s")
    if b.get("music") is True:
        bits.append("music")
    elif b.get("music") is False:
        bits.append("no music")
    if b.get("sfx"):
        bits.append("sfx")
    if b.get("brand_terms"):
        bits.append("mention: " + ", ".join(b["brand_terms"]))
    if b.get("banned_terms"):
        bits.append("avoid: " + ", ".join(b["banned_terms"][:4]))
    return " · ".join(bits)


def compliance(brief, *, duration=None, size=None, steps=None, text="", segments=None):
    """Check a finished clip against the brief. Returns a list of pass/fail rows."""
    rows = []
    steps = steps or []
    kinds = {s.get("op") for s in steps}

    if "duration_min" in brief or "duration_max" in brief:
        lo, hi = brief.get("duration_min", 0), brief.get("duration_max", 10**6)
        ok = duration is not None and lo - 0.4 <= duration <= hi + 0.4
        rows.append({"check": "duration", "brief": f"{lo}-{hi}s",
                     "actual": f"{duration:.1f}s" if duration else "unknown", "pass": ok})

    if brief.get("aspect"):
        want = brief["aspect"]
        w = h = None
        if size and "x" in str(size):
            w, h = (int(x) for x in str(size).split("x"))
        if w and h:
            actual = "9:16" if h > w else "16:9" if w > h else "1:1"
        else:
            actual = "unknown"
        rows.append({"check": "aspect", "brief": want, "actual": actual, "pass": actual == want})

    if brief.get("captions") is True and steps:
        rows.append({"check": "captions", "brief": "burned in",
                     "actual": "yes" if "captions" in kinds else "no",
                     "pass": "captions" in kinds})

    if brief.get("music") is True and steps:
        rows.append({"check": "music", "brief": "present",
                     "actual": "yes" if "music" in kinds else "no", "pass": "music" in kinds})
    elif brief.get("music") is False and steps:
        rows.append({"check": "music", "brief": "none",
                     "actual": "yes" if "music" in kinds else "no", "pass": "music" not in kinds})

    if brief.get("sfx") and steps:
        rows.append({"check": "sfx", "brief": "present",
                     "actual": "yes" if "effects" in kinds or "sfx" in kinds else "no",
                     "pass": "effects" in kinds or "sfx" in kinds})

    low = _norm(text)
    for term in brief.get("brand_terms") or []:
        ok = term.lower() in low
        rows.append({"check": f"mention '{term}'", "brief": "spoken",
                     "actual": "said" if ok else "not said", "pass": ok})

    for term in brief.get("banned_terms") or []:
        ok = term.lower() not in low
        rows.append({"check": f"avoid '{term}'", "brief": "absent",
                     "actual": "absent" if ok else "present", "pass": ok})

    if brief.get("hook_seconds") and segments:
        window = brief["hook_seconds"]
        spoke = any(s.get("start", 99) < window and s.get("text", "").strip() for s in segments)
        rows.append({"check": "hook", "brief": f"speech in first {window}s",
                     "actual": "speech found" if spoke else "silent start", "pass": spoke})

    return rows


def verdict(rows):
    bad = [r for r in rows if not r["pass"]]
    if not rows:
        return True, "nothing measurable in this brief"
    if not bad:
        return True, f"all {len(rows)} checks pass"
    return False, f"{len(bad)} of {len(rows)} checks failed: " + \
                  ", ".join(r["check"] for r in bad[:4])
