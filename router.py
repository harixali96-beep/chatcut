#!/usr/bin/env python3
"""
Optional local model router: the "free agent, no API key" part.

Straight talk first, because it decides the architecture:

There is no hosted free API that gives Claude or GPT quality with no key and no
limit. Every "free" hosted tier still needs a key, has a quota, and can change
its terms. What genuinely exists for free is a model running on your own machine
(Ollama, LM Studio, llama.cpp all serve an OpenAI-compatible endpoint) and a
parser that needs no model at all.

So chatcut does both:

- engine.parse_instruction turns a sentence into ops with regexes. Zero AI, zero
  key, zero cost, instant, and it never invents a render. This is the default.
- This module optionally asks a local model to do the routing when one is
  running. The model's answer is validated against the real op list before use,
  so a hallucinated op is dropped rather than silently doing something wrong.

If nothing is listening on the endpoint, everything falls back to the parser and
the tool still works. Nothing here can break an edit.

Environment:
    CHATCUT_LLM_URL    base url, default http://127.0.0.1:11434/v1  (Ollama)
    CHATCUT_LLM_MODEL  model name, default qwen2.5:3b
    CHATCUT_LLM=off    force the parser only
"""
import json
import os
import urllib.error
import urllib.request

import styles

DEFAULT_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen2.5:3b"

KNOWN_OPS = ["trim", "cut_silence", "speed", "podcast", "streamer", "track",
             "vertical", "zoom", "effects", "loudnorm", "captions", "sfx",
             "music", "fade"]


def endpoint():
    return os.environ.get("CHATCUT_LLM_URL", DEFAULT_URL).rstrip("/")


def model_name():
    return os.environ.get("CHATCUT_LLM_MODEL", DEFAULT_MODEL)


def disabled():
    return os.environ.get("CHATCUT_LLM", "").lower() in ("off", "0", "false", "no")


def _probe(timeout=1.2):
    """Cheap reachability check, so we never hang an edit waiting on a model."""
    try:
        req = urllib.request.Request(endpoint() + "/models", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def available():
    if disabled():
        return False
    return _probe()


SYSTEM = """You convert a video editing request into JSON. Reply with JSON only.

Allowed ops: %s

Rules:
- Return {"ops": [...]} with each item {"op": "<name>"} and optional params:
  trim: {seconds}; speed: {factor 0.25-4}; effects: {preset}; streamer: {top_ratio};
  podcast: {}; track: {mode: face|body}; zoom: {}; vertical: {}; captions: {style};
  music: {}; sfx: {}; fade: {}; loudnorm: {}; cut_silence: {}.
- Only include an op the user actually asked for. Never add captions, music or
  effects on your own.
- If the user says to remove something (hatao, remove, no captions), leave that
  op out entirely.
- The user mixes English and Roman Urdu.
""" % ", ".join(KNOWN_OPS)


def _sanitise(raw):
    """Drop anything that is not a real op, and clamp the numbers we accept."""
    out = []
    if isinstance(raw, dict):
        raw = raw.get("ops") or []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("op", "")).strip().lower()
        if name not in KNOWN_OPS:
            continue
        clean = {"op": name}
        if name == "speed":
            try:
                f = float(item.get("factor", item.get("speed", 1.5)))
                clean["factor"] = max(0.25, min(4.0, f))
            except Exception:
                clean["factor"] = 1.5
        if name == "trim":
            try:
                clean["seconds"] = max(0.5, min(7200.0, float(item["seconds"])))
            except Exception:
                pass
        if name == "effects":
            preset = str(item.get("preset", "")).strip()
            if preset:
                clean["preset"] = preset
        if name == "track":
            mode = str(item.get("mode", "face")).strip().lower()
            clean["mode"] = mode if mode in ("face", "body") else "face"
        if name == "streamer":
            try:
                clean["top_ratio"] = max(0.15, min(0.5, float(item.get("top_ratio", 0.30))))
            except Exception:
                clean["top_ratio"] = 0.30
        if name == "captions":
            style = str(item.get("style", "")).strip()
            if style and style in styles.style_names():
                clean["style"] = style
        out.append(clean)
    return out


def plan(instruction, timeout=8.0):
    """Return {"ops": [...], "source": "llm"|"rules", "note": ...}."""
    import engine

    if not disabled() and _probe():
        body = {
            "model": model_name(),
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": instruction},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            req = urllib.request.Request(
                endpoint() + "/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode())
            text = payload["choices"][0]["message"]["content"]
            ops = _sanitise(json.loads(text))
            if ops:
                return {"ops": ops, "source": "llm",
                        "model": model_name(),
                        "note": "routed by the local model, validated against the op list"}
            return {"ops": engine.parse_instruction(instruction), "source": "rules",
                    "note": "local model returned no usable op, used the parser"}
        except Exception as exc:
            return {"ops": engine.parse_instruction(instruction), "source": "rules",
                    "note": f"local model failed ({exc}), used the parser"}
    return {"ops": engine.parse_instruction(instruction), "source": "rules",
            "note": "no local model listening, used the built-in parser"}


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "facecam upar rakho, captions lagao, shorts banao"
    print("endpoint:", endpoint(), "| model:", model_name(), "| available:", available())
    print(json.dumps(plan(q), indent=2, ensure_ascii=False))
