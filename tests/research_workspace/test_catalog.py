"""Behavioral checks for bounded, read-only research catalog loading."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from yoyo.research_workspace import catalog as catalog_module
from yoyo.research_workspace.catalog import Catalog, MAX_CSV_BYTES, MAX_TABLE_ROWS


def _root(tmp_path: Path, experiments: str) -> Path:
    (tmp_path / "experiments").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "experiments" / "registry.yaml").write_text(experiments, encoding="utf-8")
    (tmp_path / "artifacts" / "registry.yaml").write_text("schema_version: 1\nartifacts: []\n", encoding="utf-8")
    feature_path = tmp_path / "yoyo" / "layers" / "l2_judgment" / "features.py"
    feature_path.parent.mkdir(parents=True)
    feature_path.write_text(
        'import pandas as pd  # Catalog must parse, never import this module.\n'
        'FEATURE_COLUMNS = [\n    "ma_spread_pct",\n    "ret_4",\n]\n',
        encoding="utf-8",
    )
    return tmp_path


def _experiment_yaml(experiment_id: str = "exp-fixture-v1") -> str:
    return (
        "schema_version: 1\nexperiments:\n"
        f"  - experiment_id: {experiment_id}\n"
        "    status: rejected\n"
        "    question: fixture question\n"
        "    artifacts: []\n"
        "    training_eligible: false\n"
        "    production_eligible: false\n"
    )


def test_registry_yaml_is_cached_by_mtime_ns_and_size(tmp_path, monkeypatch):
    root = _root(tmp_path, _experiment_yaml())
    original = catalog_module.yaml.safe_load
    calls = 0

    def counted_load(stream):
        nonlocal calls
        calls += 1
        return original(stream)

    monkeypatch.setattr(catalog_module.yaml, "safe_load", counted_load)
    catalog = Catalog(root)
    assert catalog.experiments()[0]["id"] == "exp-fixture-v1"
    assert catalog.experiments()[0]["title"] == "fixture question"
    assert calls == 1

    registry = root / "experiments" / "registry.yaml"
    registry.write_text(_experiment_yaml("exp-fixture-v2-longer"), encoding="utf-8")
    assert catalog.experiments()[0]["id"] == "exp-fixture-v2-longer"
    assert calls == 2


def test_duplicate_experiment_ids_fail_closed(tmp_path):
    root = _root(
        tmp_path,
        "schema_version: 1\nexperiments:\n"
        "  - experiment_id: exp-duplicate\n"
        "  - experiment_id: exp-duplicate\n",
    )
    with pytest.raises(ValueError, match="duplicate experiment_id"):
        Catalog(root).experiments()


def test_factors_parse_ast_literal_and_link_registered_research(tmp_path):
    root = _root(
        tmp_path,
        "schema_version: 1\nexperiments:\n"
        "  - experiment_id: exp-spike-v128-entry-clock-20260923-v1\n"
        "    status: completed\n"
        "    question: Historical clock bucket\n"
        "    artifacts: []\n",
    )
    factors = Catalog(root).factors()
    implemented = [factor for factor in factors if factor["status"] == "implemented"]
    research = [factor for factor in factors if factor["status"] == "research"]

    assert [(factor["id"], factor["source_line"]) for factor in implemented] == [
        ("l2.ma_spread_pct", 3),
        ("l2.ret_4", 4),
    ]
    assert all(factor["training_eligible"] is False for factor in factors)
    assert all(factor["production_eligible"] is False for factor in factors)
    assert research[0]["id"] == "research.spike.entry_clock_beijing"
    assert research[0]["experiment_ids"] == ["exp-spike-v128-entry-clock-20260923-v1"]
    assert research[0]["source_path"] == "experiments/registry.yaml"
    assert research[0]["source_line"] == 3


def test_evidence_is_bounded_hashes_files_and_skips_escaping_symlinks(tmp_path):
    root = _root(tmp_path / "repo", _experiment_yaml())
    experiment_dir = root / "experiments" / "active" / "exp-fixture-v1"
    summary_dir = experiment_dir / "summary_v1"
    summary_dir.mkdir(parents=True)
    (experiment_dir / "config.json").write_text(
        json.dumps({"strategy": "safe-value", "apiKey": "do-not-return"}), encoding="utf-8"
    )
    csv_path = summary_dir / "metrics.csv"
    csv_path.write_text("metric,value\n" + "ok,1\n" * 305, encoding="utf-8")
    (summary_dir / "nested").mkdir()
    (summary_dir / "nested" / "ignored.csv").write_text("x\n1\n", encoding="utf-8")
    (experiment_dir / "runtime.csv").write_text("secret\n1\n", encoding="utf-8")
    (experiment_dir / "too_large.csv").write_bytes(b"x" * (MAX_CSV_BYTES + 1))
    private_dir = experiment_dir / "private"
    private_dir.mkdir()
    (private_dir / "hidden.csv").write_text("x\n1\n", encoding="utf-8")

    outside = tmp_path / "outside.json"
    outside.write_text('{"strategy":"outside"}', encoding="utf-8")
    try:
        (experiment_dir / "delivery_receipt.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = Catalog(root).evidence("exp-fixture-v1")
    table = result["tables"][0]
    assert table["path"] == "experiments/active/exp-fixture-v1/summary_v1/metrics.csv"
    assert len(table["rows"]) == MAX_TABLE_ROWS
    assert table["total_rows"] == 305
    assert table["truncated"] is True
    assert len(table["sha256"]) == 64
    assert result["config"]["experiments/active/exp-fixture-v1/config.json"] == {"strategy": "safe-value"}
    assert all("outside.json" not in item["path"] for item in result["files"])
    assert all("runtime.csv" not in item["path"] and "hidden.csv" not in item["path"] for item in result["files"])
    assert any("2 MB" in note for note in result["notes"])
    assert any("越界" in note for note in result["notes"])


def test_missing_experiment_directory_is_explicitly_not_zero(tmp_path):
    root = _root(tmp_path, _experiment_yaml())
    result = Catalog(root).evidence("exp-fixture-v1")
    assert result["tables"] == []
    assert result["files"] == []
    assert any("不代表结果为零" in note for note in result["notes"])


def test_invalid_experiment_path_cannot_escape_root(tmp_path):
    root = _root(tmp_path, _experiment_yaml("../outside"))
    result = Catalog(root).evidence("../outside")
    assert result["tables"] == []
    assert result["files"] == []
    assert any("不在允许" in note for note in result["notes"])
