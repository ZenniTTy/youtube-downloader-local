"""Audio transcription and conversion of subtitles into running text.

Two independent entry points:
  - vtt_to_text(path): converts a VTT subtitle into clean text (fast)
  - transcribe(wav, ...): runs Whisper over a WAV (slow, minutes)

Writing the final file lives in write_transcript(), separated on purpose: today
it only emits TXT, but Whisper already returns the timings, so adding SRT later
is just writing the same data in another format.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

# Terms that Whisper commonly gets wrong in Brazilian technical content.
# This becomes the `initial_prompt`, which conditions the model to spell them
# this way.
# The prompt string is deliberately kept in Portuguese: its whole purpose is to
# prime Whisper — which transcribes with language="pt" — for how this jargon is
# written in Brazilian Portuguese. Translating it would remove the Portuguese
# context the model needs and defeat the conditioning.
DEFAULT_PROMPT = (
    "Discussão técnica sobre subagents, harness, hooks, Claude Code, "
    "spec-driven development, benchmark, monorepo, prompt, LLM, deploy, "
    "context window, agentes de IA, workflow, backend, frontend."
)

MODEL_SIZE = "small"


class TranscriptionError(Exception):
    """Friendly error during transcription."""


# --- Fast path: subtitle already available ----------------------------------

_TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
_INLINE_TAG = re.compile(r"<[^>]+>")


def vtt_to_text(vtt_path: Path) -> str:
    """Extracts running text from a VTT, without timings, tags or repeated lines.

    YouTube's auto-generated subtitles repeat each sentence while it is being
    typed on screen, so we discard lines that merely repeat what was already
    emitted.
    """
    raw = Path(vtt_path).read_text(encoding="utf-8", errors="replace")

    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or _TIMESTAMP.match(line):
            continue
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if line.isdigit():  # block numbering (SRT)
            continue
        line = html.unescape(_INLINE_TAG.sub("", line)).strip()
        if not line:
            continue
        # Rolling subtitle: ignore repetition of what was already said.
        if lines and (line == lines[-1] or line in lines[-1]):
            continue
        if lines and lines[-1].endswith(line):
            continue
        lines.append(line)

    return _join_paragraphs(lines)


def _join_paragraphs(lines: list[str]) -> str:
    """Joins the utterances and breaks into paragraphs every ~5 sentences."""
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()

    sentences = re.split(r"(?<=[.!?])\s+", text)
    paragraphs, buf = [], []
    for s in sentences:
        buf.append(s)
        if len(buf) >= 5:
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(p for p in paragraphs if p.strip())


# --- Slow path: Whisper over the audio ---------------------------------------


def transcribe(wav_path: Path, prompt: str | None = None, progress=None) -> str:
    """Transcribes a WAV with faster-whisper and returns running text.

    `progress` receives a 0..1 fraction as the segments are decoded.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "faster-whisper is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    try:
        model = WhisperModel(
            MODEL_SIZE, device="cpu", compute_type="int8", cpu_threads=4
        )
        segments, info = model.transcribe(
            str(wav_path),
            language="pt",
            initial_prompt=(prompt or DEFAULT_PROMPT),
            beam_size=5,
            vad_filter=True,
        )

        total = info.duration or 0
        pieces: list[str] = []
        for seg in segments:
            pieces.append(seg.text.strip())
            if progress and total:
                progress(min(seg.end / total, 1.0))
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError(f"Failed to transcribe the audio: {exc}") from exc

    if not pieces:
        raise TranscriptionError("No speech was recognized in this audio.")
    return _join_paragraphs(pieces)


def build_filename(title: str, channel: str = "") -> str:
    """Builds 'Title - @channel.txt', without the characters the system rejects."""
    base = title.strip() or "transcript"
    if channel:
        base = f"{base} - {channel.strip()}"
    safe = re.sub(r'[/\\:*?"<>|]', "_", base)
    safe = re.sub(r"\s+", " ", safe).strip()
    return f"{safe or 'transcript'}.txt"


def write_transcript(text: str, dest_dir: Path, title: str, channel: str = "") -> Path:
    """Writes the text to a TXT file and returns the path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / build_filename(title, channel)
    out.write_text(text, encoding="utf-8")
    return out
