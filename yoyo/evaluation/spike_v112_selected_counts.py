"""Count the owner's selected symbols under the unchanged V11.2 box-any rule.

Source: owner 2026-09-19 selected 30 names, then explicitly excluded ARY.
Reuse receipt-bound 15m/1h trades; extend the published box runner to 4h<-1d,
as mapped in V11.2 Pine. Features remain the existing causal OHLCV features;
the adapter adds no features, thresholds, or strategy choices. Exits may read
future bars solely to distinguish completed trades from boundary censoring.
This inventory makes no profitability claim. Archive coverage is disclosed.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v11_box_study as box
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v112-selected-counts-20260919-v1")
SOURCE = Path("experiments/active/exp-spike-v112-execution-20260919-v1")
LEDGER = SOURCE / "statistics/run_v2/trades.csv.gz"
NAMES = "BTC ETH SOL PEPE WIF ZEC HYPE USELESS MUBARAK UNI PUMP ARB AR BOME ENA ONE PIEVERSE AAVE NEAR ZEN DASH PENDLE XTZ JUP AERO OP FIL APT API3".split()


def one(args):
    symbol, path, meta = args
    base = inc.guarded_5m(Path(path), study.START - pd.Timedelta(days=study.WARMUP_BARS))
    result = box.run_pair(symbol, base, meta, ("4h", 240, "1d", 1440))
    output = EXP / "results" / symbol
    output.mkdir(parents=True, exist_ok=True)
    for key in ("trades", "statuses", "controls"):
        table = result.get(key, pd.DataFrame())
        if not len(table.columns):
            schemas = {
                "trades": [*study.TRADE_KEEP, "status", "arm", "trade_key", "symbol", "timeframe"],
                "statuses": ["arm", "signal_i", "status", "symbol", "timeframe"],
                "controls": ["trade_key", "arm", "matched", "reason"],
            }
            table = pd.DataFrame(columns=schemas[key])
        table.to_csv(output / f"{key}.csv", index=False)
    chart, daily = v11.bars_for(base, 240), v11.bars_for(base, 1440)
    coverage = {"symbol": symbol, "loaded_first_5m": str(base.index.min()),
                "loaded_last_5m": str(base.index.max()),
                "chart_pre_start_bars": int((chart.index < study.START).sum()),
                "daily_pre_start_bars": int((daily.index < study.START).sum()),
                "chart_bars": len(chart), "daily_bars": len(daily),
                "source_sha256": study.digest(Path(path)), "summary": result["summary"]}
    # One unchanged timeframe proves the adapter reproduces the existing ledger.
    if symbol == "BTCUSDT":
        parity = box.run_pair(symbol, base, meta, ("1h", 60, "4h", 240))["trades"]
        parity = parity[parity.arm.eq("box_any")].sort_values("trade_key")
        old = pd.read_csv(LEDGER)
        old = old[old.arm.eq("baseline") & old.symbol.eq(symbol) & old.timeframe.eq("1h")].sort_values("trade_key")
        assert parity.trade_key.tolist() == old.trade_key.tolist()
        for col in ("entry_price", "initial_stop", "exit_price", "net_r"):
            np.testing.assert_allclose(parity[col], old[col], rtol=1e-10, atol=1e-10)
        coverage["btc_1h_full_trade_parity"] = len(old)
    (output / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    return coverage


def main():
    code = _local_transitive_python((Path(__file__), Path(box.__file__)))
    declared = (*code, EXP / "PROJECT_PLAN.md")
    if not _committed(declared):
        raise ValueError("Commit the adapter, plan and dependency closure before replay")
    manifest = json.loads((SOURCE / "delivery_manifest.json").read_text())
    assert study.digest(LEDGER) == manifest["files"][str(LEDGER)]["sha256"]
    files, meta = study.series_files(), study.symbol_meta()
    mapping = {name: ("1000PEPE" if name == "PEPE" else name) + "USDT" for name in NAMES}
    assert len(mapping) == 29 and set(mapping.values()) <= files.keys()
    identity = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "mapping": mapping, "excluded_by_owner": ["ARY"],
                "signal_close_start": str(study.START), "end_exclusive": str(inc.DATA_END),
                "declared": {str(p): study.digest(p) for p in declared},
                "ledger_sha256": study.digest(LEDGER), "metadata_sha256": study.digest(study.EXCHANGE_INFO)}
    (EXP / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    coverage = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(one, (s, str(files[s]), meta[s])) for s in mapping.values()]
        for future in as_completed(tasks):
            row = future.result()
            coverage.append(row)
            print(json.dumps({"completed": len(coverage), "total": 29, "symbol": row["symbol"]}), flush=True)
    old = pd.read_csv(LEDGER)
    old = old[old.arm.eq("baseline") & old.symbol.isin(mapping.values())].copy()
    new_parts = [pd.read_csv(EXP / "results" / s / "trades.csv") for s in mapping.values()]
    new = pd.concat(new_parts, ignore_index=True)
    new = new[new.arm.eq("box_any")].copy()
    all_trades = pd.concat([old, new], ignore_index=True)
    assert not all_trades.duplicated(["symbol", "timeframe", "trade_key"]).any()
    all_trades.to_csv(EXP / "selected_trades.csv", index=False)
    rows = []
    for name, symbol in mapping.items():
        row = {"币种": name, "归档合约": symbol}
        for tf in ("15m", "1h", "4h"):
            part = all_trades[all_trades.symbol.eq(symbol) & all_trades.timeframe.eq(tf)]
            row[tf + "_已平仓"] = int(part.status.eq("closed").sum())
            row[tf + "_边界未平仓"] = int(part.status.eq("censored_boundary").sum())
            row[tf + "_全部入场"] = len(part)
        rows.append(row)
    counts = pd.DataFrame(rows)
    counts.to_csv(EXP / "counts.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{k: v for k, v in row.items() if k != "summary"} for row in coverage]).to_csv(EXP / "coverage.csv", index=False)
    outputs = sorted(p for p in EXP.rglob("*") if p.is_file() and p.name != "completion.json")
    receipt = {"completed_symbols": len(coverage), "counts": counts.filter(regex="已平仓|边界未平仓|全部入场").sum().to_dict(),
               "btc_1h_parity": next(r["btc_1h_full_trade_parity"] for r in coverage if r["symbol"] == "BTCUSDT"),
               "files": {str(p): study.digest(p) for p in outputs}, "training_eligible": False, "production_eligible": False}
    (EXP / "completion.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(counts.to_string(index=False))
    print(json.dumps(receipt["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
