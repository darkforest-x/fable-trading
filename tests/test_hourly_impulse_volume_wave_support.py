"""V31 synthetic-only oracle, invariant and frozen-membership tests.

No actual study tables or prices are loaded. Generated-domain tests use
fixed NumPy seeds because this project has no Hypothesis dependency.
"""
from copy import deepcopy
import inspect

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation import hourly_impulse_volume_wave_support as model

START = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def hours(n=230):
    x = np.arange(n, dtype=float)
    close = 100.+np.sin(x/3.)
    return pd.DataFrame(dict(open_time=pd.date_range(START, periods=n, freq="h"),
                             open=np.full(n, 100.), high=np.maximum(100., close)+1.,
                             low=np.minimum(100., close)-1., close=close, volume=10.+x % 7))


def pulse_hours():
    source = hours(115)
    source[["open", "close"]] = 100.
    source[["high", "low", "volume"]] = [102., 98., 10.]
    source.loc[100, "close"] = 101.
    source.loc[101, ["close", "volume"]] = [99., 1000.]
    return source


def requests(positions, directions=None, source=None):
    req = pd.DataFrame(dict(event_id=["m%d" % i for i in range(len(positions))],
                            signal_time=[START+i*HOUR for i in positions],
                            decision_time=[START+(i+1)*HOUR for i in positions],
                            direction=directions if directions is not None else [1]*len(positions)))
    if source is not None:
        req["signal_close"] = [float(source.close.iloc[i]) for i in positions]
    return req


def scalar_wave(source):
    """Independent first-seeded scalar EMA oracle with exact gap resets."""
    out = []
    previous_time = None
    buy_ema = sell_ema = total_ema = wave = None
    for row in source.itertuples(index=False):
        buy = row.volume if row.close > row.open else 0.
        sell = row.volume if row.close < row.open else 0.
        total = row.volume if row.volume > 0 else 1.
        if previous_time is None or row.open_time-previous_time != HOUR:
            buy_ema, sell_ema, total_ema = buy, sell, total
            wave = None
        else:
            buy_ema = (19.*buy_ema+2.*buy)/21.
            sell_ema = (19.*sell_ema+2.*sell)/21.
            total_ema = (19.*total_ema+2.*total)/21.
        raw = (buy_ema-sell_ema)/(total_ema if total_ema > 0 else 1.)*100.
        wave = raw if wave is None else (2.*wave+raw)/3.
        out.append([buy_ema, sell_ema, total_ema, raw, wave])
        previous_time = row.open_time
    return np.asarray(out)


def support_inputs(unknown_control=False):
    source = pulse_hours()
    cases = requests([101, 109], [1, -1], source)
    cases["fold"], cases["request_kind"] = "2024H1", "case"
    cases["mother_id"], cases["control_slot"] = cases.event_id, pd.NA
    cases["mother_month"], cases["matched_support"] = "2024-01", [True, False]
    controls = requests([100 if unknown_control else 102, 103, 104])
    controls["event_id"] = ["m0:c%d" % i for i in range(3)]
    controls["fold"], controls["request_kind"], controls["mother_id"] = "2024H1", "control", "m0"
    controls["control_slot"], controls["mother_month"], controls["matched_support"] = range(3), "2024-01", True
    assignments = pd.DataFrame(dict(event_id=cases.event_id, matched_support=cases.matched_support,
                                    assigned_controls=[3, 0]))
    allocation = pd.DataFrame(dict(event_id="m0", candidate_id=controls.decision_time.map(lambda t: t.isoformat()),
                                   candidate_time=controls.decision_time, control_slot=range(3),
                                   control_event_id=controls.event_id))
    return cases, controls, source, assignments, allocation


@pytest.mark.parametrize("seed", range(8))
def test_generated_scalar_oracle_mirror_and_price_scaling(seed):
    rng = np.random.default_rng(seed)
    source = hours(150)
    source["open"] = rng.uniform(90., 110., len(source))
    source["close"] = source.open+rng.uniform(-3., 3., len(source))
    source["high"] = source[["open", "close"]].max(axis=1)+1.
    source["low"] = source[["open", "close"]].min(axis=1)-1.
    source["volume"] = rng.uniform(.1, 1000., len(source))
    source.loc[::11, "volume"] = 0.
    trace = model.wave_trace(source)
    columns = ["wave_ema_buy", "wave_ema_sell", "wave_ema_total", "wave_raw", "wave_value"]
    np.testing.assert_allclose(trace[columns].to_numpy(), scalar_wave(source), rtol=1e-12, atol=1e-12)
    mirror = source.copy()
    mirror[["open", "high", "low", "close"]] = np.column_stack((
        200-source.open, 200-source.low, 200-source.high, 200-source.close))
    mirrored = model.wave_trace(mirror)
    np.testing.assert_allclose(mirrored.wave_value, -trace.wave_value, atol=1e-12)
    np.testing.assert_allclose(mirrored.wave_prior_delta, -trace.wave_prior_delta, atol=1e-12)
    req = requests([101, 110, 149], [1, -1, 1])
    mirrored_req = req.copy(); mirrored_req["direction"] *= -1
    assert model.attach_context(req, trace)[model.GATE].tolist() == model.attach_context(mirrored_req, mirrored)[model.GATE].tolist()
    scaled = source.copy(); scaled[["open", "high", "low", "close"]] *= 7.
    np.testing.assert_allclose(model.wave_trace(scaled).wave_value, trace.wave_value, atol=1e-12)


def test_source_assigns_body_direction_not_close_to_close_direction():
    source = hours(3)
    source[["open", "high", "low", "close", "volume"]] = [100., 102., 98., 100., 10.]
    source.loc[1, ["open", "close"]] = [98., 99.]
    source.loc[2, ["open", "close"]] = [102., 101.]
    trace = model.wave_trace(source)
    assert trace.wave_buy_volume.tolist() == [0., 10., 0.]
    assert trace.wave_sell_volume.tolist() == [0., 0., 10.]
    assert trace.wave_value.iloc[1] > 0


def test_doji_zero_volume_and_strict_zero_boundary():
    source = hours(110); source["close"] = source.open
    trace = model.wave_trace(source)
    assert trace.wave_value.eq(0).all()
    req = requests([101, 102], [1, -1])
    out = model.attach_context(req, trace)
    assert out[model.GATE].tolist() == ["abstain", "abstain"]
    assert out.wave_reason.tolist() == ["flat", "flat"]
    source = hours(110); source["volume"] = 0.
    trace = model.wave_trace(source)
    assert trace.wave_total_input.eq(1.).all() and trace.wave_ema_total.eq(1.).all()
    assert trace.wave_value.eq(0.).all()
    source.loc[1, ["close", "volume"]] = [101., 10.]
    trace = model.wave_trace(source)
    assert trace.wave_ema_total.iloc[1] == pytest.approx((19.+20.)/21.)
    assert trace.wave_raw.iloc[1] == pytest.approx(2000./39.)


def test_warmup_requires_100_bars_at_t_minus_two_not_current_t():
    trace = model.wave_trace(pulse_hours())
    assert not trace.wave_known.iloc[:101].any()
    assert trace.wave_known.iloc[101:].all()
    out = model.attach_context(requests([0, 1, 2, 99, 100, 101]), trace)
    assert out.wave_reason.tolist() == ["missing_prior_hours", "missing_prior_hours", "warmup", "warmup", "warmup", "prior_improving"]
    assert out[model.GATE].tolist() == ["unknown"]*5+["accepted"]
    assert out.wave_previous2_count.iloc[-1] == 100
    assert out.wave_previous_available_at.iloc[-1] == out.signal_time.iloc[-1]
    assert out.wave_previous2_available_at.iloc[-1] == out.signal_time.iloc[-1]-HOUR
    assert out.wave_available_at.iloc[-1] == out.decision_time.iloc[-1]


def test_current_k1_and_future_mutations_do_not_select_gate():
    source = pulse_hours(); trace = model.wave_trace(source)
    req = requests([101])
    before = model.attach_context(req, trace)
    changed = source.copy()
    changed.loc[101:, ["open", "high", "low", "close", "volume"]] = [400., 500., 300., 450., 1e6]
    changed_trace = model.wave_trace(changed)
    after = model.attach_context(req, changed_trace)
    assert_frame_equal(trace.iloc[:101], changed_trace.iloc[:101])
    prior = [c for c in before if "previous" in c or c in ("wave_prior_delta", "wave_known", "wave_reason", model.GATE)]
    assert_frame_equal(before[prior], after[prior])
    assert before.wave_value.iloc[0] != after.wave_value.iloc[0]
    with_close = requests([101], source=source)
    with pytest.raises(ValueError, match="signal_close"):
        model.attach_context(with_close, changed_trace)


def test_all_prefixes_immutable_and_gap_restarts_every_recursion():
    source = hours(350); before = source.copy(deep=True)
    trace = model.wave_trace(source)
    for end in [0, 1, 2, 3, 99, 100, 101, 102, 175]:
        assert_frame_equal(trace.iloc[:end], model.wave_trace(source.iloc[:end]))
    broken = source.drop(index=150)
    actual = model.wave_trace(broken)
    restarted = model.wave_trace(broken.iloc[150:].reset_index(drop=True))
    columns = [c for c in actual if c != "wave_segment"]
    assert_frame_equal(actual.iloc[150:][columns].reset_index(drop=True), restarted[columns])
    assert actual.wave_segment.nunique() == 2
    assert not actual.wave_known.iloc[150:251].any() and actual.wave_known.iloc[251]
    out = model.attach_context(requests([150, 151, 152, 153]), actual)
    assert out.wave_reason.tolist() == ["missing_signal_hour", "missing_prior_hours", "missing_prior_hours", "warmup"]
    assert out[model.GATE].eq("unknown").all()
    assert_frame_equal(source, before)


def test_empty_trace_requests_and_duplicate_row_indices():
    trace = model.wave_trace(hours(0))
    assert tuple(trace.columns) == (*model.OHLCV, *model.CONTEXT)
    out = model.attach_context(requests([101]), trace)
    assert out[model.GATE].iloc[0] == "unknown" and pd.isna(out.wave_available_at.iloc[0])
    assert model.attach_context(requests([]), trace).empty
    source = hours(120); source.index = [8]*len(source)
    assert_frame_equal(model.wave_trace(source), model.wave_trace(source.reset_index(drop=True)))


@pytest.mark.parametrize("mutation", ["reverse", "duplicate", "naive", "offset", "subhour", "numeric_time", "nan", "inf", "negative", "bool", "geometry", "negative_volume", "bool_volume", "nan_volume", "inf_volume", "reserved"])
def test_invalid_sources_fail_explicitly(mutation):
    source = hours(3)
    if mutation == "reverse": source = source.iloc[::-1]
    elif mutation == "duplicate": source.loc[1, "open_time"] = source.open_time.iloc[0]
    elif mutation in ("naive", "offset", "subhour", "numeric_time"):
        source["open_time"] = source.open_time.astype(object)
        source.loc[1, "open_time"] = {"naive": "2024-01-01 01:00:00", "offset": "2024-01-01T09:00:00+08:00",
                                      "subhour": START+pd.Timedelta(minutes=5), "numeric_time": 1700000000}[mutation]
    elif mutation in ("bool", "bool_volume"):
        col = "volume" if mutation == "bool_volume" else "close"
        source[col] = source[col].astype(object); source.loc[1, col] = True
    elif mutation == "reserved": source["wave_old"] = 0
    else:
        col = "volume" if "volume" in mutation else "close"
        source.loc[1, col] = {"nan": np.nan, "inf": np.inf, "negative": -1., "geometry": 1000.,
                             "negative_volume": -1., "nan_volume": np.nan, "inf_volume": np.inf}[mutation]
    with pytest.raises((ValueError, TypeError)): model.wave_trace(source)


@pytest.mark.parametrize("field,value", [
    ("signal_time", "2024-01-05 05:00:00"), ("decision_time", START+103*HOUR),
    ("direction", True), ("direction", "1"), ("direction", 0),
    ("event_id", None), ("signal_close", True), ("signal_close", np.nan),
])
def test_invalid_requests(field, value):
    req = requests([101]); req[field] = pd.Series([value], dtype=object)
    with pytest.raises(ValueError): model.attach_context(req, model.wave_trace(pulse_hours()))


@pytest.mark.parametrize("field,value", [
    ("wave_value", 999.), ("wave_previous", 999.), ("wave_previous2", 999.),
    ("wave_prior_delta", 999.), ("wave_count", 999), ("wave_segment", 999),
    ("wave_previous_available_at", START), ("wave_known", False),
    ("wave_reason", "bad"), ("wave_ema_total", 999.),
])
def test_supplied_trace_semantics_checked(field, value):
    trace = model.wave_trace(pulse_hours()); trace.loc[101, field] = value
    with pytest.raises(ValueError, match="semantics mismatch"):
        model.attach_context(requests([101]), trace)


def test_preserve_all_columns_index_attrs_no_outcomes_consulted():
    source = hours(120); source["future_net_return"] = [object()]*len(source)
    req = requests([101, 110]); req.index = [8, 8]
    req.attrs = {"nested": {"tag": "untouched"}}
    req["old_return"] = [object(), {"not": "data"}]
    req["classifier_fake"] = [object(), object()]
    before, attrs = req.copy(deep=True), deepcopy(req.attrs)
    out = model.attach_context(req, model.wave_trace(source))
    assert_frame_equal(out[req.columns], before); assert_frame_equal(req, before)
    assert out.attrs == attrs and out.index.tolist() == [8, 8]
    with pytest.raises(ValueError, match="overwrite"):
        model.attach_context(out, model.wave_trace(source))
    with pytest.raises(ValueError): model.attach_context(pd.concat([req, req]), model.wave_trace(source))


def test_support_own_clocks_full_triples_unmatched_and_zero_rows():
    inputs = support_inputs(); before = [x.copy(deep=True) for x in inputs]
    tables, summary = model.build_support(*inputs)
    for x, old in zip(inputs, before): assert_frame_equal(x, old)
    assert set(tables) == {"hourly_trace", "case_context", "control_context", "counts", "matched_support"}
    assert len(tables["counts"]) == 62 and tuple(tables["counts"].columns) == model.COUNT_COLUMNS
    assert len(tables["matched_support"]) == 2
    assert tables["matched_support"].control_total.tolist() == [3, 0]
    assert tables["case_context"][model.GATE].iloc[0] == "accepted"
    own = model.attach_context(inputs[1], tables["hourly_trace"])
    assert_frame_equal(tables["control_context"], own)
    assert own[model.GATE].eq("abstain").any()
    assert tables["matched_support"].complete_known.tolist() == [True, False]
    assert summary["accepted_case_control_states"]["total"] == 3
    assert summary["coverage"]["complete_known_triples_all_cases"] == dict(numerator=1, denominator=2, rate=.5)
    assert not summary["support_pass"] and not summary["economic_acceptance"] and not summary["outcomes_read_or_computed"]
    assert summary["support_values"]["minimum_fold_events"] == 0
    assert summary["support_values"]["minimum_fold_months"] == 0


def test_single_unknown_control_preserved_and_marks_incomplete_triple():
    tables, summary = model.build_support(*support_inputs(unknown_control=True))
    assert tables["case_context"][model.GATE].iloc[0] == "accepted"
    assert tables["control_context"][model.GATE].iloc[0] == "unknown"
    assert not tables["matched_support"].complete_known.any()
    assert summary["accepted_case_control_states"]["unknown"] == 1


@pytest.mark.parametrize("mutation", ["control_clock", "direction", "slot", "mother", "fold", "month", "assignment", "drop_control", "duplicate_control"])
def test_original_identity_allocation_contracts(mutation):
    c, k, h, a, m = support_inputs()
    if mutation == "control_clock": k.loc[0, ["signal_time", "decision_time"]] += HOUR
    elif mutation == "direction": k.loc[0, "direction"] = -1
    elif mutation == "slot": k.loc[0, "control_slot"] = 2
    elif mutation == "mother": k.loc[0, "mother_id"] = "m1"
    elif mutation == "fold": k.loc[0, "fold"] = "2023H1"
    elif mutation == "month": k.loc[0, "mother_month"] = "2024-02"
    elif mutation == "assignment": a.loc[0, "assigned_controls"] = 2
    elif mutation == "drop_control": k = k.iloc[1:]
    else: k = pd.concat([k, k.iloc[[0]]])
    with pytest.raises(ValueError): model.build_support(c, k, h, a, m)


def full_population():
    blocks, cases, controls, allocations = [], [], [], []
    mother_index = 0
    for month_index, origin in enumerate(pd.date_range("2023-01-01T00:00:00Z", periods=24, freq="MS")):
        block = hours(230)
        block["open_time"] = pd.date_range(origin, periods=len(block), freq="h")
        blocks.append(block)
        fold = str(origin.year)+("H1" if origin.month <= 6 else "H2")
        for j in range(11 if month_index < 11 else 10):
            identity = "mother-%03d" % mother_index
            t = origin+(110+5*j)*HOUR
            matched = mother_index < 248
            direction = 1 if mother_index % 2 else -1
            base = dict(fold=fold, mother_id=identity, mother_month=origin.strftime("%Y-%m"),
                        direction=direction, matched_support=matched)
            cases.append(dict(base, event_id=identity, signal_time=t, decision_time=t+HOUR,
                              control_slot=pd.NA, request_kind="case"))
            if matched:
                for slot in range(3):
                    ct = t+(slot+1)*HOUR
                    cid = identity+":c"+str(slot)
                    controls.append(dict(base, event_id=cid, signal_time=ct, decision_time=ct+HOUR,
                                         control_slot=slot, request_kind="control"))
                    allocations.append(dict(event_id=identity, candidate_id=(ct+HOUR).isoformat(),
                                            candidate_time=ct+HOUR, control_slot=slot, control_event_id=cid))
            mother_index += 1
    cases, controls = pd.DataFrame(cases), pd.DataFrame(controls)
    assignments = pd.DataFrame(dict(event_id=cases.event_id, matched_support=cases.matched_support,
                                    assigned_controls=np.where(cases.matched_support, 3, 0)))
    return cases, controls, pd.concat(blocks, ignore_index=True), assignments, pd.DataFrame(allocations)


def test_full251_original_grid_not_reduced_to_accepted_controls():
    tables, summary = model.build_support(*full_population())
    assert len(tables["case_context"]) == len(tables["matched_support"]) == 251
    assert len(tables["control_context"]) == 744 and len(tables["counts"]) == 62
    assert tables["matched_support"].control_total.value_counts().to_dict() == {3: 248, 0: 3}
    assert summary["population"]["case"]["known"] == 251
    assert summary["population"]["control"]["known"] == 744
    assert tables["counts"].loc[tables["counts"].dimension.eq("fold"), "total"].sum() == 995
    assert tables["matched_support"].complete_known.sum() == 248
    c = tables["case_context"]
    accepted = c.loc[c[model.GATE].eq("accepted")]
    states = tables["control_context"].loc[tables["control_context"].mother_id.isin(accepted.event_id)]
    assert summary["accepted_case_control_states"] == model.counts(states)
    assert summary["coverage"]["complete_known_triples_all_cases"]["denominator"] == 251
    assert not summary["economic_acceptance"]


def test_fixed80_support_can_pass_without_triples_or_economic_acceptance():
    blocks, records = [], []
    for fold, start, _ in model.DEFAULT_FOLDS:
        for month in range(3):
            origin = pd.Timestamp(start)+pd.DateOffset(months=month)
            source = hours(230)
            source["open_time"] = pd.date_range(origin, periods=len(source), freq="h")
            blocks.append(source)
            oracle = scalar_wave(source)[:, -1]
            for j in range(6 if month == 2 else 7):
                position = 110+5*j
                change = oracle[position-1]-oracle[position-2]
                assert change != 0
                identity = fold+"-"+str(month)+"-"+str(j)
                t = source.open_time.iloc[position]
                records.append(dict(event_id=identity, signal_time=t, decision_time=t+HOUR,
                                    direction=1 if change > 0 else -1, fold=fold,
                                    request_kind="case", mother_id=identity, control_slot=pd.NA,
                                    mother_month=t.strftime("%Y-%m"), matched_support=False))
    cases = pd.DataFrame(records)
    _, controls, _, _, allocation = support_inputs()
    assignments = pd.DataFrame(dict(event_id=cases.event_id, matched_support=False, assigned_controls=0))
    tables, summary = model.build_support(cases, controls.iloc[:0], pd.concat(blocks, ignore_index=True),
                                         assignments, allocation.iloc[:0])
    assert summary["support_values"] == dict(events=80, minimum_fold_events=20,
                                             active_months=12, minimum_fold_months=3)
    assert summary["support_pass"] and all(summary["support_gates"].values())
    assert summary["status"] == "support_pass_requires_separate_outcome_preregistration"
    assert not summary["economic_acceptance"] and not summary["outcomes_read_or_computed"]
    assert not tables["matched_support"].complete_known.any()
    assert summary["coverage"]["complete_known_triples_accepted_cases"] == dict(numerator=0, denominator=80, rate=0.)


def test_support_gate_thresholds_are_fixed_and_have_no_economic_code():
    source = inspect.getsource(model)
    assert "values[\"events\"] >= 80" in source
    assert "values[\"minimum_fold_events\"] >= 12" in source
    assert "values[\"active_months\"] >= 12" in source
    assert "values[\"minimum_fold_months\"] >= 3" in source
    assert model.WAVE_LENGTH == 20 and model.SMOOTH_LENGTH == 5 and model.PRIOR_WARMUP == 100
    assert "classifier_support" not in source and "read_csv" not in source and "read_parquet" not in source
