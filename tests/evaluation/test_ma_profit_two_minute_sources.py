"""Small, no-network checks for the future 1m-to-2m source preparation path."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pandas as pd
import pytest

import yoyo.data.ma_profit_two_minute_sources as two
import yoyo.datasets.ma_launch_owner_perfect_filter as perfect_filter
import yoyo.datasets.ma_profit_miner as miner


REAL_FORMAL_GUARD = two.formal_guard


def _csv(path: Path, minutes: list[int], *, bad: str | None = None) -> None:
    base = pd.Timestamp("2025-01-01T23:58:00Z")
    rows = []
    for index, minute in enumerate(minutes):
        price = 100 + index
        rows.append({"ts": int((base + pd.Timedelta(minutes=minute)).value // 1_000_000),
                     "open": price, "high": price + 2, "low": price - 1, "close": price + 1, "volume": 10 + index})
    if bad == "duplicate":
        rows.insert(1, dict(rows[0]))
    elif bad == "negative_volume":
        rows[0]["volume"] = -1
    elif bad == "illegal_ohlc":
        rows[0]["low"] = rows[0]["high"] + 1
    pd.DataFrame(rows).to_csv(path, index=False)


def _files(tmp_path: Path, paths: list[Path], *, shas: list[str] | None = None) -> tuple[Path, Path]:
    plan, extension = tmp_path / "plan.json", tmp_path / "extension_1m.json"
    plan.write_text('{"discovery":{"data_end_exclusive":"2025-01-02T00:10:00+00:00"}}\n')
    extension.write_text(json.dumps({"archive_max_exclusive": "2025-01-02T00:10:00+00:00",
                                     "source_plan_sha256": two.sha256(plan)}) + "\n")
    source_rows = [{"source_path": str(path), "sha256": (shas or [two.sha256(item) for item in paths])[index], "bar_minutes": 1,
                    "symbol": f"S{index}", "venue": "binance", "market": f"S{index}USDT"} for index, path in enumerate(paths)]
    sources = tmp_path / "sources_1m.json"
    sources.write_text(json.dumps({"schema_version": 1, "interval": "1m", "source_coverage_complete": True, "sources": source_rows}))
    contract = tmp_path / "two_minute_contract.json"
    cutoff = "2025-01-02T00:10:00+00:00"
    contract.write_text(json.dumps({"schema_version": 1, "interval": {"source_minutes": 1, "target_minutes": 2},
        "quality_rules": two.QUALITY_RULES, "training_eligible": False, "production_eligible": False,
        "source_manifest_sha256": two.sha256(sources), "aggregation_code_sha256": two.sha256(two.AGGREGATE_PATH),
        "original_plan_path": str(plan), "original_plan_sha256": two.sha256(plan),
        "parent_extension_1m_path": str(extension), "parent_extension_1m_sha256": two.sha256(extension),
        "cutoff_utc": cutoff, "requested_source_paths": [str(path) for path in paths],
        "requested_source_count": len(paths)}))
    return contract, sources


@pytest.fixture(autouse=True)
def _no_formal_git(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(two, "formal_guard", lambda *_: "fixture-commit")


def test_cross_utc_bucket_ohlcv_and_manifest_lineage(tmp_path: Path):
    parent = tmp_path / "one.csv"
    _csv(parent, [0, 1, 2, 3])
    contract, sources = _files(tmp_path, [parent])
    receipt = two.build(contract, sources, tmp_path / "out")
    output = Path(receipt["sources"][0]["source_path"])
    frame = pd.read_csv(output)
    assert list(frame.ts) == [int(pd.Timestamp("2025-01-01T23:58:00Z").value // 1_000_000),
                              int(pd.Timestamp("2025-01-02T00:00:00Z").value // 1_000_000)]
    assert list(frame.open) == [100, 102] and list(frame.close) == [102, 104]
    assert list(frame.high) == [103, 105] and list(frame.low) == [99, 101] and list(frame.volume) == [21, 25]
    item = receipt["sources"][0]
    assert receipt["batch_gate_open"] is True and item["parent_source_sha256"] == two.sha256(parent)
    assert item["contract_sha256"] == two.sha256(contract) and item["aggregation_code_sha256"] == two.sha256(two.AGGREGATE_PATH)


def test_output_preserves_identity_for_real_miner_load_sources_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The derived receipt itself satisfies the unchanged miner source contract."""

    monkeypatch.setattr(two, "ROOT", tmp_path)
    monkeypatch.setattr(miner, "ROOT", tmp_path)
    monkeypatch.setattr(perfect_filter, "ROOT", tmp_path)
    parent = tmp_path / "one.csv"
    _csv(parent, [0, 1])
    contract, sources = _files(tmp_path, [parent])
    original = json.loads(sources.read_text())
    original["sources"][0]["csv_sha256"] = two.sha256(parent)
    original["sources"][0]["gzip_source_path"] = "parent-only.csv.gz"
    sources.write_text(json.dumps(original))
    binding = json.loads(contract.read_text())
    binding["source_manifest_sha256"] = two.sha256(sources)
    contract.write_text(json.dumps(binding))
    receipt = two.build(contract, sources, tmp_path / "out")
    derived = receipt["sources"][0]
    assert derived["symbol"] == "S0" and derived["venue"] == "binance"
    assert derived["market"] == "S0USDT"
    assert "csv_sha256" not in derived and "gzip_source_path" not in derived
    assert derived["parent_source_metadata"]["csv_sha256"] == two.sha256(parent)
    assert miner.load_sources(tmp_path / "out" / "derived_sources.json") == [{
        "source_path": derived["source_path"], "symbol": "S0", "venue": "binance",
        "bar_minutes": 2, "sha256": derived["sha256"],
    }]


def test_partial_endpoints_and_internal_missing_minute_are_dropped_not_filled(tmp_path: Path):
    parent = tmp_path / "one.csv"
    _csv(parent, [1, 2, 3, 5, 6, 7])
    contract, sources = _files(tmp_path, [parent])
    receipt = two.build(contract, sources, tmp_path / "out")
    item = receipt["sources"][0]
    # 23:58 bucket has only minute 23:59; 00:00 is complete; 00:02 lacks
    # 00:02; 00:04 is complete.  No bar bridges either missing minute.
    assert item["leading_partial_bucket_count"] == 1
    assert item["internal_incomplete_bucket_count"] == 1
    assert item["dropped_incomplete_bucket_count"] == 2
    assert item["derived_non_bar_gaps"] == 1
    assert pd.read_csv(Path(item["source_path"])).shape[0] == 2


def test_trailing_partial_bucket_is_explicit_and_dropped(tmp_path: Path):
    parent = tmp_path / "one.csv"
    _csv(parent, [0, 1, 2])
    contract, sources = _files(tmp_path, [parent])
    receipt = two.build(contract, sources, tmp_path / "out")
    item = receipt["sources"][0]
    assert item["leading_partial_bucket_count"] == 0
    assert item["trailing_partial_bucket_count"] == 1
    assert item["dropped_incomplete_bucket_count"] == 1
    assert pd.read_csv(Path(item["source_path"])).shape[0] == 1


@pytest.mark.parametrize("bad", ["duplicate", "negative_volume", "illegal_ohlc"])
def test_bad_parent_quality_fails_the_batch_not_as_zero_candidates(tmp_path: Path, bad: str):
    parent = tmp_path / "one.csv"
    _csv(parent, [0, 1, 2, 3], bad=bad)
    contract, sources = _files(tmp_path, [parent])
    receipt = two.build(contract, sources, tmp_path / "out")
    assert receipt["batch_gate_open"] is False
    assert receipt["verified_sources"] == 0 and receipt["failed_sources"] == 1
    assert receipt["failures"][0]["parent_source_path"] == str(parent)


@pytest.mark.parametrize("timestamp", ["1735775880000.5", "1735775880000.00001", "NaN", "9223372036854775808"])
def test_fractional_nonfinite_or_out_of_range_epoch_is_rejected_before_int64_cast(tmp_path: Path, timestamp: str):
    parent = tmp_path / "one.csv"
    _csv(parent, [0, 1])
    frame = pd.read_csv(parent, dtype={"ts": "object"})
    frame.loc[0, "ts"] = timestamp
    frame.to_csv(parent, index=False)
    contract, sources = _files(tmp_path, [parent])
    receipt = two.build(contract, sources, tmp_path / "out")
    assert receipt["batch_gate_open"] is False
    assert "timestamp" in receipt["failures"][0]["reason"]


def test_no_complete_bucket_is_a_source_failure_not_an_empty_success(tmp_path: Path):
    parent = tmp_path / "one.csv"
    _csv(parent, [0])
    contract, sources = _files(tmp_path, [parent])
    receipt = two.build(contract, sources, tmp_path / "out")
    assert receipt["verified_sources"] == 0 and receipt["failed_sources"] == 1
    assert receipt["batch_gate_open"] is False
    assert "no complete two-minute bucket" in receipt["failures"][0]["reason"]


def test_parent_sha_drift_is_rejected_and_output_is_immutable(tmp_path: Path):
    parent = tmp_path / "one.csv"
    _csv(parent, [0, 1, 2, 3])
    contract, sources = _files(tmp_path, [parent], shas=["0" * 64])
    failed = two.build(contract, sources, tmp_path / "failed")
    assert failed["batch_gate_open"] is False and "SHA drift" in failed["failures"][0]["reason"]

    contract, sources = _files(tmp_path, [parent])
    output = tmp_path / "complete"
    assert two.build(contract, sources, output)["batch_gate_open"] is True
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        two.build(contract, sources, output)


def test_contract_requires_exact_complete_requested_manifest_set(tmp_path: Path):
    first, second = tmp_path / "one.csv", tmp_path / "two.csv"
    _csv(first, [0, 1])
    _csv(second, [0, 1])
    contract, sources = _files(tmp_path, [first, second])
    payload = json.loads(contract.read_text())
    payload["requested_source_paths"] = [str(first)]
    payload["requested_source_count"] = 1
    contract.write_text(json.dumps(payload))
    with pytest.raises(two.TwoMinuteSourceError, match="does not exactly cover"):
        two.build(contract, sources, tmp_path / "out")


@pytest.mark.parametrize("field, value, message", [
    ("source_plan_sha256", "0" * 64, "original-plan SHA drift"),
    ("archive_max_exclusive", "2025-01-02T00:11:00+00:00", "exceeds original plan"),
])
def test_parent_extension_must_bind_original_plan_and_not_exceed_plan_cutoff(
    tmp_path: Path, field: str, value: str, message: str
):
    parent = tmp_path / "one.csv"
    _csv(parent, [0, 1])
    contract, sources = _files(tmp_path, [parent])
    extension = Path(json.loads(contract.read_text())["parent_extension_1m_path"])
    payload = json.loads(extension.read_text())
    payload[field] = value
    extension.write_text(json.dumps(payload))
    contract_payload = json.loads(contract.read_text())
    contract_payload["parent_extension_1m_sha256"] = two.sha256(extension)
    contract.write_text(json.dumps(contract_payload))
    with pytest.raises(two.TwoMinuteSourceError, match=message):
        two.build(contract, sources, tmp_path / "out")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def test_formal_guard_requires_tracked_clean_files_and_rejects_ignored_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``git status`` alone misses ignored files, so check index membership too."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    builder, aggregate = repo / "builder.py", repo / "aggregate.py"
    contract, sources, plan, extension = (repo / name for name in ("contract.json", "sources.json", "plan.json", "extension.json"))
    for path in (builder, aggregate, contract, sources, plan, extension):
        path.write_text("{}\n")
    _git(repo, "add", "builder.py", "aggregate.py", "contract.json", "sources.json", "plan.json", "extension.json")
    _git(repo, "commit", "-m", "freeze inputs")
    monkeypatch.setattr(two, "ROOT", repo)
    monkeypatch.setattr(two, "AGGREGATE_PATH", aggregate)
    monkeypatch.setattr(two, "__file__", str(builder))
    assert REAL_FORMAL_GUARD(contract, sources, {"original_plan_path": plan, "parent_extension_1m_path": extension}) == _git(repo, "rev-parse", "HEAD")

    (repo / ".gitignore").write_text("ignored.json\n")
    ignored = repo / "ignored.json"
    ignored.write_text("{}\n")
    with pytest.raises(two.TwoMinuteSourceError, match="untracked or ignored"):
        REAL_FORMAL_GUARD(contract, ignored, {"original_plan_path": plan, "parent_extension_1m_path": extension})
