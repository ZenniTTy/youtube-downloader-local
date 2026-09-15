# CLAUDE.md

Context for AI agents working in this repository.

## What this is

A **local** app that downloads YouTube videos as MP4 and generates transcripts.
It runs on `127.0.0.1`, with no authentication, no database and no deployment.
Personal use.

This is not a product and has no commercial purpose. That is not modesty — it is
a design constraint that settles arguments: there is no multi-user story, no
tenancy, no scale. When a change only makes sense for a hosted service, it does
not make sense here.

## Non-negotiable constraints

**It is never exposed to the network.** `server.py` has a middleware that refuses
any request whose client is not loopback. Do not remove it, do not loosen it and
do not make it configurable. The app downloads videos under the network identity
of whoever hosts it, and it has no rate limiting and no auth: on the internet it
becomes a public download service running on the owner's IP. `test_loopback.py`
guards this invariant.

**No new dependencies without a strong reason.** There are four today: `fastapi`,
`uvicorn`, `yt-dlp` and `faster-whisper`. The frontend is a single HTML file with
no build step, no npm and no framework. Adding React or a bundler here is a
regression, not an improvement.

**State lives in memory.** `jobs.py` is a dictionary behind a lock. That is the
right call for a single-session local app — proposing Redis or Postgres solves a
problem this project does not have.

## Architecture

Each module owns one clear boundary:

| File | Role |
|---|---|
| `run.py` | Entrypoint; starts uvicorn bound to `127.0.0.1` |
| `server.py` | FastAPI routes, loopback middleware, job orchestration |
| `downloader.py` | All contact with `yt-dlp` — formats, downloads, subtitles, audio |
| `transcriber.py` | Whisper and VTT-to-text conversion |
| `jobs.py` | In-memory registry of transcription jobs |
| `static/index.html` | The whole interface (HTML + CSS + JS inline) |

`yt-dlp` is touched only by `downloader.py`. When YouTube breaks extraction, the
fix lives there — do not scatter `yt_dlp` calls across the rest of the code.

### Why transcription is asynchronous

Transcribing can take minutes, longer than any reasonable HTTP timeout. So
`POST /api/transcript` creates a job and returns an id immediately; the browser
polls `GET /api/transcript/{id}` and downloads the file once it is ready.

Transcription is a **cascade**: if the video has subtitles (official or
auto-generated), the text comes from them in seconds. Only when none exist is the
audio downloaded and run through Whisper. Preserve that order — inverting it makes
the common path hundreds of times slower.

## Two system dependencies, not Python ones

They fail in confusing ways, so they are worth remembering:

- **ffmpeg** — without it, 1080p and above fail, because at those resolutions
  YouTube serves video and audio separately and something has to merge them.
- **A JavaScript runtime** (node/deno/bun) — YouTube requires solving a JS
  challenge to sign media URLs. Without a runtime, every download dies with
  `HTTP 403`. `downloader.py` detects whichever one is on PATH.

When someone reports a 403, the cause is almost always one of those two: an
outdated yt-dlp or a missing JS runtime. Check that before refactoring anything.

## Verification

```bash
python3 test_loopback.py    # the local-access lock
python3 run.py              # start it and check by hand at http://127.0.0.1:8000
```

There is no automated test suite beyond that, and this is intentional: most of
the behavior is a call into `yt-dlp` against a service that changes on its own,
and mocking that would only test the mock. When proposing new tests, propose
first and let the owner pick the target — do not write tests to confirm code you
just wrote yourself.

## Deliberately out of scope

Already considered and declined: standalone MP3 extraction, playlists and batch
downloads, public hosting, authentication, a persistent queue and history. If one
of them ever makes sense, that is the owner's call — do not introduce it on your
own.
