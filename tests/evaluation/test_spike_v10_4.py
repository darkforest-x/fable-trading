"""Guards for the SPIKE V10.4 joint-signal port and its multi-timeframe runner.

What must hold: the vectorized structure search and validation equal a literal
line-by-line transcription of the Pine loops; nothing after bar t changes any
event at or before t; a joint fires when break and SPIKE are <= 6 bars apart
in either order and not at 7; a line born on a bar cannot pair on that bar;
the indicator's long reference ends on its stop bar; 5m -> higher timeframe
aggregation is plain OHLCV on UTC-epoch buckets; the V8 mask the runner
builds is the published low-timeframe V8 mask.
"""
import math

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v10_4 import (Line, V104Params, _search, _validate, joint_events,
                                         reference_long_exits)
from yoyo.evaluation.trendline_break import _confirmed_pivots

P = V104Params()


# ---------- literal transcriptions of the Pine, kept deliberately loop-for-loop ----------

def pine_validate(item, i, high, close, body, atr, tick, p=P):
    reason, spikes, previous, above_run = 0, 0, None, 0
    age = i - item.ax
    for step in range(age + 1):
        x = item.ax + step
        y = item.at(x)
        a = max(atr[x] if not math.isnan(atr[x]) else tick, tick)
        if body[x] > y + p.body_tol * a + tick * 0.0001:
            reason = 1
            break
        pierce = (high[x] - y) / a
        if pierce > p.wick_tol:
            spikes += 1
            if spikes > p.needles or pierce > max(p.needle_max, p.wick_tol) or (previous is not None and step - previous == 1):
                reason = 2
                break
            previous = step
        above_run = above_run + 1 if close[x] > y + p.break_buffer * a else 0
        if above_run >= p.break_bars:
            reason = 3
            break
    return reason, spikes


def pine_search(xs, ps, ats, source, i, low, close_i, atr, tick, p=P):
    from yoyo.evaluation.spike_v10_4 import _rank
    bag = []
    n = len(xs)
    if n >= 3:
        c = n - 1
        cx, cp, ca = xs[c], ps[c], ats[c]
        oldest = xs[0]
        total = cx - oldest
        lows = [math.nan] * (total + 1)
        valley = math.nan
        for step in range(1, total + 1):
            x = cx - step
            valley = low[x] if math.isnan(valley) else min(valley, low[x])
            lows[x - oldest] = valley
        for a in range(0, n - 2):
            ax, ap, aa = xs[a], ps[a], ats[a]
            if cx - ax >= p.span and ap > cp:
                local = []
                for b in range(a + 1, c):
                    bx, bp, ba = xs[b], ps[b], ats[b]
                    if bx - ax >= p.gap and cx - bx >= p.gap and ap > bp and bp > cp and ap - bp >= p.drop * max(aa, ba):
                        line = Line(ax, ap, bx, bp, cx, cp, 0.0, i, source)
                        at_c, at_now = line.at(cx), line.at(i)
                        fit = abs(cp - at_c) / ca
                        if fit <= p.touch and at_now > 0 and close_i <= at_now:
                            line.fit = fit
                            local.append(line)
                if local:
                    prefix = [math.nan] * (cx - ax + 1)
                    local_low = math.nan
                    for step in range(1, cx - ax):
                        value = low[ax + step]
                        local_low = value if math.isnan(local_low) else min(local_low, value)
                        prefix[step] = local_low
                    for item in local:
                        ba = max(atr[item.bx], tick)
                        ab = (min(item.ap, item.bp) - prefix[item.bx - ax - 1]) / max(aa, ba)
                        bc = (min(item.bp, cp) - lows[item.bx + 1 - oldest]) / max(ba, ca)
                        if ab >= p.pullback and bc >= p.pullback:
                            item.wave = min(ab, bc)
                            item.score = item.fit + 0.20 / (1.0 + item.wave)
                            _rank(bag, item, p)
    return bag


# ---------- synthetic markets ----------

def staircase(periods=2400, seed=5, cycle=260):
    """Falling legs with lower rebound highs, then a rally: lines get born and broken."""
    rng = np.random.default_rng(seed)
    t = np.arange(periods)
    phase = t % cycle
    wave = 3.0 * np.sin(phase / 9.0)
    trend = 200 - 0.06 * phase + np.where(phase > cycle - 40, (phase - (cycle - 40)) * 0.45, 0.0)
    close = trend + wave + rng.normal(0, 0.25, periods)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.6, periods)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.6, periods)
    tr = np.maximum(high - low, np.abs(high - np.r_[close[0], close[:-1]]))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    atr[:13] = np.nan
    return open_, high, low, close, atr


def default_facts(n, **overrides):
    facts = {"can_run": np.r_[np.zeros(14, bool), np.ones(n - 14, bool)], "confirmed_long": np.zeros(n, bool),
             "parent_high": np.full(n, 0.0), "parent_low": np.full(n, -1e9), "raw_side": np.zeros(n, int),
             "long_alive": np.ones(n, bool), "momentum": np.ones(n, bool), "current_gate": np.ones(n, bool),
             "ref_long_exit": np.zeros(n, bool)}
    facts.update(overrides)
    return facts


def _compare_search(o, h, lo, c, a, tick):
    """Walk every confirmed pivot; both implementations must return the same short list."""
    body = np.maximum(o, c)
    pivot_of, _ = _confirmed_pivots(h, P.left, P.right, np.isfinite(h))
    xs, ps, ats = [], [], []
    compared = validated = 0
    for i in range(len(c)):
        px = pivot_of[i]
        if px < 0 or math.isnan(a[px]):
            continue
        xs.append(int(px)); ps.append(float(h[px])); ats.append(max(float(a[px]), tick))
        while xs and (i - xs[0] > P.lookback or len(xs) > P.pivots_cap):
            xs.pop(0); ps.pop(0); ats.pop(0)
        fast = _search(xs, ps, ats, 0, i, lo, float(c[i]), a, tick, P)
        slow = pine_search(xs, ps, ats, 0, i, lo, float(c[i]), a, tick, P)
        assert [(l.ax, l.bx, l.cx) for l in fast] == [(l.ax, l.bx, l.cx) for l in slow]
        assert [l.score for l in fast] == [l.score for l in slow]
        compared += 1
        for line in fast:
            assert _validate(line, i, h, c, body, a, tick, P) == pine_validate(line, i, h, c, body, a, tick, P)
            validated += 1
    return compared, validated


def test_vectorized_search_and_validate_equal_literal_pine():
    compared = validated = 0
    for seed in range(4):
        for cycle in (260, 400):
            n, v = _compare_search(*staircase(periods=3000, seed=seed, cycle=cycle), 0.01)
            compared, validated = compared + n, validated + v
    assert compared > 300 and validated > 300


def test_vectorized_search_equals_literal_pine_on_real_bars():
    from yoyo.evaluation import spike_v10_4_study as study
    files = study.series_files()
    if "SOLUSDT" not in files:
        pytest.skip("local Binance archive not present")
    base = study.load_5m(files["SOLUSDT"], pd.Timestamp("2023-01-01T00:00:00Z"))
    bars, _ = study.aggregate(base.loc[base.index < pd.Timestamp("2023-07-01T00:00:00Z")], 60)
    tr = np.maximum(bars.high - bars.low, (bars.high - bars.close.shift()).abs().fillna(0))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    compared, validated = _compare_search(bars.open.to_numpy(), bars.high.to_numpy(), bars.low.to_numpy(),
                                          bars.close.to_numpy(), atr, 0.001)
    assert compared > 100 and validated > 20


def test_validate_catches_each_failure_kind_like_the_loop():
    rng = np.random.default_rng(11)
    n = 400
    for _ in range(300):
        close = 100 + np.cumsum(rng.normal(0, 0.4, n))
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) + rng.exponential(0.3, n)
        body = np.maximum(open_, close)
        atr = np.full(n, 0.8)
        ax = int(rng.integers(0, 100)); bx = ax + int(rng.integers(30, 150))
        ap = float(high[ax] + rng.uniform(0, 3)); bp = ap - float(rng.uniform(0.5, 6))
        line = Line(ax, ap, bx, bp, bx + 30, bp, 0.0, n - 1, 0)
        assert _validate(line, n - 1, high, close, body, atr, 0.01, P) == pine_validate(line, n - 1, high, close, body, atr, 0.01, P)


def test_no_future_bar_changes_past_events():
    o, h, lo, c, a = staircase(periods=1800, seed=8)
    n = len(c)
    confirmed = np.zeros(n, bool)
    confirmed[np.arange(300, n, 37)] = True
    facts = default_facts(n, confirmed_long=confirmed)
    full = joint_events(o, h, lo, c, a, tick=0.01, **facts)
    assert full.break_event.sum() > 3
    for cut in (700, 1111, 1500):
        part = joint_events(o[:cut], h[:cut], lo[:cut], c[:cut], a[:cut], tick=0.01,
                            **{k: v[:cut] for k, v in facts.items()})
        for name in ("born_event", "break_event", "joint_event"):
            assert np.array_equal(getattr(part, name), getattr(full, name)[:cut])


def _first_break(o, h, lo, c, a):
    n = len(c)
    base = joint_events(o, h, lo, c, a, tick=0.01, **default_facts(n))
    breaks = np.flatnonzero(base.break_event)
    assert len(breaks)
    return base, breaks


@pytest.mark.parametrize("offset,expected", [(0, True), (-3, True), (-6, True), (-7, False),
                                             (3, True), (6, True), (7, False)])
def test_joint_needs_break_and_spike_within_six_bars_either_order(offset, expected):
    o, h, lo, c, a = staircase(periods=1500, seed=5)
    n = len(c)
    _, breaks = _first_break(o, h, lo, c, a)
    b = int(breaks[0])
    s = b + offset
    confirmed = np.zeros(n, bool)
    confirmed[s] = True
    parent_high = np.full(n, -1e9)       # parent range trivially recovered
    result = joint_events(o, h, lo, c, a, tick=0.01,
                          **default_facts(n, confirmed_long=confirmed, parent_high=parent_high))
    fired = np.flatnonzero(result.joint_event)
    if expected:
        assert fired.tolist() == [max(b, s)]
        order = result.joints[0]["order"]
        assert order == ("same_bar" if offset == 0 else "spike_first" if offset < 0 else "break_first")
    else:
        assert max(b, s) not in fired.tolist()


def test_joint_refused_when_gate_fails_on_the_second_bar():
    o, h, lo, c, a = staircase(periods=1500, seed=5)
    n = len(c)
    _, breaks = _first_break(o, h, lo, c, a)
    b = int(breaks[0])
    confirmed = np.zeros(n, bool); confirmed[b - 2] = True
    gate = np.ones(n, bool); gate[b] = False
    result = joint_events(o, h, lo, c, a, tick=0.01,
                          **default_facts(n, confirmed_long=confirmed, parent_high=np.full(n, -1e9),
                                          current_gate=gate))
    assert not result.joint_event[b]
    assert {"i": b, "reason": "gate"} in result.refusals


def test_spike_evidence_dropped_when_ma_support_is_lost():
    o, h, lo, c, a = staircase(periods=1500, seed=5)
    n = len(c)
    _, breaks = _first_break(o, h, lo, c, a)
    b = int(breaks[0])
    confirmed = np.zeros(n, bool); confirmed[b - 3] = True
    alive = np.ones(n, bool); alive[b - 1] = False
    result = joint_events(o, h, lo, c, a, tick=0.01,
                          **default_facts(n, confirmed_long=confirmed, parent_high=np.full(n, -1e9),
                                          long_alive=alive))
    assert not result.joint_event[b]


def test_one_spike_is_consumed_once():
    o, h, lo, c, a = staircase(periods=2400, seed=5)
    n = len(c)
    confirmed = np.zeros(n, bool)
    confirmed[np.arange(300, n, 5)] = True
    result = joint_events(o, h, lo, c, a, tick=0.01,
                          **default_facts(n, confirmed_long=confirmed, parent_high=np.full(n, -1e9)))
    spikes = [j["spike_i"] for j in result.joints]
    assert len(spikes) == len(set(spikes))
    for j in result.joints:
        assert j["born_i"] < min(j["spike_i"], j["break_i"])
        assert abs(j["spike_i"] - j["break_i"]) <= P.window
        assert j["i"] == max(j["spike_i"], j["break_i"])


def test_reference_long_ends_on_stop_bar():
    n = 30
    close = np.full(n, 100.0); high = close + 0.5; low = close - 0.5; atr = np.full(n, 1.0)
    low[15] = 90.0                           # through a stop of min(99.5-0.2, 100-2) = 98
    raw = np.zeros(n, int); raw[10] = 1
    out = reference_long_exits(high, low, close, atr, ready=np.ones(n, bool), gap=np.zeros(n, bool),
                               raw_side=raw, signal_side=raw, tick=0.01)
    assert out.tolist() == [i == 15 for i in range(n)]


def test_reference_reverses_on_raw_short_even_when_v9_refuses_it():
    n = 30
    close = np.full(n, 100.0); high = close + 0.5; low = close - 0.5; atr = np.full(n, 1.0)
    raw = np.zeros(n, int); raw[10] = 1; raw[20] = -1
    sig = raw.copy(); sig[20] = 0
    out = reference_long_exits(high, low, close, atr, ready=np.ones(n, bool), gap=np.zeros(n, bool),
                               raw_side=raw, signal_side=sig, tick=0.01)
    assert np.flatnonzero(out).tolist() == [20]


def test_aggregate_is_plain_ohlcv_on_epoch_buckets():
    from yoyo.evaluation.spike_v10_4_study import aggregate
    index = pd.date_range("2025-01-01 00:00", periods=36, freq="5min", tz="UTC").delete([14, 15])
    bars = pd.DataFrame({"open": np.arange(34.) + 1, "high": np.arange(34.) + 3, "low": np.arange(34.),
                         "close": np.arange(34.) + 2, "volume": 1.0}, index=index)
    out, partial = aggregate(bars, 60)
    assert list(out.index) == list(pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC"))
    assert partial == 1
    first = out.iloc[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (1.0, 14.0, 0.0, 13.0, 12.0)
    assert out.iloc[1].volume == 10.0


def test_runner_v8_mask_is_the_published_lowtf_v8_mask():
    from yoyo.evaluation import spike_v10_4_study as study
    from yoyo.evaluation.spike_v8_lowtf_study import v8_mask
    files = study.series_files()
    if "ETHUSDT" not in files:
        pytest.skip("local Binance archive not present")
    base = study.load_5m(files["ETHUSDT"], pd.Timestamp("2022-01-01T00:00:00Z"))
    bars, _ = study.aggregate(base.loc[base.index < pd.Timestamp("2023-01-01T00:00:00Z")], 60)
    facts = study.v9_facts(bars, 60, "ETH", 0.01)
    _, _, published = v8_mask(bars, 60)
    assert published.sum() > 0
    assert np.array_equal(facts["v8"], published.to_numpy(bool))
    assert not (facts["v9"] & ~facts["v8"]).any()


def test_strict_pivots_equal_the_repo_convention_and_one_sided_breaks_plateaus_once():
    from yoyo.evaluation.spike_v10_4 import pivots
    rng = np.random.default_rng(3)
    u = np.round(100 + np.cumsum(rng.normal(0, 1, 5000)), 0)     # coarse grid -> many ties
    u[[50, 51, 900]] = np.nan
    strict, loose_ties, extra = pivots(u, 12, 8, "strict")
    reference, reference_ties = _confirmed_pivots(u, 12, 8, np.isfinite(u))
    assert np.array_equal(strict, reference) and loose_ties == reference_ties
    one_sided, _, extra_again = pivots(u, 12, 8, "right_inclusive")
    assert extra == extra_again == int(((one_sided >= 0) & (strict < 0)).sum()) > 0
    assert not ((strict >= 0) & (one_sided < 0)).any()
    plateau = np.r_[np.arange(20.0), [30.0, 30.0], np.arange(20.0)[::-1]]
    assert (pivots(plateau, 12, 8, "strict")[0] >= 0).sum() == 0
    found = pivots(plateau, 12, 8, "right_inclusive")[0]
    assert found[found >= 0].tolist() == [20]
