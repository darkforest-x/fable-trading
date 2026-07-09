"""Data-quality audit (P2-12, pulled forward): gaps, zero-volume shares and
anomalous bars per series across every timeframe on disk.

Output: analysis/output/data_audit.csv + summary json printed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.data.loader import iter_series

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT_CSV = PROJECT_DIR / "analysis" / "output" / "data_audit.csv"
BAR_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1H": 60}


def main() -> int:
    rows = []
    for bar, minutes in BAR_MINUTES.items():
        for source, symbol, frame in iter_series(bar=bar, min_bars=300):
            dt = frame["open_time"].diff().dt.total_seconds() / 60
            gaps = int((dt > minutes).sum())
            max_gap_h = round(float(dt.max() / 60), 1) if len(dt) > 1 else 0.0
            zero_vol = round(float((frame["volume"] <= 0).mean()), 4)
            ret = frame["close"].pct_change().abs()
            spikes = int((ret > 0.25).sum())  # >25% single-bar move: likely bad print
            rows.append({
                "bar": bar, "source": source, "symbol": symbol, "n_bars": len(frame),
                "first": str(frame["open_time"].iloc[0])[:10],
                "last": str(frame["open_time"].iloc[-1])[:16],
                "n_gaps": gaps, "max_gap_hours": max_gap_h,
                "zero_vol_share": zero_vol, "spike_bars": spikes,
            })
    df = pd.DataFrame(rows).sort_values(["bar", "symbol"])
    df.to_csv(OUT_CSV, index=False)
    flagged = df[(df["n_gaps"] > 5) | (df["zero_vol_share"] > 0.02) | (df["spike_bars"] > 0)]
    summary = {
        "series_total": int(len(df)),
        "by_bar": df.groupby("bar").size().to_dict(),
        "flagged": int(len(flagged)),
        "worst_gaps": df.nlargest(5, "n_gaps")[["bar", "symbol", "n_gaps", "max_gap_hours"]].to_dict("records"),
        "spike_series": flagged[flagged["spike_bars"] > 0][["bar", "symbol", "spike_bars"]].to_dict("records")[:10],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
