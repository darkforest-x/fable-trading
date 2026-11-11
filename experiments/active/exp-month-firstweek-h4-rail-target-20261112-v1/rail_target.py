"""Change only the 168h H4 take-profit anchor. Reuse the frozen V1 entries.

Use the monthly rail known before entry for both signal and matched random.
A target reached or passed at the opening price cannot be posted as a forward
profit order. Keep these candidates in skips, and compare versions on common
entries as well as on the full frozen signal opportunity set (skips earn zero).
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import vectorbt as vbt
from vectorbt.signals.enums import StopType
from vectorbt.signals.nb import generate_ohlc_stop_ex_nb

HERE = Path(__file__).resolve().parent
V1 = HERE.parent / "exp-month-firstweek-h4-strategy-20261112-v1"
sys.path[:0] = [str(V1), str(HERE.parent / "exp-month-firstweek-all-replay-20261111-v1")]
from compare_fills import DEST as BASE, MAIN, summarize
from replay import CUTOFF, HOUR, read_raw

OUT = BASE.parent / "h4_rail_target"
HOLD = 168
TAKER, MAKER = 0.0005, 0.0002


def exit_for_window(bars, side, entry, target):
    """VectorBT determines the first fixed barrier. Opens precede H1 ranges."""
    # Two observations per H1 distinguish a gap through one order from an
    # intrabar crossing of the other. In the range step VectorBT gives SL priority.
    o, h, l, c = (np.repeat(bars[:, col], 2)[:, None] for col in range(1, 5))
    h[::2] = l[::2] = c[::2] = o[::2]
    entries = np.zeros(o.shape, dtype=bool)
    entries[0] = True
    hit_px = np.full_like(o, np.nan)
    hit_kind = np.full(o.shape, -1, dtype=np.int64)
    # Extending by two floating ULPs preserves equality at decimal thresholds
    # reconstructed by the library as entry * (1 +/- distance).
    hi = np.nextafter(np.nextafter(h, np.inf), np.inf)
    lo = np.nextafter(np.nextafter(l, -np.inf), -np.inf)
    hits = generate_ohlc_stop_ex_nb(entries, o, hi, lo, c, hit_px, hit_kind,
                                    0.05, False, abs(target / entry - 1),
                                    side == -1, True, 0, True, False, True, True)
    steps = np.flatnonzero(hits[:, 0])
    if len(steps) == 0:
        reason = "timeout" if len(bars) == HOLD else "cutoff_mark"
        return len(bars) - 1, 3, reason, bars[-1, 4]
    step = int(steps[0])
    i, intrabar = divmod(step, 2)
    if hit_kind[step, 0] == StopType.TakeProfit:
        return i, 2 if intrabar else 0, "target", target
    assert hit_kind[step, 0] == StopType.StopLoss
    return i, 1 if intrabar else 0, "stop", entry * (1 - side * 0.05) if intrabar else bars[i, 1]


def excursions(bars, index, phase, side, entry, fill):
    """Observed native-style O/adverse/favorable/C prefix ending at the fill."""
    prices = [entry, fill]
    if index:
        prices.extend([bars[:index, 2].max(), bars[:index, 3].min()])
    order = (1, 3, 2) if side == 1 else (1, 2, 3)
    prices.extend(bars[index, list(order[:phase])])
    signed = side * (np.asarray(prices) / entry - 1) * 100
    return max(0., signed.max()), max(0., -signed.min())


def portfolio_ledger(frame):
    """VectorBT accounts for market entry, resting target and market exit fees."""
    prices = frame[["entry_px", "exit_px"]].to_numpy().T
    qty = frame.qty.to_numpy()
    signed = np.where(frame.side.eq("long"), qty, -qty)
    rates = np.vstack([np.full(len(frame), TAKER), np.where(frame.exit_reason.eq("target"), MAKER, TAKER)])
    pf = vbt.Portfolio.from_orders(close=prices, size=np.vstack([signed, -signed]),
                                   price=prices, size_type="amount", direction="both",
                                   fees=rates, init_cash=prices.max(axis=0) * qty * 4 + 1,
                                   allow_partial=False, cash_sharing=False, raise_reject=True,
                                   max_orders=2 * len(frame), freq="1h")
    closed = pf.trades.records.sort_values("col")
    assert len(closed) == len(frame) and closed.status.eq(1).all()
    np.testing.assert_allclose(closed.entry_price, prices[0], rtol=1e-12)
    np.testing.assert_allclose(closed.exit_price, prices[1], rtol=1e-12)
    frame["entry_fee_u"] = closed.entry_fees.to_numpy()
    frame["exit_fee_u"] = closed.exit_fees.to_numpy()
    frame["fee_u"] = frame.entry_fee_u + frame.exit_fee_u
    frame["net_pct"] = 100 * closed.pnl.to_numpy() / (prices[0] * qty)
    return frame


def main():
    source = pd.read_csv(BASE / "fills.csv", float_precision="round_trip")
    rails = pd.read_csv(BASE.parent / "group_first_watch.csv.gz", usecols=["id","symbol","rail"],
                        float_precision="round_trip").rename(columns={"id":"pair_id"})
    inputs = source.merge(rails, on=["pair_id","symbol"], how="left", validate="many_to_one")
    assert inputs.rail.gt(0).all() and inputs.trade_id.is_unique
    signs = inputs.side.map({"long":1, "short":-1})
    inputs["target_px"] = inputs.rail * (1 + signs * 0.2)
    room = signs * (inputs.target_px - inputs.entry_px)
    skipped = inputs[room <= 0].copy()
    skipped["skip_reason"] = "target_at_or_behind_fill"
    rows = []
    for symbol, part in inputs[room > 0].groupby("symbol"):
        bars = read_raw(symbol)
        bars = bars[bars[:, 0] + HOUR <= CUTOFF]
        for r in part.itertuples(index=False):
            start = bars[:, 0].searchsorted(r.entry_ms)
            window = bars[start:start+HOLD]
            assert len(window) and window[0, 0] == r.entry_ms and np.isclose(window[0, 1], r.entry_px, rtol=1e-12, atol=0)
            gaps = np.flatnonzero(np.diff(window[:, 0]) != HOUR)
            if len(gaps):
                window = window[:gaps[0]+1]
            side = 1 if r.side == "long" else -1
            i, phase, reason, fill = exit_for_window(window, side, r.entry_px, r.target_px)
            mfe, mae = excursions(window, i, phase, side, r.entry_px, fill)
            stop = r.entry_px * (1 - side * 0.05)
            double = window[i, 2] >= max(stop, r.target_px) and window[i, 3] <= min(stop, r.target_px)
            rows.append(dict(r._asdict(), exit_ms=int(window[i, 0] + (HOUR if phase else 0)),
                             exit_px=fill, exit_hour=i+1, exit_phase=phase, exit_reason=reason,
                             mfe_pct=mfe, mae_pct=mae, both_barriers=int(double)))
    frame = portfolio_ledger(pd.DataFrame(rows).sort_values(["entry_ms","symbol","trade_id"]))
    assert len(frame) + len(skipped) == len(source)
    OUT.mkdir(exist_ok=True, parents=True)
    skipped.to_csv(OUT / "skips.csv", index=False)
    tmp = OUT / "fills.tmp"
    frame.to_csv(tmp, index=False, float_format="%.17g")
    tmp.replace(OUT / "fills.csv")
    summary = summarize(OUT / "fills.csv", HOLD)
    summary.to_csv(HERE / "trade_summary.csv", index=False)
    actual = source[source['sample'].eq('signal') & source.group.isin(MAIN) & source.exit_reason.ne('cutoff_mark')]
    both = actual.merge(frame[['trade_id','exit_reason','net_pct']],on='trade_id',how='left',
                        suffixes=('_v1','_v2'),validate='one_to_one')
    both['exit_reason_v2'] = both.exit_reason_v2.fillna('skipped')
    both['net_pct_v2'] = both.net_pct_v2.fillna(0.)
    both['change_pp'] = both.net_pct_v2 - both.net_pct_v1
    attribution = both.groupby(['exit_reason_v1','exit_reason_v2']).agg(
        count=('trade_id','size'),v1_mean_net_pct=('net_pct_v1','mean'),
        v2_mean_net_pct=('net_pct_v2','mean'),total_change_pp=('change_pp','sum'))
    attribution.to_csv(HERE / "exit_changes.csv")
    print(summary[summary.组=='主策略'].round(3).to_string(index=False),flush=True)
    print(attribution.round(3).to_string(),flush=True)


if __name__ == "__main__":
    main()
