# chatcut on Streamlit (free hosting)

This folder is a ready-to-deploy Streamlit app. Same engine as the Gradio version:
chat to edit, auto clips, timeline, whisper captions, facecam/podcast layouts.

## Deploy free, in about three minutes

1. Create a new **public** GitHub repo and upload every file in this folder into it
   (keep `models/` and the folder structure).
2. Go to https://share.streamlit.io and sign in with GitHub.
3. Click **New app**, pick your repo, set the main file to `app.py`, and press **Deploy**.

Streamlit Community Cloud installs `requirements.txt` and the apt packages in
`packages.txt` for you (ffmpeg is the important one).

## Notes
- Free public apps sleep after inactivity and wake on the next visit.
- First transcription downloads the whisper model once, then it is cached.
- Everything runs on the host. No API key, no paid service, no third party upload.
