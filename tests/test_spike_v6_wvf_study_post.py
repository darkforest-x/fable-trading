import pandas as pd
import numpy as np
import pytest

from yoyo.evaluation.spike_v6_wvf_study_post import evaluate_single_control, matched_random_controls
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, simulate_v6_variant


def test_controls_are_fixed_seed_causal_matches_and_not_account_returns():
    ix = pd.date_range("2025-01-01", periods=130, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "atr": 2., "ready": True}, index=ix)
    bars.attrs["minutes"] = 60
    bars.loc[ix[120:129], "ready"] = False
    bars.loc[ix[125], "ready"] = True
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=ix)
    signals.loc[ix[127], "short_signal"] = True
    targets = pd.DataFrame({"signal_bar_open": [ix[127]], "side": [1], "net_r": [1.0], "net_return": [.02]})
    cache = {"bars": bars, "signals": signals, "data_gap": pd.Series(False, index=ix)}
    one, summary = matched_random_controls(cache, targets, tick=.1, fold_start=ix[120], fold_end=ix[-1] + pd.Timedelta(hours=1), seeds=(7,))
    assert len(one) == 1 and one.matched.iloc[0]
    assert summary.match_rate.iloc[0] == 1.0
    assert "net_return_difference" in one and "account" not in one.columns


def _parity_frame(side, mode):
    ix = pd.date_range("2025-01-01", periods=12, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "atr": 2., "ready": True}, index=ix)
    bars.attrs["minutes"] = 60
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=ix)
    candidate = 5
    # A preceding opposite event must remain unable to open because admission is false.
    signals.iloc[3, signals.columns.get_loc("short_signal" if side == 1 else "long_signal")] = True
    gap = pd.Series(False, index=ix)
    if mode == "initial":
        bars.iloc[6, bars.columns.get_indexer(["low" if side == 1 else "high"])] = 95 if side == 1 else 105
    elif mode == "trailing":
        bars.iloc[6, bars.columns.get_indexer(["high", "low", "close"])] = ([110, 99, 109] if side == 1 else [101, 90, 91])
        bars.iloc[7, bars.columns.get_indexer(["high", "low", "close"])] = ([102, 100, 101] if side == 1 else [100, 98, 99])
    elif mode == "reverse":
        signals.iloc[7, signals.columns.get_loc("short_signal" if side == 1 else "long_signal")] = True
        bars.iloc[8, bars.columns.get_loc("open")] = 103 if side == 1 else 97
    elif mode == "gap":
        gap.iloc[7] = True
    return bars, signals, gap, candidate, ix


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("mode", ["initial", "trailing", "reverse", "gap", "fold"])
def test_single_control_matches_candidate_only_reference_replay(side, mode):
    bars, raw, gap, candidate, ix = _parity_frame(side, mode)
    reference = raw.copy()
    reference.iloc[candidate, :] = False
    reference.iloc[candidate, reference.columns.get_loc("long_signal" if side == 1 else "short_signal")] = True
    admission = pd.Series(False, index=ix); admission.iloc[candidate] = True
    fold_end = ix[7] if mode == "fold" else ix[-1] + pd.Timedelta(hours=1)
    cache = {"bars": bars, "signals": raw, "data_gap": gap}
    actual = evaluate_single_control(cache, candidate, side, tick=.1, fold_start=ix[candidate], fold_end=fold_end)
    _, replay = simulate_v6_variant(bars, reference, admission=admission, variant="reference", data_gap=gap,
                                    spec=ExecutionSpec(tick=.1))
    expected = replay.iloc[0].to_dict()
    assert actual is not None
    assert bool(actual.get("censored", False)) == bool(expected["censored"])
    if actual.get("censored", False):
        return
    assert actual["exit_time"] == expected["exit_time"]
    assert actual["exit_price"] == pytest.approx(expected["exit_price"])
    assert actual["net_r"] == pytest.approx(expected["net_r"])
