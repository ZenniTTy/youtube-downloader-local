"""Isolated yt-dlp wrapper.

Two functions that are pure from the server's point of view:
  - get_info(url): metadata + usable MP4 formats
  - download(url, format_id, dest_dir): downloads and merges, returns the MP4 Path

Every failure becomes a DownloaderError with a friendly message in English.
"""

from __future__ import annotations

import shutil
import re
from pathlib import Path

import yt_dlp


class DownloaderError(Exception):
    """Friendly error to show to the user."""


def _detect_js_runtimes() -> dict[str, dict] | None:
    """Finds a JS runtime available on the PATH.

    YouTube requires solving a JS challenge to sign the media URLs. Without a
    runtime, yt-dlp returns URLs without a valid signature and the download dies
    with HTTP 403. Only `deno` is enabled by default, so we detect the others.
    """
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return {name: {}}
    return None


_JS_RUNTIMES = _detect_js_runtimes()

# Common options: quiet and no color in the log.
_COMMON_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
}
if _JS_RUNTIMES:
    _COMMON_OPTS["js_runtimes"] = _JS_RUNTIMES


def _friendly_error(exc: Exception) -> DownloaderError:
    """Translates raw yt-dlp errors into clear messages."""
    msg = str(exc)
    low = msg.lower()
    if "private" in low:
        return DownloaderError("Private video — it cannot be downloaded.")
    if "unavailable" in low or "removed" in low or "not available" in low:
        return DownloaderError("Video unavailable or removed.")
    if "age" in low and "confirm" in low:
        return DownloaderError("Age-restricted video — it cannot be downloaded.")
    if "unable to extract" in low or "unsupported url" in low or "no video" in low:
        return DownloaderError(
            "Could not read this video. Check the link — or YouTube may have "
            "changed; run `yt-dlp -U` to update."
        )
    if "403" in low or "forbidden" in low:
        return DownloaderError(
            "YouTube refused the download (403). This is usually an outdated "
            "yt-dlp or a missing JavaScript runtime. Run `pip install -U yt-dlp` "
            "and install Node (`brew install node`)."
        )
    if "is not a valid url" in low or not low.strip():
        return DownloaderError("Invalid link. Paste a YouTube link.")
    return DownloaderError(f"Failed to process the video: {msg}")


def _human_size(num_bytes: int | None) -> str | None:
    if not num_bytes:
        return None
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return None


def get_info(url: str) -> dict:
    """Returns the video's metadata and usable MP4 formats.

    Structure:
      {title, thumbnail, duration, formats: [
         {format_id, resolution, ext, filesize, needs_audio, note}
      ]}
    Raises DownloaderError on failure.
    """
    if not url or not url.strip():
        raise DownloaderError("Invalid link. Paste a YouTube link.")

    opts = {**_COMMON_OPTS, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise _friendly_error(exc) from exc
    except Exception as exc:  # network, unexpected parsing, etc.
        raise _friendly_error(exc) from exc

    if raw is None:
        raise DownloaderError("Could not get information about this video.")

    # Playlist: take the first item (the scope is a single video).
    if raw.get("_type") == "playlist" and raw.get("entries"):
        entries = [e for e in raw["entries"] if e]
        if not entries:
            raise DownloaderError("No video found at this link.")
        raw = entries[0]

    formats = _extract_video_formats(raw.get("formats", []))
    if not formats:
        raise DownloaderError("No MP4 video format available for this link.")

    return {
        "title": raw.get("title") or "Video",
        "channel": _channel_handle(raw),
        "thumbnail": raw.get("thumbnail"),
        "duration": _format_duration(raw.get("duration")),
        "formats": formats,
    }


def _channel_handle(raw: dict) -> str:
    """Channel name, without the leading '@'.

    `uploader_id` usually comes as '@name'; we fall back to the channel URL and,
    as a last resort, to the full name. Empty string if nothing is available.
    """
    ident = (raw.get("uploader_id") or "").strip()
    if ident.startswith("@"):
        return ident[1:]

    url = (raw.get("uploader_url") or raw.get("channel_url") or "").rstrip("/")
    if "/@" in url:
        return url.rsplit("/@", 1)[1]

    name = (raw.get("channel") or raw.get("uploader") or "").strip()
    return re.sub(r"\s+", "", name)


def _extract_video_formats(raw_formats: list[dict]) -> list[dict]:
    """Filters mp4 video formats and normalizes them for the UI.

    Keeps one format per height (resolution), preferring the one with the best
    bitrate. Marks needs_audio when the track has no embedded audio (it will
    require merging).
    """
    by_height: dict[int, dict] = {}
    for f in raw_formats:
        if f.get("vcodec") in (None, "none"):
            continue  # audio-only
        if f.get("ext") != "mp4":
            continue
        height = f.get("height")
        if not height:
            continue

        has_audio = f.get("acodec") not in (None, "none")
        filesize = f.get("filesize") or f.get("filesize_approx")
        candidate = {
            "format_id": f.get("format_id"),
            "height": height,
            "resolution": f"{height}p",
            "ext": "mp4",
            "filesize": _human_size(filesize),
            "_filesize_raw": filesize or 0,
            "needs_audio": not has_audio,
            "note": "" if has_audio else "video+audio will be merged",
        }
        prev = by_height.get(height)
        # Prefers the largest known filesize as a simple quality tiebreaker.
        if prev is None or candidate["_filesize_raw"] > prev["_filesize_raw"]:
            by_height[height] = candidate

    ordered = sorted(by_height.values(), key=lambda c: c["height"], reverse=True)
    for c in ordered:
        c.pop("_filesize_raw", None)
    return ordered


def _format_duration(seconds: int | float | None) -> str | None:
    if not seconds:
        return None
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def download(url: str, format_id: str, dest_dir: Path) -> Path:
    """Downloads the chosen format, merges audio when needed, returns the MP4.

    Raises DownloaderError on failure.
    """
    if not url or not url.strip():
        raise DownloaderError("Invalid link.")
    if not format_id:
        raise DownloaderError("No quality selected.")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Selects the requested format + best audio; falls back to the bare format if
    # it already has audio.
    format_selector = f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/{format_id}"
    outtmpl = str(dest_dir / "%(title)s.%(ext)s")

    opts = {
        **_COMMON_OPTS,
        "format": format_selector,
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(url, download=True)
            final = Path(ydl.prepare_filename(raw))
    except yt_dlp.utils.DownloadError as exc:
        raise _friendly_error(exc) from exc
    except Exception as exc:
        raise _friendly_error(exc) from exc

    # After merging to mp4, the actual file has the .mp4 extension.
    if final.suffix != ".mp4":
        final = final.with_suffix(".mp4")
    if not final.exists():
        # Fallback: take the most recent mp4 in the folder.
        candidates = sorted(dest_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise DownloaderError("The download finished but the file was not found.")
        final = candidates[-1]

    return final


# ---------------------------------------------------------------------------
# Subtitles
# ---------------------------------------------------------------------------

# Language priority when automatically choosing a subtitle.
_LANG_PREFERENCE = ("pt-orig", "pt-BR", "pt", "pt-PT", "en")


def get_subtitles(url: str) -> dict:
    """Lists the available subtitles, both manual and auto-generated.

    Returns {manual: [...], automatic: [...]}, each item
    {lang, name, automatic}. An empty list means the video has no
    subtitles — in that case the caller should fall back to transcribing the
    audio.
    """
    if not url or not url.strip():
        raise DownloaderError("Invalid link. Paste a YouTube link.")

    opts = {**_COMMON_OPTS, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise _friendly_error(exc) from exc

    if raw is None:
        raise DownloaderError("Could not get information about this video.")
    if raw.get("_type") == "playlist" and raw.get("entries"):
        entries = [e for e in raw["entries"] if e]
        if not entries:
            raise DownloaderError("No video found at this link.")
        raw = entries[0]

    def _pack(track_map: dict, automatic: bool) -> list[dict]:
        items = []
        for lang, tracks in (track_map or {}).items():
            name = (tracks[0].get("name") if tracks else None) or lang
            items.append({"lang": lang, "name": name, "automatic": automatic})
        return sorted(items, key=_lang_sort_key)

    return {
        "manual": _pack(raw.get("subtitles"), automatic=False),
        "automatic": _pack(raw.get("automatic_captions"), automatic=True),
    }


def _lang_sort_key(item: dict) -> tuple[int, str]:
    """Sorts so that the preferred languages come first."""
    try:
        return (_LANG_PREFERENCE.index(item["lang"]), item["lang"])
    except ValueError:
        return (len(_LANG_PREFERENCE), item["lang"])


def pick_best_subtitle(available: dict) -> dict | None:
    """Picks the best subtitle: manual beats auto-generated; language by preference."""
    for group in ("manual", "automatic"):
        tracks = available.get(group) or []
        for lang in _LANG_PREFERENCE:
            for t in tracks:
                if t["lang"] == lang:
                    return t
        if tracks:
            return tracks[0]
    return None


def download_subtitle(url: str, lang: str, automatic: bool, dest_dir: Path) -> Path:
    """Downloads a subtitle as VTT and returns the file path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    opts = {
        **_COMMON_OPTS,
        "skip_download": True,
        "writesubtitles": not automatic,
        "writeautomaticsub": automatic,
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt",
        "outtmpl": str(dest_dir / "%(title)s.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        raise _friendly_error(exc) from exc

    found = sorted(dest_dir.glob("*.vtt"))
    if not found:
        raise DownloaderError("The subtitle could not be downloaded.")
    return found[0]


def download_audio(url: str, dest_dir: Path, progress_hook=None) -> Path:
    """Downloads audio only, converted to 16kHz mono WAV (what Whisper expects)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    opts = {
        **_COMMON_OPTS,
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "audio.%(ext)s"),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "wav"},
        ],
        "postprocessor_args": ["-ar", "16000", "-ac", "1"],
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        raise _friendly_error(exc) from exc

    found = sorted(dest_dir.glob("*.wav"))
    if not found:
        raise DownloaderError("Could not extract the audio from the video.")
    return found[0]
