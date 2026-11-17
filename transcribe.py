#!/usr/bin/env python3
"""
Transcription for chatcut. faster-whisper on CPU, word-level timestamps.

Free, local, no API key. The model downloads once on first use.
"""
import os

DEFAULT_MODEL = os.environ.get("CHATCUT_WHISPER", "tiny")

_MODEL_CACHE = {}


def _load(model_size=DEFAULT_MODEL):
    if model_size not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        _MODEL_CACHE[model_size] = WhisperModel(
            model_size, device="cpu", compute_type="int8")
    return _MODEL_CACHE[model_size]


def _srt_ts(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def to_srt(segments):
    out = []
    for i, seg in enumerate(segments, 1):
        out.append(f"{i}\n{_srt_ts(seg['start'])} --> {_srt_ts(seg['end'])}\n{seg['text']}")
    return "\n\n".join(out) + "\n"


def transcribe(media_path, model_size=None, language=None, max_words_per_line=5):
    """Return {text, words, segments, srt, language, language_probability, model}."""
    model_size = model_size or DEFAULT_MODEL
    model = _load(model_size)
    it, info = model.transcribe(
        media_path, language=language, word_timestamps=True, vad_filter=True,
        beam_size=1)

    segments, words, text_parts = [], [], []
    for s in it:
        text = s.text.strip()
        if not text:
            continue
        w = [{"word": x.word.strip(), "start": round(float(x.start), 2),
              "end": round(float(x.end), 2)} for x in (s.words or [])]
        segments.append({"start": round(float(s.start), 2),
                         "end": round(float(s.end), 2),
                         "text": text, "words": w})
        words.extend(w)
        text_parts.append(text)

    return {
        "text": " ".join(text_parts).strip(),
        "words": words,
        "segments": segments,
        "srt": to_srt(segments),
        "language": info.language,
        "language_probability": round(float(info.language_probability), 3),
        "model": model_size,
        "word_count": len(words),
    }


def transcribe_to_file(media_path, srt_path, **kw):
    r = transcribe(media_path, **kw)
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write(r["srt"])
    r["srt_path"] = srt_path
    return r


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="transcribe media to text + SRT")
    ap.add_argument("media")
    ap.add_argument("-o", "--out", default=None, help="SRT output path")
    ap.add_argument("--model", default=None)
    ap.add_argument("--lang", default=None)
    a = ap.parse_args()
    res = transcribe(a.media, a.model, a.lang)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(res["srt"])
        print("wrote", a.out)
    res.pop("words")
    res.pop("segments")
    print(json.dumps(res, indent=2, ensure_ascii=False))
