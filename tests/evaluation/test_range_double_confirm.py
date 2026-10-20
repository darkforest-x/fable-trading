"""Causal next-close entry and identical confirmation for random controls."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_double_confirm import admission, candidate_context, candidates
from yoyo.evaluation.range_entry_clock import watched_entries
from yoyo.evaluation.pepe_range_exits import stop_events


def test_immediate_matches_prior_watch_primitive():
    w, a = [1, 0, 0, -1, 0, 0], [1, 1, 1, -1, -1, -1]
    result = admission(w, a, [1]*6, False)
    expected, seen = watched_entries(w, a)
    np.testing.assert_array_equal(result["signal"], expected)
    np.testing.assert_array_equal(result["observed"], seen)


@pytest.mark.parametrize("side", [-1, 1])
def test_both_bodies_next_close_repeated_entries(side):
    result = admission([side, 0, 0, 0, 0, 0],
                       [side, side, 0, side, 0, 0], [side]*6, True)
    np.testing.assert_array_equal(result["signal"], [0, 0, side, 0, side, 0])
    np.testing.assert_array_equal(result["source_i"][[2, 4]], [1, 3])


@pytest.mark.parametrize("bodies", [[1, 0, 1, 1], [1, -1, 1, 1],
                                  [1, 1, 0, 1], [1, 1, -1, 1]])
def test_doji_wrong_body_cannot_wait_an_extra_bar(bodies):
    r = admission([1, 0, 0, 0], [0, 1, 0, 0], bodies, True)
    assert not r["signal"].any()


@pytest.mark.parametrize("refresh", [-1, 1])
def test_any_new_observation_cancels_pending(refresh):
    r = admission([1, 0, refresh, 0], [0, 1, 0, 0], [1]*4, True)
    assert not r["signal"].any()


def frame():
    rng = np.random.default_rng(445)
    close = 100+np.cumsum(rng.normal(0, .3, 12*24*12))
    opening = np.r_[close[0], close[:-1]]
    return pd.DataFrame(dict(open=opening, close=close,
        low=np.minimum(opening, close)-.1, high=np.maximum(opening, close)+.1),
        index=pd.date_range("2025-01-01T00:05Z", periods=len(close), freq="5min").as_unit("ns"))


def config():
    return dict(start="2025-01-02T00:00Z", splits=["2025-01-05T00:00Z", "2025-01-09T00:00Z"],
                end="2025-01-12T00:00Z", windows_5m=[5], seed=99,
                volatility_edges=[.001, .002, .004, .008, .016])


def test_prefix_causality_partial_quarter_and_controls():
    f = frame()
    ctx = candidate_context(f, True)
    for cut in (1732, 1733, 1734):
        pre = candidate_context(f.iloc[:cut], True)
        for key in ("signal", "source_i", "observed", "atr", "eligible_side"):
            np.testing.assert_array_equal(pre[key], ctx[key][:cut])
    s, r, missing = candidates(ctx, config(), True)
    assert len(s) > 0 and not missing and len(s) == len(r)
    body = (f.close-f.open).to_numpy()
    for rows in (s, r):
        for row in rows.itertuples():
            assert row.source_i == row.entry_i-1 and row.observed_i < row.source_i
            assert row.side*body[row.source_i] > 0 and row.side*body[row.entry_i] > 0
            assert ctx["quarter"][row.observed_i] == row.side
    joined = s.merge(r, on="pair_id", suffixes=("_s", "_r"))
    for col in ("side", "segment", "week", "bucket", "end_i"):
        np.testing.assert_array_equal(joined[col+"_s"], joined[col+"_r"])
    assert not set(r.entry_i) & set(s.entry_i)


def test_stop_recomputed_at_confirmation_not_source():
    f = frame()
    ctx = candidate_context(f, True)
    s, _, _ = candidates(ctx, config(), True)
    s = s.head(1)
    i, side = int(s.iloc[0].entry_i), int(s.iloc[0].side)
    if side == 1:
        f.iloc[i, f.columns.get_loc("low")] -= 10
    else:
        f.iloc[i, f.columns.get_loc("high")] += 10
    ctx = candidate_context(f, True)
    out, _ = stop_events(ctx, s, 5, .05, .01)
    extreme = f.low.iloc[i-4:i+1].min() if side == 1 else f.high.iloc[i-4:i+1].max()
    raw = (extreme-side*f.close.iloc[i]*.0005)/.01
    expected = (np.floor(raw+1e-8) if side == 1 else np.ceil(raw-1e-8))*.01
    assert out.iloc[0].stop == expected
    assert out.iloc[0].entry == f.close.iloc[i]


def test_no_pending_signal_carries_across_segment_start():
    f = frame()
    ctx = candidate_context(f, True)
    first = np.flatnonzero(ctx["signal"])[3]
    cfg = config()
    cfg["start"] = f.index[first].strftime("%Y-%m-%dT%H:%MZ")
    s, _, _ = candidates(ctx, cfg, True)
    assert first not in s.entry_i.to_list()
