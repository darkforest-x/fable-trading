"""No-network contracts for the isolated 1m archive extension."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from yoyo.data.ma_profit_archive_extension import ArchiveExtensionError, run


ARCHIVER = '''
import json
from pathlib import Path
def sha256_file(path):
 import hashlib
 return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def admitted_symbols(exchange_info, *, before):
 return exchange_info["symbols"]
def fetch_symbol(row, *, output_dir, archive_start, archive_end_inclusive, archive_max_exclusive, interval):
 symbol=row["symbol"]
 count=output_dir / "fetch_count.txt"
 count.write_text(str(int(count.read_text()) + 1) if count.exists() else "1")
 csv=output_dir / "series" / f"binance_um_{symbol}_{interval}_2.csv"
 csv.parent.mkdir(parents=True, exist_ok=True)
 csv.write_text("open_time,open,high,low,close,volume\\n2020-01-01T00:00:00Z,1,2,1,2,3\\n2020-01-01T00:01:00Z,2,3,2,3,4\\n")
 audit=output_dir / "audits" / f"{symbol}.json"
 audit.parent.mkdir(parents=True, exist_ok=True)
 audit.write_text(json.dumps({"source":"upstream"}))
 return {"status":"complete","output_path":str(csv),"output_sha256":sha256_file(csv),"rows":2,"first_time":"2020-01-01T00:00:00+00:00","last_time":"2020-01-01T00:01:00+00:00","non_bar_gaps":0,"months_requested":["2020-01"],"months_complete":["2020-01"],"months_missing":[]}
'''


def _setup(tmp_path: Path, *, symbols: list[dict] | None = None) -> tuple[Path, Path, Path]:
    archiver = tmp_path / "archiver.py"
    archiver.write_text(ARCHIVER)
    exchange = tmp_path / "exchange_info.json"
    exchange.write_text(json.dumps({"symbols": symbols or [{"symbol": "AAAUSDT", "pair": "AAAUSDT", "status": "TRADING", "onboard_time": "2019-01-01T00:00:00Z"}]}))
    config = tmp_path / "extension.json"
    config.write_text(json.dumps({"schema_version": 1, "interval": "1m", "archive_start": "2019-09-01T00:00:00Z", "archive_end_inclusive": "2026-08-01T00:00:00Z", "archive_max_exclusive": "2026-09-01T00:00:00Z", "archiver_sha256": hashlib.sha256(archiver.read_bytes()).hexdigest(), "exchange_info_path": str(exchange), "exchange_info_sha256": hashlib.sha256(exchange.read_bytes()).hexdigest(), "expected_admitted_symbols": 1, "source_plan_sha256": "pinned-plan"}))
    return archiver, config, tmp_path / "out"


def test_compresses_deterministically_and_resumes_without_refetch(tmp_path) -> None:
    archiver, config, out = _setup(tmp_path)
    first = run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)
    assert first["source_coverage_complete"] and (out / "fetch_count.txt").read_text() == "1"
    source = json.loads((out / "sources_archive_1m.json").read_text())["sources"][0]
    gzip_path = out / source["source_path"]
    assert hashlib.sha256(gzip.open(gzip_path, "rb").read()).hexdigest() == source["csv_sha256"]
    assert not list((out / "series").glob("*.csv"))
    second = run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)
    assert second["source_coverage_complete"] and (out / "fetch_count.txt").read_text() == "1"
    gzip_path.write_bytes(b"not a gzip")
    third = run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)
    assert third["source_coverage_complete"] and (out / "fetch_count.txt").read_text() == "2"


def test_snapshot_sha_drift_fails_before_fetch(tmp_path) -> None:
    archiver, config, out = _setup(tmp_path)
    data = json.loads(config.read_text()); data["archiver_sha256"] = "0" * 64; config.write_text(json.dumps(data))
    with pytest.raises(ArchiveExtensionError, match="snapshot SHA drift"):
        run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)


def test_existing_output_rejects_changed_run_binding(tmp_path) -> None:
    archiver, config, out = _setup(tmp_path)
    run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)
    data = json.loads(config.read_text()); data["source_plan_sha256"] = "different-plan"; config.write_text(json.dumps(data))
    with pytest.raises(ArchiveExtensionError, match="run binding drift"):
        run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)


def test_failed_symbol_closes_source_coverage_gate(tmp_path) -> None:
    archiver, config, out = _setup(tmp_path)
    archiver.write_text(ARCHIVER.replace('return {"status":"complete",', 'return {"status":"no_data",'))
    data = json.loads(config.read_text()); data["archiver_sha256"] = hashlib.sha256(archiver.read_bytes()).hexdigest(); config.write_text(json.dumps(data))
    summary = run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)
    assert summary["status"] == "failed" and summary["symbols_failed"] == 1
    assert json.loads((out / "sources_archive_1m.json").read_text())["source_coverage_complete"] is False


def test_external_csv_is_never_deleted_and_is_a_recorded_failure(tmp_path) -> None:
    archiver, config, out = _setup(tmp_path)
    external = tmp_path / "outside.csv"
    external.write_text("keep me\n")
    source = archiver.read_text().replace('csv=output_dir / "series" / f"binance_um_{symbol}_{interval}_2.csv"', f'csv=Path({str(external)!r})')
    archiver.write_text(source)
    data = json.loads(config.read_text()); data["archiver_sha256"] = hashlib.sha256(archiver.read_bytes()).hexdigest(); config.write_text(json.dumps(data))
    summary = run(archiver_path=archiver, config_path=config, output_dir=out, workers=1)
    assert summary["status"] == "failed" and external.exists()
    failure = json.loads((out / "compressed_audits/AAAUSDT.json").read_text())
    assert "outside this run" in failure["error"]
