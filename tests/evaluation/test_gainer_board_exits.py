import numpy as np
import pandas as pd

from yoyo.evaluation.gainer_board_exits import fresh_events, path_exit, quiet_hours, ranks


def sym(o, h, l, c):
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    return dict(o=o, h=h, l=l, c=c, frame=pd.DataFrame(dict(open=o, high=h, low=l, close=c)))


def flat(n=10, px=100.):
    return [px] * n


def test_ranks_break_ties_by_symbol_and_fresh_needs_24_off_board_closes():
    r = pd.DataFrame({"B": [.1, .1], "A": [.1, .2]})
    assert ranks(r).iloc[0].to_dict() == {"A": 1., "B": 2.}
    rank = pd.DataFrame({"A": [5.] * 30 + [20.] * 30 + [3.] * 5})
    f = fresh_events(rank, 10, 24)
    assert not f.A.iloc[:30].any()  # first hours lack 24 prior closes
    assert f.A.iloc[60] and f.A.sum() == 1


def test_time_exit_uses_next_open_and_close_after_h_bars():
    s = sym(flat(10), flat(10), flat(10), [100, 100, 101, 102, 103, 104, 100, 100, 100, 100])
    s["o"][1] = 100.
    x = path_exit(s, 0, "long", dict(family="time", hours=3), None, .002)
    assert np.isclose(x["gross"], 102 / 100 - 1) and x["held"] == 3
    y = path_exit(s, 0, "short", dict(family="time", hours=3), None, .002)
    assert np.isclose(y["gross"], 1 - 102 / 100)
    assert np.isnan(path_exit(s, 0, "long", dict(family="time", hours=20), None, .002)["net"])


def test_tpsl_same_bar_is_stop_first_with_upper_bound():
    h, l = flat(10, 101), flat(10, 99)
    h[2], l[2] = 106, 96  # both 5% barriers inside one bar
    s = sym(flat(10), h, l, flat(10))
    x = path_exit(s, 0, "long", dict(family="tpsl", tp=5, sl=3, hours=5), None, 0.)
    assert x["ambiguous"] and np.isclose(x["gross"], -.03) and np.isclose(x["upper"], .05)


def test_trailing_stop_uses_only_earlier_highs_and_fills_gaps_at_open():
    o = [100, 100, 104, 108, 109, 100, 100, 100, 100, 100]
    h = [100, 105, 110, 110, 110, 100, 100, 100, 100, 100]
    l = [100, 99, 103, 107, 104, 100, 100, 100, 100, 100]
    s = sym(o, h, l, o)
    x = path_exit(s, 0, "long", dict(family="trail", trail=5, hours=8), None, 0.)
    # Best before bar 4 is 110 -> stop 104.5; bar 4 low 104 hits it.
    assert x["held"] == 4 and np.isclose(x["gross"], 104.5 / 100 - 1)
    o2 = list(o)
    o2[4] = 101  # gap below the 104.5 stop
    y = path_exit(sym(o2, h, l, o2), 0, "long", dict(family="trail", trail=5, hours=8), None, 0.)
    assert np.isclose(y["gross"], 101 / 100 - 1)


def test_board_exit_leaves_at_open_after_first_off_board_close():
    s = sym([100, 100, 101, 102, 103, 104, 105, 106], flat(8, 110), flat(8, 90), flat(8))
    rank = np.array([3, 4, 8, 15, 2, 2, 2, 2], float)
    x = path_exit(s, 0, "long", dict(family="board", n=10, hours=6), rank, 0.)
    assert x["held"] == 3 and np.isclose(x["gross"], 103 / 100 - 1)


def test_quiet_hours_end_before_rank_window_and_skip_board_hours():
    rank = np.full(400, 30.)
    rank[250:260] = 5
    elig = np.ones(400, bool)
    got = quiet_hours(rank, elig, 400, (168, 72), 500, np.random.default_rng(1))
    assert min(got) >= 232 and max(got) <= 328 and not set(range(250, 260)) & set(got)
