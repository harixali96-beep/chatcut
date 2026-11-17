"""Download a video from wherever the owner pasted it.

Three routes, tried in order, each one version-tolerant:
  1. Google Drive  -> gdown, but only with keyword arguments that exist in the
     installed version (gdown dropped `fuzzy` in v6, which is exactly the
     TypeError that reached the owner).
  2. Google Drive  -> plain HTTP fallback with no gdown at all, including the
     big-file confirm token, so a broken gdown cannot block a download.
  3. Everything else (YouTube, Twitch, Vimeo, direct MP4) -> yt-dlp.

No API keys, no accounts.
"""
import http.cookiejar
import inspect
import os
import re
import shutil
import urllib.request

GDRIVE_ID_RE = re.compile(r"(?:/file/d/|[?&]id=|/d/)([A-Za-z0-9_-]{20,})")

PLATFORMS = (
    ("youtube.com", "YouTube"), ("youtu.be", "YouTube"),
    ("twitch.tv", "Twitch"), ("vimeo.com", "Vimeo"),
    ("drive.google.com", "Google Drive"), ("docs.google.com", "Google Drive"),
    ("tiktok.com", "TikTok"), ("instagram.com", "Instagram"),
    ("facebook.com", "Facebook"), ("x.com", "X"), ("twitter.com", "X"),
    ("streamable.com", "Streamable"), ("dailymotion.com", "Dailymotion"),
)


def platform_of(url):
    low = (url or "").lower()
    for needle, name in PLATFORMS:
        if needle in low:
            return name
    return "direct link"


def drive_id(url):
    m = GDRIVE_ID_RE.search(url or "")
    return m.group(1) if m else None


def _gdown_download(url, dest, file_id=None):
    """Call gdown with only the keywords this installation actually accepts."""
    import gdown
    accepted = set(inspect.signature(gdown.download).parameters)
    kwargs = {"quiet": True, "output": dest}
    if file_id and "id" in accepted:
        kwargs["id"] = file_id
    elif "url" in accepted:
        kwargs["url"] = url
    if "retries" in accepted:
        kwargs["retries"] = 2
    kwargs = {k: v for k, v in kwargs.items() if k in accepted}
    return gdown.download(**kwargs)


def _drive_http(file_id, dest, timeout=180):
    """No-gdown fallback: the uc endpoint plus the large-file confirm token."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    base = "https://drive.google.com/uc?export=download&id=" + file_id
    with opener.open(base, timeout=timeout) as resp:
        head = resp.read(4096)
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" in ctype:
            token = None
            m = re.search(r"confirm=([0-9A-Za-z_]+)", head.decode("utf-8", "ignore"))
            if m:
                token = m.group(1)
            if not token:
                return None, ("Google sent a sign-in or virus-check page. In Drive set the "
                              "file to 'Anyone with the link' and paste it again.")
            with opener.open(base + "&confirm=" + token, timeout=timeout) as r2:
                with open(dest, "wb") as fh:
                    shutil.copyfileobj(r2, fh)
            return dest, "Google Drive"
        with open(dest, "wb") as fh:
            fh.write(head)
            shutil.copyfileobj(resp, fh)
    return dest, "Google Drive"


def _ytdlp(url, workdir):
    import yt_dlp
    out = os.path.join(workdir, "link_%(id)s.%(ext)s")
    with yt_dlp.YoutubeDL({"outtmpl": out, "format": "mp4/bestvideo*+bestaudio/best",
                           "quiet": True, "noplaylist": True,
                           "merge_output_format": "mp4"}) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    if not os.path.exists(path):
        stem, _ = os.path.splitext(path)
        for ext in (".mp4", ".mkv", ".webm"):
            if os.path.exists(stem + ext):
                return stem + ext
        return None
    return path


def _usable(path, minimum=100_000):
    return bool(path) and os.path.exists(path) and os.path.getsize(path) >= minimum


def fetch(url, workdir, progress=None):
    """Fetch any supported link. Returns (path, how) or (None, reason)."""
    def say(msg):
        if progress:
            progress(msg)

    url = (url or "").strip()
    if url.startswith("file://"):
        url = url[7:]
    if os.path.exists(url):
        return url, "local path"

    where = platform_of(url)
    say(f"fetching from {where}")
    os.makedirs(workdir, exist_ok=True)
    errors = []

    if where == "Google Drive":
        fid = drive_id(url)
        dest = os.path.join(workdir, "drive_input.mp4")
        try:
            _gdown_download(url, dest, file_id=fid)
            if _usable(dest):
                return dest, "Google Drive"
            errors.append("gdown returned an empty file")
        except Exception as exc:
            errors.append(f"gdown: {exc}")
        if fid:
            try:
                got, how = _drive_http(fid, dest)
                if _usable(got):
                    return got, how
                if got is None:
                    return None, how
                errors.append("direct Drive download was too small")
            except Exception as exc:
                errors.append(f"direct: {exc}")
        say("Drive failed, trying yt-dlp")

    try:
        path = _ytdlp(url, workdir)
        if _usable(path):
            return path, where
        if path:
            return path, where
        errors.append("yt-dlp produced no file")
    except Exception as exc:
        errors.append(f"yt-dlp: {exc}")

    hint = ""
    if where == "Google Drive":
        hint = " Share the file as 'Anyone with the link', or download it and upload it here."
    elif where in ("YouTube", "Twitch"):
        hint = " The video may be private, region-locked, or a live stream that has not been saved."
    return None, "could not fetch the link." + hint + " (" + "; ".join(errors)[:300] + ")"
