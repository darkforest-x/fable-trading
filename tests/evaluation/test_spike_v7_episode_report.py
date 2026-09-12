"""Reject invalid research runs and distinguish exact realized tails from MFE."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.spike_v7_episode_report import collect, cluster_effect, summarize
from yoyo.evaluation.spike_v7_episode_study import trade_metrics


def test_invalidated_or_incomplete_run_cannot_be_published(tmp_path):
    (tmp_path / "INVALIDATED.json").write_text("{}")
    with pytest.raises(ValueError, match="invalidated"):
        collect(tmp_path)
    (tmp_path / "INVALIDATED.json").unlink()
    (tmp_path / "manifest.json").write_text(json.dumps({"complete": False, "streams": 1803}))
    with pytest.raises(ValueError, match="all 3531"):
        collect(tmp_path)


def test_cluster_statistics_do_not_treat_duplicated_venues_as_new_assets():
    values = pd.DataFrame({"asset": ["A", "B", "C"], "delta": [.05, .02, -.01]})
    original = cluster_effect(values)
    duplicated = cluster_effect(pd.concat([values, values], ignore_index=True))
    assert original == duplicated
    assert original["assets"] == 3
    assert original["delta"] == pytest.approx(.02)


def test_censored_and_mfe_10r_are_not_realized_10r_winners():
    t = pd.DataFrame({"censored": [False, False, True], "net_return": [.2, -.03, .5],
                      "gross_return": [.202, -.028, .502], "net_r": [11., -1., 20.], "mfe_r": [15., 12., 25.]})
    result = trade_metrics(t)
    assert result["closed"] == 2 and result["censored"] == 1
    assert result["realized_10r"] == 1 and result["mfe_10r"] == 2
    assert result["net_r_sum"] == 10


def test_same_episode_later_entry_does_not_count_as_exact_tail_retention(tmp_path):
    accounts, events, trades, ret, controls = [], [], [], [], []
    for arm in ["baseline", "first", "first_break"]:
        for period in ["development", "validation"]:
            key = dict(arm=arm, period=period, timeframe_min=60, stream_key="a", asset="A")
            accounts.append(dict(key, valid=True, net_return=.01, max_close_drawdown=.02))
            trade = pd.DataFrame([dict(key, censored=False, net_return=.2, gross_return=.202, net_r=11., mfe_r=15., side=1)])
            trades.append(trade)
            events.append(dict(key, **trade_metrics(trade), signals=5 if arm == "baseline" else 2, fallback_signals=0))
            controls.append(dict(key, matched=True, net_return_difference=.01))
            if arm != "baseline":
                ret.append(dict(key, net_r=11., net_return=.2, exact_retained=False, same_episode_entry=True, reason="episode_consumed"))
    result = summarize(dict(accounts=pd.DataFrame(accounts), events=pd.DataFrame(events),
                            trades=pd.concat(trades), retention=pd.DataFrame(ret), controls=pd.DataFrame(controls)), tmp_path)
    summary = result["retention_summary"]
    assert summary.base_winners10.eq(1).all()
    assert summary.kept_winners10.eq(0).all()
    assert summary.episode_winners10.eq(1).all()
    assert result["event_summary"].loc[lambda x:x.arm.ne("baseline"), "signal_reduction"].eq(.6).all()
