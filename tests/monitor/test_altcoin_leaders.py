"""Causal ranking, shared-window rejection and append-only observation contracts."""
from copy import deepcopy
import gzip
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.monitor.altcoin_leaders import (HOUR, BENCHMARKS, build_snapshot, closed_rows,
                                         crypto_instrument, detect_episode)
from yoyo.monitor.altcoin_leader_service import LeaderBook, LeaderService, install, read_checkpoints

BASE = 1_700_000_000_000 // HOUR * HOUR
ETH = BENCHMARKS[0]
BTC = BENCHMARKS[1]
A, B, C, D = (x + "-USDT-SWAP" for x in "ABCD")


def bars(closes):
    return [dict(t=BASE + i * HOUR, o=c, h=c + 1, l=c - 1, c=c, v=10) for i, c in enumerate(closes)]


def fixture():
    series = {ETH: bars([100] * 25 + [105, 108, 104, 105]),
              BTC: bars([100] * 25 + [105, 108, 104, 105]),
              A: bars([100] * 25 + [110, 112, 109, 113]),
              B: bars([100] * 25 + [115, 120, 106, 107]),
              C: bars([100] * 25 + [100.5, 101, 100.5, 100.6])}
    return series, BASE + 29 * HOUR


def build(series=None, asof=None, frozen=None, symbols=None):
    raw, end = fixture()
    return build_snapshot(series or raw, symbols or set(raw) | {D}, ETH, asof or end, (asof or end) + 100, frozen)


def test_same_window_rank_preserves_missing_and_rejects_uninvolved_low_drawdown():
    result = build()
    rows = {r["symbol"]: r for r in result["items"]}
    assert result["episode"]["phase"] == "pullback"
    assert rows[A]["tier"] == 1 and rows[B]["tier"] == 2
    assert rows[A]["giveback"] == pytest.approx(0.25)
    assert rows[A]["wave_rs"] == pytest.approx(0.04)
    assert rows[A]["recovery_bars"] == 2
    assert rows[A]["volume_ratio"] == 1
    assert rows[C]["leadership"] == "not_participating"
    assert rows[D]["quality"] == "missing_checkpoint"
    assert result["coverage"] == {"total": 4, "comparable": 3, "candidates": 2}
    assert result["execution_ready"] is False


def test_prefix_cannot_see_later_prices_or_malformed_future_values():
    raw, end = fixture()
    before = build(raw, end)
    for rows in raw.values():
        rows.append(dict(t=end, o=float("nan"), h=-5, l=-9, c=1e20, v=0))
    assert build(raw, end) == before


def test_episode_confirmation_times_and_end_are_not_backdated():
    raw, end = fixture()
    ep = detect_episode(raw[ETH], ETH)
    assert ep["launch_ms"] == BASE + 26 * HOUR
    assert ep["wave_end_ms"] == BASE + 27 * HOUR
    assert ep["pullback_confirmed_ms"] == BASE + 28 * HOUR
    assert ep["recovery_confirmed_ms"] is None
    extended = bars([100] * 25 + [105, 108, 104, 105, 109])
    assert detect_episode(extended, ETH)["recovery_confirmed_ms"] == BASE + 30 * HOUR
    assert detect_episode(extended + bars([100] * 30 + [108])[-1:], ETH) is None


def test_gap_and_missing_cutoff_do_not_reuse_different_windows():
    raw, end = fixture()
    raw[A].pop(27)
    raw[B].pop()
    rows = {r["symbol"]: r for r in build(raw, end)["items"]}
    assert rows[A]["quality"] == rows[B]["quality"] == "incomplete_common_window"
    assert rows[A]["tier"] is rows[B]["tier"] is None


def test_benchmark_gap_resets_context_and_missing_latest_is_rejected():
    raw, end = fixture()
    raw[ETH].pop(20)
    with pytest.raises(ValueError, match="incomplete_common_window"):
        build(raw, end)


def test_equal_metrics_remain_tied_and_no_positive_candidate_is_invented():
    raw, end = fixture()
    raw[B] = deepcopy(raw[A])
    rows = {r["symbol"]: r for r in build(raw, end)["items"]}
    assert rows[A]["tier"] == rows[B]["tier"] == 1
    raw[A] = raw[B] = deepcopy(raw[C])
    assert build(raw, end)["coverage"]["candidates"] == 0


def test_retracement_beyond_100_and_downgrade_remain_visible():
    raw, end = fixture()
    raw[A][-1] = dict(raw[A][-1], o=98, c=98, h=99, l=97)
    row = next(r for r in build(raw, end)["items"] if r["symbol"] == A)
    assert row["giveback"] > 1 and row["leadership"] == "downgraded" and row["tier"] is None


def test_frozen_universe_survives_new_listing_and_delisting():
    first = build(symbols={ETH, BTC, A, B, C})
    raw, end = fixture()
    raw[D] = deepcopy(raw[A])
    later = build(raw, end, first["episode"], {ETH, BTC, A, C, D})
    rows = {r["symbol"]: r for r in later["items"]}
    assert rows[D]["leadership"] == "late_listing" and rows[D]["tier"] is None
    assert rows[B]["quality"] == "no_longer_in_live_universe"
    assert later["episode"]["first_observed_ms"] == first["episode"]["first_observed_ms"]
    assert later["universe_version"] == first["universe_version"]


def instrument(symbol=A, **kwargs):
    return dict(instId=symbol, state="live", instType="SWAP", instCategory="1", ruleType="normal",
                ctType="linear", settleCcy="USDT", **kwargs)


def test_unknown_category_stock_stable_and_premarket_cannot_enter_crypto_universe():
    row = instrument()
    assert crypto_instrument(row)
    for key, value in [("instCategory", None), ("instCategory", "3"), ("ruleType", "pre_market"),
                       ("state", "suspend"), ("ctType", "inverse"), ("instId", "USDC-USDT-SWAP")]:
        assert not crypto_instrument(dict(row, **{key: value}))


def test_append_only_book_deduplicates_polling_but_retains_reversion(tmp_path):
    book = LeaderBook(tmp_path / 'leaders.sqlite3')
    first = book.save(build())
    repeated = deepcopy(first); repeated.pop('snapshot_id'); repeated['observed_ms'] += 60000
    assert book.save(repeated)['snapshot_id'] == first['snapshot_id']
    changed = deepcopy(repeated); changed['items'][0]['action'] = 'wait_recovery'
    second = book.save(changed)
    assert second['snapshot_id'] > first['snapshot_id']
    third = book.save(repeated)
    assert third['snapshot_id'] > second['snapshot_id']
    assert book.latest(ETH)['snapshot_id'] == third['snapshot_id']
    assert book.get(first['snapshot_id'])['observed_ms'] == first['observed_ms']
    assert len(book.history(ETH)) == 3


def write_source(path, raw, received):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE candle_checkpoints(symbol TEXT,timeframe TEXT,payload BLOB,updated_ms INTEGER)')
        db.executemany('INSERT INTO candle_checkpoints VALUES (?,?,?,?)',
                       [(s,'1H',gzip.compress(json.dumps(rows).encode()),received) for s,rows in raw.items()])


class Client:
    def __init__(self, now): self.now = now; self.calls = 0
    def clock(self): return self.now
    def synchronize(self): return 0
    def instruments(self): self.calls += 1; return [instrument(s) for s in (ETH, BTC, A, B, C, D)]


def test_service_reads_existing_source_and_pauses_on_staleness_and_errors(tmp_path):
    raw, end = fixture(); path = tmp_path / 'monitor.sqlite3'
    write_source(path,raw,end+50)
    original = path.read_bytes()
    client = Client(end+100)
    svc = LeaderService(tmp_path,client=client)
    svc.cycle()
    snap = svc.snapshot(ETH)
    assert snap['ranking_active'] and snap['coverage']['candidates'] == 2
    assert path.read_bytes() == original
    svc.cycle(); assert client.calls == 1 and len(svc.book.history(ETH)) == 1
    svc.health['state'] = 'error'
    assert not svc.snapshot(ETH)['ranking_active']
    svc.health['state'] = 'ok'; client.now = end+HOUR
    assert svc.snapshot(ETH)['stale'] and not svc.snapshot(ETH)['ranking_active']


def test_checkpoints_cannot_arrive_from_future(tmp_path):
    raw, end = fixture(); path = tmp_path/'monitor.sqlite3'
    write_source(path,raw,end+200)
    assert read_checkpoints(path,set(raw),end+100) == ({},{})


def test_api_is_read_only_and_historical_snapshots_cannot_claim_current_ranking(tmp_path):
    raw,end = fixture(); write_source(tmp_path/'monitor.sqlite3',raw,end+50)
    svc=LeaderService(tmp_path,client=Client(end+100)); svc.cycle()
    app=FastAPI();install(app,svc);client=TestClient(app)
    live=client.get('/api/altcoin-leaders').json()
    assert live['ranking_active'] and live['orders_enabled'] is False
    history=client.get('/api/altcoin-leaders/history').json()['items']
    old=client.get(f"/api/altcoin-leaders/snapshots/{history[0]['snapshot_id']}").json()
    assert old['historical_snapshot'] and old['ranking_active'] is False
    assert client.get('/api/altcoin-leaders?benchmark=BAD').status_code==400
    assert client.get('/api/altcoin-leaders/history?limit=1000').status_code==422
    assert client.get('/api/altcoin-leaders/snapshots/99999').status_code==404
    assert client.post('/api/altcoin-leaders').status_code==405
