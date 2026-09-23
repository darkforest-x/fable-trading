"""Independent, restartable forward paper worker using existing closed data.

Only reads monitor checkpoints and accepted signals; no network, broker,
notification or ACTIVE imports. Intent time is the worker's actual observation,
not the signal bar close. Missed signals are skipped under the monitor's
unchanged freshness contract. Existing intents may reconcile after downtime.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import time

from .paper_store import PaperStore, STEP_MS, clock_ms
from .strategies import get_plugin
from .paper_source import MonitorSource, source_manifest


def tick(store, root, source, run, at=None, manifest=None):
    from yoyo.monitor import FRESH_MS
    live_clock = at is None
    at = clock_ms() if live_clock else at
    manifest = source_manifest(root) if manifest is None else manifest
    if manifest["hash"] != run["spec"]["source_hash"]:
        store.heartbeat(run["id"], "策略或成交引擎代码已变化，本次运行停止接收；请新建运行", fatal=True, at=at)
        return
    plugin = get_plugin(run["strategy_id"])
    spec = run["spec"]
    active = store.decisions(run["id"], limit=10000, active_only=True)["decisions"]
    grouped = {}
    for item in active:
        grouped.setdefault((item["symbol"], item["timeframe"]), []).append(item)
    errors = []
    for (symbol, tf), positions in grouped.items():
        snapshot = source.checkpoint(symbol, tf, at)
        if not snapshot or not snapshot["candles"]:
            errors.append(f"{symbol} {tf} 行情暂不可用")
            continue
        candles = store.merge_bars(run["id"], symbol, tf, snapshot["candles"], at)
        for item in positions:
            frozen_tick = item["payload"].get("tick", snapshot["tick"])
            if frozen_tick != snapshot["tick"]:
                raise ValueError("市场最小价格变动单位已变化，请保留当前运行并新建版本")
            trade = plugin.evaluate_trade(candles, tf, symbol, frozen_tick, item, at)
            store.update_trade(run["id"], item["event_id"], trade, at)
        if at - (candles[-1]["t"] + STEP_MS[tf]) > STEP_MS[tf] + FRESH_MS:
            errors.append(f"{symbol} {tf} 行情滞后，模拟结果停留在最后闭合 K 线")
    # Re-read status after potentially slow evaluation so pause/stop wins races.
    current = store.run(run["id"])
    if current["status"] == "running":
        cursor = current["admit_after_ms"]
        seen = store.seen_ids(run["id"])
        while True:
            events = source.events(plugin, cursor, spec["symbols"], spec["timeframes"])
            for event in events:
                if event["id"] in seen:
                    continue
                # An intent only exists after its input prefix has been retained.
                snapshot = source.checkpoint(event["symbol"], event["timeframe"], at)
                if not snapshot or not snapshot["candles"]:
                    errors.append(f"{event['symbol']} {event['timeframe']} 信号缺少行情前缀")
                    continue
                if snapshot["candles"][-1]["t"] + STEP_MS[event["timeframe"]] < event["bar_close_ms"]:
                    errors.append(f"{event['symbol']} {event['timeframe']} 等待信号对应的闭合行情")
                    continue
                store.merge_bars(run["id"], event["symbol"], event["timeframe"], snapshot["candles"], at)
                observed = clock_ms() if live_clock else at
                store.decide(run["id"], event, observed, FRESH_MS)
            if len(events) < 2000:
                break
            cursor = tuple(events[-1]["cursor"])
    store.heartbeat(run["id"], "；".join(errors[:5]) or None, at=at)


def run(root, runtime, monitor_runtime, once=False):
    os.nice(10)
    store, source = PaperStore(runtime), MonitorSource(monitor_runtime)
    with (store.runtime / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        process_manifest = source_manifest(root)
        while True:
            runs = store.runs(active_only=True)
            if not runs:
                return
            manifest = source_manifest(root)
            if manifest["hash"] != process_manifest["hash"]:
                for current in runs:
                    store.heartbeat(current["id"], "模拟进程运行期间源码已改变，请新建运行", fatal=True)
                return
            for current in runs:
                try:
                    tick(store, root, source, current, manifest=manifest)
                except ValueError as error:
                    store.heartbeat(current["id"], str(error), fatal=True)
                except Exception as error:
                    store.heartbeat(current["id"], f"行情或工作进程异常：{type(error).__name__}: {error}")
            if once:
                return
            time.sleep(15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--monitor-runtime", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run(args.root, args.runtime, args.monitor_runtime, args.once)
