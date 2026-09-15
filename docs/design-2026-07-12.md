# YouTube Downloader Local — Design

**Date:** 2026-07-12
**Author:** ZenniTTy
**Status:** Approved

> Historical document. It records the original design, before transcription was
> added. Kept as-is for context; see `CLAUDE.md` for the current architecture.

## Goal

A local system (runs only on the user's Mac, via `localhost`) for downloading
YouTube videos as MP4, choosing the resolution. Personal use — downloading your
own videos or freely licensed ones. Inspired by sites like `app.ytdown.to`, but
running 100% offline on the user's machine.

## Scope

**Includes:**
- Paste a YouTube link and see a preview (title, thumbnail, duration)
- List available MP4 formats with resolution and estimated size
- Pick a resolution and download the finished MP4 (video + audio already merged)

**Excludes (YAGNI for now):**
- Audio-only / MP3 extraction
- Playlists / batch downloads
- Public hosting / authentication
- Persistent download queue / history

## Environment

Already installed on the machine: Python 3.13, `yt-dlp` and `ffmpeg` (verified).
The app depends on those three system tools; it only needs `fastapi` and
`uvicorn` installed via pip.

## Architecture

A single Python server with FastAPI serving both the static page and the API.
The user runs one command, opens `http://localhost:8000`, pastes the link,
picks the quality and downloads. The browser handles where to save it (a normal
download), so nothing gets stuck in a server-side folder.

FastAPI was chosen over Flask for `StreamingResponse`, which delivers large
video files as a stream without loading everything into memory.

### Flow

1. User pastes the link → frontend calls `POST /api/info`.
2. Backend runs `yt-dlp` to extract metadata: title, thumbnail, duration and the
   list of available MP4 formats (with resolution and estimated filesize).
3. Frontend shows the preview + the resolutions.
4. User picks a resolution → frontend calls
   `GET /api/download?url=...&format_id=...`.
5. Backend uses `yt-dlp` to download the chosen format, merging video+audio via
   ffmpeg when YouTube serves separate tracks (common at 1080p+), and returns
   the MP4 via `StreamingResponse` with `Content-Disposition: attachment`.

## Components

| File | Responsibility |
|---|---|
| `downloader.py` | Isolated yt-dlp wrapper. Pure functions: `get_info(url)` returns metadata + formats; `download(url, format_id, dest)` downloads and merges. Testable without the server. |
| `server.py` | FastAPI app. Routes: `/` (serves the page), `POST /api/info`, `GET /api/download`. Translates downloader errors into clear HTTP responses. |
| `static/index.html` | Single-page interface (HTML + CSS + JS inline). Link field, button, preview card, quality list, loading/error states. |
| `requirements.txt` | `fastapi`, `uvicorn`. |
| `README.md` | How to install and run. |

### `downloader.py` contract

- `get_info(url: str) -> dict` — returns
  `{title, thumbnail, duration, formats: [{format_id, resolution, ext, filesize, note}]}`.
  Filters down to usable video formats only (mp4, with or without embedded audio
  — when without, flags that a merge will be needed). Raises `DownloaderError`
  with a friendly message on failure.
- `download(url, format_id, dest_dir) -> Path` — downloads the chosen format,
  merges it with the best audio when needed (final format mp4), and returns the
  file path. Raises `DownloaderError` on failure.

## Formats: how to handle merging

YouTube serves high resolutions (1080p, 1440p, 4K) with video and audio in
separate tracks. The list shown to the user uses the resolution as the label; on
download, the backend passes yt-dlp a selection like
`"<format_id>+bestaudio[ext=m4a]/<format_id>"` and forces `merge_output_format=mp4`,
so the user always receives a single MP4 with audio.

## Error handling

Each case becomes a clear on-screen message, not a raw error:

- Invalid or non-YouTube URL → "Invalid link. Paste a YouTube link."
- Private / removed / age-restricted video → the yt-dlp message, condensed
  ("Video unavailable: ...").
- Outdated yt-dlp (extraction error) → hint: "YouTube may have changed; run
  `yt-dlp -U` to update."
- ffmpeg missing during merge → detected at boot with a warning.

## Tests

- `downloader.py` tested in isolation. Format parsing/normalization tests use a
  fixture of yt-dlp output (no network). An optional, marked smoke test against
  a public, freely licensed video.
- `server.py`: route tests with the downloader mocked (info OK, info with error,
  download with error) via FastAPI's `TestClient`.

## How to run

```bash
pip install -r requirements.txt
uvicorn server:app --port 8000
# open http://localhost:8000
```

## Known risks

- **yt-dlp fragility:** YouTube changes its encryption frequently; keep it
  updated with `yt-dlp -U`. Documented in the README.
- **Legal/ToS:** downloading without authorization violates YouTube's ToS. The
  app is intended for personal use with your own or freely licensed content.
  Warning in the README.
