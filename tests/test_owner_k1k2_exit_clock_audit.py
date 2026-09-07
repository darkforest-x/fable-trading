"""Synthetic state/transition clocks and metamorphic invariants, no market I/O."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.owner_k1k2_exit_clock_audit import AUDIT_COLUMNS, audit_clock


def fixture(direction=1, before=-1, first=-1, hold=5):
    e = pd.Timestamp("2023-01-02T00:00:00Z")
    times = pd.date_range(e-pd.Timedelta(minutes=10), e+pd.Timedelta(minutes=20), freq="5min")
    raw = pd.DataFrame(dict(open_time=times, open=100., high=101., low=99., close=100.))
    native = raw.copy()
    native["hl2"] = 100.
    native["ma"] = 99.
    native["ma_side"] = 1
    native["segment_id"] = "opaque-management-segment"
    for time, side in ((e-pd.Timedelta(minutes=5), before), (e, first)):
        native.loc[native.open_time.eq(time), ["ma", "ma_side"]] = [99. if side == 1 else 101., side]
    native.attrs = {"bar_minutes": 5, "ma_kind": "SMA", "ma_length": 40}
    trades = pd.DataFrame([dict(event_id="case", decision_time=e, entry_time=e,
        direction=direction, flow_pass=True, outcome="colour_exit", hold_minutes=hold,
        exit_time=e+pd.Timedelta(minutes=hold), net_return=-.001)])
    return trades, native, raw


@pytest.mark.parametrize("direction", [-1, 1])
def test_already_opposite_is_continuation_not_new_flip(direction):
    t, n, r = fixture(direction, -direction, -direction)
    out = audit_clock(t, n, r).iloc[0]
    assert out.entry_already_opposite and out.first_postentry_opposite
    assert out.color_exit_without_new_flip and not out.first_postentry_new_flip
    assert out.exited_first5m and out.exact_known_clocks
    assert out.entry_bar_open == t.decision_time.iloc[0]-pd.Timedelta(minutes=5)
    assert out.first_postentry_bar_open == t.decision_time.iloc[0]
    assert out.exit_available_at == t.exit_time.iloc[0]


@pytest.mark.parametrize("direction", [-1, 1])
def test_aligned_to_opposite_is_a_new_flip(direction):
    out = audit_clock(*fixture(direction, direction, -direction)).iloc[0]
    assert out.first_postentry_new_flip and not out.color_exit_without_new_flip
    assert not out.entry_already_opposite


@pytest.mark.parametrize("direction", [-1, 1])
def test_hl2_equality_is_source_long_not_forced_mirror(direction):
    t, n, r = fixture(direction, 1, 1)
    n.ma = n.hl2
    n.ma_side = 1
    out = audit_clock(t, n, r).iloc[0]
    assert out.entry_side == 1
    assert bool(out.entry_aligned) == (direction == 1)


@pytest.mark.parametrize("which", ["entry", "first", "boundary", "warmup", "segment", "side", "hl2"])
def test_unknown_is_not_false_or_stale_fallback(which):
    t, n, r = fixture()
    e = t.decision_time.iloc[0]
    if which == "entry": n = n.loc[~n.open_time.eq(e-pd.Timedelta(minutes=5))]
    elif which == "first": n = n.loc[~n.open_time.eq(e)]
    elif which == "boundary": r = r.loc[~r.open_time.eq(e)]
    elif which == "warmup": n.loc[n.open_time.eq(e-pd.Timedelta(minutes=5)), "ma"] = np.nan
    elif which == "segment": n.loc[n.open_time.eq(e-pd.Timedelta(minutes=5)), "segment_id"] = None
    elif which == "side": n.loc[n.open_time.eq(e-pd.Timedelta(minutes=5)), "ma_side"] = 0
    else: n.loc[n.open_time.eq(e-pd.Timedelta(minutes=5)), "hl2"] = 105.
    out = audit_clock(t, n, r).iloc[0]
    assert not out.exact_known_clocks
    assert pd.isna(out.color_exit_without_new_flip)
    assert pd.isna(out.first_postentry_new_flip)


def test_management_segment_ids_are_not_compared_to_raw_numbers():
    t, n, r = fixture()
    r["segment_id"] = 700
    assert audit_clock(t, n, r).exact_known_clocks.iloc[0]
    n.loc[n.open_time.eq(t.decision_time.iloc[0]), "segment_id"] = "different"
    out = audit_clock(t, n, r).iloc[0]
    assert not out.exact_known_clocks and pd.isna(out.first_postentry_new_flip)


def test_later_raw_gap_does_not_erase_known_first_state_but_invalidates_path():
    t, n, r = fixture(hold=20)
    r = r.loc[~r.open_time.eq(t.decision_time.iloc[0]+pd.Timedelta(minutes=10))]
    out = audit_clock(t, n, r).iloc[0]
    assert out.entry_context_known and out.first_postentry_context_known
    assert not out.exact_known_clocks and not out.exited_first5m


@pytest.mark.parametrize("scale", [.01, .5, 2., 1000.])
def test_price_scale_equivariance_and_inputs_unmodified(scale):
    t, n, r = fixture()
    baseline = audit_clock(t, n, r)
    originals = [x.copy(deep=True) for x in (t, n, r)]
    for x in (n, r):
        for c in ["open", "high", "low", "close"]: x[c] *= scale
    n.hl2 *= scale; n.ma *= scale
    out = audit_clock(t, n, r)
    for c in AUDIT_COLUMNS:
        if c.endswith(("_hl2", "_sma40")): np.testing.assert_allclose(out[c], baseline[c]*scale)
        else: pd.testing.assert_series_equal(out[c], baseline[c])
    pd.testing.assert_frame_equal(t, originals[0])
    before = [x.copy(deep=True) for x in (t, n, r)]
    audit_clock(t, n, r)
    for x, y in zip(before, (t, n, r)): pd.testing.assert_frame_equal(x, y)


def test_future_suffix_and_boundary_hlc_do_not_change_already_available_context():
    t, n, r = fixture()
    e, x = t.decision_time.iloc[0], t.exit_time.iloc[0]
    baseline = audit_clock(t, n, r)
    r.loc[r.open_time.ge(x), ["high", "low", "close"]] = np.nan
    n.loc[n.open_time.ge(x), ["hl2", "ma", "ma_side"]] = np.nan
    pd.testing.assert_frame_equal(baseline, audit_clock(t, n, r))
    pd.testing.assert_frame_equal(baseline, audit_clock(t, n.loc[n.open_time.lt(x)], r.loc[r.open_time.le(x)]))
    r.loc[r.open_time.eq(e), ["high", "low", "close"]] = np.nan
    after = audit_clock(t, n, r)
    for c in [x for x in AUDIT_COLUMNS if x.startswith("entry_")]:
        pd.testing.assert_series_equal(baseline[c], after[c])
    assert not after.first_postentry_context_known.iloc[0]


def test_pnl_and_gate_are_copied_but_do_not_choose_labels():
    t, n, r = fixture()
    baseline = audit_clock(t, n, r)
    t.net_return = 100.
    t.flow_pass = False
    out = audit_clock(t, n, r)
    pd.testing.assert_frame_equal(baseline[list(AUDIT_COLUMNS)], out[list(AUDIT_COLUMNS)])
    assert out.net_return.iloc[0] == 100 and not out.flow_pass.iloc[0]


@pytest.mark.parametrize("which", ["trades", "native", "raw"])
def test_duplicate_identity_or_clock_rejected(which):
    t, n, r = fixture()
    if which == "trades": t = pd.concat([t, t])
    elif which == "native": n = pd.concat([n, n.iloc[-1:]])
    else: r = pd.concat([r, r.iloc[-1:]])
    with pytest.raises(ValueError, match="[Uu]nique|duplicate"): audit_clock(t, n, r)


def test_empty_and_rejected_preserve_original_rows_and_nullable_states():
    t, n, r = fixture()
    empty = audit_clock(t.iloc[:0], n, r)
    assert len(empty) == 0 and set(AUDIT_COLUMNS).issubset(empty)
    t.outcome = "entry_invalid_risk"; t.exit_time = pd.NaT; t.hold_minutes = np.nan
    out = audit_clock(t, n, r).iloc[0]
    assert not out.entry_context_known and pd.isna(out.entry_already_opposite)
    assert pd.isna(out.exited_first5m)


def test_rows_duplicate_indices_attrs_and_contract_guards():
    t, n, r = fixture()
    t = pd.concat([t, t.assign(event_id="second", direction=-1)])
    t.attrs["lineage"] = "synthetic"
    out = audit_clock(t, n, r)
    assert list(out.index) == [0, 0] and list(out.event_id) == ["case", "second"]
    assert out.attrs == t.attrs
    pd.testing.assert_frame_equal(out[t.columns], t)
    with pytest.raises(ValueError, match="overwriting"): audit_clock(out, n, r)
    n.attrs["ma_length"] = 39
    with pytest.raises(ValueError, match="SMA40"): audit_clock(t, n, r)


@pytest.mark.parametrize("which", ["raw", "native"])
def test_empty_source_preserves_unknown_not_false(which):
    t, n, r = fixture()
    if which == "raw": r = r.iloc[:0]
    else: n = n.iloc[:0]
    out = audit_clock(t, n, r).iloc[0]
    assert not out.exact_known_clocks and not out.entry_context_known
    assert pd.isna(out.entry_already_opposite) and pd.isna(out.color_exit_without_new_flip)


@pytest.mark.parametrize("bad", ["2023-01-02T00:00:00", "2023-01-02T00:01:00Z", True])
def test_invalid_trade_clocks_rejected(bad):
    t, n, r = fixture()
    t["decision_time"] = [bad]
    with pytest.raises(ValueError, match="clock"): audit_clock(t, n, r)


def test_nonfinite_management_segment_unknown_and_holding_mismatch_rejected():
    t, n, r = fixture()
    n.segment_id = np.inf
    assert not audit_clock(t, n, r).entry_context_known.iloc[0]
    t.hold_minutes = 10
    with pytest.raises(ValueError, match="holding clock"): audit_clock(t, n, r)
