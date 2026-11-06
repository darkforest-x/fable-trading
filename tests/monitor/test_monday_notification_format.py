"""A location alert must never look like an executed live entry."""
import math

from yoyo.monitor.monday_fib import notification_text


def event(**changes):
    return dict(symbol="TEST-USDT-SWAP", side="upper", level=1.23456,
                observed_ms=1793544660000, live_entries=[],
                live_thresholds={"live-allin3": .6, "live-wick": .5}, **changes)


def test_below_both_gates_is_a_touch_alert_with_explicit_no_entry():
    text = notification_text(event(live_allin3_score=.4421318), .4379638)
    assert text.splitlines()[0] == "🔔 TEST · 触价提醒"
    assert "live 未开仓" in text and "上沿 1.618 · 点位 1.23456" in text
    assert "11-01 22:51 北京触价" in text
    assert "5m 收盘分 0.4380" in text and "触价前分 0.4421" in text
    assert "live-allin3 · 未开：触价前分 < 0.6" in text
    assert "live-wick · 未开：收盘分 < 0.5" in text
    assert "已模拟开仓" not in text


def test_actual_entries_and_capacity_misses_are_distinct():
    e = event(live_allin3_score=.8)
    e['live_entries'] = [{'name': 'live-wick6x3', 'taken': True, 'note': None},
                         {'name': 'live-allin3', 'taken': False, 'note': 'busy'}]
    text = notification_text(e, .7)
    assert text.splitlines()[0] == "🟢 TEST · live开仓"
    assert "模拟开仓 · 做空" in text
    assert "✓ live-wick6x3 · 已模拟开仓" in text
    assert "— live-allin3 · 未开：已有持仓" in text
    assert "live 未开仓" not in text


def test_only_capacity_shadow_is_not_an_entry():
    e = event(live_allin3_score=.8)
    e['live_entries'] = [{'name': 'live-allin3', 'taken': False, 'note': 'no_slot'}]
    text = notification_text(e, .3)
    assert text.splitlines()[0] == "🟠 TEST · live未开"
    assert "live 未开仓" in text and "未开：满仓" in text
    assert "已模拟开仓" not in text


def test_no_entry_above_threshold_does_not_invent_a_specific_rule_failure():
    text = notification_text(event(live_allin3_score=.7), .8)
    assert "live 未开仓" in text
    assert "live-allin3 · 未开：无入场记录" in text
    assert "live-wick · 未开：无入场记录" in text
    assert "满仓" not in text and "其他条件未通过" not in text


def test_rounding_cannot_turn_a_rejected_score_into_the_displayed_gate():
    text = notification_text(event(live_allin3_score=.599999), .499999)
    assert "触价前分 0.599999" in text and "5m 收盘分 0.499999" in text
    text = notification_text(event(live_allin3_score=.6), math.nextafter(.4, 1))
    assert "5m 收盘分 0.4000\n" not in text
    assert "触价前分 0.6000" in text and "live-allin3 · 未开：触价前分 <" not in text


def test_unavailable_scores_are_explicit_without_false_threshold_failure():
    text = notification_text(event(live_allin3_score=float('nan')), None)
    assert "5m 收盘分 —" in text and "触价前分 —" in text
    assert "live-allin3 · 未开：评分暂缺" in text
    assert "live-wick · 未开：评分暂缺" in text
    assert "nan" not in text and "分 <" not in text
