"""CLI: python3 -m src.execution [--dry-run] [--once] [--write-examples]

DEMO-only. Requires data/okx_demo_keys.json created by the owner for live
paper orders. Secrets are never printed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.execution.config import DEFAULT_CONFIG_PATH, ExecutorConfig
from src.execution.executor import run_loop, run_once
from src.execution.okx_client import KEYS_PATH, OkxDemoClient, OkxDemoError


def write_examples() -> None:
    cfg = ExecutorConfig()
    p = cfg.save_example()
    keys_ex = Path("data/okx_demo_keys.example.json")
    keys_ex.parent.mkdir(parents=True, exist_ok=True)
    keys_ex.write_text(
        '{\n  "api_key": "REPLACE_ME",\n  "secret_key": "REPLACE_ME",\n'
        '  "passphrase": "REPLACE_ME"\n}\n',
        encoding="utf-8",
    )
    print(f"wrote {p}")
    print(f"wrote {keys_ex}")
    print(f"real keys path (gitignored): {KEYS_PATH}")
    print("kill switch: touch data/executor_KILL   # pause new entries")
    print("             rm data/executor_KILL      # resume")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OKX DEMO executor (paper only)")
    p.add_argument("--config", type=Path, default=None, help="executor_config.json")
    p.add_argument("--dry-run", action="store_true", help="no orders; log intent only")
    p.add_argument("--once", action="store_true", help="single poll then exit")
    p.add_argument("--write-examples", action="store_true", help="write example config/keys templates")
    p.add_argument("--ping", action="store_true", help="check demo balance (needs keys)")
    args = p.parse_args(argv)

    if args.write_examples:
        write_examples()
        return 0

    cfg = ExecutorConfig.load(args.config)

    if args.ping:
        try:
            client = OkxDemoClient()
            bal = client.balance()
            # never print key material; only high-level totals if present
            data = bal.get("data") or []
            print({"ok": True, "environment": client.environment, "accounts": len(data)})
            if data:
                details = data[0].get("details") or []
                usdt = [d for d in details if d.get("ccy") == "USDT"]
                if usdt:
                    print({"USDT_eq": usdt[0].get("eq"), "USDT_avail": usdt[0].get("availEq")})
            return 0
        except OkxDemoError as exc:
            print(f"ping failed: {exc}", file=sys.stderr)
            return 2

    if args.once or args.dry_run:
        summary = run_once(cfg, dry_run=args.dry_run)
        from src.execution.executor import json_dumps

        print(json_dumps(summary))
        return 0

    run_loop(cfg, dry_run=False, once=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
