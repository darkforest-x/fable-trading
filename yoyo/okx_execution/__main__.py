"""Local-only Task 59 setup, read-only preflight and explicitly started worker.

No server endpoint, public tunnel, environment echo, service auto-installation,
account-mode mutation, withdrawal or funding operation is provided.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import json
import os
from pathlib import Path
import sys
import time

from .client import Client, APIError, TransportError
from .config import Config, DEFAULT_RUNTIME, LIVE_ACK, private_directory, read_private, write_private
from .engine import Engine, TERMINAL
from .source import Source
from .store import Store


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="SPIKE V13.1 / OKX standalone execution")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create non-secret configuration; sends no request")
    init.add_argument("--demo", action="store_true")
    init.add_argument("--host", default="https://www.okx.com")
    sub.add_parser("credentials", help="hidden terminal input; save once outside git")
    sub.add_parser("status", help="local ledger only; never loads API secrets")
    sub.add_parser("preflight", help="read-only exchange checks; never changes account settings")
    preview = sub.add_parser("preview", help="read-only fresh signal order plans; no persistent decisions")
    preview.add_argument("--lookback-minutes", type=int, default=30)
    run = sub.add_parser("run", help="continuous worker; requires an explicit execution acknowledgement")
    run.add_argument("--ack", required=True, choices=[LIVE_ACK, "EXECUTE-DEMO"])
    run.add_argument("--manage-only", action="store_true", help="manage owned positions without new entries")
    args = parser.parse_args(argv)
    os.umask(0o077)
    runtime = private_directory(args.runtime)
    if args.command == "init":
        config = Config(demo=args.demo, host=args.host)
        config.validate()
        write_private(runtime / "config.json", asdict(config))
        Store(runtime)
        print_json(dict(initialized=True, trading_started=False, configuration=asdict(config)))
        return
    config = Config(**read_private(runtime / "config.json"))
    config.validate()
    store = Store(runtime)
    if args.command == "status":
        rows = store.trades()
        print_json(dict(configuration=asdict(config), credential_file_present=(runtime / "credentials.json").is_file(),
                        cutoff_ms=store.meta("cutoff_ms"), heartbeat_ms=store.meta("heartbeat_ms"),
                        errors=store.meta("errors", []), worker_error=store.meta("worker_error"),
                        active=[{k: t.get(k) for k in ("id", "symbol", "timeframe", "side", "size", "state", "reason")}
                                for t in rows if t["state"] not in TERMINAL],
                        states={s: sum(t["state"] == s for t in rows) for s in sorted({t["state"] for t in rows})}))
        return
    if args.command == "credentials":
        if not sys.stdin.isatty():
            raise ValueError("enter credentials in a local terminal, not a pipe/chat/tool argument")
        if (runtime / "credentials.json").exists():
            raise ValueError("credentials already exist; replacement is a separate owner action")
        values = {name: getpass.getpass(label) for name, label in
                  (("api_key", "OKX API key: "), ("secret", "OKX secret key: "), ("passphrase", "OKX passphrase: "))}
        if any(not value.strip() or value != value.strip() for value in values.values()):
            raise ValueError("empty credential or surrounding whitespace")
        write_private(runtime / "credentials.json", dict(values, demo=config.demo))
        print_json(dict(saved=True, trading_started=False))
        return
    credentials = read_private(runtime / "credentials.json")
    execute = args.command == "run"
    if execute and args.ack != ("EXECUTE-DEMO" if config.demo else LIVE_ACK):
        raise ValueError("execution acknowledgement differs from credential environment")
    client = Client(config, credentials, writes=execute)
    engine = Engine(config, client, Source(Path(config.source)), store)
    if args.command == "preflight":
        result = engine.preflight()
        print_json(dict(ok=True, orders_sent=0, account_mode=result["account_mode"],
                        position_mode=result["position_mode"], available_usdt=result["available"],
                        equity_usdt=result["equity"], exchange_roundtrip_trading_verified=False))
    elif args.command == "preview":
        if not 1 <= args.lookback_minutes <= 30:
            raise ValueError("preview lookback must be within existing freshness window")
        account = engine.preflight()
        now = engine.clock()
        for event in engine.source.events(now - args.lookback_minutes * 60_000, now):
            try:
                result = engine.prepare(event, account)
                print_json({k: v for k, v in result.items() if k != "event"})
            except (ValueError, APIError) as exc:
                print_json(dict(id=event["id"], status="unavailable", reason=str(exc)))
    elif execute:
        with store.lock():
            if store.meta("cutoff_ms") is None:
                account = engine.preflight()
                store.set_meta("account_uid", account["uid"])
                store.set_meta("configuration", asdict(config))
                store.set_meta("cutoff_ms", engine.clock())
            else:
                account = engine.account()
                if account["uid"] != store.meta("account_uid") or asdict(config) != store.meta("configuration"):
                    raise ValueError("execution account/configuration changed")
            print_json(dict(worker_started=True, demo=config.demo, new_entries=not args.manage_only,
                            cutoff_ms=store.meta("cutoff_ms"), max_positions=None, aggregate_risk_limit=None,
                            note="Ctrl-C stops the local worker; exchange stops remain, trailing/opposite management stops."))
            last_errors = None
            try:
                while True:
                    try:
                        engine.tick(entries=not args.manage_only)
                        store.set_meta("worker_error", None)
                        errors = store.meta("errors", [])
                        if errors != last_errors:
                            if errors:
                                print_json(dict(management_errors=errors, new_entries_paused=True))
                            last_errors = errors
                    except (APIError, TransportError, ValueError, KeyError) as exc:
                        store.set_meta("worker_error", str(exc))
                        print_json(dict(error=str(exc), new_entries_sent=False))
                    time.sleep(2)
            except KeyboardInterrupt:
                print_json(dict(worker_stopped=True, exchange_orders_not_canceled=True))


if __name__ == "__main__":
    try:
        main()
    except (APIError, TransportError, ValueError, FileNotFoundError, BlockingIOError) as exc:
        print_json(dict(error=str(exc), completed=False))
        raise SystemExit(1)
