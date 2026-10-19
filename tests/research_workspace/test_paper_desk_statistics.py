"""Account metrics count complete position lifecycles and all settlement fees."""
from decimal import Decimal as D

import pytest

from yoyo.research_workspace import paper_desk as book
from yoyo.research_workspace.paper_desk_analytics import history, trade_statistics


def position(key, remaining='0.00', opened=1000, closed=61000):
    return dict(id=key, remaining=remaining, opened_ms=opened, closed_ms=closed)


def fill(key, gross, fee, quantity='1', price='100'):
    return dict(position_id=key, gross_pnl=gross, fee=fee, quantity=quantity, price=price)


def stats(positions, fills):
    return trade_statistics(dict(initial_cash='1000'), positions, fills)


def test_complete_lifecycle_merges_additions_and_partial_closes_and_includes_every_fee():
    positions = [position('a'), position('b'), position('flat'), position('open', '1', closed=None)]
    fills = [fill('a','0','.1','2'), fill('a','10','.055','1','110'),
             fill('a','0','.18','3','120'), fill('a','60','.26','4','130'),
             fill('b','0','.05'), fill('b','-20','.04','1','80'),
             fill('flat','0','.05'), fill('flat','.1','.05'),
             fill('open','0','.05'), fill('open','8','.05')]
    s = stats(positions, fills)
    assert (s['closed_positions'], s['wins'], s['losses'], s['breakeven']) == (3,1,1,1)
    assert D(s['win_rate']) == D(1)/3*100
    assert D(s['closed_net']) == D('49.315')
    assert D(s['total_net']) == D('57.215')
    assert D(s['total_fees']) == D('.885')
    assert D(s['return_percent']) == D('5.7215')
    assert D(s['average_win']) == D('69.405')
    assert D(s['average_loss']) == D('-20.09')
    assert D(s['payoff_ratio']) == D('69.405') / D('20.09')
    assert D(s['profit_factor']) == D(s['payoff_ratio'])
    assert D(s['best_trade']) == D('69.405') and D(s['worst_trade']) == D('-20.09')
    assert s['average_holding_ms'] == 60000


def test_fees_can_turn_a_gross_winner_into_a_net_loser_without_double_counting_clearance():
    s = stats([position('a')], [fill('a','0','.05'), dict(fill('a','.04','.06'),clearance_fee='.01')])
    assert s['wins'] == 0 and s['losses'] == 1
    assert D(s['total_fees']) == D('.11')  # Clearance is already included in fill.fee.
    assert D(s['closed_net']) == D('-.07')
    assert D(s['profit_factor']) == 0 and s['payoff_ratio'] is None


@pytest.mark.parametrize('gross,win,loss,flat', [('10',1,0,0),('-10',0,1,0),('0',0,0,1)])
def test_one_sided_or_flat_results_do_not_invent_ratios(gross,win,loss,flat):
    s = stats([position('a')], [fill('a',gross,'0')])
    assert (s['wins'],s['losses'],s['breakeven']) == (win,loss,flat)
    assert s['payoff_ratio'] is None
    assert s['profit_factor'] == ('0' if loss else None)
    assert D(s['win_rate']) == (100 if win else 0)


def test_empty_and_unfinished_positions_have_no_win_rate_or_holding_time():
    for positions,fills in [([],[]),([position('a','1',closed=None)],[fill('a','0','.05')])]:
        s = stats(positions,fills)
        assert s['closed_positions'] == 0 and s['win_rate'] is None
        assert s['best_trade'] is None and s['worst_trade'] is None
        assert s['average_holding_ms'] is None and s['payoff_ratio'] is None


def test_balance_drawdown_uses_the_running_peak_and_can_exceed_one_hundred_percent():
    fills = [fill('a','0','10'),fill('a','110','0'),fill('b','-550','0'),fill('b','550','0')]
    assert D(stats([position('a'),position('b')],fills)['max_balance_drawdown_percent']) == 50
    fills.append(fill('c','-1650','0'))
    assert D(stats([position('a'),position('b'),position('c')],fills)['max_balance_drawdown_percent']) == 150


def test_history_statistics_are_account_scoped_lifetime_and_reconcile_cash(tmp_path):
    desk = book.PaperDesk(tmp_path)
    a = desk.create_account('Stats','1000','stats-account-create')['id']
    other = desk.create_account('Empty','500','stats-other-create')['id']
    def quote(price):
        now=book.clock_ms()
        return desk.save_quote(dict(symbol='BTC-USDT-SWAP',bid=price,ask=price,source_ms=now,received_ms=now))
    q=quote('100');p=desk.open(a,'long','2',q['id'],'','stats-open-request',{},100)['position']
    desk.close(p['id'],'1',quote('110')['id'],'stats-partial-close')
    assert history(desk,a)['statistics']['closed_positions'] == 0
    q=quote('120');desk.open(a,'long','3',q['id'],'','stats-add-request',{'BTC-USDT-SWAP':q},100)
    desk.close(p['id'],'4',quote('130')['id'],'stats-final-close')
    current=history(desk,a);old_month=history(desk,a,'2020-01')
    assert current['statistics'] == old_month['statistics']
    assert current['statistics']['closed_positions'] == 1
    assert D(current['statistics']['closed_net']) == D('69.405')
    assert D(current['statistics']['total_net']) == D(current['total_net'])
    assert D(current['statistics']['total_net']) == D(desk.export(a)['account']['cash'])-1000
    assert D(current['statistics']['turnover']) == 1190
    assert history(desk,other)['statistics']['closed_positions'] == 0
    assert D(history(desk,other)['statistics']['total_fees']) == 0


def test_drawdown_sees_every_event_even_when_chart_points_would_be_compressed():
    fills=[fill('a','0','0') for _ in range(1500)]
    fills[751]=fill('a','-200','0');fills[752]=fill('a','200','0')
    s=stats([position('a')],fills)
    assert D(s['max_balance_drawdown_percent']) == 20
    assert D(s['total_net']) == 0
