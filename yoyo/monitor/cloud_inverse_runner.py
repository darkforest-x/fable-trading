"""Run the paper-only live-cloud-v17 ledger against locally observed public signals."""
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import threading
import time

from .cloud_inverse_account import CloudInversePaper

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(os.environ.get("FABLE_IMPULSE_RUNTIME", Path.home() / "Library/Application Support/Fable/ImpulseMonitor"))
PUBLIC = ROOT / "yoyo/monitor/static/strategy-booklet/live-cloud-v17-forward.json"
LABEL = "com.fable.cloud-inverse-paper"
LOG = logging.getLogger(LABEL)


def now_ms():
    return time.time_ns() // 1_000_000


def publish(path, snapshot):
    """Readers see either the last complete snapshot or the new one."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    os.replace(tmp, path)


def install(runtime, public):
    """Register only this independent paper ledger in the user's launchd domain."""
    domain = f"gui/{os.getuid()}"
    service = domain + "/" + LABEL
    logs = Path.home() / "Library/Logs/Fable/ImpulseMonitor"
    logs.mkdir(parents=True, exist_ok=True)
    plist = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    args = [sys.executable, "-m", "yoyo.monitor.cloud_inverse_runner", "--runtime", str(runtime),
            "--snapshot", str(public)]
    body = plistlib.dumps(dict(Label=LABEL, ProgramArguments=args, WorkingDirectory=str(ROOT),
                               RunAtLoad=True, KeepAlive=True, ThrottleInterval=15,
                               EnvironmentVariables={"PYTHONUNBUFFERED":"1", "PYTHONDONTWRITEBYTECODE":"1"},
                               StandardOutPath=str(logs / (LABEL + ".log")),
                               StandardErrorPath=str(logs / (LABEL + ".log"))))
    loaded = subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0
    if not plist.is_file() or plist.read_bytes() != body:
        if loaded:
            raise RuntimeError(f"Loaded service has a different configuration: {service}")
        plist.parent.mkdir(parents=True, exist_ok=True)
        tmp = plist.with_suffix(".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, plist)
    if not loaded:
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print(f"Loaded {service}")


def cycle(feed, account):
    """Keep exit monitoring independent of failures reading the external signal table."""
    errors = []
    for name, operation in (("读取收盘信号", feed.queue_new_events),
                            ("持仓估值", feed.advance_open_positions)):
        try:
            operation(account, now_ms())
        except Exception as exc:
            LOG.exception("%s failed", name)
            errors.append(f"{name}暂不可用（{type(exc).__name__}）")
    # Admission requires this pass's account marks and complete signal ordering.
    if not errors:
        try:
            feed.process_queued(account, now_ms())
        except Exception as exc:
            LOG.exception("Signal admission failed")
            errors.append(f"入场判定暂不可用（{type(exc).__name__}）")
    health = feed.health()
    market = dict(feed.market_state)
    degraded = errors or health.get("degraded") or health.get("ok") is False
    return account.snapshot(phase="degraded" if degraded else "watching",
                            health=health, market=market, error="；".join(errors) or None)


def run(runtime, public, once=False):
    from .cloud_inverse_feed import CloudInverseFeed

    runtime.mkdir(parents=True, exist_ok=True)
    public.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with (runtime / (LABEL + ".lock")).open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        account = CloudInversePaper(runtime)
        publish(public, account.snapshot(phase="starting"))
        feed = CloudInverseFeed(runtime)
        while not stop.is_set():
            try:
                state = cycle(feed, account)
            except Exception as exc:
                LOG.exception("Paper cycle failed")
                state = account.snapshot(phase="degraded", error=f"行情循环待恢复（{type(exc).__name__}）")
            publish(public, state)
            if once:
                return state
            stop.wait(10)
        publish(public, account.snapshot(phase="stopped"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=RUNTIME)
    parser.add_argument("--snapshot", type=Path, default=PUBLIC)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--once", action="store_true")
    args = parser.parse_args()
    runtime, public = args.runtime.expanduser().resolve(), args.snapshot.expanduser().resolve()
    if args.install:
        install(runtime, public)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        state = run(runtime, public, args.once)
        if args.once:
            print(json.dumps({"phase":state["phase"], "error":state.get("error"), "account":state["account"]}))


if __name__ == "__main__":
    main()
