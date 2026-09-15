"""In-memory registry of the transcription jobs.

Transcribing takes minutes, so the HTTP request cannot hold the process. The
flow is: POST creates the job and returns an id; the browser polls the progress;
when it finishes, it downloads the file. Since the app is local and single
session, a dictionary protected by a lock is enough — no need for Redis or a
database.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Job:
    id: str
    status: str = "pending"  # pending | running | done | error
    stage: str = ""          # text shown to the user
    progress: float = 0.0    # 0..1
    source: str = ""         # "subtitle" or "transcription"
    title: str = ""
    channel: str = ""
    file_path: Path | None = None
    error: str | None = None
    tmp_dir: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def as_dict(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "progress": round(self.progress, 3),
                "source": self.source,
                "title": self.title,
                "channel": self.channel,
                "error": self.error,
                "ready": self.status == "done",
            }


_jobs: dict[str, Job] = {}
_registry_lock = threading.Lock()


def create() -> Job:
    job = Job(id=uuid.uuid4().hex[:12])
    with _registry_lock:
        _jobs[job.id] = job
    return job


def get(job_id: str) -> Job | None:
    with _registry_lock:
        return _jobs.get(job_id)


def cleanup(job_id: str) -> None:
    """Remove the job and delete its temporary files."""
    with _registry_lock:
        job = _jobs.pop(job_id, None)
    if job and job.tmp_dir:
        shutil.rmtree(job.tmp_dir, ignore_errors=True)
