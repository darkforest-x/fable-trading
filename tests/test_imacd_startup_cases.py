"""Synthetic public-history pagination and exposed-case reporting checks.

Every HTTP client below is a fake; these tests never call OKX or the monitor.
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import imacd_startup_cases as cases
from yoyo.monitor.okx import MarketError


def history_rows(n=1200, case=cases.CASES[0]):
    end = cases._milliseconds(case.end)
    period = cases.PERIOD_MS[case.timeframe]
    return [[str(end - period * (i + 1)), "100", "102", "98", "101", "5", "0", "0", "1"]
            for i in range(n)]


class FakeClient:
    def __init__(self, rows, overlap=False):
        self.rows = rows
        self.overlap = overlap
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        after = int(params["after"])
        selected = [r for r in self.rows if int(r[0]) < after
                    or (self.overlap and int(r[0]) == after)]
        return selected[:int(params["limit"])]


@pytest.mark.parametrize("case", cases.CASES)
def test_native_pagination_returns_exact_confirmed_sorted_window_and_never_current(case):
    fake = FakeClient(history_rows(1350, case))
    bars, meta = cases.fetch_history(fake, case)
    assert len(bars) == 1200
    assert meta["history_complete"]
    assert meta["pages"] == 12
    assert bars.index.is_unique and bars.index.is_monotonic_increasing
    period = pd.Timedelta(milliseconds=cases.PERIOD_MS[case.timeframe])
    assert (bars.index[1:] - bars.index[:-1] == period).all()
    assert bars.index[-1] + period == cases._utc(case.end)
    for path, params in fake.calls:
        assert path == "/api/v5/market/history-candles"
        assert params["instId"] == case.symbol
        assert params["bar"] == case.timeframe
        assert int(params["after"]) <= cases._milliseconds(case.end)


def test_identical_page_overlap_deduplicates_without_shortening_target():
    fake = FakeClient(history_rows(1300), overlap=True)
    bars, meta = cases.fetch_history(fake, cases.CASES[0])
    assert len(bars) == 1200
    assert bars.index.is_unique
    assert meta["pages"] == 13


def test_unconfirmed_and_after_end_candles_never_enter_history():
    rows = history_rows(1300)
    end = cases._milliseconds(cases.CASES[0].end)
    future = [str(end), "100", "102", "98", "101", "5", "0", "0", "1"]
    current = list(future)
    current[8] = "0"

    class NewerRowsClient(FakeClient):
        def get(self, path, params):
            result = super().get(path, params)
            return [future, current, *result] if len(self.calls) == 1 else result

    bars, _ = cases.fetch_history(NewerRowsClient(rows), cases.CASES[0])
    assert len(bars) == 1200
    assert bars.index[-1] < cases._utc(cases.CASES[0].end)


def test_unconfirmed_interior_bar_is_a_gap_not_silently_filled():
    rows = history_rows(1300)
    rows[200][8] = "0"
    with pytest.raises(MarketError, match="confirmed_history_gap"):
        cases.fetch_history(FakeClient(rows), cases.CASES[0])


def test_conflicting_duplicate_is_rejected():
    class ConflictingClient(FakeClient):
        def get(self, path, params):
            result = super().get(path, params)
            if len(self.calls) == 2:
                result = [list(r) for r in result]
                result[0][4] = "100"
            return result

    with pytest.raises(MarketError, match="conflicting_confirmed"):
        cases.fetch_history(ConflictingClient(history_rows(1300), overlap=True), cases.CASES[0])


def test_stuck_cursor_and_finite_page_budget_fail_closed():
    class Stuck:
        def get(self, path, params):
            return [[params["after"], "100", "102", "98", "101", "5", "0", "0", "1"]]

    with pytest.raises(MarketError, match="did_not_advance"):
        cases.fetch_history(Stuck(), cases.CASES[0])
    with pytest.raises(MarketError, match="page_budget_exhausted"):
        cases.fetch_history(FakeClient(history_rows(1300)), cases.CASES[0], max_pages=2)


@pytest.mark.parametrize("n", [0, 100, 339, 500])
def test_short_history_is_explicit_not_padded(n):
    bars, meta = cases.fetch_history(FakeClient(history_rows(n)), cases.CASES[0])
    assert len(bars) == n
    assert not meta["history_complete"]
    assert meta["source_exhausted"]
    review = cases.review_case(bars, cases.CASES[0])
    assert review["status"] == ("insufficient_warmup" if n < 340 else "partial_history")
    assert review["release_count"] == 0


def test_continuous_but_old_history_does_not_claim_window_coverage():
    rows = history_rows(1301)[1:]
    with pytest.raises(MarketError, match="history_missing_window_end"):
        cases.fetch_history(FakeClient(rows), cases.CASES[0])


@pytest.mark.parametrize("column,value,error", [
    (0, "1", "unaligned"), (1, "NaN", "nonfinite"), (2, "99", "ohlcv"),
    (3, "-1", "ohlcv"), (4, "oops", "number"), (5, "-1", "ohlcv"),
    (8, "2", "confirmation"),
])
def test_invalid_source_rows_are_rejected(column, value, error):
    rows = history_rows(1300)
    rows[10][column] = value
    with pytest.raises(MarketError, match=error):
        cases.fetch_history(FakeClient(rows), cases.CASES[0])


def test_fixed_case_window_lists_all_sides_and_prices_without_reference_selection(monkeypatch):
    case = cases.CASES[0]
    bars, _ = cases.fetch_history(FakeClient(history_rows(1200)), case)
    f = pd.DataFrame(index=bars.index)
    for name in ["release_side", "near_zero_bars", "release_band", "contraction_ratio",
                 "proximity_atr", "separation_delta"]:
        f[name] = 0.0
    for name in ["keep_contraction", "keep_proximity", "keep_separation", "dense"]:
        f[name] = False
    candidates = [cases._utc(case.start) - pd.Timedelta(hours=4),
                  cases._utc(case.start), bars.index[-1]]
    f.loc[candidates, "release_side"] = [1, 1, -1]
    f.loc[candidates, "near_zero_bars"] = [20, 40, 55]
    f.loc[candidates[1], "keep_contraction"] = True
    f.loc[candidates[-1], "keep_separation"] = True
    bars.loc[candidates[1], "close"] = case.reference_price
    bars.loc[candidates[-1], "close"] = 120.0
    monkeypatch.setattr(cases, "build_features", lambda _: f)
    review = cases.review_case(bars, case)
    assert review["status"] == "ok"
    assert review["release_count"] == 2
    assert [r["side"] for r in review["releases"]] == ["long", "short"]
    assert [r["near_zero_bars"] for r in review["releases"]] == [40, 55]
    assert review["releases"][0]["reference_price_delta"] == 0
    assert review["releases"][1]["reference_price_delta"] < 0
    assert not review["screenshot_timestamp_verified"]
    assert review["releases"][0]["keep_contraction"]
    assert not review["releases"][0]["keep_proximity"]
    assert review["releases"][1]["keep_separation"]


def test_nonfinite_optional_feature_becomes_null_in_json(monkeypatch):
    case = cases.CASES[0]
    bars, _ = cases.fetch_history(FakeClient(history_rows(1200)), case)
    real = cases.build_features(bars)
    real.loc[bars.index[-1], "release_side"] = 1
    real.loc[bars.index[-1], "contraction_ratio"] = np.nan
    monkeypatch.setattr(cases, "build_features", lambda _: real)
    review = cases.review_case(bars, case)
    assert review["releases"][0]["contraction_ratio"] is None


def test_uncommitted_or_changed_builder_is_blocked_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(cases, "FROZEN_PATHS", ("builder.py",))
    (tmp_path / "builder.py").write_bytes(b"working")

    def git_changed(args, **kwargs):
        return "deadbeef\n" if args[1] == "rev-parse" else b"frozen"

    monkeypatch.setattr(cases.subprocess, "check_output", git_changed)
    with pytest.raises(RuntimeError, match="differs from frozen"):
        cases.frozen_sources(tmp_path)

    def git_untracked(args, **kwargs):
        if args[1] == "rev-parse":
            return "deadbeef\n"
        raise subprocess.CalledProcessError(128, args)

    monkeypatch.setattr(cases.subprocess, "check_output", git_untracked)
    with pytest.raises(RuntimeError, match="must be committed"):
        cases.frozen_sources(tmp_path)


def test_existing_output_prevents_client_creation_and_preserves_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cases, "EXP", tmp_path / "exp")
    monkeypatch.setattr(cases, "DATA", tmp_path / "data")
    monkeypatch.setattr(cases, "frozen_sources", lambda: {"builder_commit": "frozen"})
    output = tmp_path / "exp/results/cases"
    output.mkdir(parents=True)
    evidence = output / "manifest.json"
    evidence.write_text("do not overwrite")

    def forbidden_client(*args, **kwargs):
        pytest.fail("client must not exist before output preflight")

    monkeypatch.setattr(cases, "OKX", forbidden_client)
    with pytest.raises(FileExistsError, match="already exists"):
        cases.run()
    assert evidence.read_text() == "do not overwrite"
    assert not (tmp_path / "data").exists()


def test_fake_run_persists_all_cases_with_partial_and_failed_sources_separately(monkeypatch, tmp_path):
    monkeypatch.setattr(cases, "ROOT", tmp_path)
    monkeypatch.setattr(cases, "EXP", tmp_path / "exp")
    monkeypatch.setattr(cases, "DATA", tmp_path / "data")
    monkeypatch.setattr(cases, "frozen_sources", lambda: {
        "builder_commit": "frozen", "source_files": [{"path": "builder", "sha256": "hash"}]})
    called_rates = []

    class AllCasesClient:
        def __init__(self, rate):
            called_rates.append(rate)
            self.requests = 0
            self.clients = {case.symbol: FakeClient(history_rows(200 if case.symbol.startswith("ZEC") else 1200, case))
                            for case in cases.CASES}

        def get(self, path, params):
            self.requests += 1
            if params["instId"].startswith("XAU"):
                raise MarketError("synthetic_source_unavailable")
            return self.clients[params["instId"]].get(path, params)

    monkeypatch.setattr(cases, "OKX", AllCasesClient)
    output = cases.run()
    manifest = json.loads(output.read_text())
    assert called_rates == [2.0]
    assert manifest["builder_commit"] == "frozen"
    assert not manifest["production_eligible"]
    assert not manifest["monitor_mutations"]
    assert [r["status"] for r in manifest["cases"]] == [
        "ok", "insufficient_warmup", "source_or_validation_failed", "ok"]
    for record in manifest["cases"]:
        assert not record["screenshot_timestamp_verified"]
        if "source_path" in record:
            source = pd.read_csv(tmp_path / record["source_path"])
            assert len(source) == record["history"]["received_bars"]
            assert source["confirm"].eq(1).all()
            assert source["ts"].is_monotonic_increasing
    assert not (tmp_path / "data/cases/XAU-USDT-SWAP_4H.csv").exists()
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        cases.run()
    assert output.read_bytes() == before
