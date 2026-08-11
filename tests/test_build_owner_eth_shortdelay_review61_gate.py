from pathlib import Path

from scripts.build_owner_eth_shortdelay_review61_gate import render_html


def _row(index: int) -> dict:
    return {
        "calibration_id": f"R{index:03d}_event",
        "symbol": "ETH_USDT_SWAP",
        "proposal_image_path": "analysis/output/example.png",
        "future_review_image_path": "analysis/output/example_future.png",
        "legacy_core_local_in_frozen_window": [8, 14],
        "proposal_selected_local_in_frozen_window": [3, 7],
        "proposal_core_bars": 5,
        "post_bars": 3,
        "proposal_win_len": 16,
    }


def test_review_html_contains_unselected_decision_controls(monkeypatch, tmp_path):
    image = tmp_path / "analysis/output/example.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    future_image = tmp_path / "analysis/output/example_future.png"
    future_image.write_bytes(b"png")
    source = tmp_path / "proposal_manifest.jsonl"
    source.write_text("source")
    output = tmp_path / "analysis/html/review.html"
    rows = [_row(index) for index in range(1, 62)]

    import scripts.build_owner_eth_shortdelay_review61_gate as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    page = render_html(rows, source, output)

    assert page.count('class="sample-card"') == 61
    assert page.count('data-role="train"') == 61
    assert page.count('data-role="future"') == 61
    assert page.count("✓ 认可新框") == 61
    assert page.count("↔ 还要改") == 61
    assert page.count("✕ 剔除") == 61
    assert "浏览后全部认可" in page
    assert "复制确认结果" in page
    assert "未来48根" in page
    assert "2,020个train正事件" in page
    assert "不是3–5天" in page
    assert "owner_decisions_preselected" not in page
    assert "data-decision=\"pending\"" in page
    assert "训练输入" in page
