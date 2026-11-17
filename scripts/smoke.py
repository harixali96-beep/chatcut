"""Fast checks that catch the bug classes this project keeps hitting.

Run locally: python scripts/smoke.py
CI runs the same file, so a red build means one of these broke.

No ffmpeg rendering here on purpose: this must finish in seconds.
"""
import ast
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

FAILS = []


def check(name, fn):
    try:
        detail = fn()
        print(f"PASS  {name}  {detail or ''}")
    except Exception as exc:  # noqa: BLE001 - a check failing IS the point
        FAILS.append((name, repr(exc)))
        print(f"FAIL  {name}  {exc!r}")


# 1. every module must import ------------------------------------------------
MODULES = ["engine", "styles", "brief", "router", "autoclip", "timeline_ui",
           "chatcut_ui", "describe", "fetchsource", "mcp_server", "transcribe"]
for _m in MODULES:
    check(f"import {_m}", lambda _m=_m: (importlib.import_module(_m), "")[1])

# 2. no mod.attr may point at a name that does not exist ---------------------
#    This exact scan caught two real crashes: engine.style_names and
#    brief.read_brief_file. It runs before anything is rendered, so the class
#    of bug cannot reach the user again.
def check_attributes():
    local = {f[:-3] for f in os.listdir(".") if f.endswith(".py")}
    bad = []
    for fn in sorted(local):
        tree = ast.parse(open(fn + ".py").read())
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in local:
                        aliases[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom) and node.module in local:
                for a in node.names:
                    aliases[a.asname or a.name] = node.module
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in aliases):
                mod = importlib.import_module(aliases[node.value.id])
                if not hasattr(mod, node.attr):
                    bad.append(f"{fn}.py:{node.lineno} {node.value.id}.{node.attr}")
    if bad:
        raise AssertionError("missing attributes: " + "; ".join(bad))
    return f"{len(local)} modules clean"


check("no missing attributes", check_attributes)

# 3. the parser on the sentences real users type -----------------------------
import engine  # noqa: E402

SENTENCES = {
    "silence hatao, captions lagao, shorts banao": ["cut_silence", "vertical", "captions"],
    "facecam upar rakho": ["streamer"],
    "cinematic lagao": ["effects"],
    "speed 1.5x karo": ["speed"],
    "podcast split karo": ["podcast"],
    "zoom in karo": ["zoom"],
    "": [],
    "🎬🎬🎬": [],
}

for text, expected in SENTENCES.items():
    check(f"parse {text[:30]!r}",
          lambda text=text, expected=expected: (
              (lambda got: (got == expected) or (_ for _ in ()).throw(
                  AssertionError(f"expected {expected}, got {got}")))
              ([o["op"] for o in engine.parse_instruction(text)]), "")[1])

# 4. a default plan must never shorten the video -----------------------------
def default_plan_is_safe():
    ops = [o["op"] for o in engine.DEFAULT_OPS]
    if "cut_silence" in ops:
        raise AssertionError("cut_silence must stay opt-in; it silently shrank a 4-min video")
    return ops


check("default plan keeps runtime", default_plan_is_safe)

# 5. briefs: parse, check, and read real files -------------------------------
import brief  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402


def brief_roundtrip():
    b = brief.parse_brief("30-60 seconds, 9:16, captions, mention Acme, no swearing")
    rows = brief.compliance(b, duration=42, size="1080x1920",
                            steps=[{"op": "captions"}], text="the acme app")
    ok, msg = brief.verdict(rows)
    if not ok:
        raise AssertionError(msg)
    return msg


check("brief parses and checks", brief_roundtrip)


def brief_reads_files():
    tmp = tempfile.mkdtemp()
    txt = os.path.join(tmp, "b.txt")
    open(txt, "w").write("45 seconds, 9:16, no swearing")
    docx = os.path.join(tmp, "b.docx")
    xml = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body><w:p><w:r>'
           "<w:t>Brief: 45 seconds, 9:16</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", xml)
    for path in (txt, docx):
        text, note = brief.read_brief_file(path)
        if not text:
            raise AssertionError(f"{os.path.basename(path)} read nothing: {note}")
    text, note = brief.read_brief_file(os.path.join(tmp, "nope.pdf"))
    if text:
        raise AssertionError("a missing file must read as empty, not raise")
    return "txt + docx + missing-file handling"


check("read_brief_file", brief_reads_files)

# 6. the agent connection exposes the whole toolkit --------------------------
def mcp_tools():
    import mcp_server
    names = [t["name"] for t in mcp_server.TOOLS]
    for need in ("chatcut_edit", "chatcut_autopilot", "chatcut_brief",
                 "chatcut_autoclip", "chatcut_transcribe", "chatcut_styles",
                 "chatcut_probe"):
        if need not in names:
            raise AssertionError(f"MCP is missing {need}")
    return f"{len(names)} tools"


check("mcp tool surface", mcp_tools)

# 7. deploy files still parse ------------------------------------------------
def deploy_files():
    import tomllib
    cfg = tomllib.load(open(".streamlit/config.toml", "rb"))
    if not cfg["server"]["headless"]:
        raise AssertionError("headless must be true for a server deploy")
    try:
        import yaml
    except ImportError:
        return "config.toml ok, yaml skipped (pyyaml not installed)"
    yaml.safe_load(open("render.yaml"))
    if not os.path.exists("Dockerfile"):
        raise AssertionError("Dockerfile is referenced by render.yaml but missing")
    return "config.toml + render.yaml + Dockerfile"


check("deploy files", deploy_files)

# verdict --------------------------------------------------------------------
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    for name, err in FAILS:
        print(f"  - {name}: {err}")
    sys.exit(1)

print("all checks passed")
