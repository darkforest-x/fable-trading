"""Independent score identity, account isolation and original cost scenario."""
from unittest.mock import Mock

from test_monday_reclaim_paper import PaperFixture, H5, MIN
from yoyo.monitor.monday_reclaim_paper import ReclaimPaper, LIVE_ALLIN3_TRACK, ALLIN3_TRACK, settle


class LiveAllinTests(PaperFixture):
    def live(self):
        from test_monday_reclaim_paper import FakeFib
        e = ReclaimPaper(self.tmp.name, FakeFib(self.tmp.name, self.client), enable_live_allin3=True)
        e._score = Mock(return_value=('scored', .7, 1, {'bucket': -20}, None, 4))
        return e

    def touch_live(self, e, symbol='ABC-USDT-SWAP'):
        e.handle_touch(self.levels, symbol, 'lower', 93.8, self.touch+500, self.touch)

    def test_live_opens_before_original_score_and_keeps_its_score_after_close(self):
        e = self.live(); self.touch_live(e)
        snap = e.snapshot()
        self.assertEqual(snap['signals'][0]['status'], 'pending_score')
        self.assertEqual(snap['tracks']['allin3']['open'], [])
        p = snap['tracks']['live-allin3']['open'][0]
        self.assertEqual((p['track'], p['score'], p['notional'], p['score_basis']), (7060, .7, 3000, 'pre_touch'))
        self.assertFalse(e._score.call_args.args[-1])
        self.client.now = self.touch//H5*H5+H5+3000
        e._score.return_value = ('scored', .9, 1, {'bucket': -10}, None, 4)
        e.score_due()
        snap = e.snapshot()
        self.assertEqual(snap['tracks']['allin3']['open'][0]['score'], .9)
        self.assertEqual(snap['tracks']['live-allin3']['open'][0]['score'], .7)
        self.assertEqual(snap['live_signals'][0]['score'], .7)
        notice = e.notification_score(dict(week=self.levels['week'], symbol='ABC-USDT-SWAP', side='lower',
                                           candle_ms=self.touch, observed_ms=self.touch+500))
        self.assertEqual((notice['score'], notice['live_allin3_score']), (.9, .7))
        self.touch_live(e)
        self.assertEqual(len(e.snapshot()['tracks']['live-allin3']['open']), 1)

    def test_separate_capacity_fees_slippage_and_refills(self):
        e = self.live(); self.touch_live(e); self.touch_live(e, 'DEF-USDT-SWAP')
        ps = e.snapshot()['tracks']['live-allin3']['open']
        self.assertEqual(sum(p['taken'] for p in ps), 1)
        self.assertEqual(next(p for p in ps if not p['taken'])['note'], 'busy')
        p = next(p for p in ps if p['taken'])
        with e.db() as db:
            e._close(db, db.execute('SELECT * FROM positions WHERE id=?', (p['id'],)).fetchone(), self.touch+MIN, p['entry']*.7, 'backstop')
        snap = e.snapshot(); live = snap['tracks']['live-allin3']
        self.assertAlmostEqual(live['closed'][0]['pnl'], 3000*settle(1,p['entry'],p['entry']*.7,'backstop')[3]/1e4)
        self.assertEqual(live['closed'][0]['fee_bp'], 7)
        self.assertEqual((live['equity'], live['stakes']), (1000, 2))
        self.assertEqual(snap['tracks']['allin3']['equity'], 1000)
        self.assertAlmostEqual(e.analytics('live-allin3')['coverage']['ledger_difference'], 0)

    def test_low_or_unavailable_pre_score_cannot_borrow_touch_score(self):
        e = self.live(); e._score.return_value = ('feature_error', None, 0, None, 'short_history', None)
        self.touch_live(e)
        self.client.now = self.touch//H5*H5+H5+3000
        e._score.return_value = ('scored', .9, 1, {}, None, 4); e.score_due()
        self.assertEqual(e.snapshot()['tracks']['live-allin3']['open'], [])
        self.assertIsNone(e.snapshot()['live_signals'][0]['score'])

    def test_late_original_replay_cannot_rewind_live_position(self):
        e = self.live(); self.touch_live(e)
        e.handle_quote('ABC-USDT-SWAP', {'candle_ms': self.touch+MIN, 'high': 120, 'low': 10, 'price': 100}, skip_live=True)
        self.assertEqual(len(e.snapshot()['tracks']['live-allin3']['open']), 1)

    def test_history_uses_pre_score_for_both_events_and_positions(self):
        e = self.live(); self.touch_live(e)
        p = e.snapshot()['tracks']['live-allin3']['open'][0]
        with e.db() as db:
            e._close(db, db.execute('SELECT * FROM positions WHERE id=?', (p['id'],)).fetchone(), self.touch+MIN, p['target'], 'target')
        self.client.now = self.touch+2*86400000
        for tab in ('events', 'closed'):
            page = e.record_history('live-allin3', tab=tab)
            self.assertEqual(page['total'], 1)
            self.assertEqual(page['rows'][0]['score'], .7)
            self.assertEqual(page['rows'][0]['score_basis'], 'pre_touch')

    def test_restart_does_not_fund_old_signals(self):
        e = self.engine(-1., enable_allin3=True)
        e.model.score = lambda _: .7
        self.touch_live(e)
        restarted = self.live(); self.touch_live(restarted)
        self.assertEqual(restarted.snapshot()['tracks']['live-allin3']['open'], [])
        self.assertEqual(restarted.snapshot()['live_signals'], [])
