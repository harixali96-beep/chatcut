"""chatcut on Streamlit. Free public hosting path (Streamlit Community Cloud)."""
import os, sys, json, shutil, tempfile
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine, transcribe, autoclip, describe, router  # noqa: E402
try:
    import timeline_ui  # noqa: F401  (drag UI is Gradio-only; kept for the shared timeline JSON)
except Exception:
    timeline_ui = None

WORK = tempfile.mkdtemp(prefix="chatcut-st-")
os.makedirs(WORK, exist_ok=True)

st.set_page_config(page_title="chatcut", page_icon="✂️", layout="wide")
st.title("✂️ chatcut")
st.caption("Chat to edit video. Free, local, open source. ffmpeg + whisper, no paid API.")


def source_path(upload, link):
    """Return a local video path for an uploaded file or a link/path."""
    if upload is not None:
        dest = os.path.join(WORK, "in_" + os.path.basename(upload.name))
        with open(dest, "wb") as fh:
            fh.write(upload.getbuffer())
        return dest, "upload"
    u = (link or "").strip()
    if not u:
        return None, "give a file or a link"
    if u.startswith("file://"):
        u = u[7:]
    if os.path.exists(u):
        return u, "local path"
    try:
        import yt_dlp
        out = os.path.join(WORK, "link_%(id)s.%(ext)s")
        with yt_dlp.YoutubeDL({"outtmpl": out, "format": "mp4/best",
                               "quiet": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(u, download=True)
            path = ydl.prepare_filename(info)
        return path, "downloaded"
    except Exception as exc:
        return None, f"could not fetch the link: {exc}"


with st.sidebar:
    st.header("Source")
    upload = st.file_uploader("Upload a video", type=["mp4", "mov", "mkv", "webm", "m4v"])
    link = st.text_input("...or a link", placeholder="YouTube / Drive / direct MP4 URL")
    model_size = st.selectbox("Whisper model", ["tiny", "base", "small"], index=0)
    st.caption("tiny is fastest. bigger is more accurate and slower on CPU.")

tab_edit, tab_clips, tab_tl = st.tabs(["Edit", "Auto clips", "Timeline"])

with tab_edit:
    instr = st.text_area("What should it do?",
                         "silence hatao, shorts banao, captions lagao aur cinematic lagao",
                         height=90)
    c1, c2 = st.columns(2)
    with c1:
        style = st.selectbox("Caption style", ["auto"] + (engine.list_styles() or [])[:40])
    with c2:
        use_llm = st.checkbox("Use a local model if one is running", value=False,
                              help="Ollama or LM Studio on this machine. No API key, no cost.")
    music = st.file_uploader("Background music (optional)", type=["mp3", "m4a", "wav"])
    if st.button("Run edit", type="primary"):
        src, how = source_path(upload, link)
        if not src:
            st.error(how)
        else:
            music_path = None
            if music is not None:
                music_path = os.path.join(WORK, "music_" + os.path.basename(music.name))
                with open(music_path, "wb") as fh:
                    fh.write(music.getbuffer())
            ops = None
            if use_llm:
                plan = router.plan(instr)
                ops = plan["ops"]
                st.info(f"Router: {plan['source']}" + (f" ({plan.get('note')})" if plan.get("note") else ""))
            with st.spinner("editing..."):
                out = os.path.join(WORK, "edit_out.mp4")
                try:
                    res = engine.edit(src, instr, out, srt=None, music=music_path,
                                      style=style, model=model_size,
                                      want_description=True, ops=ops)
                except Exception as exc:
                    st.error(f"failed: {exc}")
                    res = None
            if res:
                st.video(res["output"])
                r = res.get("result") or {}
                st.success(f"{r.get('duration')}s · {r.get('size')} · audio {r.get('has_audio')}")
                with open(res["output"], "rb") as fh:
                    st.download_button("Download the video", fh.read(),
                                       file_name="chatcut_edit.mp4", mime="video/mp4")
                for step in res.get("steps", []):
                    st.write(step)
                tr = (res.get("transcript") or {}).get("srt_path")
                if tr and os.path.exists(tr):
                    with open(tr, "rb") as fh:
                        st.download_button("Download the captions (.srt)", fh.read(), file_name="captions.srt")
                desc = res.get("description")
                if desc and not desc.get("error"):
                    st.subheader("Description")
                    st.text(desc.get("description", ""))

with tab_clips:
    st.write("Point it at a long video and it picks the moments worth posting.")
    a1, a2, a3 = st.columns(3)
    with a1:
        want = st.number_input("How many clips", 1, 10, 3)
    with a2:
        lo = st.number_input("Shortest clip (s)", 5, 120, 18)
    with a3:
        hi = st.number_input("Longest clip (s)", 10, 180, 45)
    facecam = st.checkbox("Put a facecam on top, gameplay below", value=True)
    if st.button("Make clips", type="primary"):
        src, how = source_path(upload, link)
        if not src:
            st.error(how)
        else:
            with st.spinner("finding the good parts... this is the slow one"):
                try:
                    man = autoclip.auto_clip(src, os.path.join(WORK, "clips"), n=int(want),
                                            min_len=float(lo), max_len=float(hi),
                                            model=model_size, facecam=facecam)
                except Exception as exc:
                    st.error(f"failed: {exc}")
                    man = None
            if man:
                st.success(f"{man.get('produced')} of {man.get('requested')} clips")
                if man.get("note"):
                    st.warning(man["note"])
                for c in man.get("clips", []):
                    if not c.get("file"):
                        continue
                    st.subheader(f"{c['index']}. {c.get('title', '')}")
                    st.caption(f"{c['window']['start']}-{c['window']['end']}s · score {c['score']} · {c.get('layout')}")
                    st.video(c["file"])
                    with open(c["file"], "rb") as fh:
                        st.download_button(f"Download clip {c['index']}", fh.read(),
                                           file_name=os.path.basename(c["file"]), mime="video/mp4",
                                           key=f"dl{c['index']}")
                st.download_button("Download the manifest (JSON)",
                                   json.dumps(man, indent=1).encode(),
                                   file_name="clips.json", mime="application/json")

with tab_tl:
    st.write("Load the video, edit the numbers, reorder rows, then render.")
    if st.button("Load timeline"):
        src, how = source_path(upload, link)
        if not src:
            st.error(how)
        else:
            info = engine.probe(src)
            st.session_state["tl_src"] = src
            st.session_state["tl_rows"] = [{"start": 0.0, "end": round(info["duration"], 2)}]
            st.info(f"loaded {info['duration']:.1f}s from {how}")
    if st.session_state.get("tl_rows"):
        rows = st.data_editor(st.session_state["tl_rows"], num_rows="dynamic",
                              column_config={
                                  "start": st.column_config.NumberColumn("start (s)", min_value=0.0, step=0.1),
                                  "end": st.column_config.NumberColumn("end (s)", min_value=0.1, step=0.1)},
                              use_container_width=True, key="tl_editor")
        if st.button("Render timeline", type="primary"):
            segs = [{"start": float(r["start"]), "end": float(r["end"])}
                    for r in rows if float(r["end"]) > float(r["start"]) + 0.05]
            if not segs:
                st.error("no usable clips in the table")
            else:
                with st.spinner("rendering..."):
                    try:
                        res = engine.render_timeline(st.session_state["tl_src"], segs,
                                                     os.path.join(WORK, "timeline.mp4"))
                        st.video(res["output"])
                        st.success(f"{len(segs)} clips -> {res['result']['duration']}s")
                        with open(res["output"], "rb") as fh:
                            st.download_button("Download the render", fh.read(),
                                               file_name="timeline.mp4", mime="video/mp4")
                    except Exception as exc:
                        st.error(f"failed: {exc}")

st.divider()
st.caption("Everything runs on this machine. No account, no API key, no upload to a third party.")
