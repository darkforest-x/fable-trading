"""Independent input and whole-baseline audit before PEPE grid selection.

Compare archived/API OHLC with previously registered native timeframe candles
and the recorded TradingView 146-trade signatures. No additional optimization.
"""
import json
import re
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.pepe_range_data import EXP, validate
from yoyo.evaluation.pepe_range_exits import load_context, aggregate, stop_events, replay, prepare
from yoyo.evaluation.monday_range_entry_diagnostic import load


def main():
    if (EXP/"inputs"/"okx_pepe_5m.csv.gz").exists():
        ctx = load_context()
    else:
        # Current-year source is complete while older native repairs may run.
        paths = sorted((EXP/"inputs").glob("okx_PEPE_USDT_SWAP_5m_2026-*.csv"))
        recent = validate(pd.concat([pd.read_csv(p) for p in paths], ignore_index=True))
        assert (recent.ts.diff().dropna() == 300000).all()
        recent.index = pd.to_datetime(recent.ts, unit="ms", utc=True)+pd.Timedelta(minutes=5)
        ctx = prepare(recent)
    frame = ctx["frame"]
    checks = []
    for tf, width in [("5m", 5), ("15m", 15), ("1H", 60)]:
        prior = load(tf, width)
        current = frame if width == 5 else aggregate(frame, width)
        common = prior.index.intersection(current.index)
        a = current.loc[common, ["open", "high", "low", "close"]].to_numpy()
        b = prior.loc[common, ["open", "high", "low", "close"]].to_numpy()
        diff = np.abs(a-b)
        assert (diff < 1e-15).all(), (tf, float(diff.max()))
        checks.append(dict(tf=tf, rows=len(common), max_price_difference=float(diff.max())))
    log = open("output/pine/monday_range_v2_7_native_15m.txt").read()
    line = re.search(r"V27_NATIVE_CHECKS_PASS count=146[^\n]+", log).group()
    through = int(re.search(r"through=([\d,]+)", line).group(1).replace(",", ""))
    last = int(frame.index.searchsorted(pd.Timestamp(through, unit="ms", tz="UTC")))
    first = last-29999
    # A fresh fixed-5m Pine request waits for its first confirmed H1 arrow.
    first_hour = first + int(np.flatnonzero(ctx["hour"][first:last+1])[0])
    idx = np.flatnonzero(ctx["signal"] & (np.arange(len(frame)) > first_hour) &
                         (np.arange(len(frame)) <= last))
    source = pd.DataFrame(dict(entry_i=idx, side=ctx["signal"][idx], end_i=last))
    base, matrix = stop_events(ctx, source, 5, .05, 1e-9)
    result = replay(ctx, matrix)
    stamps = frame.index.asi8//1_000_000
    entered = stamps[base.entry_i.to_numpy(int)]
    observed = stamps[ctx["observed"][base.entry_i.to_numpy(int)]]
    exits = result[:, 0].astype(int)
    ended = stamps[exits].copy()
    gap = np.isin(result[:, 2], [1, 2]) & (base.side.to_numpy()*(ctx["bars"][exits, 0]-result[:, 5]) < 0)
    ended[gap] -= 300000
    ended[result[:, 2] == 0] = 0
    weights = np.arange(1, len(base)+1)
    got = [
        float(np.sum(weights*(entered+observed)/60000)),
        float(np.sum(weights*(base.entry+3*base.stop)/1e-9)),
        float(np.sum(weights*(ended/60000+result[:, 1]/1e-9+result[:, 2]))),
        float(np.sum(weights*base.side)),
    ]
    expected = [float(re.search(key+r"=([\d,.-]+)", line).group(1).replace(",", ""))
                for key in ("timeSig", "priceSig", "exitSig", "sideSig")]
    assert len(base) == 146
    np.testing.assert_allclose(got, expected, rtol=0, atol=.01)
    # Retain the full recovered ledger; signatures are supplementary evidence.
    ledger = base.copy()
    ledger["entered_ms"], ledger["observed_ms"], ledger["ended_ms"] = entered, observed, ended
    ledger["exit_price"], ledger["reason"], ledger["peak_r"] = result[:, 1], result[:, 2], result[:, 3]
    ledger.to_csv(EXP/"native_146_replay.csv", index=False)
    receipt = dict(input_overlap=checks, native_baseline_count=len(base),
                   recovered_signature=got, native_signature=expected,
                   source_start_utc=str(frame.index[first]-pd.Timedelta(minutes=5)),
                   through_utc=str(frame.index[last]),
                   input_audit_start_utc=str(frame.index[0]),
                   builder_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    (EXP/"parity_receipt.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
