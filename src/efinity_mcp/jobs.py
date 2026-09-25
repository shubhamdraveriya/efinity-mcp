"""Background jobs for long-running Efinity processes (compiles, programming, STA scripts)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import env

# efx_run prints "   map :\tPASS" / "   pnr :\tFAIL" after each stage
_STAGE_RESULT = re.compile(r"^\s*([\w .\-]+?)\s*:\s*(PASS|FAIL)\b")
_STAGE_START = re.compile(r"^Running:\s+(efx_run_\w+)\.py")
_STAGE_NAMES = {
    "efx_run_map": "synthesis",
    "efx_run_dbg": "debugger auto instantiation",
    "efx_run_pt_unified": "interface",
    "efx_run_pt": "interface",
    "efx_run_pnr": "place & route",
    "efx_run_pgm": "bitstream",
    "efx_run_sim": "simulation",
}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Job:
    id: str
    kind: str
    description: str
    cmd: list[str]
    cwd: Path
    log_path: Path
    project_key: str | None = None
    timeout: float | None = None
    project: object | None = None  # project.Project for flow jobs
    started: float = field(default_factory=time.time)
    finished: float | None = None
    returncode: int | None = None
    state: str = "running"  # running | succeeded | failed | cancelled | timeout
    current_stage: str | None = None
    stages: list[dict] = field(default_factory=list)
    lines: deque = field(default_factory=lambda: deque(maxlen=4000))
    proc: subprocess.Popen | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def elapsed(self) -> float:
        return round((self.finished or time.time()) - self.started, 1)

    def summary(self, tail: int = 30) -> dict:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "description": self.description,
            "state": self.state,
            "returncode": self.returncode,
            "elapsed_s": self.elapsed(),
            "started": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
            "current_stage": self.current_stage if self.state == "running" else None,
            "stages": self.stages,
            "log_file": str(self.log_path),
            "output_tail": list(self.lines)[-tail:] if tail else [],
        }


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(
        self,
        kind: str,
        description: str,
        cmd: list[str],
        cwd: Path,
        env_vars: dict[str, str],
        project_key: str | None = None,
        timeout: float | None = None,
        success_check=None,
    ) -> Job:
        with self._lock:
            if project_key:
                busy = [j for j in self._jobs.values() if j.project_key == project_key and j.state == "running"]
                if busy:
                    raise RuntimeError(
                        f"Job {busy[0].id} ({busy[0].description}) is still running on this project. "
                        "Wait for it or cancel it first."
                    )
            job_id = uuid.uuid4().hex[:8]
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            log_path = env.logs_dir() / f"{stamp}_{kind}_{job_id}.log"
            job = Job(job_id, kind, description, cmd, cwd, log_path, project_key, timeout)
            self._jobs[job_id] = job

        job.proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env_vars,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            # own process group on POSIX, so cancel can stop efx_run's child tools too
            start_new_session=not env.IS_WINDOWS,
        )
        threading.Thread(target=self._pump, args=(job, success_check), daemon=True).start()
        if timeout:
            threading.Thread(target=self._watchdog, args=(job,), daemon=True).start()
        return job

    def _pump(self, job: Job, success_check):
        with job.log_path.open("w", encoding="utf-8") as log:
            log.write(f"# {' '.join(job.cmd)}\n# cwd: {job.cwd}\n")
            for raw in job.proc.stdout:
                line = raw.rstrip("\r\n")
                log.write(line + "\n")
                log.flush()
                job.lines.append(line)
                if m := _STAGE_START.match(line):
                    job.current_stage = _STAGE_NAMES.get(m.group(1), m.group(1))
                elif m := _STAGE_RESULT.match(line):
                    job.stages.append({"stage": m.group(1).strip(), "result": m.group(2), "at_s": job.elapsed()})
        job.returncode = job.proc.wait()
        job.finished = time.time()
        if job.state == "running":
            ok = job.returncode == 0 and not any(s["result"] == "FAIL" for s in job.stages)
            if ok and success_check is not None:
                ok = success_check(job)
            job.state = "succeeded" if ok else "failed"
        job.done.set()

    def _watchdog(self, job: Job):
        if not job.done.wait(job.timeout):
            job.state = "timeout"
            self._kill(job)

    @staticmethod
    def _kill(job: Job):
        if job.proc and job.proc.poll() is None:
            if env.IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(job.proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                try:
                    os.killpg(job.proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    job.proc.kill()

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state == "running":
            job.state = "cancelled"
            self._kill(job)
            job.done.wait(15)
        return job

    def get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise ValueError(f"No job with id {job_id}. Use list_jobs to see jobs from this session.") from None

    def running_for(self, project_key: str) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.project_key == project_key and j.state == "running"), None)

    def wait(self, job: Job, seconds: float) -> bool:
        return job.done.wait(max(0.0, seconds))

    def all(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)
