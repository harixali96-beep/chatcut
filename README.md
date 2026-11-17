# chatcut

**Chat to edit video. Free, open source, no paid API.**

Upload a clip or paste a YouTube / Drive link, then say what you want in plain
English or Roman Urdu:

> silence hatao, facecam upar rakho, captions lagao, shorts banao

It cuts, reframes, captions and renders locally with **ffmpeg + whisper**. No
API key, no credits, no account.

## What it does

| Op | Say something like | What happens |
|---|---|---|
| `trim` | "pehle 30 second lo" | keeps a section |
| `cut_silence` | "silence hatao" | removes pauses over 0.5s |
| `vertical` | "shorts banao" | crops to 1080x1920 |
| `track` | "face tracking karo" / "body track karo" | follow-cam on face or torso |
| `podcast` | "podcast split karo" | 2 speakers stacked, auto-reverts to one frame when only one person on screen |
| `streamer` | "facecam upar rakho" | facecam top strip + gameplay below |
| `zoom` | "zoom karo" | smooth punch-in |
| `effects` | "cinematic lagao" | 19 grades (bw, vintage, noir, glitch, vhs, ...) |
| `captions` | "captions lagao" | 120 animated styles, retimed after cuts |
| `sfx` | "sfx dalo" | synthesised whooshes on cuts |
| `music` | "background music dalo" | bed with sidechain ducking under speech |
| `loudnorm` | "awaz theek karo" | -16 LUFS |

Plus **Auto clips**: point it at a long stream VOD and it scores every window on
speech density, hook words, questions and length, then renders the best ones as
vertical captioned clips with a title, summary and hashtags.

## Interfaces

- **Edit** tab - chat to edit, descriptions, `.srt` download
- **Auto clips** tab - long video in, N shorts out
- **Timeline** tab - trim and reorder clips, then render

Also exports timeline JSON, **EDL** and **FCPXML** (opens in Premiere / Resolve).

## Run locally

```bash
pip install -r requirements.txt
# system packages: ffmpeg (see packages.txt)
streamlit run app.py
```

## Deploy free (Streamlit Community Cloud)

1. Go to **https://share.streamlit.io** and sign in with GitHub.
2. **New app** -> repository `harixali96-beep/chatcut` -> branch `main` -> main file `app.py`.
3. **Deploy**. `packages.txt` installs ffmpeg for you.

Public apps are free. No card, no subscription.

## Optional: a local model instead of the built-in parser

The built-in parser needs no AI at all. If you want free-form language, run
[Ollama](https://ollama.com) locally and the tool will use it automatically:

```bash
ollama pull qwen2.5:3b
export CHATCUT_LLM_URL=http://127.0.0.1:11434/v1
export CHATCUT_LLM_MODEL=qwen2.5:3b
```

Every op the model asks for is checked against the real op list first, so a
hallucinated op can never reach ffmpeg.

## Honest limits

- Clip scoring is heuristic, not a trained virality model.
- Face tracking uses YuNet (faces) with face-anchored body framing; it is not skeletal tracking.
- Transcription runs on CPU: `tiny` is fast, bigger models are slower.
- There is no hosted free frontier-quality LLM API. The tool is designed to need none.

## License

MIT
