"""Closed signal history: full-cohort arithmetic, timestamps and API behavior."""
import json

import pytest
from fastapi.testclient import TestClient

from yoyo.monitor.signal_analytics import closed_summary, ledger, period_start
from yoyo.monitor.v130_policy import POLICY

NOW = 1792044000000


def event(i, r, *, status=None, end=None, side="long", tf="15m"):
    end = NOW - (10 - i) * 60000 if end is None else end
    return dict(id=str(i), source="live", confirmation="retest", symbol=f"C{i}-USDT-SWAP",
                side=side, timeframe=tf, bar_close_ms=NOW - 86400000,
                performance=dict(status=status or ("profit" if r > 0 else "loss" if r < 0 else "breakeven"),
                                 exit_r=r, current_r=99, peak_r=999, entry_time_ms=end - 60000,
                                 exit_time_ms=end, exit_reason="initial_stop"))


class Store:
    def __init__(self, rows):
        self.rows = rows

    def get_meta(self, key, default):
        assert key == "notification_policy:" + POLICY
        return {"activated_ms": 1}

    def list_events(self, **kwargs):
        assert kwargs["complete"] is True
        return self.rows


def query(rows, **kwargs):
    return ledger(Store(rows), now=NOW, confirmation="retest", outcome="closed", **kwargs)


def test_exact_net_r_arithmetic_does_not_subtract_costs_again():
    rows = [event(i, r) for i, r in enumerate([3, -1, -2, 0, 4])]
    s = closed_summary(rows)
    assert (s["total"], s["wins"], s["losses"], s["breakeven"]) == (5, 2, 2, 1)
    assert s["net_r"] == 4 and s["average_r"] == .8 and s["median_r"] == 0
    assert s["win_rate"] == .4 and s["profit_factor"] == pytest.approx(7 / 3)
    assert s["payoff_ratio"] == pytest.approx(7 / 3)
    assert s["average_win_r"] == 3.5 and s["average_loss_r"] == -1.5
    assert s["best_r"] == 4 and s["worst_r"] == -2
    assert s["max_drawdown_r"] == 3
    assert s["max_consecutive_wins"] == 1 and s["max_consecutive_losses"] == 2
    assert s["average_holding_ms"] == 60000
    assert closed_summary(list(reversed(rows))) == s


def test_simultaneous_exits_are_aggregated_without_fabricating_streak_order():
    rows = [event(1, 2, end=1000), event(2, 5, end=2000), event(3, -1, end=2000), event(4, -3, end=3000)]
    s = closed_summary(rows)
    assert s["max_drawdown_r"] == 3
    assert s["ambiguous_exit_order"] is True
    assert s["max_consecutive_wins"] is None and s["max_consecutive_losses"] is None
    assert closed_summary(list(reversed(rows))) == s


@pytest.mark.parametrize("values", [[], [1, 2], [-1, -2], [0, 0]])
def test_empty_one_sided_and_flat_samples_never_emit_infinity(values):
    s = closed_summary([event(i, r) for i, r in enumerate(values)])
    assert s["payoff_ratio"] is None
    if not any(v < 0 for v in values):
        assert s["profit_factor"] is None
    if not values:
        assert s["net_r"] is None and s["win_rate"] is None and s["max_drawdown_r"] is None
    json.dumps(s, allow_nan=False)


def test_missing_r_and_dates_remain_unknown_and_disable_order_statistics():
    rows = [event(1, 2), event(2, None, status="profit"), event(3, -1)]
    rows[2]["performance"]["exit_time_ms"] = None
    s = closed_summary(rows)
    assert (s["total"], s["measured"], s["missing_r"], s["missing_exit_time"]) == (3, 2, 1, 1)
    assert s["net_r"] == 1 and s["win_rate"] == .5
    assert s["max_drawdown_r"] is None and s["max_consecutive_losses"] is None
    assert s["measured_holding"] == 2
    assert query(rows)["total"] == 3


def test_closed_history_filters_before_pagination_and_retains_both_outcomes():
    rows = [event(i, 1 if i % 2 else -1, end=NOW - i) for i in range(2003)]
    rows += [event(3000, 999, status="active"), event(3001, 999, status="unknown"),
             event(3002, -1, status="profit")]  # Conflicting sign is unknown.
    a, b = query(rows, limit=24), query(rows, offset=24, limit=24, sort="r_desc")
    assert a["total"] == 2003 and len(a["items"]) == 24 and a["has_more"]
    assert a["stats"]["active"] == a["stats"]["unknown"] == 0
    assert a["closed_statistics"] == b["closed_statistics"]
    assert a["closed_statistics"]["net_r"] == -1
    assert a["closed_statistics"]["measured"] == 2003
    assert {row["performance"]["status"] for row in a["items"]} == {"profit", "loss"}


def test_history_period_and_newest_order_use_exit_record_not_signal_start():
    today = period_start(NOW, "today")
    old = event(1, 2, end=today - 1)
    recent = event(2, -1, end=today)
    recent["bar_close_ms"] = today - 86400000  # Opened yesterday, closed today.
    later = event(3, 3, end=today + 1000)
    later["bar_close_ms"] = today - 2 * 86400000
    future = event(4, 5, end=NOW + 1)
    result = query([old, recent, later, future], period="today")
    assert [r["id"] for r in result["items"]] == ["3", "2"]
    assert result["date_basis"] == "exit_record_time"
    assert result["closed_statistics"]["total"] == 2
    assert [r["id"] for r in query([old, recent, later], sort="oldest")["items"]] == ["1", "2", "3"]


def test_breakdowns_reconcile_after_symbol_side_and_timeframe_filters():
    rows = [event(1, 2), event(2, -1, side="short", tf="1H"), event(3, 0)]
    result = query(rows)["closed_statistics"]
    for group in ("by_timeframe", "by_side", "by_exit_reason"):
        assert sum(s["total"] for s in result[group]) == result["total"]
        assert sum(s["net_r"] or 0 for s in result[group]) == result["net_r"]
    filtered = query(rows, side="short", timeframe="1H", search="C2")
    assert filtered["total"] == 1 and filtered["closed_statistics"]["net_r"] == -1


def test_current_signal_api_accepts_closed_history_without_mutating_journal(tmp_path, monkeypatch):
    from yoyo.monitor.server import create_app
    from yoyo.monitor.v130_policy import arm_v130
    from test_v130_notifications import event as admitted_event
    monkeypatch.setattr('yoyo.monitor.telegram.credentials', lambda: None)
    monkeypatch.setattr('yoyo.monitor.bark.credentials', lambda *a: None)
    app = create_app(tmp_path, start_monitor=False)
    app.state.monitor.client.clock = lambda: NOW
    store = app.state.monitor.store
    store.set_meta('migration:spike-v9-reset-v1', {'activated_ms': 0})
    arm_v130(store, 1)
    for row in [event(1, 2), event(2, -1), event(3, 50, status="active")]:
        assert store.upsert_event(admitted_event(close=row['bar_close_ms'], symbol=row['symbol'],
                                                performance=row['performance']))
    before = store.list_events(complete=True)
    with TestClient(app) as client:
        response = client.get("/api/signals", params={"view": "ledger", "confirmation": "retest", "outcome": "closed", "limit": 1})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2 and len(data["items"]) == 1
    assert data["closed_statistics"]["net_r"] == 1
    assert store.list_events(complete=True) == before


def test_curve_is_chronological_complete_and_aggregates_simultaneous_exits():
    rows = [event(1, 2, end=1000), event(2, 5, end=2000), event(3, -1, end=2000), event(4, -3, end=3000)]
    s = query(rows)['closed_statistics']
    curve = s['curve']
    assert curve['status'] == 'ready'
    assert [p['closed'] for p in curve['points']] == [1, 2, 1]
    assert [p['cumulative_r'] for p in curve['points']] == [2, 6, 3]
    assert max(p['drawdown_r'] for p in curve['points']) == s['max_drawdown_r']
    assert curve['points'][-1]['cumulative_r'] == s['net_r']
    assert query(list(reversed(rows)), limit=1)['closed_statistics']['curve'] == curve
    rows[0]['performance']['exit_r'] = None
    assert query(rows)['closed_statistics']['curve'] == {'status':'incomplete','points':[]}
    assert query([])['closed_statistics']['curve'] == {'status':'empty','points':[]}
