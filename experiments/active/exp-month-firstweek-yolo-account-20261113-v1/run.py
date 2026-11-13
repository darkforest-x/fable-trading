"""Run one committed native execution and its independent shared-cash pools."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BUILDER_FILES = ["config.json", "execution.py", "account.py", "run.py"]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def _require_committed_main() -> str:
    branch = _git("branch", "--show-current")
    if branch != "main":
        raise RuntimeError(f"Research replay is allowed on main only; current branch is {branch!r}")
    for name in BUILDER_FILES:
        path = f"experiments/active/{HERE.name}/{name}"
        subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                       check=True, capture_output=True, text=True)
    changed = _git("status", "--short", "--", *[
        f"experiments/active/{HERE.name}/{name}" for name in BUILDER_FILES
    ])
    if changed:
        raise RuntimeError(f"Commit the execution/account builder before replaying:\n{changed}")
    return _git("rev-parse", "HEAD")


def _resolve(path_text: str) -> Path:
    p = Path(path_text)
    return p if p.is_absolute() else ROOT / p


def _normalize(frame: pd.DataFrame, source_path: Path, default_pool: str | None) -> pd.DataFrame:
    data = frame.copy()
    aliases = {"id": "trade_id", "direction": "side"}
    for old, new in aliases.items():
        if new not in data and old in data:
            data[new] = data[old]
    if "confirm_close_ms" not in data:
        if "known_ms" in data:
            data["confirm_close_ms"] = data["known_ms"]
        elif "touch_ms" in data:
            # The first hourly touch is known only at its close; its next 5m
            # open is the supplied close timestamp.
            data["confirm_close_ms"] = pd.to_numeric(data["touch_ms"], errors="raise") + 60 * 60_000
    if "pool" not in data:
        if default_pool is None:
            raise ValueError(f"{source_path}: pool missing; supply --default-pool")
        data["pool"] = default_pool
    required = {"trade_id", "symbol", "side", "confirm_close_ms", "pool", "near_edge", "tape_path"}
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError(f"{source_path}: missing normalized signal fields {missing}")
    data["trade_id"] = data.trade_id.astype(str)
    data["symbol"] = data.symbol.astype(str).str.upper()
    data["pool"] = data.pool.astype(str)
    data["confirm_close_ms"] = pd.to_numeric(data.confirm_close_ms, errors="raise").astype("int64")
    return data


def _write_csv(frame: pd.DataFrame, path: Path, *, gzip: bool = False) -> None:
    tmp = path.with_name(path.name + ".tmp")
    frame.to_csv(tmp, index=False, compression="gzip" if gzip else None,
                 float_format="%.17g")
    os.replace(tmp, path)


def _write_json(data: dict[str, Any], path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")
    os.replace(tmp, path)


def run(args: argparse.Namespace) -> Path:
    commit = _require_committed_main()
    inputs: list[pd.DataFrame] = []
    for value in args.signals:
        path = _resolve(value)
        if not path.is_file():
            raise FileNotFoundError(path)
        inputs.append(_normalize(pd.read_csv(path, float_precision="round_trip"), path, args.default_pool))
    signals = pd.concat(inputs, ignore_index=True)
    if signals.trade_id.duplicated().any():
        raise ValueError("Input files contain duplicate trade_id values")
    available_pools = sorted(signals.pool.astype(str).unique())
    selected_pools = sorted(args.pools) if args.pools else available_pools
    missing_pools = sorted(set(selected_pools) - set(available_pools))
    if missing_pools:
        raise ValueError(f"Requested pools are absent from input: {missing_pools}")
    selected = signals[signals.pool.astype(str).isin(selected_pools)].reset_index(drop=True)
    if selected.empty:
        raise ValueError("No signal rows selected")

    output_name = args.output_name or "_".join(p.replace("/", "_") for p in selected_pools)
    out = HERE / "results" / output_name
    if out.exists():
        raise FileExistsError(f"Refusing to replay into existing result directory: {out}")
    out.mkdir(parents=True, exist_ok=False)

    from execution import execute
    from account import run as run_accounts
    from funding import lookup as funding_lookup

    native = execute(selected)
    if len(native["trades"]):
        accounts = run_accounts(native["trades"], native["bars_by_trade_id"], funding_lookup)
    else:
        accounts = {"trades": pd.DataFrame(), "curve": pd.DataFrame(),
                    "funding": pd.DataFrame(), "summary": pd.DataFrame()}

    skip = native["skips"]
    if len(skip):
        skipped_by_id = skip.set_index(skip.trade_id.astype(str))
    else:
        skipped_by_id = pd.DataFrame()
    native_ids = set(native["trades"].trade_id.astype(str)) if len(native["trades"]) else set()
    funnel_rows = []
    for row in selected.to_dict("records"):
        tid = str(row["trade_id"])
        funnel = dict(row)
        if tid in native_ids:
            funnel.update(execution_status="native_filled", skip_reason="")
        elif len(skip) and tid in skipped_by_id.index:
            detail = skipped_by_id.loc[tid]
            funnel.update(execution_status="pretrade_skipped",
                          skip_reason=str(detail["skip_reason"]))
        else:
            raise AssertionError(f"{tid}: neither native fill nor preserved pretrade skip")
        funnel_rows.append(funnel)

    _write_csv(pd.DataFrame(funnel_rows), out / "signal_funnel.csv")
    _write_csv(native["trades"], out / "native_trades.csv")
    _write_csv(native["native_fills"], out / "framework_fills.csv")
    _write_csv(skip, out / "pretrade_skips.csv")
    _write_csv(accounts["trades"], out / "account_trades.csv")
    _write_csv(accounts["curve"], out / "account_curve_5m.csv.gz", gzip=True)
    _write_csv(accounts["funding"], out / "funding_cashflows.csv")
    _write_csv(accounts["summary"], out / "account_summary.csv")

    receipt = {
        "experiment_id": "exp-month-firstweek-yolo-account-20261113-v1",
        "git_commit": commit,
        "git_branch": "main",
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "input_paths": [str(_resolve(p)) for p in args.signals],
        "selected_pools": selected_pools,
        "input_signal_rows": int(len(selected)),
        "execution": native["audit"],
        "account_summary": accounts["summary"].to_dict(orient="records"),
        "outputs": sorted(p.name for p in out.iterdir()),
        "result_scope": "simulation research only",
    }
    _write_json(receipt, out / "execution_receipt.json")
    print(accounts["summary"].to_string(index=False), flush=True)
    print(f"native positions={len(native['trades'])}, pretrade skips={len(skip)}, output={out}", flush=True)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signals", nargs="+", default=["primary_signals.csv"],
                   help="one or more input CSVs with the frozen execution fields")
    p.add_argument("--pools", nargs="*", default=None,
                   help="selected independent account names; defaults to every pool in the inputs")
    p.add_argument("--default-pool", default=None,
                   help="pool name only when an input CSV has no pool column")
    p.add_argument("--output-name", default=None,
                   help="unique result directory name; an existing directory is never overwritten")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
