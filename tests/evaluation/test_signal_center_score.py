"""Signal-center score builder: live-rule parity and no look-ahead in features."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import signal_center_score as scs

CONFIG = "experiments/active/exp-signal-center-score-20261106-v1/config.json"
SYMBOL = "ARCUSDT"


@pytest.fixture(scope="module")
def frame():
    from yoyo.evaluation.monday_limit_reclaim_inputs import jobs
    import json
    from pathlib import Path
    cfg = json.loads((scs.ROOT / CONFIG).read_text())
    job = next(j for j in jobs(cfg["source_config"]) if j["symbol"] == SYMBOL)
    if not all(Path(p).exists() for p in job["paths"]):
        pytest.skip("frozen source not mounted")
    return scs.five_minute(job).iloc[:120_000]


def empty_ctx():
    z = np.array([0], dtype=np.int64)
    ctx = dict(close_ns=z, ret24=np.array([np.nan]))
    for tf in scs.TFS:
        ctx[f"side_{tf}_ns"], ctx[f"side_{tf}"] = z, np.array([np.nan])
    return ctx


def test_12h_raw_signals_match_live_scanner(frame):
    from yoyo.monitor.cloud_signals import ema_points, signal
    from yoyo.monitor.monday_fib import week_window
    _, _, _, _, _, raw, _, _ = scs.build_inputs(frame, SYMBOL, empty_ctx())
    mine = sorted((int(t) // 10**6 - 43_200_000, int(s)) for t, s in
                  raw[raw.tf == "12H"][["close_ns", "side"]].itertuples(index=False))
    mr = scs.monday_ranges(frame)
    anchors = {}
    for wk, r in mr.iterrows():
        w = week_window(int(wk.tz_convert("UTC").value // 10**6) + 3_600_000)
        anchors[w["week"]] = dict(state="ready", symbol=SYMBOL, week=w["week"], end_ms=w["end"],
                                  expires_ms=w["expires"], upper=r.upper, lower=r.lower, high=r.high, low=r.low)
    b = scs.tf_bars(frame, 720)
    rows = [dict(time=int(t) // 10**6, open=o, high=h, low=l, close=c)
            for t, o, h, l, c in zip(b.index.asi8, b.open, b.high, b.low, b.close)]
    live = []
    for bar, p in zip(rows, ema_points(rows)):
        a = anchors.get(week_window(bar["time"])["week"])
        s = a and signal(SYMBOL, bar, p, a, now=10**15)
        if s:
            live.append((s["signal_open_ms"], s["side"]))
    assert len(live) > 5 and mine == sorted(live)


def test_features_ignore_bars_after_the_signal(frame):
    _, _, _, _, full, _, _, _ = scs.build_inputs(frame, SYMBOL, empty_ctx())
    cut_ns = int(full.close_ns.quantile(.6))
    keep = frame[frame.index.asi8 + 300 * 10**9 <= cut_ns + 300 * 10**9 * 3]
    # Corrupt nothing before the cut; drop everything after it.
    _, _, _, _, part, _, _, _ = scs.build_inputs(keep, SYMBOL, empty_ctx())
    early = full[full.close_ns <= cut_ns - 300 * 10**9 * 3].set_index("signal_id")
    part = part.set_index("signal_id").loc[early.index]
    cols = [c for c in scs.FEATURES if c in early.columns]
    assert len(early) > 20 and len(cols) >= 20
    pd.testing.assert_frame_equal(early[cols], part[cols], check_exact=False, rtol=1e-12)
