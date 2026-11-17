# chatcut engine v2

Chat to edit video. Free, local, open source (MIT). Pure Python + ffmpeg.

You type a sentence. It parses the sentence into an ordered edit plan, executes that
plan with ffmpeg, and hands back the media, a timeline, and an EDL/FCPXML you can open
in Premiere, Resolve or Final Cut. No API keys, no credits, no account, no cloud.

## Install

```bash
sudo apt install ffmpeg          # or: brew install ffmpeg
pip install opencv-python-headless   # only for face tracking
python3 engine.py --help
```

## Use

```bash
# the whole stack in one sentence
python3 engine.py in.mp4 "silence hatao, face tracking karo, zoom karo, captions lagao, sfx dalo aur cinematic effect lagao" \
  -o out.mp4 --srt cap.srt --timeline-dir tl/

python3 engine.py in.mp4 "pehle 45 seconds lo, 1.5x speed karo aur fade lagao" -o out.mp4
python3 engine.py in.mp4 "shorts banao, captions lagao aur background music dalo" -o out.mp4 --srt cap.srt --music bed.m4a
python3 engine.py in.mp4 "kaunsa plan banega" -o out.mp4 --plan-only
python3 engine.py --list-styles     # 120 caption styles
python3 engine.py --list-effects    # 19 effects
```

## Interfaces

```bash
python3 app.py          # browser UI: upload / link, chat instruction, draggable timeline
python3 transcribe.py in.mp4 -o out.srt
python3 rules_pdf.py    # writes chatcut-editing-rules.pdf
```

Two tabs: **Edit** (chat instruction, output, description, SRT) and **Timeline**
(drag clip edges to trim, drag a clip sideways to reorder, then render).

## Streamer and gameplay footage

```bash
python3 engine.py stream.mp4 "facecam upar rakho, gameplay neechay, captions lagao" -o out.mp4
```

A small talking head parked in a corner is treated as a facecam: it becomes a strip
on top and the gameplay is cropped underneath. The corner and box are found from the
footage, and if no small corner face holds for most of the clip the step is skipped
with a reason instead of guessing.

## Long video to clips

```bash
python3 autoclip.py vod.mp4 -n 3 --min-len 15 --max-len 45
```

Transcribes once, scores every sentence window on speech density, hook words,
questions and numbers, then cuts the best non-overlapping windows into vertical
captioned clips with a title, summary and hashtags. Writes clips.json next to them.

## Router

`router.py` optionally asks a local OpenAI-compatible model (Ollama, LM Studio) to
route the sentence, and validates the answer against the real op list. With nothing
listening it falls back to the built-in parser, so no key is ever required.

## Ops

| op | triggers | what it does |
|---|---|---|
| `trim` | "pehle 30 second", "first 2 minutes" | keep the head |
| `cut_silence` | "silence hatao", "remove pauses" | silences cut, segments rejoined, cut points recorded |
| `track` | "face tracking karo", "body track karo" | YuNet detection, smoothed path, 9:16 follow-cam. `body` anchors on the chest instead of the head |
| `podcast` | "podcast hai, upar neechay split karo" | two tracked panels top/bottom, switching back to a single full frame the moment only one person is on screen |
| `vertical` | "shorts", "9:16", "reels" | 1080x1920 cover crop |
| `zoom` | "zoom karo", "camera movement" | slow punch-in over the clip |
| `effects` | "cinematic", "vintage", "noir", ... | 19 graded looks, single filter chain |
| `loudnorm` | "awaz theek karo" | EBU R128 to -16 LUFS |
| `speed` | "1.5x", "2 guna" | retime picture and sound |
| `fade` | "fade lagao" | fade in/out |
| `captions` | "captions lagao" | 120 ASS styles, retimed after cuts |
| `sfx` | "sfx dalo" | synthesised whoosh on every cut point |
| `music` | "background music dalo" | bed with real sidechain ducking, 8:1, dips under speech |

Negation is keyword-local: "captions lagao" adds captions, "captions hata do" and
"captions nahi chahiye" remove them, and an unrelated "silence hatao" elsewhere in the
sentence does not switch captions off.

## Verified runs

Real renders, not claims:

- face tracking: 42 faces detected on a 10s pan, path simplified 34 points to 5, subject held within 8-11 px of centre through the whole clip, clamped correctly when the source ran out of frame
- silence + track + zoom + captions + sfx + cinematic: 10.00s in, 2.69s of silence removed, 3 segments, 1080x1920, 31 faces tracked, 2 SFX placed, 16 caption lines retimed onto the cut timeline, EDL and FCPXML written, 14.2s wall time
- trim + speed + fade: 6.00s trimmed at 1.5x with fades, 4.08s out
- podcast split: 2 speakers, 3.55s silence removed, two 1080x960 tracked panels stacked into 1080x1920, cinematic grade, 16 caption lines retimed, ducked music bed, EDL and FCPXML written, 8.44s out
- body tracking keeps the chest in frame; face mode crops at the neck. Single-face footage is correctly refused by the podcast split ("needs two faces on screen at the same time")
- dynamic podcast layout: 12s clip with a second speaker present only 0-4s and 8-12s produced split 0-4.08, single 4.08-8.16, split 8.16-12.0, verified by face position per panel in the rendered file
- draggable timeline: driven in a real browser, docking 9 tested trim/reorder cases (node unit tests), then dragging a clip edge changed 0:12 to 2.806s and the render produced exactly 2.806s with audio
- rules PDF: 5 pages, 8385 extractable characters, every section verified present
- streamer layout: facecam found top-left at 100% sample coverage, rendered 1080x576 strip + 1080x1344 gameplay, and face detection on the output confirmed 1 face in the strip and 0 in the gameplay area at every sampled timestamp
- auto clips: 60s source, 60 windows scored, 1 clip produced (24s) with the facecam layout, captions and description; the run reports when the remaining gaps are too short for another clip
- router: with no model listening every request fell back to the parser; a hallucinated op, an out-of-range speed and an invalid track mode were all dropped or clamped

## Still open

- Hugging Face deployment itself: the Space folder is ready (SPACE_README.md,
  requirements.txt, packages.txt) but publishing needs the owner's account token.
- GitHub skill import: the ops were written natively instead of vendoring
  third-party repos, which would add mixed licences and languages.

## Not included yet

- transcription: bring an SRT for now, or wire faster-whisper into one function
- speaker switching: the tracker follows the largest face; the podcast layout stacks both speakers instead of cutting between them
- person segmentation: body framing is anchored on the detected face, there is no skeleton or YOLO person model in the sandbox (the ONNX host was unreachable)
- SFX library: whoosh and impact are synthesised, there is no sample pack
- the web UI and the chat surface

## Merged from other open-source clippers

Ideas ported (not vendored) from two MIT projects, with the algorithms
reimplemented against this engine so the free deploy stays dependency-free:

- **openshorts** (mutonby) — their `split_layout` note that a stacked frame puts
  the captions on the seam where they cover nobody: captions now anchor to the
  seam for both stacked layouts (podcast 0.5, facecam at the panel edge) instead
  of sitting on the lower speaker's face. Their `active_speaker` mouth-motion
  plus audio-energy attribution is ported as `track.speaker_activity`.
- **Openclip** (aionixOS) — confirmation that the reframe decision belongs to a
  per-video layout picker, which this engine already does deterministically.

Deliberately **not** merged: their torch/ultralytics/mediapipe/Gemini stack. It
would add gigabytes and API keys to a tool whose whole point is running free.

### The active-speaker gate is off by default

`SPEAKER_SIGNAL=1` lets mouth motion plus audio energy refuse a stacked layout
when one voice holds the floor. Upstream ships this off and so does this, for a
reason found in testing: the heuristic misfired on a two-face clip that geometry
handled correctly. The numbers are always reported in the step output, so you can
see the verdict without it silently changing the edit.

## Use it as an MCP server

    python3 mcp_server.py

Speaks JSON-RPC over stdio, so any MCP client can drive the editor:

    {"mcpServers": {"chatcut": {"command": "python3",
      "args": ["/absolute/path/to/mcp_server.py"]}}}

Tools: `chatcut_probe`, `chatcut_styles`, `chatcut_edit`, `chatcut_autoclip`,
`chatcut_transcribe`.
