"""Full-history, tranche-aware paper analytics; never infer stats from UI pages."""
import unittest
import sqlite3
from yoyo.monitor.reclaim_analytics import DAY, build_analytics, compress_curve, paired_wick_payoff

NOW = 40*DAY
ACCOUNT = dict(track=60, started_ms=DAY, start_equity=1000, equity=1000)
def position(id=1, **kw):
    return dict(dict(id=id, track=60, symbol='SOL-USDT-SWAP', side=1, taken=1, notional=100,
                     remaining_notional=100, entry=100, stage='pending_confirm'), **kw)
def close(id=1, **kw):
    return position(id, **dict(dict(stage='closed', remaining_notional=0, pnl=10, net_bp=1000,
                                   fee_bp=7, exit_ms=NOW-DAY, reason='manual'), **kw))
def fill(id=1, **kw):
    return dict(dict(position_id=id, notional=50, exit_ms=NOW-DAY, pnl=5, fee_bp=7, reason='manual'), **kw)
def analyze(rows, fills=(), **kw):
    return build_analytics(rows, fills, kw.pop('account',ACCOUNT), kw.pop('quotes',{}), NOW, **kw)

class AnalyticsTests(unittest.TestCase):
    def test_wick_pair_uses_common_automatic_settlements_and_equal_notional_returns(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript("""
                CREATE TABLE tracks(track, started_ms);
                CREATE TABLE positions(id INTEGER PRIMARY KEY, signal_id, track, fill_ms, entry,
                    target, taken, notional REAL, stage, pnl REAL, exit_ms, reason);
                CREATE TABLE exits(position_id, reason);
            """)
            db.executemany("INSERT INTO tracks VALUES (?,?)", [(8060, DAY), (8260, 4*DAY)])
            def add(signal, gains, fill=5*DAY, end=NOW, taken=1, stage='closed'):
                for i, (track, amount) in enumerate(((8060, 100), (8260, 500))):
                    db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (signal*2+i, signal, track, fill, 1, 2+i, taken if i else 1,
                                amount, stage if i else 'closed', amount*gains[i],
                                end if i else end-DAY, 'target'))
            add(1,(.1,.2)); add(2,(-.05,-.04))
            add(3,(1,2),fill=3*DAY); add(4,(1,2),stage='pending_confirm')
            add(5,(1,2),taken=0); add(6,(1,2))
            db.execute("INSERT INTO exits VALUES (12,'manual')")
            add(7,(.4,.2),end=NOW-8*DAY); add(8,(.3,.4),end=NOW+DAY)
            row=paired_wick_payoff(db,8060,8260,NOW,7)
            self.assertEqual((row['pairs'],row['changed_targets']),(2,2))
            self.assertAlmostEqual(row['original']['payoff'],2.)
            self.assertAlmostEqual(row['midpoint']['payoff'],5.)
            self.assertAlmostEqual(row['midpoint']['mean_net_pct'],8.)
            self.assertEqual(paired_wick_payoff(db,8060,8260,NOW,0)['pairs'],3)
            db.execute("DELETE FROM positions WHERE signal_id=2")
            self.assertIsNone(paired_wick_payoff(db,8060,8260,NOW,7)['midpoint']['payoff'])

    def test_empty_account_has_no_invented_win_rate_or_profit_factor(self):
        a=analyze([]);s=a['summary']
        self.assertEqual(s['realized_pnl'],0);self.assertEqual(s['unrealized_pnl'],0)
        self.assertIsNone(s['win_rate']);self.assertIsNone(s['profit_factor'])
        self.assertEqual(s['profit_factor_state'],'empty');self.assertEqual(a['curve'][-1]['equity'],1000)

    def test_legacy_and_tranches_are_not_double_counted_and_shadows_are_excluded(self):
        rows=[close(1,pnl=10),close(2,pnl=-6,side=-1),close(3,pnl=999,taken=0),close(4,pnl=900,track=5)]
        fills=[fill(2,pnl=-2),fill(2,pnl=-4,exit_ms=NOW)]
        a=analyze(rows,fills,account={**ACCOUNT,'equity':1004});s=a['summary']
        self.assertEqual(s['realized_pnl'],4);self.assertEqual(s['closed_count'],2);self.assertEqual(s['exit_count'],3)
        self.assertEqual(s['win_rate'],50);self.assertAlmostEqual(s['profit_factor'],10/6)
        self.assertAlmostEqual(s['settled_fees'],.14);self.assertAlmostEqual(a['coverage']['ledger_difference'],0)

    def test_partial_realization_is_money_but_not_a_closed_trade(self):
        p=position(remaining_notional=75)
        a=analyze([p],[fill(notional=25,pnl=2)],account={**ACCOUNT,'equity':1002},
                  quotes={p['symbol']:{'price':110,'received_ms':NOW}},connected=True)
        s=a['summary'];self.assertEqual(s['realized_pnl'],2);self.assertEqual(s['closed_count'],0)
        self.assertEqual(s['open_notional'],75);self.assertEqual(s['unrealized_pnl'],7.5)
        self.assertAlmostEqual(s['settled_fees'],.0175);self.assertEqual(a['by_symbol'][0]['realized_pnl'],2)

    def test_period_cash_and_complete_trade_clocks_are_distinct(self):
        p=close(pnl=3,exit_ms=NOW-DAY)
        a=analyze([p],[fill(pnl=8,exit_ms=NOW-10*DAY),fill(pnl=-5,exit_ms=NOW-DAY)],
                  account={**ACCOUNT,'equity':1003},days=7)
        s=a['summary'];self.assertEqual(s['realized_pnl'],-5);self.assertEqual(s['period_start_equity'],1008)
        self.assertEqual(s['win_rate'],100);self.assertEqual(s['profit_factor_state'],'no_losses')
        self.assertEqual(a['curve'][-1]['equity'],1003);self.assertEqual(s['max_drawdown'],5)

    def test_curve_drawdown_uses_all_events_and_groups_simultaneous_fills(self):
        p=position();fills=[fill(pnl=100,exit_ms=NOW-3*DAY),fill(pnl=-50,exit_ms=NOW-2*DAY),
                           fill(pnl=40,exit_ms=NOW-DAY),fill(pnl=-30,exit_ms=NOW-DAY)]
        a=analyze([p],fills,account={**ACCOUNT,'equity':1060})
        self.assertEqual(a['summary']['max_drawdown'],50);self.assertAlmostEqual(a['summary']['max_drawdown_pct'],50/1100*100)
        self.assertEqual(len([p for p in a['curve'] if p['time_ms']==NOW-DAY]),1)
        self.assertEqual(a['curve'][-1]['pnl'],60)

    def test_no_recent_100_cap_or_future_leak_in_statistics(self):
        rows=[close(i,pnl=1,exit_ms=NOW-1000-i) for i in range(145)]
        rows += [close(999,pnl=1000,exit_ms=NOW+DAY)]
        a=analyze(rows,account={**ACCOUNT,'equity':1145})
        self.assertEqual(a['summary']['closed_count'],145);self.assertEqual(a['summary']['realized_pnl'],145)
        self.assertEqual(a['coverage']['ledger_difference'],0)

    def test_missing_quotes_and_fees_are_explicit(self):
        p=position();a=analyze([p],connected=True)
        self.assertIsNone(a['summary']['unrealized_pnl']);self.assertEqual(a['summary']['missing_quotes'],1)
        for q in [{'price':110,'received_ms':NOW-90001},{'price':110,'received_ms':NOW+1},{'price':0,'received_ms':NOW}]:
            self.assertIsNone(analyze([p],quotes={p['symbol']:q},connected=True)['summary']['unrealized_pnl'])
        a=analyze([close(fee_bp=None)]);self.assertIsNone(a['summary']['settled_fees']);self.assertEqual(a['coverage']['missing_fees'],1)
        a=analyze([close(pnl=None)]);self.assertEqual(a['coverage']['invalid_events'],1);self.assertEqual(a['coverage']['missing_trade_pnl'],1)

    def test_compressed_curve_retains_endpoints_and_local_extrema(self):
        points=[dict(time_ms=i,equity=1000+(-1)**i*i) for i in range(5000)]
        result=compress_curve(points)
        self.assertLessEqual(len(result),1000);self.assertEqual(result[0],points[0]);self.assertEqual(result[-1],points[-1])
        self.assertEqual(max(p['equity'] for p in result),max(p['equity'] for p in points))
        self.assertEqual(min(p['equity'] for p in result),min(p['equity'] for p in points))

if __name__=='__main__': unittest.main()
