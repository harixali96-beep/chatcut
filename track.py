#!/usr/bin/env python3
"""
Face tracking for auto reframe. OpenCV YuNet (232 KB ONNX, MIT-friendly).

Produces a smoothed crop path, then hands ffmpeg a piecewise-linear crop
expression so the frame follows the subject without a visible pan.

If no face is found the path falls back to a weighted centre crop, so the
pipeline never hard-fails on footage without people.
"""
import json
import os
import subprocess
import tempfile

import cv2
import numpy as np
from statistics import median

MODEL_URLS = [
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx",
]
MODEL_NAME = "face_detection_yunet_2023mar.onnx"


def model_path():
    for base in (os.path.join(os.path.dirname(__file__), "models"), "/tmp"):
        p = os.path.join(base, MODEL_NAME)
        if os.path.exists(p) and os.path.getsize(p) > 10000:
            return p
    target_dir = os.path.join(os.path.dirname(__file__), "models")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, MODEL_NAME)
    for url in MODEL_URLS:
        try:
            subprocess.run(["curl", "-sSL", "-o", target, url], check=True, timeout=120)
            if os.path.getsize(target) > 10000:
                return target
        except Exception:
            continue
    for url in MODEL_URLS:
        try:
            subprocess.run(["curl", "-sSL", "-o", "/tmp/" + MODEL_NAME, url],
                           check=True, timeout=120)
            return "/tmp/" + MODEL_NAME
        except Exception:
            continue
    raise RuntimeError("could not fetch the YuNet face model")


def detect_faces(video, sample_fps=4.0, score_thresh=0.55):
    """Sample the video and return [(time, face_count, cx, cy, fw, fh, conf)]."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(fps / max(0.5, sample_fps))))

    det = cv2.FaceDetectorYN.create(model_path(), "", (width, height),
                                    score_thresh, 0.3, 5000)
    det.setInputSize((width, height))

    out = []
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                _, faces = det.detect(frame)
                t = idx / fps
                if faces is None or len(faces) == 0:
                    out.append((t, 0, None, None, None, None, 0.0))
                else:
                    # choose the largest face: usually the main subject
                    best = max(faces, key=lambda f: f[2] * f[3])
                    fx, fy, fw, fh = (float(best[0]), float(best[1]),
                                      float(best[2]), float(best[3]))
                    out.append((t, len(faces), fx + fw / 2, fy + fh / 2,
                                fw, fh, float(best[-1])))
        idx += 1
    cap.release()
    return {"width": width, "height": height, "fps": fps,
            "frames": total, "samples": out}


def build_path(samples, src_w, out_w, window=5, max_seg_sec=1.5, hold_still_px=12,
               subject="face", src_h=None, out_h=None):
    """Smoothed crop path in source pixels, as (time, x) points."""
    usable = [(t, cx, cy, fh) for (t, n, cx, cy, fw, fh, c) in samples
              if cx is not None and n >= 1]
    if not usable:
        center = max(0, (src_w - out_w) / 2)
        return [(0.0, center)], {"faces_found": 0, "fallback": "center"}

    # body framing anchors lower: the chest, not the head
    if subject == "body" and src_h:
        usable = [(t, cx, min(src_h, cy + fh * 1.25), fh) for (t, cx, cy, fh) in usable]

    # moving average over the subject centres
    xs = [x for _, x, _, _ in usable]
    smoothed = []
    for i in range(len(xs)):
        lo, hi = max(0, i - window // 2), min(len(xs), i + window // 2 + 1)
        smoothed.append(sum(xs[lo:hi]) / (hi - lo))

    # crop x is the left edge that centres the face
    lefts = [max(0.0, min(src_w - out_w, x - out_w / 2)) for x in smoothed]

    # collapse near-identical values so the expression stays short
    pts = [(usable[0][0], lefts[0])]
    for (t, _, _, _), lx in zip(usable[1:], lefts[1:]):
        if abs(lx - pts[-1][1]) >= hold_still_px or t - pts[-1][0] >= max_seg_sec:
            pts.append((t, lx))
    if len(pts) == 1:
        pts.append((usable[-1][0] + 0.01, pts[0][1]))
    pts = simplify_path(pts, tol=6.0)
    return pts, {"faces_found": len(usable), "points": len(pts),
                 "path_min": round(min(l for _, l in pts), 1),
                 "path_max": round(max(l for _, l in pts), 1)}


def cluster_two(samples):
    """Split detections into the two dominant on-screen speakers by x position."""
    total = len(samples) or 1
    two_up = sum(1 for (t, n, cx, cy, fw, fh, c) in samples if n and n >= 2)
    if two_up / total < 0.15:
        return None  # one face moving reads as two clusters; that is not a podcast
    pts = [(t, cx, cy, fh) for (t, n, cx, cy, fw, fh, c) in samples
           if cx is not None and n and n >= 2]
    if len(pts) < 4:
        return None
    xs = sorted(x for _, x, _, _ in pts)
    centre = xs[len(xs) // 2]
    left = [p for p in pts if p[1] <= centre]
    right = [p for p in pts if p[1] > centre]
    if len(left) < 2 or len(right) < 2:
        return None
    return left, right


def facecam_region(video_path, max_height_frac=0.34, min_height_frac=0.05):
    """Find a small talking-head box in a corner: the streamer facecam.

    A facecam is small relative to the frame and parked in one corner, unlike a
    speaker who fills the frame. Returns ok=False with a reason when the footage
    does not look like that, so callers never invent a layout.
    """
    data = detect_faces(video_path)
    W, H = data["width"], data["height"]
    if not W or not H:
        return {"ok": False, "reason": "could not read frame size"}

    corners = {"top-left": [], "top-right": [], "bottom-left": [], "bottom-right": []}
    for (t, n, cx, cy, fw, fh, conf) in data["samples"]:
        if not n or fh < min_height_frac * H or fh > max_height_frac * H:
            continue
        left = cx < W / 2
        top = cy < H / 2
        key = ("top-" if top else "bottom-") + ("left" if left else "right")
        corners[key].append((cx, cy, fw, fh))

    total = max(1, len(data["samples"]))
    best, best_n = None, 0
    for name, boxes in corners.items():
        if len(boxes) > best_n:
            best, best_n = name, len(boxes)
    coverage = best_n / total
    if not best or coverage < 0.45:
        return {"ok": False,
                "reason": "no small face held a corner of the frame "
                          f"(best {coverage * 100:.0f}% of samples)",
                "coverage": round(coverage, 2)}

    boxes = corners[best]
    xs = sorted(b[0] for b in boxes)
    ys = sorted(b[1] for b in boxes)
    ws = sorted(b[2] for b in boxes)
    hs = sorted(b[3] for b in boxes)
    mid = len(boxes) // 2
    cx, cy, fw, fh = xs[mid], ys[mid], ws[mid], hs[mid]

    # pad the box out so the face has headroom and shoulders
    px, py = fw * 0.55, fh * 0.75
    x = max(0.0, cx - fw / 2 - px)
    y = max(0.0, cy - fh / 2 - py)
    w = min(float(W) - x, fw + 2 * px)
    h = min(float(H) - y, fh + 2 * py)
    return {"ok": True, "corner": best, "coverage": round(coverage, 2),
            "region": {"x": round(x, 1), "y": round(y, 1),
                       "w": round(w, 1), "h": round(h, 1)},
            "frame": [W, H]}


def build_streamer_plan(video_path, out_w=1080, out_h=1920, top_ratio=0.30,
                        region=None):
    """Facecam strip on top, gameplay under it, stacked into 9:16."""
    info = facecam_region(video_path) if region is None else {"ok": True, "region": region}
    if not info.get("ok"):
        return info
    W, H = _frame_size(video_path) if "_frame_size" in globals() else (1920, 1080)
    r = info["region"]
    top_h = int(round(out_h * top_ratio / 2)) * 2
    bot_h = out_h - top_h

    # facecam: fill the strip, centre crop (no letterboxing)
    f = (f"[0:v]crop={int(r['w'])}:{int(r['h'])}:{int(r['x'])}:{int(r['y'])},"
         f"scale={out_w}:{top_h}:force_original_aspect_ratio=increase,"
         f"crop={out_w}:{top_h}[cam]")

    # gameplay: the widest 9:16-ish slice that avoids the facecam corner
    gw = int(round(bot_h * out_w / bot_h))
    gw = int(min(W, max(2, gw // 2 * 2)))
    gh = int(H // 2 * 2)
    want_aspect = out_w / bot_h
    gw2 = int(round(gh * want_aspect / 2) * 2)
    if gw2 <= W:
        gw, gh = gw2, gh
    else:
        gw, gh = W // 2 * 2, int(round(W / want_aspect / 2) * 2)
    gx = int(max(0, min(W - gw, (W - gw) / 2)))
    gy = int(max(0, min(H - gh, (H - gh) / 2)))
    b = (f"[0:v]crop={gw}:{gh}:{gx}:{gy},"
         f"scale={out_w}:{bot_h}:force_original_aspect_ratio=increase,"
         f"crop={out_w}:{bot_h}[play]")

    return {"ok": True, "corner": info.get("corner"), "coverage": info.get("coverage"),
            "top_ratio": top_ratio, "panel": f"{out_w}x{top_h} + {out_w}x{bot_h}",
            "facecam": r, "gameplay": {"x": gx, "y": gy, "w": gw, "h": gh},
            "filter": f"{f};{b};[cam][play]vstack=inputs=2[v]"}


def build_two_speaker_plan(video, out_w=1080, out_h=1920, sample_fps=4.0):
    """Podcast layout: each speaker gets a tracked panel, stacked top and bottom."""
    data = detect_faces(video, sample_fps=sample_fps)
    src_w, src_h = data["width"], data["height"]
    groups = cluster_two(data["samples"])
    if not groups:
        return {"ok": False, "reason": "did not find two distinct speakers",
                "faces": data["samples"]}

    panel_h = out_h // 2
    crop_w = min(src_w, int(round(src_h * out_w / panel_h)))
    speakers, filters = [], []
    for i, grp in enumerate(groups):
        times = sorted({t for t, _, _, _ in grp})
        series = []
        for t in times:
            at = [p for p in grp if p[0] == t]
            if at:
                series.append((t, sum(p[1] for p in at) / len(at)))
        if not series:
            return {"ok": False, "reason": f"speaker {i} had no stable samples"}
        pts, _ = build_path(
            [(t, 1, x, None, None, None, 1.0) for t, x in series],
            src_w, crop_w)
        pts = simplify_path(pts, tol=6.0)
        e = crop_expression(pts, src_w, crop_w)
        med_x = median([p[1] for p in grp])
        med_y = median([p[2] for p in grp if p[2] is not None] or [0])
        med_h = median([p[3] for p in grp if p[3] is not None] or [0])
        box = [med_x - med_h / 2.0, med_y - med_h / 2.0, med_h, med_h]
        speakers.append({"index": i,
                         "x_range": [round(min(x for _, x in series), 1),
                                     round(max(x for _, x in series), 1)],
                         "box": [round(v, 1) for v in box],
                         "path": [[round(t, 2), round(x, 1)] for t, x in pts]})
        filters.append(
            f"[s{i}]crop={crop_w}:{src_h}:{e}:0,scale={out_w}:{panel_h}[p{i}]")

    graph = ("[0:v]split=2[sa][sb];"
             + "[sa]null[a0];[sb]null[s1];").replace("[sa]null[a0];", "")
    graph = "[0:v]split=2[s0][s1];" + ";".join(filters) +             f";[p0][p1]vstack=inputs=2[v]"
    verdict = {"judged": False, "conversation": None, "shares": None}
    try:
        verdict = speaker_activity(video, [sp["box"] for sp in speakers])
    except Exception as exc:
        verdict["reason"] = f"active-speaker check failed: {exc}"
    return {"ok": True, "filter": graph, "panel": f"{out_w}x{panel_h}",
            "crop": f"{crop_w}x{src_h}", "speakers": speakers,
            "conversation": verdict.get("conversation"),
            "speaker_shares": verdict.get("shares"),
            "speaker_reason": verdict.get("reason"),
            "source": {"w": src_w, "h": src_h}}


def mode_intervals(samples, duration, min_sec=0.8):
    """When to split and when to show one person full frame.

    Two faces on screen -> split. One face -> normal 9:16. Runs shorter than
    min_sec are merged into their neighbour so the layout does not flicker.
    """
    if not samples or duration <= 0:
        return [(0.0, duration, "single")]
    pts = sorted(samples, key=lambda s: s[0])
    runs = []
    for (t, n, cx, cy, fw, fh, c) in pts:
        mode = "split" if (n or 0) >= 2 else "single"
        if runs and runs[-1][2] == mode:
            runs[-1][1] = t
        else:
            runs.append([t, t, mode])
    if len(runs) == 1:
        runs[0][0], runs[0][1] = 0.0, duration
        return [(0.0, duration, runs[0][2])]

    # a run starts where it starts and ends where the next one starts
    for i in range(len(runs) - 1):
        runs[i][1] = runs[i + 1][0]
    runs[0][0] = 0.0
    runs[-1][1] = duration

    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, r in enumerate(runs):
            if r[1] - r[0] < min_sec:
                if i > 0:
                    runs[i - 1][1] = r[1]
                    runs.pop(i)
                else:
                    runs[1][0] = 0.0
                    runs.pop(0)
                changed = True
                break
    # collapse neighbours that ended up with the same layout
    merged = []
    for r in runs:
        if merged and merged[-1][2] == r[2]:
            merged[-1][1] = r[1]
        else:
            merged.append([r[0], r[1], r[2]])
    return [(round(a, 3), round(b, 3), m) for a, b, m in merged if b - a > 0.05]


def simplify_path(points, tol=6.0):
    """Drop points that sit on the line between their neighbours."""
    if len(points) <= 2:
        return points
    kept = [points[0]]
    for i in range(1, len(points) - 1):
        t0, x0 = kept[-1]
        t2, x2 = points[i + 1]
        t1, x1 = points[i]
        dt = t2 - t0
        pred = x0 if dt <= 0 else x0 + (x2 - x0) * (t1 - t0) / dt
        if abs(x1 - pred) > tol:
            kept.append(points[i])
    kept.append(points[-1])
    return kept


def crop_expression(points, src_w, out_w):
    """Nested piecewise-linear ffmpeg expression in `t`."""
    if len(points) < 2:
        x = points[0][1] if points else 0.0
        return str(int(x))
    exprs = []
    exprs.append(f"{points[-1][1]:.1f}")
    for i in range(len(points) - 2, -1, -1):
        t0, x0 = points[i]
        t1, x1 = points[i + 1]
        dt = max(0.01, t1 - t0)
        seg = (f"{x0:.1f}+{x1 - x0:.1f}*clip((t-{t0:.3f})/{dt:.3f},0,1)")
        exprs.append(f"if(lt(t,{t1:.3f}),{seg},{exprs[-1]})")
    e = exprs[-1]
    raw = f"max(0,min({src_w - out_w},{e}))"
    return raw.replace(",", "\\,")


def track_crop_filter(points, src_w, src_h, out_w, out_h):
    """Crop in source pixels first, then scale. Keeps the path in one space."""
    crop_w = min(src_w, int(round(src_h * out_w / out_h)))
    e = crop_expression(points, src_w, crop_w)
    return (f"crop={crop_w}:{src_h}:{e}:0,scale={out_w}:{out_h}")


def analyse(video, out_w=1080, out_h=1920, sample_fps=4.0, verbose=False,
            subject="face"):
    data = detect_faces(video, sample_fps=sample_fps)
    # the crop happens in source pixels; width follows the target aspect
    crop_w = min(data["width"], int(round(data["height"] * out_w / out_h)))
    pts, info = build_path(data["samples"], data["width"], crop_w,
                           subject=subject, src_h=data["height"], out_h=out_h)
    filt = track_crop_filter(pts, data["width"], data["height"], out_w, out_h)
    result = {"ok": True, "subject": subject,
              "source": {"w": data["width"], "h": data["height"],
                         "fps": round(data["fps"], 2)},
              "path": [[round(t, 2), round(x, 1)] for t, x in pts],
              "filter": filt, "info": info}
    if verbose:
        result["samples"] = [[round(t, 2), n,
                              round(cx, 1) if cx is not None else None]
                             for (t, n, cx, cy, fw, fh, c) in data["samples"]]
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    r = analyse(a.video, verbose=a.verbose)
    if a.json:
        print(json.dumps(r, indent=2, default=float))
    else:
        print("faces found:", r["info"].get("faces_found"),
              "| path points:", r["info"].get("points"),
              "| x range:", r["info"].get("path_min"), "-", r["info"].get("path_max"))


# ---------------------------------------------------------- active speaker
# Ported from openshorts/active_speaker.py (MIT). Full diarisation is
# overkill for "which of these two faces is moving its mouth right now":
# measure frame-to-frame change in each mouth region, and only trust the
# comparison in windows where the audio says somebody is speaking at all.
# Off by default, like upstream openshorts: it is a heuristic and it must not
# silently override clear geometry. SPEAKER_SIGNAL=1 lets it gate the split.
SPEAKER_SIGNAL = os.environ.get("SPEAKER_SIGNAL", "0") == "1"
WINDOW_SECONDS = 0.4
AUDIO_FLOOR = 0.18      # a window is speech only above this share of the peak
MIN_MARGIN = 0.18       # winner must beat the other by this share of the pair
MIN_WINDOWS = 8         # too few speech windows and we do not judge


def mouth_region(box, frame_w, frame_h):
    """Pixel rect (x0, y0, x1, y1) around the mouth of a face box."""
    x, y, w, h = [float(v) for v in box]
    my = y + h * 0.72
    mw, mh = max(8.0, w * 0.45), max(8.0, h * 0.16)
    x0 = int(max(0, min(x + w / 2 - mw / 2, frame_w - 2)))
    x1 = int(min(frame_w, max(x0 + 2, x + w / 2 + mw / 2)))
    y0 = int(max(0, min(my - mh / 2, frame_h - 2)))
    y1 = int(min(frame_h, max(y0 + 2, my + mh / 2)))
    return x0, y0, x1, y1


def audio_rms(path, window_s=WINDOW_SECONDS):
    """Per-window RMS of the audio track, decoded with ffmpeg."""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000",
         "-f", "s16le", "-"], capture_output=True)
    if not p.stdout:
        return []
    a = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = max(1, int(16000 * window_s))
    return [float(np.sqrt(np.mean(a[i:i + n] ** 2)))
            for i in range(0, max(1, len(a) - n + 1), n)]


def speaker_activity(video, boxes, window_s=WINDOW_SECONDS, sample_fps=12.0):
    """Which speaker holds the floor, from mouth motion plus audio energy."""
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(fps / sample_fps)))
    regions = [mouth_region(b, fw, fh) for b in boxes]
    rows, prev, idx = [], None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if prev is not None:
                d = cv2.absdiff(g, prev)
                rows.append((idx / fps,
                             [float(d[y0:y1, x0:x1].mean())
                              for x0, y0, x1, y1 in regions]))
            prev = g
        idx += 1
    cap.release()

    rms = audio_rms(video, window_s)
    n_win = len(rms)
    if n_win < MIN_WINDOWS or not rows:
        return {"judged": False, "reason": "not enough audio or motion to judge",
                "shares": None, "conversation": None}

    tot = [[0.0] * len(boxes) for _ in range(n_win)]
    cnt = [0] * n_win
    for t, vals in rows:
        k = int(t // window_s)
        if 0 <= k < n_win:
            for i, v in enumerate(vals):
                tot[k][i] += v
            cnt[k] += 1
    for k in range(n_win):
        if cnt[k]:
            tot[k] = [v / cnt[k] for v in tot[k]]

    peak = max(rms) or 1.0
    shares_top = [max(1e-6, max(row[i] for row in tot)) for i in range(len(boxes))]
    wins, speaking = [0] * len(boxes), 0
    for k in range(n_win):
        if rms[k] < AUDIO_FLOOR * peak:
            continue
        speaking += 1
        row = [tot[k][i] / shares_top[i] for i in range(len(boxes))]
        hi = max(range(len(row)), key=lambda i: row[i])
        pair = sum(row) or 1e-6
        if (row[hi] - min(row)) / pair >= MIN_MARGIN:
            wins[hi] += 1

    shares = [round(w / max(1, speaking), 3) for w in wins]
    conversation = speaking >= MIN_WINDOWS and min(shares) >= 0.15
    return {"judged": True, "shares": shares, "speaking_windows": speaking,
            "windows": n_win, "conversation": conversation,
            "reason": ("both speakers take turns" if conversation else
                       "one voice holds the floor, stacking would waste the frame")}
