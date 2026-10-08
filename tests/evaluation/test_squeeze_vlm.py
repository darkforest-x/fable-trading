"""Pipeline mechanics of exp-owner-squeeze-vlm-20261008-v1 (no network, no paid calls)."""
from __future__ import annotations

import json

import numpy as np
import pytest
import pandas as pd


from yoyo.evaluation import squeeze_breakout_v1 as sb
from yoyo.evaluation import squeeze_vlm as sv


def bars(n: int = 900, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    o = np.r_[c[0], c[:-1]]
    frame = pd.DataFrame({"open": o, "close": c, "volume": rng.lognormal(3, 0.4, n)},
                         index=1_700_000_000_000 + np.arange(n) * 3_600_000)
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.002
    frame["low"] = frame[["open", "close"]].min(axis=1) * 0.998
    return frame


def test_render_ignores_everything_after_the_signal_and_is_deterministic():
    b = bars()
    i = 700
    full = sv.render(b, i, "TESTUSDT", 60)
    later = b.copy()
    later.iloc[i + 1:, :] = later.iloc[i + 1:, :] * 3  # a different future must not change the picture
    assert sv.render(later, i, "TESTUSDT", 60) == full
    assert sv.render(b.iloc[: i + 1], i, "TESTUSDT", 60) == full
    assert sv.sha256(full) == sv.sha256(sv.render(b, i, "TESTUSDT", 60))
    assert full[:8] == b"\x89PNG\r\n\x1a\n"


def test_loose_net_is_the_base_candle_with_bb_and_volume_only():
    f = sb.features(bars(1200, 9))
    f["bb_recent"] = True  # synthetic series rarely compress; open the gate to exercise the logic
    allowed = np.ones(len(f), bool)
    allowed[-1] = False
    got = sv.loose_events(f, allowed)
    hits = []
    for s in (1, -1):
        base, conds = sb.conditions(f, s)
        m = base.to_numpy() & conds["bb"].to_numpy() & conds["volume"].to_numpy() & allowed
        hits += [(int(i), s) for i in np.flatnonzero(m)]
    assert got == sb.with_cooldown(hits) and len(got) > 0
    assert sv.LOOSE == ("bb", "volume")


class FakeClient:
    calls = 0

    def __init__(self, fail=False):
        self.fail = fail

    def analyze(self, image, references, criteria, context):
        FakeClient.calls += 1
        if self.fail:
            from yoyo.vision_research.zhipu import ZhipuError
            raise ZhipuError("timeout", "no answer")
        assert criteria == sv.CRITERIA and len(references) == 1
        return {"decision": {"verdict": "match", "side": "long", "current_state": "launching", "summary": "ok",
                             "evidence": [], "risks": []}, "usage": {"total_tokens": 10}, "latency_ms": 1.0,
                "response_id": "r1", "model": "glm-5.3-flash"}


def png(tmp_path, name, seed):
    path = tmp_path / f"{name}.png"
    path.write_bytes(sv.render(bars(seed=seed), 700, name, 60))
    return {"item_id": name, "set": "examples", "path": str(path)}


def test_judge_never_rebills_and_keeps_errors(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    items = [png(tmp_path, "A", 1), png(tmp_path, "B", 2)]
    ref = [png(tmp_path, "R", 3)]
    FakeClient.calls = 0
    rows = sv.judge(items, ref, model="glm-5.3-flash", client_factory=FakeClient, workers=1, ledger=ledger)
    assert FakeClient.calls == 2 and {r["verdict"] for r in rows} == {"match"}
    assert sv.judge(items, ref, model="glm-5.3-flash", client_factory=FakeClient, workers=1, ledger=ledger) == []
    assert FakeClient.calls == 2  # same image, references, criteria, model and prompt: no new request
    other = sv.judge(items[:1], [], model="glm-5.3-flash", client_factory=lambda: FakeClient(fail=True), workers=1, ledger=ledger)
    assert other[0]["status"] == "error" and other[0]["error_code"] == "timeout"
    assert len(sv.read_ledger(ledger)) == 3


class FakeBinance:
    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def klines(self, symbol, interval, start_ms, end_ms, limit):
        self.calls += 1
        return [r for r in self.rows if start_ms <= r[0] <= end_ms][:2]


def test_binance_bars_paginate_regrid_and_drop_the_forming_bar():
    step = 3_600_000
    t0 = 1_700_000_000_000
    now = int(pd.Timestamp.now(tz="UTC").value // 10**6)
    rows = [[t0 + k * step, "1", "2", "0.5", "1.5", "10", t0 + (k + 1) * step - 1] for k in (0, 1, 3, 4)]
    rows.append([now - 60_000, "1", "2", "0.5", "1.5", "10", now + step])  # still forming
    client = FakeBinance(rows)
    got = sv.binance_bars("XUSDT", 60, t0, t0 + 5 * step, client=client)
    assert list(got.index) == [t0 + k * step for k in range(5)] and np.isnan(got.close.iloc[2])
    assert client.calls >= 2
    assert sv.binance_bars("XUSDT", 60, now - step, now + step, client=FakeBinance(rows[-1:])).empty


def test_example_time_is_beijing_bar_open_and_criteria_cover_the_owner_conditions():
    assert sv.example_signal_ms({"signal_open_bj": "2026-10-08 15:20"}) == pd.Timestamp("2026-10-08T07:20:00Z").value // 10**6
    for word in ("BB", "六条均线", "吞没", "成交量", "最后一根"):
        assert word in sv.CRITERIA
    cfg = json.loads(sv.CONFIG.read_text())
    assert cfg["criteria_version"] == sv.CRITERIA_VERSION and cfg["window_bars"] == sv.WINDOW
    assert cfg["round_trip_cost"] == 0.002


def test_effort_is_part_of_the_ledger_key_but_max_keeps_old_keys():
    base = sv.ledger_key("a", ["r"], "glm-5.3-flash", "p")
    assert sv.ledger_key("a", ["r"], "glm-5.3-flash", "p", "max") == base
    assert sv.ledger_key("a", ["r"], "glm-5.3-flash", "p", "low") != base


def test_review_sheets_mix_groups_blind_and_keep_the_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "EXP", tmp_path)
    items, ledger = [], {}
    for k in range(12):
        path = tmp_path / f"{k}.png"
        path.write_bytes(sv.render(bars(seed=k), 700, f"S{k}", 60))
        items.append({"item_id": f"S{k}", "path": str(path)})
        ledger[str(k)] = {"item_id": f"S{k}", "set": "screen", "status": "ok", "reasoning_effort": "low",
                          "verdict": "match" if k < 6 else "no_match"}
    pd.DataFrame(items).to_csv(tmp_path / "manifest_screen.csv", index=False)
    pd.DataFrame({"item_id": [f"S{k}" for k in range(12)], "net_r_3r": 0.0}).to_csv(tmp_path / "candidates.csv.gz", index=False)
    monkeypatch.setattr(sv, "read_ledger", lambda path=None: ledger)
    key = sv.review_sheets("screen", {"match": 4, "other": 3}, "low", seed=1)
    assert len(key) == 7 and (key.verdict == "match").sum() == 4 and list(key.number) == list(range(1, 8))
    assert key.verdict.tolist() != sorted(key.verdict.tolist())  # groups are interleaved, not blocked
    assert (tmp_path / "review" / "screen" / "sheet_1.png").exists() and (tmp_path / "review" / "screen" / "key_hidden.csv").exists()


def test_shape_stats_are_causal_and_match_the_definition():
    b = bars(900, 11)
    f = sb.features(b)
    stats = sv.shape_stats(b, f)
    later = b.copy()
    later.iloc[801:, :] *= 2
    again = sv.shape_stats(later, sb.features(later))
    for k in stats:
        np.testing.assert_allclose(stats[k][:801], again[k][:801], equal_nan=True)
    i = 800
    body = (b.close - b.open).abs().to_numpy()
    assert stats["body_vs_med36"][i] == pytest.approx(body[i] / np.median(body[i - 36:i]))
    rng = b.high.iloc[i - 24:i].max() - b.low.iloc[i - 24:i].min()
    assert stats["range24_vs_body"][i] == pytest.approx(rng / body[i])
    gate = {"min_body_vs_med36": 8, "max_range24_vs_body": 1.6, "min_vol_vs_med36": 4}
    frame = pd.DataFrame({"body_vs_med36": [13.2, 5.0], "range24_vs_body": [1.22, 1.2], "vol_vs_med36": [6.2, 9.0]})
    assert sv.clean_squeeze(frame, gate).tolist() == [True, False]


def test_okx_bars_page_backwards_and_keep_confirmed_only():
    step = 300_000
    t0 = 1_791_000_000_000
    rows = [[str(t0 + k * step), "1", "2", "0.5", "1.5", "10", "15", "20", "1"] for k in range(250)]
    rows[-1][8] = "0"  # newest still forming
    def opener(url):
        after = int(url.split("after=")[1].split("&")[0])
        page = [r for r in reversed(rows) if int(r[0]) < after][:100]
        return {"data": page}
    got = sv.okx_bars("ALGO-USDT-SWAP", 5, t0 + 10 * step, t0 + 250 * step, opener=opener)
    assert got.index[0] == t0 + 10 * step and got.index[-1] == t0 + 248 * step
    assert got.volume.iloc[0] == 15.0 and sv.okx_inst("ALGOUSDT") == "ALGO-USDT-SWAP"
