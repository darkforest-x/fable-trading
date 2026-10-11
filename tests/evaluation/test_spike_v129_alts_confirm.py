"""Mechanics of exp-spike-v129-alts-confirm-20261011-v1."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v129_alts_confirm as ac


def test_config_matches_the_eth_selection():
    cfg = ac.config()
    assert cfg["selected"] == {"5": "K3_m0.5", "15": "K12_m0.5"} and cfg["timeframes"] == [5, 15]
    assert len(cfg["expected_universe"]) == cfg["universe_size"] == 20
    assert "ETHUSDT" not in cfg["expected_universe"] and "BTCUSDT" not in cfg["expected_universe"]


def arrays(closes, lows=None, highs=None):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + 0.1 if highs is None else np.asarray(highs, float)
    l = np.minimum(o, c) - 0.1 if lows is None else np.asarray(lows, float)
    return o, h, l, c


def test_long_confirms_at_first_close_beyond_threshold_and_uses_new_risk():
    # entry 100 at bar 0 open, stop 98 (risk 2); +0.5R = 101 first closed at bar 2; enter bar 3 open 101.2
    o, h, l, c = arrays([100.2, 100.6, 101.2, 101.5, 103, 104, 105])
    net_r, e1 = ac._confirm(o, h, l, c, 1, 0, 6, 100.0, 98.0, 105.0, "trailing_stop", 12, 0.5)
    assert e1 == o[3] == c[2]
    assert np.isclose(net_r, ((105 / e1 - 1) - 0.002) / ((e1 - 98) / e1))


def test_stop_touch_or_timeout_or_early_exit_skips():
    o, h, l, c = arrays([100.2, 99.0, 101.5, 102])
    l[1] = 97.9  # stop printed before confirmation
    assert np.isnan(ac._confirm(o, h, l, c, 1, 0, 3, 100.0, 98.0, 102.0, "trailing_stop", 12, 0.5)[0])
    o, h, l, c = arrays([100.1, 100.2, 100.3, 100.4, 100.5])
    assert np.isnan(ac._confirm(o, h, l, c, 1, 0, 4, 100.0, 98.0, 100.5, "trailing_stop", 3, 0.5)[0])  # not within 3
    o, h, l, c = arrays([100.2, 101.5, 102, 103])
    assert np.isnan(ac._confirm(o, h, l, c, 1, 0, 1, 100.0, 98.0, 100.5, "initial_stop", 12, 0.5)[0])  # exited first
    o, h, l, c = arrays([100.2, 101.5, 102, 103])
    assert np.isnan(ac._confirm(o, h, l, c, 1, 0, 2, 100.0, 98.0, 101.5, "opposite_v6_next_open", 12, 0.5)[0])


def test_short_mirror():
    o, h, l, c = arrays([99.8, 98.9, 98.5, 97])
    net_r, e1 = ac._confirm(o, h, l, c, -1, 0, 3, 100.0, 102.0, 97.0, "trailing_stop", 12, 0.5)
    assert e1 == o[2]
    assert np.isclose(net_r, ((1 - 97 / e1) - 0.002) / ((102 - e1) / e1))


def test_per_signal_counts_skips_as_zero():
    s = pd.DataFrame({"net_r": [1.0, -1.0], "control_net_r": [0.0, 0.5], "sig_K3_m0.5": [0.4, np.nan],
                      "ctl_K3_m0.5": [np.nan, -0.2]})
    got = ac.per_signal(s, "K3_m0.5")
    assert list(got.confirmed) == [0.4, 0.0] and list(got.control_confirmed) == [0.0, -0.2]
    assert list(got.entered) == [True, False]
