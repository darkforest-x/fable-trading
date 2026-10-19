"""Frozen-list fidelity, scoped admissions and legacy-reference preservation."""
import csv
from pathlib import Path

import pytest

from yoyo.contracts.signal_universe import (
    DIRECTIONAL_EXCLUDED_BASES, EXCLUDED_BASES, RESEARCH_TO_OKX_BASE,
    exclusion_reason, instrument_exclusion, public_policy,
)
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.signal_analytics import ledger
from yoyo.monitor.store import Store
from yoyo.monitor.v130_policy import arm_v130
from yoyo.monitor.v130_worker import observe
from test_signal_universe import instrument
from test_v130_integration import inputs
from test_v130_notifications import event, setup, CLOSE


def test_four_lists_match_exact_full_history_rankings_and_never_combined_or_early_lists():
    path = Path(__file__).resolve().parents[2] / 'experiments/active/exp-spike-v131-symbol-summary-20261017-v1/results_v1/bottom20_20261017_symbols.csv'
    with path.open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    for tf, sides in DIRECTIONAL_EXCLUDED_BASES.items():
        for side, bases in sides.items():
            selected = [r['symbol'][:-4] for r in rows if r['scope'] == 'full_history_retrospective'
                        and r['timeframe'] == tf and r['side'] == ('1' if side == 'long' else '-1')]
            assert list(bases) == selected and len(set(bases)) == 20
            for base in bases:
                mapped = RESEARCH_TO_OKX_BASE.get(base, base)
                assert exclusion_reason(mapped+'-USDT-SWAP', timeframe=tf, side=side)


@pytest.mark.parametrize('tf,side,base', [('15m','long','AXL'), ('15m','short','DOGE'),
                                       ('1H','long','ACH'), ('1H','short','ARC')])
def test_each_exclusion_leaves_other_direction_other_periods_and_instrument_discovery(tf, side, base):
    symbol = base+'-USDT-SWAP'
    assert exclusion_reason(symbol, timeframe=tf, side=side) == 'owner_excluded_timeframe_side'
    assert exclusion_reason(symbol, timeframe=tf, side='short' if side == 'long' else 'long') is None
    for other in ['15m','30m','1H','4H']:
        if other != tf:
            assert exclusion_reason(symbol, timeframe=other, side=side) is None
    assert instrument_exclusion(instrument(base)) is None
    assert exclusion_reason(symbol) is None  # Manual universe is unchanged.
    assert exclusion_reason(base+'X-USDT-SWAP', timeframe=tf, side=side) is None


def test_explicit_aliases_do_not_strip_arbitrary_numbers_or_confuse_luna_classic():
    for research, base in RESEARCH_TO_OKX_BASE.items():
        tf, side = ('15m','long') if research == 'LUNA2' else ('1H','long' if research == '1000XEC' else 'short')
        assert exclusion_reason(base+'-USDT-SWAP', timeframe=tf, side=side)
        assert exclusion_reason('100'+base+'-USDT-SWAP', timeframe=tf, side=side) is None
    assert exclusion_reason('LUNC-USDT-SWAP', timeframe='15m', side='long') is None
    assert public_policy()['selection_basis'] == 'full_history_retrospective_owner_selected'
    for base in EXCLUDED_BASES:
        assert exclusion_reason(base+'-USDT-SWAP', timeframe='4H', side='short') == 'owner_excluded_asset'


@pytest.mark.parametrize('channel', ['telegram','bark'])
def test_already_queued_leg_is_rechecked_without_sending(channel, tmp_path):
    store, _ = setup(tmp_path)
    blocked = event(symbol='AXL-USDT-SWAP')
    assert store.upsert_event(blocked, notify=True, bark_notify=True)
    assert delivery_error(store, blocked, CLOSE+1001, channel) == 'owner_excluded_timeframe_side'
    short = event(symbol='AXL-USDT-SWAP', side='short', direction='short', initial_stop=102.)
    assert delivery_error(store, short, CLOSE+1001, channel) is None
    assert store.event_count() == 1


def test_new_signal_is_dropped_but_existing_reference_keeps_exiting_and_history(tmp_path, monkeypatch):
    from yoyo.monitor import v130_worker
    parent, context = inputs()
    final = int(context['built'].index[-1].value // 1_000_000) + 900_000
    store = Store(tmp_path/'monitor.sqlite3')
    arm_v130(store, final-1)
    blocked = observe(store,parent,context,symbol='AXL-USDT-SWAP',tick=.1,clock=lambda: final+1)
    assert blocked['events'] == [] and store.event_count() == 0
    with monkeypatch.context() as patch:
        patch.setattr(v130_worker,'exclusion_reason',lambda *a,**kw:None)
        observe(store,parent,context,symbol='AXL-USDT-SWAP',tick=.1,clock=lambda:final+1)
    original = store.list_events(summary=False)[0]
    view = ledger(store,now=final+1,confirmation='retest')
    assert view['total'] == 1 and not view['items'][0]['is_fresh']
    assert not view['items'][0]['entry_eligible']
    parent, context = inputs([(11.2,11.4,7.9,9.)])
    observe(store,parent,context,symbol='AXL-USDT-SWAP',tick=.1,clock=lambda:final+900_001)
    updated = store.list_events(summary=False)[0]
    assert updated['id'] == original['id']
    assert updated['performance']['status'] == 'loss'
    assert store.event_count() == 1
