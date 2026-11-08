"""Native October midpoint legs for frozen E20 entries with entry-close beyond_pct < 1.5."""
from concurrent.futures import ProcessPoolExecutor
import json
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_close_stop_grid import process
from yoyo.evaluation.monday_limit_live_october import load_fills
from yoyo.evaluation.range_framework import ROOT


OUT = ROOT / "experiments/active/exp-live-wick-shallow-midpoint-20261109-v1"


def run_symbol(task):
    symbol, facts, cfg, job = task
    ids = set(facts.event)
    expected = facts.set_index("event")
    for path in ["OHLC", "OLHC"]:
        directory = OUT / path.lower() / "symbols" / symbol
        if not (directory / "done.json").exists():
            process(symbol, path, OUT, cfg, ids)
        audit = json.loads((directory / "done.json").read_text())
        native = pd.read_csv(directory / "trades.csv.gz")
        native.index = native.trade_id.str.split(":").str[:3].str.join(":")
        if (set(native.index)!=ids or len(native)!=len(ids) or audit["positions"]!=len(ids)
                or audit["version"]!="1.231.0" or not native.trade_id.str.contains(":range_0.5:").all()):
            raise ValueError(f"{symbol}: inconsistent midpoint native legs")
        e = expected.loc[native.index]
        midpoint = e.target + e.side*(e.target-e.level).abs()*.5/.618
        if ((native.target_price-midpoint).abs()>e.tick*.51).any() or ((native.entry-e.close).abs()>e.tick*.51).any():
            raise ValueError(f"{symbol}: original geometry changed")
    legs = load_fills((str(OUT), symbol, job))
    entry = pd.to_datetime(legs.event.map(expected.t), utc=True) + pd.Timedelta(minutes=5)
    if not np.array_equal(legs.entry_t.to_numpy(),entry.to_numpy()):
        raise ValueError(f"{symbol}: entry clock changed")
    return legs


if __name__ == "__main__":
    design = OUT / "october_midpoint_design.json"
    committed("yoyo/evaluation/live_wick_october_midpoint_fills.py", str(design.relative_to(ROOT)))
    cfg = json.loads(design.read_text())
    survivors = pd.read_csv(ROOT / cfg["source"] / "survivors.csv.gz")
    shallow = survivors[survivors.beyond_pct < cfg["cut"]]
    assert len(survivors)==17 and len(shallow)==10 and not survivors.event.duplicated().any()
    jobs = json.loads((ROOT/cfg["jobs"]).read_text())
    with ProcessPoolExecutor(3) as pool:
        legs = pd.concat(pool.map(run_symbol, [(s,g,cfg,jobs[s]) for s,g in shallow.groupby("symbol")]))
    legs.sort_values("entry_t").to_csv(OUT / "october_midpoint_legs.csv.gz", index=False)
