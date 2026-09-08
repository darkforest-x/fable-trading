"""Synthetic cross-coin freeze, clocks, cash accounting and orchestration tests."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import altcoin_transfer_research as transfer


def write_csv(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def bars(start="2025-12-01", end="2026-01-11", *, launch=False):
    index = pd.date_range(start, end, inclusive="left", freq="15min", tz="UTC")
    close = np.full(len(index), 100.)
    if launch:
        close[index >= pd.Timestamp("2026-01-02", tz="UTC")] = 112.
    opening = np.r_[close[0], close[:-1]]
    return pd.DataFrame(dict(ts=index.asi8//1_000_000, open=opening,
        high=np.maximum(opening, close)+1, low=np.minimum(opening, close)-1,
        close=close, volume=10., open_time=index))


@pytest.fixture
def scene(tmp_path, monkeypatch):
    canonical = tmp_path/"canonical"
    canonical.mkdir()
    crypto = tmp_path/"crypto.json"
    crypto.write_text(json.dumps(dict(eligible_instruments=[f"{s}-USDT-SWAP" for s in
        ["BTC", "AAA", "BBB", "CCC", "MISSING", "SOPH", "USELESS"]],
        frozen_at="2026-09-03T17:23:19Z", rule="instCategory=1; no outcome ranking")))
    main = tmp_path/"main.json"
    main.write_text(json.dumps(dict(sources=[dict(symbol="BTC")])))
    selection = tmp_path/"selection"; selection.mkdir()
    (selection/"summary.csv").write_text("synthetic,summary\n1,2\n")
    lock = dict(selection_only="development2023-24 plus validation2025",
        summary_sha256=transfer.sha(selection/"summary.csv"),
        selections=[dict(minutes=m, allowed_audit_arms=sorted(transfer.ALLOWED[m])) for m in transfer.PERIODS])
    (selection/"selection_lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(transfer, "CRYPTO_SHA", transfer.sha(crypto))
    monkeypatch.setattr(transfer, "MAIN_SHA", transfer.sha(main))
    monkeypatch.setattr(transfer, "SELECTION_SHA", transfer.sha(selection/"selection_lock.json"))
    monkeypatch.setattr(transfer, "SELECTION_CORRECTION_PENDING", False)
    monkeypatch.setattr(transfer, "END", "2026-01-11T00:00:00Z")
    monkeypatch.setattr(transfer, "FOLDS", (("audit_pre", "2026-01-01", "2026-01-03"),
        ("audit_seen", "2026-01-03", "2026-01-07"), ("external_recent", "2026-01-07", transfer.END)))
    monkeypatch.setattr(transfer, "committed_sources", lambda: dict(commit="synthetic", hashes={}))
    def source(symbol, frame):
        return write_csv(canonical/f"okx_{symbol}_USDT_SWAP_15m_{len(frame)}.csv", frame)
    def prepare(out=None):
        out = out or tmp_path/"prepared"
        report = transfer.prepare_dataset(out, crypto_path=crypto, main_path=main,
            canonical_dir=canonical, selection_dir=selection)
        return out, report
    return dict(root=tmp_path, canonical=canonical, crypto=crypto, main=main,
                selection=selection, source=source, prepare=prepare)


def test_universe_is_filename_intersection_without_length_or_price_selection(scene):
    for name in ("BTC", "SOPH", "USELESS", "STOCK"):
        scene["source"](name, bars())
    scene["source"]("AAA", bars().iloc[:1])
    scene["source"]("BBB", bars())
    universe = transfer.freeze_universe(scene["crypto"], scene["main"], scene["canonical"])
    assert universe["pool_symbols"] == ["AAA", "BBB"]
    assert universe["capital_sleeves"] == 2
    assert universe["missing_source_symbols"] == ["CCC", "MISSING"]
    assert "AAA" in universe["pool_symbols"]  # one row cannot remove a member


def test_preparation_keeps_failed_sleeves_and_only_readonly_references(scene, monkeypatch):
    good = scene["source"]("AAA", bars())
    missing = scene["source"]("BBB", bars().drop(index=12))
    short = scene["source"]("CCC", bars().iloc[:-1])
    before = {p: transfer.sha(p) for p in (good, missing, short)}
    monkeypatch.setattr(transfer.parent, "evaluate_symbol", lambda *a, **k: pytest.fail("outcomes during prepare"))
    out, manifest = scene["prepare"]()
    assert manifest["universe"]["capital_sleeves"] == 3
    assert manifest["complete_symbols"] == 1 and manifest["unavailable_symbols"] == 2
    assert {r["symbol"]: r.get("reason") for r in manifest["symbols"]} == {
        "AAA": None, "BBB": "missing_intervals", "CCC": "incomplete_requested_tail"}
    assert manifest["symbols"][0]["source"]["sha256"] == before[good]
    assert not list(out.rglob("*.csv"))
    assert list(out.iterdir()) == [out/"manifest.json"]
    assert {p: transfer.sha(p) for p in before} == before
    with pytest.raises(ValueError, match="already exists"):
        scene["prepare"](out)


def test_prefix_never_parses_future_prices_or_following_tail_timestamps(scene):
    path = scene["source"]("AAA", bars())
    with path.open("ab") as handle:
        handle.write(b"1768089600000,future,prices,are,not,parsed,ignored\nnot-a-timestamp,also,not,parsed\n")
    # The first excluded row is 2026-01-11 00:00 UTC; the rest is opaque hash input.
    out, manifest = scene["prepare"]()
    assert manifest["complete_symbols"] == 1
    assert manifest["symbols"][0]["source"]["sha256"] == transfer.sha(path)
    assert manifest["symbols"][0]["audit"]["end_close"] == transfer._utc(transfer.END).isoformat()


def test_invalid_geometry_and_ambiguous_files_do_not_shrink_pool(scene):
    invalid = bars(); invalid.loc[0, "high"] = 90
    scene["source"]("AAA", invalid)
    scene["source"]("BBB", bars())
    scene["source"]("BBB", bars().iloc[:-1])
    _, manifest = scene["prepare"]()
    assert manifest["universe"]["capital_sleeves"] == 2
    assert manifest["complete_symbols"] == 0
    assert {r["reason"] for r in manifest["symbols"]} == {"invalid_ohlcv", "ambiguous_multiple_canonical_files"}


def test_manifest_identity_and_lock_are_fixed(scene):
    scene["source"]("AAA", bars())
    scene["crypto"].write_text(scene["crypto"].read_text()+"\n")
    with pytest.raises(ValueError, match="identity changed"):
        scene["prepare"]()
    scene["crypto"].write_text(scene["crypto"].read_text().rstrip())
    selection = scene["selection"]/"selection_lock.json"
    selection.write_text(selection.read_text()+"\n")
    with pytest.raises(ValueError, match="selection lock changed"):
        scene["prepare"]()


def test_cash_sleeves_cover_unavailable_history_and_preserve_utc_alignment():
    calendar = pd.date_range("2026-01-01T01:00:00Z", periods=4, freq="1h")
    traded = pd.Series([.99, 1.20], index=calendar[2:])
    equity = transfer.combine_sleeves({"AAA": traded}, calendar, 4)
    assert equity.tolist() == pytest.approx([1, 1, .9975, 1.05])
    assert transfer.combine_sleeves({}, calendar, 4).eq(1).all()
    with pytest.raises(ValueError, match="Missing"):
        transfer.combine_sleeves({"AAA": traded.iloc[:-1]}, calendar, 4)
    with pytest.raises(ValueError, match="denominator"):
        transfer.combine_sleeves({"AAA": traded, "BBB": traded}, calendar, 1)


def synthetic_result(folder, *, valid=True):
    folder.mkdir(parents=True)
    calendar = pd.date_range("2026-01-31T01:00:00Z", "2026-02-01T00:00:00Z", freq="1h")
    values = np.linspace(1, 1.2, len(calendar))
    frame = pd.DataFrame({"audit_pre|all_core|base": values,
                          "audit_pre|all_core|base|adverse": values-.02}, index=calendar)
    frame.to_pickle(folder/"curves.pkl.gz", compression="gzip")
    event = dict(symbol="AAA", minutes=60, fold="audit_pre", cohort="all_core", arm="base",
        valid=valid, natural_exit=True, censored=False, portfolio_selected=True,
        net_bp=120, gross_bp=140, excess_bp=20, control_mean_net_bp=100,
        month="2026-01", event_id="AAA_60_base_1", mfe_r=2, net_r=1, capture_ratio=.5)
    pd.DataFrame([event]).to_csv(folder/"events.csv.gz", index=False)
    pd.DataFrame().to_csv(folder/"controls.csv.gz", index=False)
    (folder/"diagnostics.json").write_text("[]")
    (folder/"completion.json").write_text("{}")


def test_summary_uses_fixed_denominator_matched_coverage_and_prior_month_close(tmp_path):
    synthetic_result(tmp_path/"AAA_60")
    manifest = dict(universe=dict(capital_sleeves=2), symbols=[dict(symbol="AAA"), dict(symbol="BBB")])
    summary = transfer.summarize_transfer(tmp_path, manifest, periods=(60,),
        folds=(("audit_pre", "2026-01-31", "2026-02-01"),))
    row = summary.loc[summary.cohort.eq("all_core") & summary.arm.eq("base")].iloc[0]
    assert row.portfolio_net_pct == pytest.approx(10)
    assert row.capital_sleeves == 2 and row.curve_symbols == 1
    assert row.matched_case_net_bp == 120 and row.control_net_bp == 100 and row.excess_bp == 20
    assert row.n == row.natural_n == row.matched_n == 1
    monthly = pd.read_csv(tmp_path/"monthly_portfolio.csv")
    assert set(monthly.month) == {"2026-01"}
    baseline = monthly.loc[monthly.cohort.eq("all_core") & monthly.arm.eq("base")].iloc[0]
    assert baseline.initial_capital_change_bp == pytest.approx(1000)
    assert summary.loc[summary.cohort.eq("high_vol"), "n"].eq(0).all()
    assert summary.loc[summary.cohort.eq("high_vol"), "portfolio_net_pct"].eq(0).all()


def test_summary_rejects_invalid_event_rows(tmp_path):
    synthetic_result(tmp_path/"AAA_60", valid=False)
    with pytest.raises(ValueError, match="Invalid events"):
        transfer.summarize_transfer(tmp_path, dict(universe=dict(capital_sleeves=1), symbols=[dict(symbol="AAA")]),
            periods=(60,), folds=(("audit_pre", "2026-01-31", "2026-02-01"),))


def test_real_modules_synthetic_integration_keeps_short_4h_cash_and_resumes(scene):
    scene["source"]("AAA", bars(launch=True))
    scene["source"]("BBB", bars().drop(index=12))
    prepared, manifest = scene["prepare"]()
    out = scene["root"]/"results"
    summary = transfer.evaluate_dataset(prepared, out, selection_dir=scene["selection"])
    assert len(summary) == 60
    assert summary.capital_sleeves.eq(2).all()
    assert summary.loc[summary.minutes.eq(240), "portfolio_net_pct"].eq(0).all()
    assert summary.loc[summary.minutes.eq(240), "n"].eq(0).all()
    assert summary.loc[summary.minutes.eq(60) & summary.cohort.eq("all_core"), "n"].sum() > 0
    assert not list((prepared/"feature_cache").rglob("*.pkl.gz"))
    receipt = (out/"AAA_60/completion.json").read_bytes()
    replay = transfer.evaluate_dataset(prepared, out, selection_dir=scene["selection"])
    pd.testing.assert_frame_equal(summary, replay)
    assert (out/"AAA_60/completion.json").read_bytes() == receipt
    (out/"AAA_60/events.csv.gz").write_bytes(b"modified")
    with pytest.raises(ValueError, match="Completed symbol output changed"):
        transfer.evaluate_dataset(prepared, out, selection_dir=scene["selection"])


def test_changed_referenced_source_is_rejected_before_any_outcome(scene, monkeypatch):
    path = scene["source"]("AAA", bars())
    prepared, _ = scene["prepare"]()
    path.write_bytes(path.read_bytes()+b"\n")
    monkeypatch.setattr(transfer.parent, "evaluate_symbol", lambda *a, **k: pytest.fail("outcomes before identity guard"))
    with pytest.raises(ValueError, match="History identity changed"):
        transfer.evaluate_dataset(prepared, scene["root"]/"results", selection_dir=scene["selection"])


def test_source_guard_checks_one_fixed_commit_and_all_loaded_adapters(tmp_path, monkeypatch):
    actual = {str(path.relative_to(transfer.ROOT)) for path in transfer._guard_paths()}
    assert {"yoyo/evaluation/altcoin_trend_research.py", "yoyo/data/altcoin_history.py",
            "yoyo/evaluation/imacd_formation_research.py", "yoyo/evaluation/imacd_startup_research.py",
            "yoyo/evaluation/altcoin_accounting.py", "yoyo/monitor/signals.py"}.issubset(actual)
    source = tmp_path/"builder.py"; source.write_text("a = 1\n")
    monkeypatch.setattr(transfer, "ROOT", tmp_path)
    monkeypatch.setattr(transfer, "_guard_paths", lambda: [source])
    calls = []
    def git(args, **kwargs):
        calls.append(args)
        return "abc123\n" if args[1] == "rev-parse" else source.read_bytes()
    monkeypatch.setattr(transfer.subprocess, "check_output", git)
    assert transfer.committed_sources()["commit"] == "abc123"
    assert calls[1] == ["git", "show", "abc123:builder.py"]
    monkeypatch.setattr(transfer.subprocess, "check_output", lambda args, **kw:
        "abc123\n" if args[1] == "rev-parse" else b"old")
    with pytest.raises(ValueError, match="Commit all"):
        transfer.committed_sources()


def test_cli_refuses_uncommitted_sources_before_any_input_or_output(tmp_path, monkeypatch):
    def reject(): raise ValueError("Commit all transfer builders")
    monkeypatch.setattr(transfer, "committed_sources", reject)
    assert transfer.main(["prepare", "--out-dir", str(tmp_path/"out")]) == 1
    assert not (tmp_path/"out").exists()


def test_old_lock_cannot_run_while_membership_clock_correction_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "SELECTION_CORRECTION_PENDING", True)
    with pytest.raises(ValueError, match="corrected decision-close"):
        transfer._selection(tmp_path/"not_read")
