"""Subprocess job runner: FIFO queue, concurrency limit, timeout, cancel.

Argv always comes from whitelist.build_argv — never from client free strings.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from src.webapp.jobs.store import JobStore, TERMINAL, get_store
from src.webapp.jobs.whitelist import JOB_TYPES, build_argv, human_summary, validate_params
from src.webapp.ops_flags import executor_enabled

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs" / "jobs"


def job_log_dir() -> Path:
    raw = os.environ.get("OPS_JOB_LOG_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_LOG_DIR


def max_concurrent_jobs() -> int:
    raw = os.environ.get("OPS_MAX_CONCURRENT_JOBS", "1").strip() or "1"
    try:
        n = int(raw)
    except ValueError:
        n = 1
    return max(1, min(n, 4))


class JobRunner:
    """Background worker thread that drains queued jobs with concurrency=1 default."""

    def __init__(self, store: JobStore | None = None) -> None:
        self.store = store or get_store()
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._cancel_requested: set[str] = set()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._started = False
        # Orphan running rows once per process lifetime.
        self.store.mark_orphaned_running()

    def start_background(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._loop, name="ops-job-runner", daemon=True
            )
            self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def enqueue(
        self, job_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not executor_enabled():
            raise PermissionError(
                "本实例已禁用任务执行器（ENABLE_JOB_EXECUTOR!=1；VPS 默认关闭）。"
            )
        clean = validate_params(job_type, params)
        argv = build_argv(job_type, clean)
        summary = human_summary(job_type, clean)
        log_dir = job_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        # Placeholder id path rewritten after create — use temp then update via known id.
        # Create first to obtain id, then set log path consistently.
        job = self.store.create(
            job_type=job_type,
            params=clean,
            argv=argv,
            summary=summary,
            log_path="",  # filled immediately below
        )
        log_path = log_dir / f"{job['id']}.log"
        log_path.touch(exist_ok=True)
        # Patch log_path in store
        with self.store._lock, self.store._conn() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE jobs SET log_path = ? WHERE id = ?",
                (str(log_path), job["id"]),
            )
        job = self.store.get(job["id"])
        assert job is not None
        self.start_background()
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] in TERMINAL:
            return job
        with self._lock:
            self._cancel_requested.add(job_id)
            proc = self._procs.get(job_id)
        if job["status"] == "queued":
            self.store.finish(
                job_id, status="cancelled", exit_code=None, error_summary="cancelled"
            )
            return self.store.get(job_id)  # type: ignore[return-value]
        if proc is not None and proc.poll() is None:
            self._terminate_process_group(proc)
        # If still running, worker will observe cancel / exit and finish row.
        # Best-effort mark if process already gone.
        if proc is None or proc.poll() is not None:
            self.store.finish(
                job_id,
                status="cancelled",
                exit_code=proc.returncode if proc else None,
                error_summary="cancelled",
            )
        return self.store.get(job_id)  # type: ignore[return-value]

    def read_log_tail(self, job_id: str, *, max_lines: int = 200) -> str:
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        path = job.get("log_path") or ""
        if not path:
            return ""
        p = Path(path)
        if not p.is_file():
            return ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = text.splitlines()
        max_lines = max(1, min(int(max_lines), 5000))
        return "\n".join(lines[-max_lines:])

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._pump()
            except Exception:
                # Never crash the worker thread permanently.
                time.sleep(0.5)
            # Wait for wake or short poll.
            self._wake.wait(timeout=0.5)
            self._wake.clear()

    def _pump(self) -> None:
        if not executor_enabled():
            return
        # Cap concurrent running processes.
        if self.store.count_running() >= max_concurrent_jobs():
            return
        job = self.store.claim_next_queued()
        if not job:
            return
        job_id = job["id"]
        with self._lock:
            if job_id in self._cancel_requested:
                self.store.finish(
                    job_id,
                    status="cancelled",
                    error_summary="cancelled_before_start",
                )
                return
        # Launch in a dedicated thread so concurrency limit can schedule next
        # only after count_running drops (claim sets running immediately).
        t = threading.Thread(
            target=self._run_job, args=(job,), name=f"ops-job-{job_id[:8]}", daemon=True
        )
        t.start()

    def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        job_type = job["job_type"]
        argv = job.get("argv") or build_argv(job_type, job.get("params") or {})
        timeout = JOB_TYPES[job_type].timeout_sec if job_type in JOB_TYPES else 3600
        log_path = Path(job.get("log_path") or (job_log_dir() / f"{job_id}.log"))
        log_path.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        # Whitelisted env append only.
        env["PYTHONPATH"] = (
            str(PROJECT_ROOT)
            if not env.get("PYTHONPATH")
            else f"{PROJECT_ROOT}{os.pathsep}{env['PYTHONPATH']}"
        )

        header = (
            f"# job_id={job_id}\n"
            f"# job_type={job_type}\n"
            f"# argv={argv!r}\n"
            f"# cwd={PROJECT_ROOT}\n"
            f"# started\n"
        )
        try:
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(header)
                logf.flush()
                try:
                    proc = subprocess.Popen(
                        argv,
                        cwd=str(PROJECT_ROOT),
                        env=env,
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,
                    )
                except OSError as exc:
                    self.store.finish(
                        job_id,
                        status="failed",
                        exit_code=127,
                        error_summary=f"spawn_failed: {exc}",
                    )
                    return

                with self._lock:
                    self._procs[job_id] = proc

                status, exit_code, err = self._wait_proc(job_id, proc, timeout)
                with open(log_path, "a", encoding="utf-8") as logf2:
                    logf2.write(f"\n# finished status={status} exit_code={exit_code}\n")
                self.store.finish(
                    job_id,
                    status=status,
                    exit_code=exit_code,
                    error_summary=err,
                )
        finally:
            with self._lock:
                self._procs.pop(job_id, None)
                self._cancel_requested.discard(job_id)
            self._wake.set()

    def _wait_proc(
        self, job_id: str, proc: subprocess.Popen[str], timeout: int
    ) -> tuple[str, int | None, str | None]:
        deadline = time.monotonic() + max(1, timeout)
        while True:
            with self._lock:
                cancel = job_id in self._cancel_requested
            if cancel:
                self._terminate_process_group(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._kill_process_group(proc)
                    proc.wait(timeout=5)
                return "cancelled", proc.returncode, "cancelled"

            rc = proc.poll()
            if rc is not None:
                if rc == 0:
                    return "succeeded", rc, None
                return "failed", rc, f"exit_code={rc}"

            if time.monotonic() >= deadline:
                self._terminate_process_group(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._kill_process_group(proc)
                    proc.wait(timeout=5)
                return "timeout", proc.returncode, f"timeout_sec={timeout}"

            time.sleep(0.2)

    @staticmethod
    def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
        try:
            if proc.pid:
                os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except Exception:
                pass

    @staticmethod
    def _kill_process_group(proc: subprocess.Popen[str]) -> None:
        try:
            if proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:
                pass


_runner: JobRunner | None = None
_runner_lock = threading.Lock()


def get_runner() -> JobRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = JobRunner()
        return _runner


def reset_runner_for_tests(store: JobStore | None = None) -> JobRunner:
    global _runner
    with _runner_lock:
        if _runner is not None:
            _runner.stop()
        _runner = JobRunner(store=store or get_store())
        return _runner
