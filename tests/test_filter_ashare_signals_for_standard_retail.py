import pandas as pd

from scripts.filter_ashare_signals_for_standard_retail import (
    annotate_access,
    classify_board,
    normalize_code,
    restricted_name_reason,
)


def test_board_classification_uses_a_conservative_main_board_whitelist():
    assert classify_board("600336", 1) == "SH_MAIN"
    assert classify_board("000682", 0) == "SZ_MAIN"
    assert classify_board("688310", 1) == "STAR"
    assert classify_board("689009", 1) == "STAR"
    assert classify_board("300652", 0) == "CHINEXT"
    assert classify_board("302132", 0) == "CHINEXT"
    assert classify_board("920166", 0) == "BSE"
    assert classify_board("830001", 0) == "BSE"
    assert classify_board("430001", 0) == "BSE"


def test_code_normalization_preserves_leading_zeroes():
    assert normalize_code("682") == "000682"
    assert normalize_code(682) == "000682"
    assert normalize_code("000682") == "000682"


def test_restricted_names_are_not_presented_as_basic_account_eligible():
    assert restricted_name_reason("*ST 示例") == "risk_warning_name"
    assert restricted_name_reason("ST示例") == "risk_warning_name"
    assert restricted_name_reason("示例退") == "delisting_name"
    assert restricted_name_reason("澳柯玛") == ""


def test_access_annotation_keeps_only_plain_main_board_rows():
    rows = pd.DataFrame(
        [
            {"code": "600336", "market": 1, "name": "澳柯玛", "direction": "LONG", "chart": "a.png", "chart_sha256": "a"},
            {"code": "000682", "market": 0, "name": "东方电子", "direction": "SHORT", "chart": "b.png", "chart_sha256": "b"},
            {"code": "688310", "market": 1, "name": "迈得医疗", "direction": "SHORT", "chart": "c.png", "chart_sha256": "c"},
            {"code": "300652", "market": 0, "name": "雷迪克", "direction": "SHORT", "chart": "d.png", "chart_sha256": "d"},
            {"code": "920166", "market": 0, "name": "海圣医疗", "direction": "LONG", "chart": "e.png", "chart_sha256": "e"},
            {"code": "600000", "market": 1, "name": "*ST 示例", "direction": "LONG", "chart": "f.png", "chart_sha256": "f"},
        ]
    )
    annotated = annotate_access(rows)
    assert annotated.loc[annotated["retail_eligible"], "code"].tolist() == ["600336", "000682"]
    assert annotated.loc[~annotated["retail_eligible"], "board"].tolist() == [
        "STAR",
        "CHINEXT",
        "BSE",
        "SH_MAIN",
    ]
