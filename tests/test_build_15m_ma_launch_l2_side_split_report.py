"""Report contract for the 15m L2 side-split experiment."""
from __future__ import annotations

import json

from scripts.build_15m_ma_launch_l2_side_split_report import (
    PREREG_PATH,
    TRAINING_PATH,
    VERIFY_PATH,
    build_report,
)


def test_report_states_failure_and_separates_l1_5_from_economic_l2() -> None:
    report = build_report(
        json.loads(PREREG_PATH.read_text(encoding="utf-8")),
        json.loads(TRAINING_PATH.read_text(encoding="utf-8")),
        json.loads(VERIFY_PATH.read_text(encoding="utf-8")),
    )
    assert "总判定：**FAIL**" in report
    assert "L1.5 不是收益模型" in report
    assert "LONG 独立回归" in report
    assert "SHORT 独立回归" in report
    assert "未读取holdout" in report
    assert "置换检验" in report
    assert "匹配随机对照" in report
