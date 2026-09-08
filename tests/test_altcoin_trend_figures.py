"""Synthetic-only selection, chronology and portable chart delivery checks."""
from pathlib import Path
import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from yoyo.data.altcoin_features import build_altcoin_features
from yoyo.evaluation.altcoin_accounting import evaluate_events
from yoyo.evaluation.altcoin_trend_figures import (
    MAX_CANDLES, NOTICE, build_gallery, render_event, select_examples, sha,
)
from yoyo.evaluation.imacd_formation_research import aggregate


def selection_row(net_r, *, symbol="SOPH", minutes=60, i=380,
                  cohort="owner_illustration", arm="base", side=1, fold="recent_test"):
    return dict(event_id=f"{symbol}_{minutes}_{arm}_{i}", symbol=symbol,
                minutes=minutes, signal_i=i, cohort=cohort, arm=arm,
                side=side, fold=fold, net_r=net_r)


def synthetic_bars(n=540, minutes=60, side=1, sustained=False):
    bars = pd.DataFrame(
        {"open": 100., "high": 101., "low": 99., "close": 100., "volume": 10.},
        index=pd.date_range("2024-01-01", periods=n, freq=f"{minutes}min", tz="UTC"),
    )
    bars.iloc[380, :4] = [100, 141, 99, 140] if side == 1 else [100, 101, 59, 60]
    if sustained:
        for i in range(381, n):
            previous = bars.close.iloc[i - 1]
            close = previous + side * .05
            bars.iloc[i, :4] = [previous, max(previous, close) + .1,
                                min(previous, close) - .1, close]
    return bars


def saved_event(bars, *, minutes=60, side=1):
    features = build_altcoin_features(bars)
    assert features.release_side.iloc[380] == side
    ledger = evaluate_events(bars, features, [380], [side], first_i=0, last_i=len(bars) - 1)
    event = ledger.iloc[0].to_dict()
    assert event["valid"]
    event.update(symbol="SOPH", minutes=minutes, arm="base", cohort="owner_illustration",
                 fold="recent_test", event_id=f"synthetic_{minutes}_{side}",
                 decision_close_time=event["decision_time"], portfolio_selected=False)
    return event, features


def test_each_owner_group_gets_best_worst_lower_median_deterministically():
    rows = []
    for symbol in ("SOPH", "USELESS"):
        for minutes in (60, 240):
            for i, net in enumerate((-8., -3., 2., 9., np.nan)):
                rows.append(selection_row(net, symbol=symbol, minutes=minutes, i=380 + i))
    original = pd.DataFrame(rows)
    result = select_examples(original)
    assert result == select_examples(original.sample(frac=1, random_state=17))
    assert len(result) == 12
    assert {item["event"]["net_r"] for item in result} == {-8, -3, 9}
    assert all(item["selected_with_outcome"] for item in result)
    assert all(len(item["roles"]) == 1 for item in result)
    assert original.net_r.isna().sum() == 4


def test_sparse_all_negative_groups_are_deduplicated_without_fabricated_zero():
    rows = pd.DataFrame([selection_row(-2.), selection_row(np.nan, i=381),
                         selection_row(-5., symbol="USELESS"),
                         selection_row(-3., symbol="USELESS", i=381)])
    chosen = select_examples(rows)
    soph = [item for item in chosen if item["event"]["symbol"] == "SOPH"]
    assert len(soph) == 1 and len(soph[0]["roles"]) == 3
    assert len(chosen) == 3 and max(item["event"]["net_r"] for item in chosen) == -2
    assert select_examples(rows, phase="validation") == []


def test_gallery_includes_largest_winner_omitted_by_prior_week_volatility():
    rows=[selection_row(40,symbol='ALT',cohort='all_core',i=400),
          selection_row(10,symbol='ALT',cohort='all_core',i=500),
          selection_row(10,symbol='ALT',cohort='high_vol',i=500),
          selection_row(-2,symbol='ALT',cohort='all_core',i=600)]
    result=select_examples(pd.DataFrame(rows))
    omitted=[x for x in result if any('高波动筛选遗漏' in role for role in x['roles'])]
    assert len(omitted)==1 and omitted[0]['event']['signal_i']==400
    assert omitted[0]['selected_with_outcome'] is True


def test_high_vol_selection_and_missed_anchor_require_actual_comparison_metadata():
    base = [selection_row(net, symbol="ZEC", i=i, cohort="high_vol")
            for i, net in ((380, 12), (381, 8), (382, -4))]
    comparison = selection_row(2, symbol="ZEC", i=380, cohort="high_vol", arm="gate_rvol2")
    misleading = [selection_row(100, symbol="BTC", cohort="high_vol"),
                  selection_row(200, symbol="ETH", cohort="all_core"),
                  selection_row(300, symbol="ZEC", cohort="all_core", i=399)]
    lock = {"selections": [{"minutes": 60, "selected_arm": "gate_rvol2"}]}
    result = select_examples(pd.DataFrame(base + [comparison] + misleading), selection=lock)
    assert len(result) == 4
    omitted = [item for item in result if any("高波动筛选遗漏" in role for role in item["roles"])]
    assert len(omitted) == 1 and omitted[0]["event"]["signal_i"] == 399
    missed = [item for item in result if any("未保留" in role for role in item["roles"])]
    assert len(missed) == 1 and missed[0]["event"]["signal_i"] == 381
    assert "不等于漏掉整段行情" in missed[0]["roles"][0]
    absent_arm = select_examples(pd.DataFrame(base + misleading), selection=lock)
    assert len(absent_arm) == 3
    assert not any("未保留" in role for item in absent_arm for role in item["roles"])


def test_nonfinite_or_empty_returns_never_become_diagnostics():
    rows = pd.DataFrame([selection_row(np.nan), selection_row(np.inf, i=381),
                         selection_row(-np.inf, i=382)])
    assert select_examples(rows) == []
    with pytest.raises(ValueError, match="selection columns"):
        select_examples(pd.DataFrame({"net_r": [3]}))


@pytest.mark.parametrize("minutes,side", [(60, 1), (60, -1), (240, 1), (240, -1)])
def test_render_verified_long_and_short_preserves_economics_and_future_clock(tmp_path, minutes, side):
    bars = synthetic_bars(minutes=minutes, side=side)
    event, features = saved_event(bars, minutes=minutes, side=side)
    path = tmp_path / f"example_{minutes}_{side}.png"
    receipt = render_event(event, bars, path, ["合成测试 · 事后选例"], features=features)
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert receipt["png_sha256"] == sha(path)
    assert receipt["history_bars"] == 100 and receipt["future_bars"] >= 72
    assert receipt["candle_count"] <= MAX_CANDLES
    assert receipt["future_first_open"] == pd.Timestamp(event["decision_time"]).isoformat()
    assert receipt["net_r"] == event["net_r"] and receipt["mfe_r"] == event["mfe_r"]
    assert receipt["exit_visible"] and not receipt["training_eligible"]
    assert receipt["portfolio_selected"] is False and not receipt["original_model_input"]
    assert not receipt["future_visible_at_decision"] and plt.get_fignums() == []


def test_capped_panorama_discloses_exit_outside_window_and_keeps_full_ledger_return(tmp_path):
    bars = synthetic_bars(n=1000, sustained=True)
    event, features = saved_event(bars)
    assert event["exit_i"] == len(bars) - 1 and event["censored"]
    receipt = render_event(event, bars, tmp_path / "capped.png", ["合成延伸趋势"], features=features)
    assert receipt["candle_count"] == MAX_CANDLES
    assert not receipt["exit_visible"] and receipt["window_capped"] and receipt["censored"]
    assert receipt["net_r"] == event["net_r"]
    assert pd.Timestamp(receipt["last_close"]) < pd.Timestamp(receipt["exit_time"])


@pytest.mark.parametrize("alteration,match", [
    ({"arm": "ma21"}, "baseline"),
    ({"decision_close_time": "2024-01-01T00:00:00Z"}, "clock"),
    ({"entry_price": 200.}, "next-open"),
    ({"initial_stop": 1.}, "frozen risk"),
    ({"net_bp": 1000000.}, "returns disagree"),
    ({"net_r": np.nan}, "finite"),
    ({"mfe_r": -1.}, "negative"),
    ({"exit_time": "2024-01-01T00:00:00Z"}, "exit clock"),
])
def test_inconsistent_ledger_cannot_produce_authoritative_chart(tmp_path, alteration, match):
    bars = synthetic_bars()
    event, features = saved_event(bars)
    event.update(alteration)
    with pytest.raises(ValueError, match=match):
        render_event(event, bars, tmp_path / "bad.png", [], features=features)
    assert not (tmp_path / "bad.png").exists()


def fixture_files(tmp_path):
    raw = pd.DataFrame(
        {"open": 100., "high": 101., "low": 99., "close": 100., "volume": 10.},
        index=pd.date_range("2024-01-01", periods=540 * 4, freq="15min", tz="UTC"),
    )
    raw.iloc[380 * 4 + 3, :4] = [100, 141, 99, 140]
    bars = aggregate(raw, 60)
    event, _ = saved_event(bars)
    history_csv = tmp_path / "synthetic_soph_15m.csv"
    raw.assign(ts=raw.index.asi8 // 1_000_000).to_csv(history_csv, index=False)
    history_path = tmp_path / "history_manifest.json"
    history_path.write_text(json.dumps({
        "end_exclusive": (raw.index[-1] + pd.Timedelta(minutes=15)).isoformat(),
        "symbols": [{"symbol": "SOPH", "status": "complete",
                     "output_path": str(history_csv), "output_sha256": sha(history_csv)}],
    }))
    events_path = tmp_path / "events_all.csv.gz"
    pd.DataFrame([event]).to_csv(events_path, index=False)
    return events_path, history_path, history_csv


def test_gallery_is_portable_future_only_hashed_and_sparse_examples_are_deduplicated(tmp_path):
    events_path, history_path, history_csv = fixture_files(tmp_path)
    out = tmp_path / "gallery"
    result = build_gallery(events_path, history_path, out)
    assert result["selected_count"] == 1
    assert len(list((out / "future_only").glob("*.png"))) == 1
    assert len(result["examples"][0]["roles"]) == 3
    html = (out / "index.html").read_text()
    assert NOTICE in html and "future_only/" in html
    assert "https://" not in html and "http://" not in html
    assert "不是随机样本" in html and "候选事件" in html
    assert json.loads((out / "manifest.json").read_text()) == result
    assert result["input_sha256"]["events"] == sha(events_path)
    assert result["input_sha256"]["history_manifest"] == sha(history_path)
    assert result["source_histories"]["SOPH_60"]["sha256"] == sha(history_csv)
    assert not result["training_eligible"] and not result["model_inference"]
    assert Path(result["examples"][0]["path"]).parts[0] == "future_only"
    with pytest.raises(ValueError, match="already exists"):
        build_gallery(events_path, history_path, out)


def test_empty_phase_is_explicit_and_no_other_period_is_substituted(tmp_path):
    events_path, history_path, _ = fixture_files(tmp_path)
    out = tmp_path / "empty"
    result = build_gallery(events_path, history_path, out, phase="validation")
    assert result["selected_count"] == 0 and result["source_histories"] == {}
    assert list((out / "future_only").iterdir()) == []
    assert "没有补零或换成其他时段" in (out / "index.html").read_text()


def test_history_digest_mismatch_fails_before_drawing(tmp_path):
    events_path, history_path, history_csv = fixture_files(tmp_path)
    history_csv.write_text(history_csv.read_text() + "\n")
    out = tmp_path / "bad_history"
    with pytest.raises(ValueError, match="SHA mismatch"):
        build_gallery(events_path, history_path, out)
    assert list((out / "future_only").glob("*.png")) == []
    assert not (out / "manifest.json").exists()
