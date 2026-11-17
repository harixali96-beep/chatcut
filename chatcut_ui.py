"""A ChatCut-shaped shell: a prompt-first home screen, then the editor.

The look follows app.chatcut.io: dark canvas, one big "what do you want to
create" box, category chips, preset cards, and inside a project a preview with
the tool row and a bottom tab bar. Everything here is plain Streamlit plus CSS,
so it stays free and needs no build step.
"""
import streamlit as st

PILLS = [
    ("Long video to shorts", "is lambi video ki 3 clips bana do, captions ke saath"),
    ("Add captions", "captions lagao, style auto"),
    ("Speech cleanup", "silence hatao aur filler words nikaalo"),
    ("Talking head edit", "shorts banao, captions lagao, cinematic lagao"),
    ("Podcast split", "podcast ko 9:16 me split karo"),
    ("Streamer layout", "facecam upar aur gameplay neeche, shorts banao"),
]

CSS = """
<style>
  .cc-hero {font-size:13px;letter-spacing:.14em;text-transform:uppercase;
            color:#8b8fa3;margin-bottom:6px}
  .cc-h1 {font-size:30px;font-weight:700;color:#f5f7fa;margin:0 0 14px 0;line-height:1.15}
  .cc-sub {color:#9aa0b4;font-size:13px;margin-bottom:18px}
  .cc-card {background:#141B2D;border:1px solid #242c42;border-radius:14px;
            padding:14px 16px;margin-bottom:8px}
  .cc-card b {color:#f5f7fa;font-size:14px}
  .cc-card span {color:#9aa0b4;font-size:12px}
  .cc-chip {display:inline-block;background:#1b2236;border:1px solid #2b3450;
            color:#c9cee0;border-radius:999px;padding:6px 14px;font-size:12px;
            margin:0 6px 8px 0}
  .cc-bar {display:flex;gap:10px;align-items:center;color:#9aa0b4;font-size:12px;
           border-top:1px solid #1d2439;padding-top:10px;margin-top:10px}
  .cc-dot {width:8px;height:8px;border-radius:50%;background:#7C3AED;display:inline-block}
  div[data-testid="stTextArea"] textarea {background:#141B2D;border:1px solid #2b3450;
            border-radius:14px;color:#f5f7fa;font-size:15px}
  .stButton button {border-radius:999px}
  div[data-testid="stSidebar"] {background:#0e1320}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def _rows(styles):
    """Group presets by their motion, which is the part that actually differs."""
    families = {}
    for s in styles:
        parts = s.split("-")
        motion = parts[-2] if len(parts) > 2 and parts[-1] in ("in", "up", "down") else parts[-1]
        families.setdefault(motion.replace("_", " "), []).append(s)
    order = ["karaoke", "pop", "bounce", "scale", "shake", "slide", "typewriter"]
    rows = [(k.title(), v) for k, v in families.items() if k in order]
    rows += [(k.title() + " variants", v) for k, v in families.items() if k not in order]
    return rows


def render_home(styles=()):
    """Returns True when the home screen is showing and the app should stop."""
    if st.session_state.get("view", "home") != "home":
        return False

    left, right = st.columns([1, 5])
    with left:
        st.markdown('<div class="cc-hero">chatcut</div>', unsafe_allow_html=True)
    with right:
        st.caption("free · local · no account · no API key")

    st.markdown('<div class="cc-h1">What do you want to create today?</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="cc-sub">Describe it, paste a YouTube / Twitch / Drive link, '
                'or upload footage. One box does all of it.</div>', unsafe_allow_html=True)

    text = st.text_area("prompt", height=120, key="home_prompt",
                        label_visibility="collapsed",
                        placeholder="Describe the video you want to make, or paste a link "
                                    "and tell me how to edit it…")
    c1, c2 = st.columns([4, 1])
    with c2:
        go = st.button("Create →", type="primary", use_container_width=True)

    if go:
        if text.strip():
            st.session_state.instruction = text.strip()
            st.session_state.view = "editor"
            st.rerun()
        else:
            st.warning("Pehle likho ya link paste karo, phir Create dabao.")

    st.markdown("".join(f'<span class="cc-chip">{p}</span>' for p, _ in PILLS),
                unsafe_allow_html=True)

    st.write("")
    st.markdown('<div class="cc-hero">start from a preset</div>', unsafe_allow_html=True)
    cols = st.columns(3)
    for i, (label, prompt) in enumerate(PILLS):
        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(f'<div class="cc-card"><b>{label}</b><br>'
                            f'<span>{prompt[:52]}…</span></div>', unsafe_allow_html=True)
                if st.button("Use this", key=f"preset_{i}", use_container_width=True):
                    st.session_state.instruction = prompt
                    st.session_state.view = "editor"
                    st.rerun()

    if styles:
        st.write("")
        for title, group in _rows(list(styles)):
            if not group:
                continue
            st.markdown(f'<div class="cc-hero">{title} · {len(group)}</div>',
                        unsafe_allow_html=True)
            st.caption("  ·  ".join(group[:14]) + ("  …" if len(group) > 14 else ""))

    st.markdown('<div class="cc-bar"><span class="cc-dot"></span> everything runs on this '
                'machine · ffmpeg + whisper · no paid API</div>', unsafe_allow_html=True)
    return True


def editor_header():
    """Top bar inside a project, with the way back."""
    left, mid, right = st.columns([1, 4, 1])
    with left:
        if st.button("← Home", use_container_width=True):
            st.session_state.view = "home"
            st.rerun()
    with mid:
        st.markdown('<div class="cc-hero">project · untitled</div>',
                    unsafe_allow_html=True)
    with right:
        st.caption("Export is the download button under the video")
