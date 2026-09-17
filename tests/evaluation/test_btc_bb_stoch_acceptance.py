"""The holdout gate, the scoring boundary and the frozen pass rule."""
import json

import pandas as pd
import pytest

from yoyo.data.authorized_holdout_window import read_authorized_window
from yoyo.evaluation import btc_bb_stoch_acceptance as acceptance


def grant(**overrides):
    base = dict(ledger_index=2, experiment_id="exp-btc-bb-stoch-holdout-acceptance-20260917-v1",
                window_start="2026-02-28T16:00:00Z", window_end="2026-08-31T16:00:00Z",
                evaluation_start="2026-05-04T00:00:00Z")
    base.update(overrides)
    return base


def test_reader_requires_a_recorded_ledger_entry_naming_this_experiment(tmp_path):
    empty = tmp_path / "x.csv"
    empty.write_text("ts,open,high,low,close,volume,open_time\n")
    with pytest.raises(ValueError, match="no entry"):
        read_authorized_window(empty, 5, grant(ledger_index=99))
    with pytest.raises(ValueError, match="does not name this experiment"):
        read_authorized_window(empty, 5, grant(experiment_id="exp-not-registered-v1"))
    with pytest.raises(ValueError, match="authorization needs"):
        read_authorized_window(empty, 5, {"ledger_index": 2})


def test_reader_refuses_a_window_that_is_not_an_acceptance_run(tmp_path):
    empty = tmp_path / "x.csv"
    empty.write_text("ts,open,high,low,close,volume,open_time\n")
    # Before the boundary there is a frozen reader with a hard refusal; this
    # module must not become a way around it.
    with pytest.raises(ValueError, match="use read_prefix before the boundary"):
        read_authorized_window(empty, 5, grant(evaluation_start="2026-04-01T00:00:00Z"))
    with pytest.raises(ValueError, match="inside the authorized window"):
        read_authorized_window(empty, 5, grant(evaluation_start="2026-09-15T00:00:00Z"))


def test_frozen_reader_still_refuses_the_holdout():
    from yoyo.data.spike_fanshen_prefix import read_prefix
    with pytest.raises(ValueError, match="holdout"):
        read_prefix("unused", 5, "2026-05-04T00:00Z")


def test_pass_rule_needs_all_three_and_is_not_satisfied_by_two():
    cfg = dict(pass_criteria=dict(signflip_p_below=0.01))
    good = dict(stats=dict(net_r=5.0), control=dict(excess_mean_net_r=0.05, p=0.004))
    assert acceptance.verdict(good, cfg)["passed"]
    for broken in (dict(stats=dict(net_r=-1.0), control=dict(excess_mean_net_r=0.05, p=0.004)),
                   dict(stats=dict(net_r=5.0), control=dict(excess_mean_net_r=-0.01, p=0.004)),
                   dict(stats=dict(net_r=5.0), control=dict(excess_mean_net_r=0.05, p=0.02)),
                   dict(stats=dict(net_r=5.0), control=dict(excess_mean_net_r=0.05, p=None))):
        got = acceptance.verdict(broken, cfg)
        assert not got["passed"] and sum(got["checks"].values()) == 2


def test_rejected_grid_candidates_are_not_among_the_scored_arms():
    cfg = json.loads((acceptance.EXP / "config.json").read_text())
    assert set(cfg["arms"]) == {"v2_baseline", "be_cost"}
    for key in cfg["rejected_candidates_not_scored"]:
        assert key not in cfg["arms"]
    assert cfg["holdout_consumed"] is True and cfg["parameter_search"] is False
    assert pd.Timestamp(cfg["holdout_authorization"]["evaluation_start"]) == pd.Timestamp("2026-05-04T00:00:00Z")
