import json

import pandas as pd
import pytest

import yoyo.evaluation.spike_v6_bb_post as post


def _write_raw(tmp_path, *, changed_b=False):
    raw = tmp_path / "raw"; raw.mkdir()
    stamp = pd.Timestamp("2025-01-02T00:00:00Z")
    ix = pd.date_range("2024-12-25", periods=240, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "atr": 2., "ready": True}, index=ix)
    bars.attrs["minutes"] = 60
    diagnostic = pd.DataFrame({"ready": True}, index=ix); diagnostic.loc[ix[0], "ready"] = False
    signals_cache = pd.DataFrame({"long_signal": False, "short_signal": False}, index=ix)
    pd.to_pickle({"bars": bars, "signals": signals_cache, "data_gap": pd.Series(False, index=ix), "diagnostic": diagnostic}, raw / "T_60m_bb_diagnostic.pkl.gz")
    prep = tmp_path / "input_manifest.json"
    prep.write_text(json.dumps({"streams": [{"symbol": "T", "timeframe_min": 60, "tick": .1}]}))
    (raw / "manifest.json").write_text(json.dumps({"preparation_manifest": {"path": str(prep)}}))
    signal_rows = []
    for variant, admitted, reason in (("A0", True, "raw_baseline"), ("A", True, "admitted"), ("B", True, "admitted"), ("C", False, "width_not_expanding"), ("D", False, "width_not_expanding")):
        signal_rows.append({"symbol": "T", "timeframe_min": 60, "fold": "development", "variant": variant,
                            "side": 1, "signal_bar_open": stamp, "signal_confirm_time": stamp + pd.Timedelta(hours=1),
                            "raw_v6": True, "admitted": admitted, "rejection_reason": reason})
    pd.DataFrame(signal_rows).to_csv(raw / "T_60m_development_A_signals.csv.gz", index=False, compression="gzip")
    trade_rows = []
    for variant in ("A0", "A", "B"):
        trade_rows.append({"symbol": "T", "timeframe_min": 60, "fold": "development", "variant": variant,
                           "side": 1, "signal_bar_open": stamp, "entry_time": stamp + pd.Timedelta(hours=1),
                           "exit_time": stamp + pd.Timedelta(hours=3), "exit_reason": "opposite_v6_next_open",
                           "entry_price": 100., "exit_price": 104., "net_r": 12. if not (changed_b and variant == "B") else 11.,
                           "net_return": .24, "mfe_r": 11., "censored": False})
    pd.DataFrame(trade_rows).to_csv(raw / "T_60m_development_A_trades.csv.gz", index=False, compression="gzip")
    return raw


def test_build_writes_standard_ledgers_metrics_retention_and_variant_control_pairs(tmp_path, monkeypatch):
    raw = _write_raw(tmp_path)
    calls = []

    def fake_controls(cache, targets, *, tick, fold_start, fold_end, seeds):
        calls.append((cache, targets.copy(), tick, tuple(seeds)))
        rows = [{"seed": seed, "target_time": targets.signal_bar_open.iloc[0], "side": 1, "matched": True,
                 "control_net_r": 2., "control_net_return": .04} for seed in seeds]
        return pd.DataFrame(rows), pd.DataFrame()

    monkeypatch.setattr(post, "matched_random_controls", fake_controls)
    output = tmp_path / "post"; post.build(raw, output)
    assert {"signals.csv.gz", "closed_trades.csv.gz", "all_trades.csv.gz", "censored_trades.csv.gz", "metrics_by_timeframe.csv", "metrics_by_side.csv", "retention_by_timeframe.csv", "matched_control_summary.csv"}.issubset({p.name for p in output.iterdir()})
    assert len(calls) == 1 and calls[0][3] == tuple(range(99))
    assert not calls[0][0]["bars"].ready.iloc[0]  # common BB readiness is imposed for controls
    assert set(pd.read_csv(output / "matched_control_pairs.csv.gz").variant) == {"A", "B"}
    retention = pd.read_csv(output / "retention_by_timeframe.csv")
    assert retention.loc[retention.variant.eq("C"), "missed_same_entry_net_r_ge_10"].iloc[0] == 1
    rejected = pd.read_csv(output / "rejected_outcomes_by_reason.csv")
    assert rejected.loc[rejected.variant.eq("D"), "rejected_winners"].iloc[0] == 1
    receipt = json.loads((output / "post_manifest.json").read_text())
    assert receipt["raw_manifest"]["sha256"] and "matched_control_pairs.csv.gz" in receipt["output_hash_exclusions"]


def test_shared_a_and_b_entries_must_have_identical_outcomes(tmp_path, monkeypatch):
    raw = _write_raw(tmp_path, changed_b=True)
    monkeypatch.setattr(post, "matched_random_controls", lambda *args, **kwargs: (pd.DataFrame(), pd.DataFrame()))
    with pytest.raises(AssertionError, match="shared A/B entry has different net_r"):
        post.build(raw, tmp_path / "post")


def test_empty_control_population_writes_empty_control_summaries(tmp_path, monkeypatch):
    raw = _write_raw(tmp_path)
    trades = pd.read_csv(raw / "T_60m_development_A_trades.csv.gz")
    trades.loc[trades.variant.eq("A0")].to_csv(raw / "T_60m_development_A_trades.csv.gz", index=False, compression="gzip")
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("A0 must not enter the control policy")

    monkeypatch.setattr(post, "matched_random_controls", forbidden)
    output = tmp_path / "post"; post.build(raw, output)
    assert not called
    assert pd.read_csv(output / "matched_control_summary.csv").empty


def test_pair_summary_is_weighted_by_pairs_and_has_seed_percentiles():
    pairs = pd.DataFrame({
        "fold": ["development"] * 3, "variant": ["A"] * 3, "timeframe_min": [60] * 3,
        "side": [1, 1, -1], "seed": [0, 1, 0], "matched": [True, True, True],
        "strategy_net_r_difference": [10., 10., 0.], "strategy_net_return_difference": [.1, .1, 0.],
    })
    allside = post._pair_summary(pairs, ["fold", "variant", "timeframe_min"])
    assert allside.mean_net_r_difference.iloc[0] == pytest.approx(20 / 3)
    side = post._pair_summary(pairs, ["fold", "variant", "timeframe_min", "side"])
    assert {"seed_mean_net_r_difference_p05", "seed_mean_net_r_difference_p95"}.issubset(side.columns)


def test_unmatched_controls_without_control_return_columns_are_safe(tmp_path, monkeypatch):
    raw = _write_raw(tmp_path)

    def unmatched(cache, targets, *, seeds, **kwargs):
        return pd.DataFrame([{"seed": seed, "target_time": targets.signal_bar_open.iloc[0], "side": 1, "matched": False} for seed in seeds]), pd.DataFrame()

    monkeypatch.setattr(post, "matched_random_controls", unmatched)
    output = tmp_path / "post"; post.build(raw, output)
    summary = pd.read_csv(output / "matched_control_summary.csv")
    assert (summary.matched_pairs == 0).all()
    assert "control_net_r" in pd.read_csv(output / "matched_control_pairs.csv.gz").columns
