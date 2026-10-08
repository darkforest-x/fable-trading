"""Market-wide sync shock v4: does the environment before a shock decide whether it continues?

Owner 2026-10-08: "继续做环境过滤吧". v3 found that the shock's own strength (joint z,
joint volume ratio, alt breadth) does not order 1H outcomes. v4 asks the same question of
the market around the shock instead: BTC's prior trend and its volatility level.

Single variable: conditioning on the pre-shock environment. Events, outcomes and paired
controls are v2's own rows for its loosest config (z>=2, volume x2, breadth >=60%, single
sync bar, 24h cooldown) on 30m and 1H, the same set v3 used. Each event gets a tercile of
one environment feature at a time:

  trend30  BTC close / close 720h earlier - 1   (primary trend)
  trend7   BTC close / close 168h earlier - 1   (secondary trend)
  vol30    std of BTC 1H log returns over the previous 720h (volatility level)

Environment clock: BTC 1H bars from complete 5m buckets; for an event bar opening at t the
last close used is the 1H bar closing at or before t, so the shock bar never enters its own
environment (a 30m bar at HH:30 sees the HH:00 close). Tercile edges come from events before
``select_before`` and are applied unchanged later; strata and contrasts reuse v3's code.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import market_sync_shock_v3 as v3

EXP = Path("experiments/active/exp-market-sync-shock-20261008-v4")
CONFIG = EXP / "config.json"
FEATURES = ("trend30", "trend7", "vol30")
HOUR_MS = 3_600_000


def btc_hourly(raw_btc: pd.DataFrame) -> pd.DataFrame:
    """Complete 1H bars indexed by close time (ms) with environment features at that close."""
    bars = v1.resample(raw_btc, 60)
    close = bars.close.copy()
    close.index = bars.index + HOUR_MS
    full = close.reindex(np.arange(close.index.min(), close.index.max() + HOUR_MS, HOUR_MS))  # gaps stay NaN
    logret = np.log(full).diff()
    return pd.DataFrame({
        "close": full,
        "trend30": full / full.shift(720) - 1.0,
        "trend7": full / full.shift(168) - 1.0,
        "vol30": logret.rolling(720, min_periods=700).std(),
    })


def attach_environment(events: pd.DataFrame, env: pd.DataFrame) -> pd.DataFrame:
    """Environment at the last 1H close at or before each event bar's open."""
    opens = pd.to_datetime(events.time).astype("int64") // 1_000_000
    asof = (opens // HOUR_MS) * HOUR_MS  # a close lands on the hour; HH:30 bars see HH:00
    if (asof > opens).any():
        raise ValueError("environment would read past the event bar open")
    got = env.reindex(asof.to_numpy())
    return events.assign(env_close_ms=asof.to_numpy(), **{f: got[f].to_numpy() for f in FEATURES})


def run(output: Path) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_market_sync_shock_v4.py"),
               Path(v3.__file__), Path(v1.__file__))
    if not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    src = Path(cfg["source_results"])
    v2_cfg = json.loads((src.parent / "config.json").read_text())
    began = time.perf_counter()
    sel = cfg["event_set"]
    events = pd.read_csv(src / "events.csv")
    events = events.loc[events.config.eq(sel["config"]) & events.minutes.isin(sel["timeframes"])]
    trades = pd.read_csv(src / "trades.csv.gz")
    trades = trades.loc[trades.config.eq(sel["config"]) & trades.minutes.isin(sel["timeframes"])
                        & trades.exit.isin(cfg["exits"]) & trades.instrument.isin(cfg["instruments"])]
    raw_btc = v1.read_5m("BTCUSDT", pd.Timestamp(v2_cfg["warmup_start"]), pd.Timestamp(v2_cfg["end"]))
    events = attach_environment(events, btc_hourly(raw_btc))
    missing = int(events[list(FEATURES)].isna().any(axis=1).sum())
    events, strata, contrasts, edges = v3.analyse(events, trades, cfg, features=FEATURES)
    output.mkdir(parents=True, exist_ok=True)
    events.to_csv(output / "events_v4.csv", index=False)
    strata.to_csv(output / "strata.csv", index=False)
    contrasts.to_csv(output / "contrasts.csv", index=False)
    edges.to_csv(output / "edges.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head,
        "v2_receipt": json.loads((src / "receipt.json").read_text()),
        "v2_events_sha256": v3.sha256(src / "events.csv"), "v2_trades_sha256": v3.sha256(src / "trades.csv.gz"),
        "events": len(events), "events_missing_environment": missing, "strata_rows": len(strata),
        "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    for name in ("primary", "secondary"):
        pc = cfg[name]
        view = contrasts.loc[(contrasts.minutes == pc["minutes"]) & (contrasts.side == pc["side"])
                             & (contrasts.exit == pc["exit"]) & (contrasts.instrument == pc["instrument"])]
        print(name); print(view.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v4")
    run(parser.parse_args().output)


if __name__ == "__main__":
    main()
