"""Static review-site build contract: every manifest entry must be locally renderable."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "yoyo/evaluation/static/spike_v1_review/build_site.py"
SOURCE = ROOT / "yoyo/evaluation/static/spike_v1_review"


def make_data(tmp_path: Path, include_charts: bool = True) -> Path:
    data = tmp_path / "data"; charts = data / "charts"; charts.mkdir(parents=True)
    records = []
    for index in range(1, 134):
        chart_path = f"charts/{index:03d}_okx.json"
        records.append({"sequence": index, "id": f"event-{index}", "symbol": "TEST-USDT-SWAP", "timeframe": "30m", "chart_path": chart_path})
        if include_charts:
            (data / chart_path).write_text(json.dumps({"candles": [{"t": 1, "o": 1, "h": 2, "l": 1, "c": 2, "v": 3, "md": 0, "sb": 0}]}))
    (data / "manifest.json").write_text(json.dumps({"schema_version": 1, "records": records}))
    return data


def test_review_builder_copies_only_a_complete_133_record_contract(tmp_path: Path) -> None:
    data = make_data(tmp_path)
    out = tmp_path / "site"
    completed = subprocess.run([sys.executable, str(BUILD), "--data-dir", str(data), "--out-dir", str(out)], text=True, capture_output=True, check=True)
    assert json.loads(completed.stdout)["records"] == 133
    assert (out / "data/manifest.json").is_file()
    assert len(list((out / "data/charts").glob("*.json"))) == 133
    assert "Lightweight Charts" in (out / "index.html").read_text()
    assert "addCandlestickSeries" in (out / "app.js").read_text()
    assert "setMarkers" in (out / "app.js").read_text()
    assert 'src="vendor/lightweight-charts.standalone.production.js"' in (out / "index.html").read_text()
    assert 'src="https://' not in (out / "index.html").read_text()
    assert (out / "vendor/LICENSE").is_file() and (out / "vendor/NOTICE").is_file()


def test_review_builder_refuses_a_manifest_with_a_missing_chart(tmp_path: Path) -> None:
    data = make_data(tmp_path, include_charts=False)
    completed = subprocess.run([sys.executable, str(BUILD), "--data-dir", str(data), "--out-dir", str(tmp_path / "site")], text=True, capture_output=True)
    assert completed.returncode != 0
    assert "missing controlled chart data" in completed.stderr
