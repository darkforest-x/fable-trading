"""Synthetic report packaging contracts; no outcome or market files are read."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_report as subject


MARKDOWN = """# 小周期退出能否改善趋势交易

## Executive Summary

先看完整结果。

## 退出方式的比较
<!-- SOURCE: exit_search -->

这八种退出使用同一组信号，柱形比较扣费后的净收益。
<!-- EXIT_CHART -->

## 规则与限制
<!-- SOURCE: config -->

保留原来的叙述。

### 实现细节

```text
## This fenced line is not a peer section
```

## 后续问题

还需要未来数据。
"""


def synthetic_search():
    policies = list(subject.EXIT_LABELS)
    return pd.DataFrame({
        "stage": ["exit"] * 8 + ["ma_length"],
        "value": policies + [40],
        "events": [80] * 9,
        "mean_net_bp": [-80., -70., -60., -50., -40., -30., -20., -10., 9999.],
        "win_rate": [.25] * 9,
        "profit_factor": [.5] * 9,
    })


def build():
    return subject.build_artifact(MARKDOWN, synthetic_search(), generated_at="2026-09-06T00:00:00Z", fixture=True)


def test_peer_sections_preserve_prose_title_and_source_identity():
    artifact = build()
    blocks = artifact["manifest"]["blocks"]
    assert blocks[0]["body"] == "# " + artifact["manifest"]["title"]
    assert "sourceId" not in blocks[0]
    assert "sourceId" not in blocks[1]
    assert blocks[2]["sourceId"] == "exit_search"
    assert blocks[3]["type"] == "chart"
    assert blocks[4]["sourceId"] == "config"
    assert "## This fenced line is not a peer section" in blocks[4]["body"]
    assert len(blocks) == 6
    assert all(block["layout"] == "full" for block in blocks)
    assert "<!--" not in "\n".join(block.get("body", "") for block in blocks)


def test_chart_snapshot_retains_signed_values_counts_rates_and_lineage():
    original = synthetic_search()
    artifact = subject.build_artifact(MARKDOWN, original, generated_at="2026-09-06", fixture=True)
    rows = artifact["snapshot"]["datasets"]["exit_policy_results"]
    assert len(rows) == 8
    assert rows[0]["mean_net_bp"] == -10.
    assert rows[-1]["mean_net_bp"] == -80.
    assert rows[0]["n"] == 80
    assert rows[0]["win_rate"] == .25
    assert rows[0]["profit_factor"] == .5
    assert rows[0]["mean_net_bp_signed"] == "-10.00 bp"
    assert all(row["mean_net_bp"] != 9999 for row in rows)
    assert original.equals(synthetic_search())
    chart = artifact["manifest"]["charts"][0]
    assert chart["encodings"]["y"]["field"] == "mean_net_bp"
    assert "color" not in chart["encodings"]
    assert chart["palette"] == {"kind": "sequential", "name": "blue"}
    assert chart["referenceLines"][0]["value"] == 0
    source = next(item for item in artifact["sources"] if item["id"] == "exit_search")
    assert source["path"].endswith("/results/development_search.csv")
    assert source["query"]["sql"] == subject.EXIT_SQL
    assert "upstream strategy calculation is Python" in source["query"]["description"]
    assert json.loads(json.dumps(artifact, allow_nan=False)) == artifact


@pytest.mark.parametrize("change", ["no_marker", "duplicate_marker", "unknown_source", "absolute_path"])
def test_incomplete_or_unsafe_report_fails_instead_of_silently_rewriting(change):
    markdown = MARKDOWN
    if change == "no_marker":
        markdown = markdown.replace(subject.EXIT_CHART_MARKER, "")
    elif change == "duplicate_marker":
        markdown = markdown.replace(subject.EXIT_CHART_MARKER, subject.EXIT_CHART_MARKER + "\n" + subject.EXIT_CHART_MARKER)
    elif change == "unknown_source":
        markdown = markdown.replace("SOURCE: config", "SOURCE: unknown")
    else:
        markdown += "\n[private](/Users/private/trading/data.csv)\n"
    with pytest.raises(ValueError):
        subject.build_artifact(markdown, synthetic_search(), generated_at="2026-09-06")


def test_chart_requires_all_registered_policies_without_cherry_picking():
    search = synthetic_search().iloc[1:]
    with pytest.raises(ValueError, match="eight"):
        subject.reviewed_exit_rows(search)


def test_canonical_installed_plugin_validator_accepts_synthetic_artifact():
    plugin = Path.home() / ".codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/mcp/server.cjs"
    node = shutil.which("node")
    if not plugin.exists() or not node:
        pytest.skip("installed native report validator is unavailable")
    script = "const fs=require('node:fs'); const server=require(process.argv[1]); const result=server.callTool('validate_artifact', JSON.parse(fs.readFileSync(0,'utf8'))); if(!result.ok)throw Error(JSON.stringify(result)); process.stdout.write(JSON.stringify({ok:result.ok}));"
    process = subprocess.run([node, "-e", script, str(plugin)], input=json.dumps(build()), text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["ok"] is True
