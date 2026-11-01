"""live-wick6x3 / live-wick3x3: market entry at the touch 5m close for survivor touches (owner 2026-11-01)."""
import json
import unittest
from unittest.mock import Mock

from test_monday_reclaim_paper import PaperFixture, FakeFib, H5, H1, MIN
from yoyo.monitor.monday_reclaim_paper import (ReclaimPaper, SCORE_GRACE_MS, WICK6_TRACK, WICK3_TRACK, settle_market,
                                               wick_entry)


class LiveWickTests(PaperFixture):
    def wick(self, score=.7):
        e = ReclaimPaper(self.tmp.name, FakeFib(self.tmp.name, self.client), enable_v3=False, enable_spike=False,
                         enable_allin3=False, enable_live_wick=True)
        e._score = Mock(return_value=('scored', score, 1, {'bucket': -20}, None, 4))
        return e

    def set_bar(self, low, close):
        self.client.override[('5m', self.bar_ms)] = (95., 95., low, close)

    def setUp(self):
        super().setUp()
        self.bar_ms = self.touch//H5*H5
        self.close = self.bar_ms+H5
        for ts in range(self.touch, self.close+H5, MIN):  # quiet minutes near the entry, as TouchCloseScoreTests
            self.client.override[('1m', ts)] = (93.9, 94.2, 93.85, 94.)

    def fire(self, e, symbol='ABC-USDT-SWAP'):
        e.handle_touch(self.levels, symbol, 'lower', 93.8, self.touch+500, self.touch)

    def scored(self, e):
        self.client.now = self.close+SCORE_GRACE_MS
        e.score_due(self.client.now)

    def wick_rows(self, e):
        return self.rows(e, f"SELECT * FROM positions WHERE track IN ({WICK6_TRACK},{WICK3_TRACK}) ORDER BY track")

    def test_survivor_enters_at_the_touch_close_with_level_reclaim_and_entry_stop(self):
        e = self.wick(); self.set_bar(92., 92.5)
        self.fire(e); self.scored(e)
        rows = self.wick_rows(e)
        self.assertEqual([r['track'] for r in rows], [WICK6_TRACK, WICK3_TRACK])
        for r, notional in zip(rows, (500., 1000.)):
            self.assertEqual((r['entry'], r['fill_ms'], r['target'], r['reclaim_ref'], r['taken']),
                             (92.5, self.close, 100., 93.82, 1))
            self.assertAlmostEqual(r['notional'], notional)
            self.assertAlmostEqual(r['backstop'], 92.5*.8)
            self.assertEqual(r['decision_ms'], self.touch//H1*H1+2*H1)
        snap = e.snapshot()
        self.assertEqual(snap['live-wick6x3']['slots'], 6)
        self.assertEqual(len(snap['tracks']['live-wick3x3']['open']), 1)
        # The original near-edge accounts still book their own rows at the level.
        self.assertTrue(all(p['entry'] == 93.82 for t in ('60', '5') for p in snap['tracks'][t]['open']))

    def test_notification_lookup_names_the_live_accounts_of_the_touch(self):
        e = self.wick(); self.set_bar(92., 92.5)
        identity = dict(week=self.levels['week'], symbol='ABC-USDT-SWAP', side='lower', candle_ms=self.touch,
                        observed_ms=self.touch+500)
        self.fire(e)
        self.assertIsNone(e.notification_score(identity))  # waits for the touch 5m close, as the score does
        self.scored(e)
        names = [(x['name'], x['taken']) for x in e.notification_score(identity)['live_entries']]
        # 92.5 closes 1.4 percent past 93.82, short of the far account's 1.5 percent, so that account has no row.
        self.assertEqual(names, [('live-wick6x3', True), ('live-wick3x3', True), ('live-wick6x3hi', True)])

    def test_not_entered_when_recovered_inside_collapsed_or_low_score(self):
        for low, close, score in ((92., 94., .7), (92., 93.5, .7), (60., 62., .7), (92., 92.5, .45)):
            with self.subTest(close=close, score=score):
                self.tearDown(); self.setUp()
                e = self.wick(score); self.set_bar(low, close)
                self.fire(e); self.scored(e)
                self.assertEqual(self.wick_rows(e), [])

    def test_entry_facts(self):
        facts, why = wick_entry(1, 93.82, 100., (0, 95., 95., 92., 92.5))
        self.assertAlmostEqual(facts['recovery'], .5/1.82)
        self.assertAlmostEqual(facts['beyond_pct'], 100*1.32/93.82)
        self.assertEqual(wick_entry(-1, 116.18, 110., (0, 115., 118., 115., 117.))[0]['close'], 117.)
        self.assertEqual(wick_entry(1, 93.82, 100., (0, 95., 95., 92., 94.))[1], 'closed_inside')

    def test_reclaim_is_judged_against_the_level_without_offset(self):
        e = self.wick(); self.set_bar(92., 92.5)
        self.fire(e); self.scored(e)
        self.confirm(e, 93.9)  # above the level 93.82: armed, stop moves to the level
        rows = self.wick_rows(e)
        self.assertTrue(all((r['stage'], r['be_price']) == ('breakeven', 93.82) for r in rows))

    def test_failed_reclaim_exits_even_above_the_entry_and_costs_match_the_backtest(self):
        e = self.wick(); self.set_bar(92., 92.5)
        self.fire(e); self.scored(e)
        self.confirm(e, 93.5)  # above the entry but still beyond the level
        r = self.wick_rows(e)[0]
        self.assertEqual((r['stage'], r['reason'], r['exit_price']), ('closed', 'failed_reclaim', 93.5))
        gross, fee, net, _ = settle_market(1, 92.5, 93.5, 'failed_reclaim')
        self.assertEqual(fee, 10.)
        self.assertAlmostEqual(net, gross-30.)
        self.assertAlmostEqual(r['pnl'], 500*net/1e4)
        self.assertEqual(settle_market(1, 92.5, 100., 'target')[2], settle_market(1, 92.5, 100., 'target')[0]-17.)

    def test_slots_and_isolated_equity(self):
        e = self.wick(); self.set_bar(92., 92.5)
        for k in range(4):
            sym = f'S{k}-USDT-SWAP'
            self.client.override[('5m', self.bar_ms)] = (95., 95., 92., 92.5)
            self.fire(e, sym)
        self.scored(e)
        three = self.rows(e, f"SELECT taken,note FROM positions WHERE track={WICK3_TRACK} ORDER BY id")
        six = self.rows(e, f"SELECT taken FROM positions WHERE track={WICK6_TRACK}")
        self.assertEqual([r['taken'] for r in three], [1, 1, 1, 0])
        self.assertEqual(three[-1]['note'], 'no_slot')
        self.assertEqual(sum(r['taken'] for r in six), 4)
        snap = e.snapshot()
        self.assertEqual((snap['tracks']['live-wick6x3']['equity'], snap['tracks']['live-wick3x3']['equity']), (1000, 1000))

    def test_backstop_loss_is_capped_at_the_margin(self):
        e = self.wick(); self.set_bar(92., 92.5)
        self.fire(e); self.scored(e)
        r = self.wick_rows(e)[1]
        with e.db() as db:
            e._close(db, db.execute('SELECT * FROM positions WHERE id=?', (r['id'],)).fetchone(), self.close+MIN, r['backstop'], 'backstop')
        r = self.wick_rows(e)[1]
        self.assertGreaterEqual(r['pnl'], -1000/3)
        self.assertAlmostEqual(r['pnl'], 1000*settle_market(1, 92.5, 74., 'backstop')[2]/1e4)
        self.assertAlmostEqual(e.snapshot()['tracks']['live-wick3x3']['equity'], 1000+r['pnl'])

    def test_high_score_account_boosts_only_from_076(self):
        for score, margin in ((.7, 1000/6), (.8, 1000/6*1.5)):
            with self.subTest(score=score):
                self.tearDown(); self.setUp()
                e = self.wick(score); self.set_bar(92., 92.5)
                self.fire(e); self.scored(e)
                hi = self.rows(e, "SELECT notional FROM positions WHERE track=8160")
                base = self.rows(e, f"SELECT notional FROM positions WHERE track={WICK6_TRACK}")
                self.assertAlmostEqual(hi[0]['notional'], margin*3)
                self.assertAlmostEqual(base[0]['notional'], 500.)
        self.assertEqual(e.snapshot()['live-wick6x3hi']['boost_from_score'], .76)

    def test_far_account_needs_the_close_15_percent_past_the_level(self):
        for low, close, opened in ((92., 92.5, 0), (90., 91.5, 1)):  # 1.41 vs 2.47 percent past 93.82
            with self.subTest(close=close):
                self.tearDown(); self.setUp()
                e = self.wick(); self.set_bar(low, close)
                self.fire(e); self.scored(e)
                far = self.rows(e, "SELECT notional FROM positions WHERE track=9160")
                self.assertEqual(len(far), opened)
                self.assertEqual(len(self.rows(e, f"SELECT 1 FROM positions WHERE track={WICK3_TRACK}")), 1)
                if opened:
                    self.assertAlmostEqual(far[0]['notional'], 1000.)


class LiveWickBookletTests(unittest.TestCase):
    def test_paths_continue_the_saved_account_through_october(self):
        import pandas as pd
        from yoyo.monitor import strategy_live_wick as w
        for track, old in (('live-wick6x3', 'v5'), ('live-wick3x3', 'v7'), ('live-wick6x3hi', 'v8'), ('live-wick3x3far', 'v9')):
            directory, name, _ = w.SOURCES[track]
            saved = pd.read_csv(w.A / f'exp-monday-limit-close-survivor-paths-20261101-{old}' / f'{name}-ledger.csv.gz')
            now = pd.read_csv(directory / f'{name}-ledger.csv.gz')
            self.assertTrue(now.trade_id.iloc[:len(saved)].reset_index(drop=True).equals(saved.trade_id))
            self.assertLess(float(abs(now.equity.iloc[:len(saved)].to_numpy()-saved.equity.to_numpy()).max()), 1e-6)
            history = json.loads((w.ROOT / 'yoyo/monitor/static/strategy-booklet' / f'{track}-history.json').read_text())
            self.assertIn('2026-10', [m[0] for m in history['monthly']])

    def test_section_streaks_come_from_the_account_and_doubling_is_not_adopted(self):
        from yoyo.monitor import strategy_live_wick as w
        for track in w.SOURCES:
            (section,) = w.sections(track)
            self.assertIn('先不做倍投', section['note'])
            self.assertRegex(section['rows'][0][1], r'^\d+笔 / \d+笔$')
        self.assertEqual(len(w.sections('live-wick6x3hi')[0]['rows']), 2)  # weighted account: streaks only, no doubling row
