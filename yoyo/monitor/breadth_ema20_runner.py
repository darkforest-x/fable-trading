"""Run the breadth-ema20 forward-only paper ledger against public Binance data.

Install on this Mac with .venv/bin/python -m yoyo.monitor.breadth_ema20_runner --install.
The other monitor accounts and their processes have separate lifecycles.
"""
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

ROOT = Path(__file__).resolve().parents[2]
LABEL = "com.fable.breadth-ema20-paper"
RUNTIME = Path(os.environ.get("FABLE_IMPULSE_RUNTIME", Path.home() / "Library/Application Support/Fable/ImpulseMonitor"))
PUBLIC = ROOT / "yoyo/monitor/static/strategy-booklet/breadth-ema20-forward.json"
BAR_MS = 300_000
ENTRY_FRESH_MS = 90_000
LOG = logging.getLogger("breadth-ema20")


def now_ms():
    return time.time_ns() // 1_000_000


def public_account(snapshot):
    fields = ("start_equity", "equity", "btc_equity", "alt_equity", "initial_sleeves",
              "open_positions", "open_btc_positions", "open_alt_positions", "last_entry_ms",
              "started_ms", "cursor_close_ms", "funding_complete", "funding_missing_positions")
    account = {key: snapshot[key] for key in fields if key in snapshot}
    account["recent_positions"] = [
        {key: p[key] for key in ("branch", "symbol", "entry_ms", "entry", "exit_ms") if key in p}
        for p in snapshot.get("positions", [])[:20]
    ]
    return account


class Heartbeat:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.state = {"strategy": "breadth-ema20", "mode": "paper", "phase": "warming",
                      "process_started_ms": now_ms(), "market": {}, "account": None}

    def update(self, **fields):
        with self.lock:
            self.state.update(fields)

    def publish(self):
        with self.lock:
            payload = dict(self.state, heartbeat_ms=now_ms())
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temp.replace(self.path)

    def tick(self, stopped):
        while not stopped.wait(10):
            try:
                self.publish()
            except OSError:
                LOG.exception("Could not publish paper state")


def install():
    """Install this one user service without touching the monitor's launchd job."""
    domain = "gui/%d" % os.getuid()
    logs = Path.home() / "Library/Logs/Fable/ImpulseMonitor"
    logs.mkdir(parents=True, exist_ok=True)
    path = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    plist = {"Label": LABEL, "ProgramArguments": [sys.executable, "-m", __spec__.name],
             "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
             "ProcessType": "Interactive", "ThrottleInterval": 15, "ExitTimeOut": 15,
             "StandardOutPath": str(logs / (LABEL + ".log")),
             "StandardErrorPath": str(logs / (LABEL + ".log")),
             "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                                      "FABLE_IMPULSE_RUNTIME": str(RUNTIME)}}
    running = subprocess.run(["launchctl", "print", domain + "/" + LABEL],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = plistlib.dumps(plist)
    if not path.exists() or path.read_bytes() != blob:
        if running:
            raise RuntimeError("Loaded service has different configuration: review " + str(path))
        temp = path.with_suffix(".tmp")
        temp.write_bytes(blob)
        temp.replace(path)
    if not running:
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    print("Loaded", domain + "/" + LABEL)


def save_touches(path, feed):
    if path is not None:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(feed.recent_touches(), allow_nan=False, separators=(",", ":")), encoding="utf-8")
        temp.replace(path)


def run(account, feed, status, stop, touch_path=None):
    if touch_path is not None and touch_path.is_file():
        feed.seed_saved_touches(json.loads(touch_path.read_text(encoding="utf-8")))

    def progress(item):
        status.update(progress={k: item[k] for k in ("done", "total", "symbol", "status") if k in item})

    ready = False
    market = {}
    failures = 0
    while not stop.is_set():
        try:
            if not ready:
                status.update(phase="warming")
                coverage = feed.warmup(progress=progress)
                market = warm_market(coverage)
                status.update(market=market)
                if not market["complete"]:
                    detail = coverage.get("error") or list(coverage.get("source_failures", {}).items())[:3]
                    raise RuntimeError(f"Market history incomplete: {market} ({detail})")
                save_touches(touch_path, feed)
                ready = True
            if stop.is_set():
                return
            current = now_ms()
            through = (current - 1_000) // BAR_MS * BAR_MS
            cursor = account.cursor_ms()
            start = through - BAR_MS if cursor is None else cursor
            if start < through:
                # Re-read open symbols for each close; the previous bar can open a basket.
                boundary = start + BAR_MS
                held = account.open_symbols()
                event = next(iter(feed.iter_closed_events(start, boundary, position_symbols=held)), None)
                if event is None or event["close_ms"] != boundary:
                    raise RuntimeError("Feed did not return expected close %d" % boundary)
                expected = event.get("market_symbols_expected", 0)
                observed = event.get("market_symbols_observed", 0)
                age_ms = now_ms() - boundary
                live = bool(event.get("is_realtime")) and not event.get("is_replay") and 0 <= age_ms <= ENTRY_FRESH_MS
                quotes_ready = event.get("source_coverage_complete") if live else event.get("bar_available")
                if (not quotes_ready or event.get("market_rebuild_complete") is False
                        or (live and (not expected or expected != observed))):
                    raise RuntimeError(f"Incomplete market at {boundary}: {observed}/{expected}")
                event["candidates"] = [c for c in event.get("candidates", [])
                                       if c.get("rule_pass") is True and c.get("data_complete") is True]
                required = set(held) | {"BTCUSDT"}
                for candidate in event["candidates"]:
                    if candidate.get("branch") == "ALT":
                        required.update(member["symbol"] for member in candidate["members"])
                event["bars_by_symbol"] = {s: row for s, row in event.get("bars_by_symbol", {}).items() if s in required}
                if stop.is_set():
                    return
                event.update(is_replay=not live, is_realtime=live)
                if event.get("new_first_touches"):
                    save_touches(touch_path, feed)
                result = account.process_closed_bar(event)
                if not result["processed"]:
                    raise RuntimeError("Paper event deferred: %s" % result.get("reason", "unknown"))
                market = dict(market, symbols=expected, covered=observed,
                              trailing28=event.get("trailing28_any_touch_unique_n", market.get("trailing28")),
                              lower_first_touches=sum(r.get("side") == 1 for r in event.get("new_first_touches", [])),
                              btc_close=event.get("close"))
                status.update(market=market, account=public_account(account.snapshot()), last_close_ms=boundary,
                              market_lag_ms=age_ms, last_event_realtime=live,
                              phase="running" if boundary == through and live else "catching_up",
                              error=None)
                failures = 0
                LOG.info("close=%s live=%s age_ms=%s", boundary, live, age_ms)
                continue
            stop.wait(min(10, max(.5, (start + BAR_MS + 1_000 - now_ms()) / 1000)))
        except Exception as exc:
            failures += 1
            status.update(phase="degraded", error="行情或账本数据待补，正在重试")
            LOG.warning("Paper cycle failed: %s", exc, exc_info=failures == 1)
            stop.wait(min(30, 2 ** min(failures, 5)))


def warm_market(coverage):
    return {"complete": coverage["complete"], "symbols": coverage["total"], "covered": coverage["covered"],
            "trailing28": coverage.get("trailing28_any_touch_unique_n"),
            "verified_touch_hours": coverage.get("verified_first_touch_windows")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    if parser.parse_args().install:
        install()
        return
    from yoyo.monitor.breadth_ema20_account import BreadthEma20Paper
    from yoyo.monitor.breadth_ema20_feed import BinanceBreadthEma20Feed

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / (LABEL + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        account = BreadthEma20Paper(RUNTIME)
        status = Heartbeat(PUBLIC)
        status.update(account=public_account(account.snapshot()))
        status.publish()
        heartbeat = threading.Thread(target=status.tick, args=(stop,), daemon=True)
        heartbeat.start()
        try:
            run(account, BinanceBreadthEma20Feed(), status, stop, RUNTIME / "breadth-ema20-touches.json")
        finally:
            stop.set()
            heartbeat.join()
            status.update(phase="stopped")
            status.publish()


if __name__ == "__main__":
    main()
