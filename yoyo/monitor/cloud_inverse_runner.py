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

from .cloud_inverse_account import HOUR_MS, STEP_12H_MS, CloudInversePaper
from .monday_fib import week_window

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


def next_eligible_close(now):
    """First future UTC 12H close whose entire candle uses a finished NY Monday."""
    first_open = now // STEP_12H_MS * STEP_12H_MS
    for i in range(15):
        opening = first_open + i * STEP_12H_MS
        close = opening + STEP_12H_MS
        week = week_window(opening)
        if week["end"] <= opening and close <= week["expires"]:
            return close
    raise ValueError("No eligible 12h bar within a full New York week")


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
    synced_at = feed.health().get("clock_synchronized_ms") or 0
    if now_ms() - synced_at > 300_000:
        try:
            feed.synchronize()
        except Exception as exc:
            LOG.exception("Public exchange clock unavailable")
            errors.append(f"交易所公开时钟暂不可用（{type(exc).__name__}）")
    for name, operation in (("读取收盘信号", feed.queue_new_events),
                            ("持仓估值", feed.advance_open_positions)):
        try:
            operation(account, feed.clock())
        except Exception as exc:
            LOG.exception("%s failed", name)
            errors.append(f"{name}暂不可用（{type(exc).__name__}）")
    # Admission requires this pass's account marks and complete signal ordering.
    last_due_close = (feed.clock() - 120000) // HOUR_MS * HOUR_MS
    overdue = [p["symbol"] for p in account.open_positions() if p["last_mark_ms"] < last_due_close]
    if overdue:
        errors.append("持仓缺少已收盘报价，估值待核对：" + "、".join(sorted(overdue)))
    if not errors:
        try:
            feed.process_queued(account, feed.clock())
        except Exception as exc:
            LOG.exception("Signal admission failed")
            errors.append(f"入场判定暂不可用（{type(exc).__name__}）")
    if errors:
        from .cloud_inverse_feed import SIGNAL_BATCH_MAX_DELAY_MS
        checked = feed.clock()
        for boundary in account.queued_boundaries():
            if checked - boundary > SIGNAL_BATCH_MAX_DELAY_MS:
                for event in account.queued_candidates(boundary):
                    account.mark_missed(event["event_id"], "admission_unavailable_at_deadline", now_ms=checked)
    raw_health = feed.health()
    health = {k:raw_health[k] for k in (
        "state","source_db","public_market","public_only","private_or_order_api_used","clock_offset_ms",
        "clock_synchronized_ms","source_boundary_ms","source_expected","source_completed","rankable_count",
        "queued_new","source_rows_seen","source_complete","signal_batch_max_delay_ms","ticker_max_age_ms",
        "last_processed_boundary_ms","last_market_observed_ms","minute_bars_processed","hour_bars_processed",
    ) if k in raw_health}
    market = {k:feed.market_state[k] for k in (
        "boundary_ms","source_boundary_ms","expected_symbols","completed_symbols","rankable_count","source_complete",
        "rankable_minimum","rankable_threshold_count","observed_at_ms","last_signal_close_ms",
    ) if k in feed.market_state}
    market["next_eligible_close_ms"] = next_eligible_close(feed.clock())
    late = (market.get("source_complete") is False and feed.clock() - market.get("boundary_ms",0) >
            (health.get("signal_batch_max_delay_ms") or 900000))
    if late:
        errors.append("本轮 12H 收盘扫描未在 15 分钟内完成")
    degraded = errors or str(raw_health.get("state","")).startswith("degraded") or raw_health.get("state") in ("error","source_stale")
    phase = "waiting" if raw_health.get("state") == "waiting_source_scan" else "watching"
    return account.snapshot(phase="degraded" if degraded else phase,
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
