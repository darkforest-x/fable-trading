"""Compare a two-bar price action pattern at registered NY Monday 1.618 levels.

Reads the frozen 5m OHLC and its audited weekly offsets. For each side, the
feature uses the Monday high/low, touch-bar high/low/close and only the next
5m close. Only the direction label reads a subsequent close, exactly 4h later.
Run from the repo root: python -m yoyo.evaluation.monday_pa_followthrough
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("experiments/active/exp-monday-pa-followthrough-20261108-v1")
BAR = pd.Timedelta(minutes=5)


def observations(symbol, cfg):
    """Read OHLC at first weekly touch and the adjacent close, then attach its label."""
    source = cfg["source_experiment"]
    weeks = json.loads((Path("experiments/active") / source / symbol / "weeks.json").read_text())
    candles = pd.read_csv(
        Path("data/crypto/market") / source / symbol / "candles.csv.gz",
        usecols=["open_time", "high", "low", "close"], parse_dates=["open_time"],
    ).set_index("open_time")
    if not candles.index.to_series().diff().iloc[1:].eq(BAR).all():
        raise ValueError(f"Noncontinuous archive for {symbol}")
    high, low, close = (candles[c].to_numpy() for c in ["high", "low", "close"])
    split = pd.Timestamp(cfg["split_utc"])
    rows = []
    for w in weeks:
        if not cfg["first_ny_week"] <= w["week"] <= cfg["last_ny_week"]:
            continue
        start, end = w["monitor_start_i"], w["monitor_end_i_exclusive"]
        if not (w["valid"] and w["monday_grid_complete"] and
                candles.index[start] == pd.Timestamp(w["monday_end_utc"]) and
                candles.index[end-1] + BAR == pd.Timestamp(w["next_monday_utc"])):
            raise ValueError(f"Incomplete registered week {symbol} {w['week']}")
        width = w["monday_high"] - w["monday_low"]
        for side, name in [(1, "lower"), (-1, "upper")]:
            level = w["monday_low"] - .618 * width if side == 1 else w["monday_high"] + .618 * width
            crossed = low[start:end] <= level if side == 1 else high[start:end] >= level
            hits = np.flatnonzero(crossed)
            row = dict(week=w["week"], symbol=symbol, side=name, level=level, state="no_touch")
            rows.append(row)
            if not len(hits):
                continue
            t = start + int(hits[0])
            a, b = t + 1, t + 1 + cfg["horizon_5m_bars"]
            row.update(touch_open=candles.index[t], touch_high=high[t], touch_low=low[t], touch_close=close[t])
            if a >= end:
                row["state"] = "confirmation_censored"
                continue
            time = candles.index[a] + BAR
            row.update(decision=time, confirm_close=close[a], period="2026" if time >= split else "2024-25")
            if side * (close[t] - level) <= 0 or side * (close[a] - level) <= 0:
                row["state"] = "not_two_inside"
                continue
            if b >= end or (time < split <= candles.index[b] + BAR):
                row["state"] = "label_censored"
                continue
            reference = high[t] if side == 1 else low[t]
            row.update(state="eligible", breakout=bool(side * (close[a] - reference) > 0),
                       label_at=candles.index[b] + BAR, label_close=close[b],
                       inward=bool(side * (close[b] - close[a]) > 0))
    return rows


def compare(rows, weeks, cfg):
    """Resample entire NY weeks so coincident symbols and sides stay together."""
    counts = np.zeros((len(weeks), 2, 2), dtype=int)
    for row in rows.itertuples():
        group = int(row.breakout)
        idx = weeks.get_loc(row.week)
        counts[idx, group] += [int(row.inward), 1]
    totals = counts.sum(axis=0)
    rng = np.random.default_rng(cfg["seed"])
    draws = rng.integers(0, len(weeks), size=(cfg["bootstrap_draws"], len(weeks)))
    samples = counts[draws].sum(axis=1)
    both = (samples[:, :, 1] > 0).all(axis=1)
    rates = samples[both, :, 0] / samples[both, :, 1]
    answer = {k: dict(inward=int(h), n=int(n), rate=float(h/n) if n else None)
              for k, (h, n) in zip(["held_only", "breakout"], totals)}
    answer["difference_pp"] = (float(100 * (totals[1, 0]/totals[1, 1] - totals[0, 0]/totals[0, 1]))
                               if totals[:, 1].all() else None)
    # A single contributing week cannot identify a between-week sampling distribution.
    group_weeks = (counts[:, :, 1] > 0).sum(axis=0)
    answer["contributing_weeks"] = dict(zip(["held_only", "breakout"], map(int, group_weeks)))
    answer["week_cluster_95_pp"] = (np.percentile(100*(rates[:, 1]-rates[:, 0]), [2.5, 97.5]).tolist()
                                    if len(rates) and min(group_weeks) >= 2 else None)
    answer["valid_bootstrap_draws"] = int(both.sum())
    return answer


def main():
    cfg = json.loads((OUT / "config.json").read_text())
    ledger = pd.DataFrame(row for symbol in cfg["symbols"] for row in observations(symbol, cfg))
    expected_weeks = pd.date_range(cfg["first_ny_week"], cfg["last_ny_week"], freq="7D").strftime("%Y-%m-%d")
    assert not ledger.duplicated(["symbol", "week", "side"]).any()
    assert len(ledger) == 2 * len(cfg["symbols"]) * len(expected_weeks)
    eligible = ledger[ledger.state.eq("eligible")]
    assert (eligible.label_at - eligible.decision).eq(cfg["horizon_5m_bars"] * BAR).all()
    summary = {"opportunities": len(ledger), "state_counts": ledger.state.value_counts().to_dict(), "comparisons": {}}
    for period in ["2024-25", "2026", "all"]:
        data = eligible if period == "all" else eligible[eligible.period.eq(period)]
        # Split week can contribute on either side of UTC Jan 1; include it in both resampling frames.
        weeks = expected_weeks
        if period == "2026":
            weeks = weeks[weeks >= "2025-12-29"]
        if period == "2024-25":
            weeks = weeks[weeks <= "2025-12-29"]
        groups = {"both": data, **{s: data[data.symbol.eq(s)] for s in cfg["symbols"]},
                  **{s: data[data.side.eq(s)] for s in ["lower", "upper"]}}
        summary["comparisons"][period] = {k: compare(g, weeks, cfg) for k, g in groups.items()}
    ledger.to_csv(OUT / "events.csv.gz", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
