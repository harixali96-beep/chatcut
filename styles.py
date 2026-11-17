#!/usr/bin/env python3
"""
120 animated caption styles, generated as ASS.

15 palettes x 8 motions. Every style is a plain ASS Style line plus per-line
animation tags, so it burns with the stock ffmpeg subtitles filter.
No plugins, no paid fonts, no render service.
"""
import re

# name -> (primary, secondary, outline, back, bold, fontsize, border, shadow, uppercase)
PALETTES = {
    "bold-white":   ("&H00FFFFFF", "&H0000E5FF", "&H00000000", "&H80000000", 1, 17, 3, 0, 1),
    "bold-yellow":  ("&H0000E5FF", "&H00FFFFFF", "&H00000000", "&H80000000", 1, 17, 3, 0, 1),
    "neon-green":   ("&H0039FF14", "&H00FFFFFF", "&H00000000", "&H80000000", 1, 17, 3, 0, 1),
    "hot-pink":     ("&H00B478FF", "&H00FFFFFF", "&H00202020", "&H80000000", 1, 17, 3, 0, 1),
    "electric-blue":("&H00FFB400", "&H00FFFFFF", "&H00000000", "&H80000000", 1, 17, 3, 0, 1),
    "crimson":      ("&H002020E0", "&H00FFFFFF", "&H00000000", "&H80000000", 1, 17, 3, 0, 1),
    "mint":         ("&H00C8FF96", "&H00FFFFFF", "&H00101010", "&H80000000", 1, 17, 3, 0, 1),
    "orange-pop":   ("&H000080FF", "&H00FFFFFF", "&H00000000", "&H80000000", 1, 17, 3, 0, 1),
    "violet":       ("&H00FF64C8", "&H00FFFFFF", "&H00000000", "&H80000000", 1, 17, 3, 0, 1),
    "sand":         ("&H00C0DCFF", "&H00FFFFFF", "&H00202020", "&H80000000", 1, 17, 3, 0, 1),
    "mono-box":     ("&H00FFFFFF", "&H0000E5FF", "&H00101010", "&HFF000000", 1, 16, 2, 0, 1),
    "invert":       ("&H00000000", "&H00000000", "&H00FFFFFF", "&H40FFFFFF", 1, 17, 3, 0, 1),
    "cream":        ("&H00E8F5FF", "&H00FFFFFF", "&H00303030", "&H80000000", 1, 17, 3, 0, 0),
    "gold":         ("&H0040C8FF", "&H00FFFFFF", "&H00101010", "&H80000000", 1, 18, 3, 1, 1),
    "ice":          ("&H00FFF5E1", "&H00FFFFFF", "&H00202020", "&H80000000", 1, 17, 3, 0, 1),
}

# name -> (enter tag, per-word tag, exit fade seconds)
MOTIONS = {
    "pop":         ("\\fscx60\\fscy60\\t(0,120,\\fscx100\\fscy100)", "", 0.08),
    "slide-up":    ("\\move(540,1120,540,1060,0,160)", "", 0.08),
    "slide-down":  ("\\move(540,960,540,1020,0,160)", "", 0.08),
    "bounce":      ("\\fscx70\\fscy130\\t(0,100,\\fscx110\\fscy90)\\t(100,220,\\fscx100\\fscy100)", "", 0.08),
    "typewriter":  ("", "", 0.0),
    "karaoke":     ("", "\\k", 0.06),
    "shake":       ("\\t(0,60,\\frz3)\\t(60,120,\\frz-3)\\t(120,180,\\frz0)", "", 0.06),
    "scale-in":    ("\\fscx0\\fscy0\\t(0,180,\\fscx100\\fscy100)", "", 0.10),
}

STYLES = {}
for pname, p in PALETTES.items():
    for mname, m in MOTIONS.items():
        STYLES[f"{pname}-{mname}"] = {
            "palette": pname,
            "motion": mname,
            "primary": p[0], "secondary": p[1], "outline_color": p[2],
            "back": p[3], "bold": p[4], "fontsize": p[5],
            "border": p[6], "shadow": p[7], "uppercase": p[8],
            "enter": m[0], "word_tag": m[1], "fade": m[2],
        }

assert len(STYLES) == 120, len(STYLES)


def style_names():
    return sorted(STYLES)


def pick_style(instruction="", index=None):
    """Choose a style from the chat text, else deterministically by index."""
    t = (instruction or "").lower()
    names = style_names()
    for s in names:
        if s.replace("-", " ") in t or s in t:
            return s
    keys = ["yellow", "pink", "green", "blue", "red", "violet", "gold", "mint",
            "orange", "mono", "invert", "cream", "ice", "sand", "white"]
    for i, k in enumerate(keys):
        if k in t:
            motion = "pop"
            for m in MOTIONS:
                if m.replace("-", " ") in t:
                    motion = m
                    break
            return f"{[p for p in PALETTES if k in p][0]}-{motion}"
    if index is None:
        index = 0
    return names[index % len(names)]


TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _srt_to_segments(srt_text):
    segs = []
    for block in re.split(r"\n\s*\n", srt_text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.search(r"(-->|->)", lines[1] if "-->" in lines[1] else lines[0])
        tl = lines[1] if "-->" in lines[1] else lines[0]
        text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
        a, b = [x.strip() for x in tl.split("-->")[:2]]
        ma, mb = TS.match(a), TS.match(b)
        if not (ma and mb):
            continue
        st = int(ma[1]) * 3600 + int(ma[2]) * 60 + int(ma[3]) + int(ma[4]) / 1000
        en = int(mb[1]) * 3600 + int(mb[2]) * 60 + int(mb[3]) + int(mb[4]) / 1000
        segs.append({"start": st, "end": en, "text": " ".join(text_lines).strip()})
    return segs


def _split_words(seg):
    """Distribute a line's duration across its words by character length."""
    words = seg["text"].split()
    if not words:
        return []
    total = sum(len(w) for w in words) or 1
    span = max(0.05, seg["end"] - seg["start"])
    out, cur = [], seg["start"]
    for w in words:
        d = span * len(w) / total
        out.append({"word": w, "start": cur, "end": cur + d})
        cur += d
    return out


def _ass_ts(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_ass(segments_or_srt, style_name, play_res=(1080, 1920),
              max_words=5, margin_v=None, anchor_frac=None):
    # anchor_frac: vertical centre for the caption block, 0=top 1=bottom.
    # A stacked layout has a seam where the two panels meet, and captions
    # belong there (ported from openshorts split_layout): the frame bottom
    # is the lower speaker face.
    """Return ASS text. Accepts SRT text or a list of segments."""
    st = STYLES.get(style_name) or STYLES[pick_style()]
    if isinstance(segments_or_srt, str):
        segments = _srt_to_segments(segments_or_srt)
    else:
        segments = segments_or_srt

    w, h = play_res
    margin_v = margin_v if margin_v is not None else int(h * 0.15)
    align = 2
    if anchor_frac is not None:
        a = max(0.02, min(0.98, float(anchor_frac)))
        line_h = int(h * 0.045)
        if a <= 0.5:
            align, margin_v = 8, max(0, int(a * h) - line_h // 2)
        else:
            align, margin_v = 2, max(0, int((1.0 - a) * h) - line_h // 2)

    def up(x):
        return x.upper() if st["uppercase"] else x

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{st['fontsize']},{st['primary']},{st['secondary']},{st['outline_color']},{st['back']},{st['bold']},0,0,0,100,100,0,0,1,{st['border']},{st['shadow']},{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for seg in segments:
        words = _split_words(seg)
        if not words:
            continue
        # group into lines of max_words
        for i in range(0, len(words), max_words):
            chunk = words[i:i + max_words]
            start, end = chunk[0]["start"], chunk[-1]["end"] + 0.05
            if end <= start:
                end = start + 0.4
            if st["motion"] == "karaoke":
                parts = []
                for wd in chunk:
                    cs = max(1, int(round((wd["end"] - wd["start"]) * 100)))
                    parts.append("{\\k%d}%s" % (cs, up(wd["word"])))
                body = " ".join(parts)
            else:
                txt = up(" ".join(wd["word"] for wd in chunk))
                body = "{%s}%s" % (st["enter"], txt) if st["enter"] else txt
            if st["fade"]:
                body = "{\\fad(%d,%d)}%s" % (int(st["fade"] * 1000), int(st["fade"] * 1000), body)
            events.append(
                f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Default,,0,0,0,,{body}")
    return header + "\n".join(events) + "\n"


if __name__ == "__main__":
    print(f"{len(STYLES)} styles")
    print(", ".join(style_names()[:12]) + ", ...")
