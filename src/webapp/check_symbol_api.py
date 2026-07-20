"""Dashboard bridge to scripts/check_symbol.py (one-shot symbol probe).

The web process must never import ultralytics/lightgbm itself: the dual-libomp
segfault (docs/learnings/duplicate-libomp-segfault-needs-omp-threads-1.md) and
the lightgbm-before-predict crash would take the whole dashboard down, and a
10-60s YOLO run would block the event loop's worker anyway. So the probe runs
in a short-lived subprocess speaking the script's `--json` contract (last
stdout line = one JSON object with a rendered "text" field).

Single-flight: one probe at a time process-wide; concurrent clicks get a
friendly busy message instead of stacking CPU-bound YOLO runs on the VPS.
Read-only end to end -- the child fetches incremental klines into memory only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT / "scripts" / "check_symbol.py"
# Local Mac run is ~6s; measured VPS CPU run is ~41s and the forward pulse
# can contend for cores every 15 min. 150s keeps headroom without letting a
# hung child pin the single-flight lock forever.
TIMEOUT_S = 150

_busy = threading.Lock()


def _python() -> str:
    """Prefer the project venv (has torch/ultralytics/lightgbm) over the
    interpreter running the dashboard, which may be a slim venv."""
    venv = PROJECT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def check_symbol_payload(symbol: str, mode: str = "live") -> dict:
    if not _busy.acquire(blocking=False):
        return {
            "ok": False,
            "busy": True,
            "detail": "已有一个检测在跑，请等它结束后再试（单次约 40–90 秒）",
        }
    try:
        env = {
            **os.environ,
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "PYTHONPATH": str(PROJECT),
        }
        try:
            proc = subprocess.run(
                [_python(), str(SCRIPT), symbol, "--mode", mode, "--json"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                env=env,
                cwd=str(PROJECT),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": f"检测超时（>{TIMEOUT_S}s），子进程已终止；请稍后重试"}
        lines = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
        if proc.returncode != 0 or not lines:
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            return {"ok": False, "detail": f"检测进程失败 (exit {proc.returncode})：{tail}"}
        try:
            # Last line is the JSON contract; earlier lines may be pipeline
            # warnings (e.g. "frozen: skipping ...") printed to stdout.
            result = json.loads(lines[-1])
        except json.JSONDecodeError:
            return {"ok": False, "detail": f"输出解析失败：{lines[-1][:200]}"}
        return {"ok": True, "result": result}
    finally:
        _busy.release()
