"""Re-run the ORIGINAL V10.4 code (commit ccc52dff29) for 1h only, for a trade-by-trade reproduction.

Run with PYTHONPATH pointing at a read-only `git archive ccc52dff29` export whose
`data` is a symlink to this repository's `data`; the script refuses to run if
`yoyo` resolves anywhere else. It mirrors the original `run_symbol` loading path
(same earliest 5m load, same 1h floor) and calls the original `run_stream`
unchanged, then writes that stream's trades, joints and controls.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path

import pandas as pd


def one(args):
    symbol, export, out = args
    import yoyo.evaluation.spike_v10_4_study as study
    from yoyo.evaluation.spike_v10_4 import V104Params
    assert Path(study.__file__).resolve().is_relative_to(Path(export).resolve()), study.__file__
    path = study.series_files()[symbol]
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = study.load_5m(path, earliest)
    since = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * 60)
    bars, _ = study.aggregate(base.loc[base.index >= since.floor("60min")], 60)
    folder = Path(out) / symbol
    folder.mkdir(parents=True, exist_ok=True)
    if len(bars) == 0 or not study.in_window(bars.index, 60).any():
        (folder / "skipped.txt").write_text("no_bar_in_window\n")
        return symbol, None
    result = study.run_stream(symbol, "1h", bars, study.symbol_meta()[symbol], V104Params())
    for name in ("trades", "controls", "joints"):
        if len(result[name]):
            result[name].to_csv(folder / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    (folder / "summary.json").write_text(json.dumps(result["summary"], default=str) + "\n")
    return symbol, result["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    import yoyo.evaluation.spike_v10_4_study as study
    assert Path(study.__file__).resolve().is_relative_to(args.export.resolve()), study.__file__
    symbols = sorted(study.series_files())
    args.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(one, (s, str(args.export), str(args.out))) for s in symbols]
        done = [f.result() for f in as_completed(futures)]
    (args.out / "done.json").write_text(json.dumps({"symbols": len(done), "yoyo": study.__file__}, indent=2) + "\n")
    print(json.dumps({"symbols": len(done)}))
