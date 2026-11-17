#!/usr/bin/env python3
"""chatcut MCP server: the editor as agent tools, over stdio.

Any MCP client (Claude Desktop, Claude Code, Codex, Cursor) can drive the
engine with these four tools. No API key, no network, no server to run:
the client spawns this process and talks JSON-RPC over stdin/stdout.

    {"command": "python3", "args": ["/abs/path/chatcut/mcp_server.py"]}
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import engine        # noqa: E402
import styles        # noqa: E402
import autoclip      # noqa: E402
import transcribe    # noqa: E402

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "chatcut_probe",
        "description": "Read duration, size, fps and audio presence of a video.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "local video path"}},
            "required": ["path"],
        },
    },
    {
        "name": "chatcut_styles",
        "description": "List the available animated caption style names.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "chatcut_edit",
        "description": (
            "Edit a video from a plain-language instruction. Understands English and "
            "Roman Urdu. Ops: trim, cut_silence, vertical, track (face/body), podcast "
            "split, facecam/streamer layout, zoom, effects, captions, sfx, music, fade, "
            "speed, loudnorm. Example: 'silence hatao, facecam upar rakho, captions lagao'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "instruction": {"type": "string"},
                "output": {"type": "string", "description": "optional output path"},
                "srt": {"type": "string", "description": "optional SRT path"},
                "style": {"type": "string", "description": "caption style name"},
                "music": {"type": "string", "description": "optional music file"},
            },
            "required": ["path", "instruction"],
        },
    },
    {
        "name": "chatcut_autoclip",
        "description": "Cut a long video into N vertical clips, scored on speech density, hook words, questions and length.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "count": {"type": "integer", "default": 3},
                "min_len": {"type": "number", "default": 15},
                "max_len": {"type": "number", "default": 45},
                "facecam": {"type": "boolean", "default": True},
            },
            "required": ["path"],
        },
    },
    {
        "name": "chatcut_transcribe",
        "description": "Transcribe a video with whisper and return the text plus an SRT path.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"},
                           "model": {"type": "string", "default": "tiny"}},
            "required": ["path"],
        },
    },
    {
        "name": "chatcut_brief",
        "description": (
            "Read a content brief (Whop reward rules etc.) and report the measurable "
            "constraints it asks for: duration, aspect, captions, hook, music, sfx, brand "
            "mention, banned words. Pass seconds/size/text to also check a finished clip."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "brief": {"type": "string"},
                "seconds": {"type": "number"},
                "size": {"type": "string", "description": "like 1080x1920"},
                "text": {"type": "string", "description": "clip transcript, to verify mentions"},
            },
            "required": ["brief"],
        },
    },
    {
        "name": "chatcut_autopilot",
        "description": (
            "Edit a video and never come back empty. If the sentence names no edit, the "
            "default plan runs: silence cut, 9:16, captions, loudness. Give a brief and its "
            "rules are folded in and then checked against the finished clip."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "local video path"},
                "instruction": {"type": "string", "description": "what to do, plain language"},
                "brief": {"type": "string", "description": "optional reward brief"},
                "output": {"type": "string"},
                "music": {"type": "string"},
            },
            "required": ["path"],
        },
    },
]


def _text(s):
    return {"content": [{"type": "text", "text": s if isinstance(s, str) else json.dumps(s, indent=1)}],
            "isError": False}


def _err(s):
    return {"content": [{"type": "text", "text": str(s)}], "isError": True}


def call_tool(name, args):
    if name == "chatcut_probe":
        return _text(engine.probe(args["path"]))
    if name == "chatcut_styles":
        return _text({"styles": styles.style_names()})
    if name == "chatcut_edit":
        out = args.get("output") or os.path.join(
            tempfile.mkdtemp(prefix="chatcut-mcp-"), "out.mp4")
        res = engine.edit(args["path"], args["instruction"], out,
                          srt=args.get("srt"), music=args.get("music"),
                          style=args.get("style"), want_description=True)
        keep = {k: res.get(k) for k in ("output", "plan", "steps", "result",
                                        "description", "exports")}
        return _text(keep)
    if name == "chatcut_autoclip":
        res = autoclip.auto_clip(args["path"], None,
                                 n=int(args.get("count", 3)),
                                 min_len=float(args.get("min_len", 15)),
                                 max_len=float(args.get("max_len", 45)),
                                 want_facecam=bool(args.get("facecam", True)))
        slim = {"produced": res.get("produced"), "requested": res.get("requested"),
                "note": res.get("note"),
                "clips": [{k: c.get(k) for k in ("index", "file", "title", "score",
                                                 "window", "result")}
                          for c in res.get("clips", [])]}
        return _text(slim)
    if name == "chatcut_brief":
        import brief as _b
        br = _b.parse_brief(args["brief"])
        rows = []
        if args.get("seconds") is not None or args.get("size"):
            rows = _b.compliance(br, duration=args.get("seconds"), size=args.get("size"),
                                 text=args.get("text", ""))
        return _text({"reads_as": _b.describe_brief(br), "constraints": br,
                      "checks": rows, "verdict": _b.verdict(rows)})
    if name == "chatcut_autopilot":
        import brief as _b
        br = _b.parse_brief(args.get("brief") or "")
        instr = args.get("instruction") or ""
        extra = []
        if br.get("captions"):
            extra.append("captions lagao")
        if br.get("music"):
            extra.append("background music dalo")
        if br.get("sfx"):
            extra.append("sfx lagao")
        if br.get("aspect") == "9:16":
            extra.append("shorts banao")
        if extra:
            instr = (instr.rstrip(" ,") + ", " + ", ".join(extra)).strip(", ")
        out = args.get("output") or os.path.join(
            tempfile.mkdtemp(prefix="chatcut-mcp-"), "out.mp4")
        res = engine.edit(args["path"], instr, out, music=args.get("music"),
                          want_description=True)
        r = res.get("result") or {}
        rows = _b.compliance(br, duration=r.get("duration"), size=r.get("size"),
                             steps=res.get("steps"),
                             text=(res.get("transcript") or {}).get("text", "")) if br else []
        keep = {k: res.get(k) for k in ("output", "plan", "steps", "result",
                                        "description", "note")}
        keep["brief"] = ({"reads_as": _b.describe_brief(br), "checks": rows,
                          "verdict": _b.verdict(rows)} if br else None)
        return _text(keep)
    if name == "chatcut_transcribe":
        tr = transcribe.transcribe(args["path"], model_size=args.get("model", "tiny"))
        return _text({"language": tr.get("language"), "text": tr.get("text"),
                      "srt": tr.get("srt_path"), "words": len(tr.get("words") or [])})
    return _err(f"unknown tool: {name}")


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "chatcut", "version": "1.0.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        try:
            result = call_tool(params.get("name"), params.get("arguments") or {})
        except Exception as exc:
            result = _err(f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    if method in ("notifications/initialized", "initialized"):
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        out = handle(req)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
