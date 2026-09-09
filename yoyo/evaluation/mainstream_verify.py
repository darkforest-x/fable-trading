"""Independent post-run accounting verification, never used to select signals.

Replays outcome boundaries directly from cached OHLC/MD arrays without calling
the outcome engine, and reconciles per-sleeve money to complete equity curves.
First-run comparison establishes that the reporting audit did not change
signal selection, prices, exits or portfolio equity. No fitting or mutation of
source data; all artifacts are verified against the manifest before reading.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.mainstream_research import verify_cached_books, dump, digest, SYMBOLS, FOLDS


def verify(out):
    manifest = verify_cached_books(out)
    events = pd.read_csv(out/"events.csv.gz")
    first_run = pd.read_csv(out.parent/"events.csv.gz")
    unchanged = [c for c in first_run.columns if c in events.columns]
    pd.testing.assert_frame_equal(first_run.set_index("event_id")[unchanged[1:]].sort_index(),
        events.set_index("event_id")[unchanged[1:]].sort_index(), check_dtype=False, check_exact=False,
        atol=1e-12, rtol=1e-12)
    replays, reconciled = 0, 0
    for symbol in SYMBOLS:
        folder = out/symbol
        curves = pd.read_pickle(folder/"curves.pkl.gz", compression="gzip")
        old_curves = pd.read_pickle(out.parent/symbol/"curves.pkl.gz", compression="gzip")
        for key in curves:
            pd.testing.assert_series_equal(curves[key], old_curves[key], check_exact=True)
        for minutes in (60, 240):
            bars = pd.read_pickle(folder/f"bars_{minutes}.pkl.gz", compression="gzip")
            f = pd.read_pickle(folder/f"features_{minutes}.pkl.gz", compression="gzip")
            known = f.higher_source_close.notna()
            assert (f.loc[known, "higher_source_close"] <= (f.index+pd.Timedelta(minutes=minutes))[known]).all()
            o, h, low, close = [bars[col].to_numpy() for col in ("open", "high", "low", "close")]
            md = f.md.to_numpy()
            for fold, _, end in FOLDS:
                last = int(np.flatnonzero(bars.index+pd.Timedelta(minutes=minutes) <= pd.Timestamp(end, tz="UTC"))[-1])
                q = events.loc[events.symbol.eq(symbol) & events.minutes.eq(minutes) & events.fold.eq(fold)]
                for row in q.loc[q.valid.eq(True)].itertuples():
                    entry_i, signal_i = int(row.entry_i), int(row.signal_i)
                    assert entry_i == signal_i+1
                    assert abs(row.entry_price-o[entry_i]) < 1e-10
                    risk = 2*f.atr.iloc[signal_i]
                    stop = o[entry_i]-risk
                    pending = False
                    answer = None
                    for j in range(entry_i, last+1):
                        if o[j] <= stop:
                            answer = (j, o[j]); break
                        if pending:
                            answer = (j, o[j]); break
                        if low[j] <= stop:
                            answer = (j, stop); break
                        if row.exit_rule == "fixed3r" and h[j] >= o[entry_i]+3*risk:
                            answer = (j, o[entry_i]+3*risk); break
                        if j == last:
                            answer = (j, close[j]); break
                        if row.exit_rule == "md" and md[j] <= 0:
                            pending = True
                    assert answer[0] == int(row.exit_i), (row.event_id, answer, row.exit_i)
                    assert abs(answer[1]-row.exit_price) < 1e-8*row.entry_price
                    assert abs(answer[1]/o[entry_i]-1-.002-row.net_return) < 1e-10
                    replays += 1
                for arm, selected in q.loc[q.portfolio_selected.eq(True)].groupby("arm"):
                    selected = selected.sort_values(["entry_i", "event_id"])
                    equity = 1.
                    for row in selected.itertuples():
                        assert abs(row.portfolio_entry_equity-equity) < 1e-10
                        equity *= 1+row.net_return
                        assert abs(row.portfolio_exit_equity-equity) < 1e-10
                    curve = curves[f"{fold}|{minutes}|{arm}"]
                    assert abs(equity-curve.iloc[-1]) < 1e-10
                    assert abs(selected.portfolio_net_pnl.sum()-(equity-1)) < 1e-10
                    reconciled += 1
    result = dict(status="passed", generated_at=pd.Timestamp.now(tz="UTC"),
        independently_replayed_candidates=replays, reconciled_nonempty_sleeves=reconciled,
        original_candidate_outcomes_unchanged=True, all_original_sleeve_curves_byte_values_unchanged=True,
        source_and_cache_hashes_verified=True, higher_context_close_clock_verified=True,
        verifier_sha256=digest(__file__), generator_commit=manifest["code_commit"],
        scope="Verification of the same fixed historical run; no new strategy selection or future feature input")
    dump(result, out/"verification.json")
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("analysis/output/mainstream_super_trend_20260910/verified"))
    verify(parser.parse_args().out)
