"""V2.37 adds only the5m confirmation gate to H1-qualified15m frames.

Native fixtures cover inclusive five-minute position windows, publication,
per-quarter deduplication, H1 admission and pre-confirmation stop outcomes.
"""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OLD=(ROOT/'yoyo/evaluation/pine/monday_range_v2_36.pine').read_text()
NEW=(ROOT/'yoyo/evaluation/pine/monday_range_v2_37.pine').read_text()


def section(s,first,last):
    return s.split(first,1)[1].split(last,1)[0]


def test_five_minute_engine_and_pairing_unchanged():
    assert section(OLD,'type TxReviewCandidate','txConfirmed() =>').strip()==section(
        NEW,'type TxReviewCandidate','// Preserve the original H1-qualified15m').strip()
    assert 'txEntryTf == "5" ? txReviewEngine() : txFilteredQuarterEngine()' in NEW


def test_old_admission_and_management_preserved():
    assert section(OLD,'txObserve(', '// A review candidate')==section(NEW,'txObserve(', '// A review candidate')
    engine=section(NEW,'txFilteredQuarterEngine() =>','txConfirmed() =>')
    assert 'txRefreshObservation(watch, "15", hClose, hourSide, hourEnd' in engine
    assert 'txEntryEligible(watch, "15", qClose, fiveSide, fiveEnd, quarterSide, quarterEnd)' in engine
    assert 'TxTrade.new(time_close, watch.since, watch.side, close, initial, risk, initial)' in engine
    assert 'TxFilteredQuarter.new(trade, quarterEnd - 900000)' in engine


def test_other_modules_unchanged():
    assert OLD[OLD.index('type WfTrade'):]==NEW[NEW.index('type WfTrade'):]
    assert section(OLD,'fs_periodK =','// === V2.2 fixed-timeframe')==section(NEW,'fs_periodK =','// === V2.2 fixed-timeframe')


def test_units_and_visibility_clocks():
    match=section(NEW,'txMatchQuarter(', 'txPackQuarter(')
    assert 'txReviewMatches(five.signalAt, five.side, candidate.signalAt, candidate.trade.side, window)' in match
    assert 'candidate.trade.enteredAt <= nowAt and five.closeAt <= nowAt' in match
    assert 'candidate.confirmedAt := math.max(candidate.trade.enteredAt, five.closeAt)' in match
    assert 'candidate.signalAt + window * 300000 + 300000' in match
    assert 'candidate.trade.observedAt :=' not in match
    assert 'pending.remove(i)' in match


def test_hidden_outcomes_advance_and_matching_is_observable():
    engine=section(NEW,'txFilteredQuarterEngine() =>','txConfirmed() =>')
    assert engine.index('for candidate in pending')<engine.index('if txEntryEligible')<engine.index('total += txMatchQuarter(')
    assert 'txAdvance(candidate.trade' in engine and 'txExitOnH1(candidate.trade' in engine
    assert 'txReviewMatches(' not in engine


def test_version_and_retrospective_label():
    assert 'shorttitle="区间翻身 V2.37"' in NEW and 'V237_FIXTURE' not in NEW
    assert 'txEntryTf == "15" and fields.size() == 15' in NEW
    assert 'retrospectiveFrame := confirmed > entered' in NEW
    assert '前后各25分钟' in NEW


def test_native_fixture_presence():
    native=(ROOT/'tests/evaluation/monday_range_v2_37_native_checks.pine').read_text()
    for name in ['V237_FIXTURES_PASS','V237_LEDGER_PASS','lateStop','oneQuarter','expiryEdge','hourRequired','windowUnits']:
        assert name in native
