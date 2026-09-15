"""Local FastAPI server of the YouTube Downloader.

Routes:
  GET  /               -> serves the interface
  POST /api/info       -> metadata + MP4 formats of the video
  GET  /api/download   -> downloads and delivers the MP4

Runs locally only: `uvicorn server:app --port 8000`
"""

from __future__ import annotations

import re
import shutil
import tempfile
import threading
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import downloader
import jobs
import transcriber
from downloader import DownloaderError
from transcriber import TranscriptionError

app = FastAPI(title="YouTube Downloader Local")

STATIC_DIR = Path(__file__).parent / "static"


def _disposition(name: str) -> str:
    """Builds the Content-Disposition with both forms of the name (RFC 6266).

    The ASCII `filename=` comes first as a fallback and the UTF-8 `filename*=`
    carries the accents. Starlette, when it receives `filename=`, emits only the
    second form for accented names — and then some browsers ignore the header
    and save the file under an internal name.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r'[^A-Za-z0-9._@ -]', "_", ascii_name).strip() or "download"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'


class InfoRequest(BaseModel):
    url: str


class TranscriptRequest(BaseModel):
    url: str
    lang: str | None = None       # chosen subtitle; None = best available
    automatic: bool | None = None
    force_audio: bool = False     # ignores subtitles and always transcribes
    prompt: str | None = None     # glossary of the video (technical jargon)


@app.middleware("http")
async def _loopback_only(request, call_next):
    """Refuses any request that does not come from this machine.

    The app has no authentication and no rate limit, and it downloads videos
    under the network identity of whoever hosts it — exposed to the internet it
    becomes a public download service running on your IP. Binding to loopback is
    the guarantee that this does not happen by accident, even if someone starts
    it with `--host 0.0.0.0`.

    The check is on the client IP, not on the bind: that is what keeps holding
    when the server listens on every interface.
    """
    client = request.client.host if request.client else None
    if client not in ("127.0.0.1", "::1", "localhost"):
        return PlainTextResponse(
            "This app is for local use. Access it via http://127.0.0.1 on this machine.",
            status_code=403,
        )
    return await call_next(request)


@app.on_event("startup")
def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        print(
            "WARNING: ffmpeg not found on PATH. Downloads at 1080p and above, which "
            "require merging audio and video, may fail. Install it with "
            "`brew install ffmpeg`."
        )
    if not any(shutil.which(rt) for rt in ("deno", "node", "bun")):
        print(
            "WARNING: no JavaScript runtime (deno/node/bun) found on PATH. "
            "YouTube requires one to sign the media URLs — without it downloads "
            "fail with HTTP 403. Install it with `brew install node`."
        )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/api/info")
def api_info(req: InfoRequest) -> dict:
    try:
        return downloader.get_info(req.url)
    except DownloaderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/download")
def api_download(
    url: str = Query(...),
    format_id: str = Query(...),
) -> FileResponse:
    # Its own temporary folder per download; cleaned up after delivery.
    tmp_dir = Path(tempfile.mkdtemp(prefix="ytdl_"))
    try:
        file_path = downloader.download(url, format_id, tmp_dir)
    except DownloaderError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # defensive
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500, detail=f"Unexpected error: {exc}"
        ) from exc

    cleanup = BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True)
    return FileResponse(
        path=file_path,
        media_type="video/mp4",
        headers={"Content-Disposition": _disposition(file_path.name)},
        background=cleanup,
    )


# ---------------------------------------------------------------------------
# Transcription / subtitles
# ---------------------------------------------------------------------------


@app.post("/api/subtitles")
def api_subtitles(req: InfoRequest) -> dict:
    """Lists the subtitles available for the video."""
    try:
        return downloader.get_subtitles(req.url)
    except DownloaderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/transcript")
def api_transcript_start(req: TranscriptRequest) -> dict:
    """Starts the job and returns the id right away (does not block)."""
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="Invalid link.")

    job = jobs.create()
    job.update(tmp_dir=Path(tempfile.mkdtemp(prefix="yttx_")), status="running",
               stage="Starting...")
    threading.Thread(target=_run_transcript_job, args=(job, req), daemon=True).start()
    return {"job_id": job.id}


@app.get("/api/transcript/{job_id}")
def api_transcript_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.as_dict()


@app.get("/api/transcript/{job_id}/download")
def api_transcript_download(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status == "error":
        raise HTTPException(status_code=400, detail=job.error or "Failed.")
    if job.status != "done" or not job.file_path:
        raise HTTPException(status_code=409, detail="Not ready yet.")

    return FileResponse(
        path=job.file_path,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _disposition(job.file_path.name)},
        background=BackgroundTask(jobs.cleanup, job_id),
    )


def _run_transcript_job(job: jobs.Job, req: TranscriptRequest) -> None:
    """Runs the cascade: ready subtitle when there is one, else transcribe audio."""
    try:
        job.update(stage="Querying the video...", progress=0.05)
        info = downloader.get_info(req.url)
        title = info.get("title") or "transcript"
        channel = info.get("channel") or ""
        job.update(title=title, channel=channel)

        track = None
        if not req.force_audio:
            available = downloader.get_subtitles(req.url)
            if req.lang:
                pool = available["automatic"] if req.automatic else available["manual"]
                track = next((t for t in pool if t["lang"] == req.lang), None)
            else:
                track = downloader.pick_best_subtitle(available)

        if track:
            job.update(stage=f"Downloading subtitle ({track['name']})...",
                       source="subtitle", progress=0.4)
            vtt = downloader.download_subtitle(
                req.url, track["lang"], track["automatic"], job.tmp_dir
            )
            text = transcriber.vtt_to_text(vtt)
        else:
            job.update(stage="No subtitles found — extracting audio...",
                       source="transcription", progress=0.1)
            wav = downloader.download_audio(req.url, job.tmp_dir)

            job.update(stage="Transcribing with Whisper (may take a while)...",
                       progress=0.2)

            def on_progress(frac: float) -> None:
                # 20%..95% of the bar corresponds to the transcription itself.
                job.update(progress=0.2 + frac * 0.75)

            text = transcriber.transcribe(wav, prompt=req.prompt, progress=on_progress)

        job.update(stage="Writing file...", progress=0.97)
        out = transcriber.write_transcript(text, job.tmp_dir, title, channel)
        job.update(status="done", stage="Done", progress=1.0, file_path=out)

    except (DownloaderError, TranscriptionError) as exc:
        job.update(status="error", error=str(exc), stage="Failed")
    except Exception as exc:  # defensive
        job.update(status="error", error=f"Unexpected error: {exc}", stage="Failed")
