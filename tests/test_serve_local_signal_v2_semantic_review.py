import json

import pytest

from scripts.serve_local_signal_v2_semantic_review import append_verdict, load_verdicts


def test_verdict_autosave_is_append_only_and_latest_wins(tmp_path) -> None:
    (tmp_path / "review_manifest.jsonl").write_text(
        json.dumps({"review_id": "S001"}) + "\n", encoding="utf-8"
    )
    append_verdict(tmp_path, "S001", "YES")
    append_verdict(tmp_path, "S001", "NO")
    assert load_verdicts(tmp_path) == {"S001": "NO"}
    assert len((tmp_path / "owner_verdicts.jsonl").read_text().splitlines()) == 2


def test_verdict_rejects_unknown_id_and_value(tmp_path) -> None:
    (tmp_path / "review_manifest.jsonl").write_text(
        json.dumps({"review_id": "S001"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown review_id"):
        append_verdict(tmp_path, "S999", "YES")
    with pytest.raises(ValueError, match="YES, NO, or SKIP"):
        append_verdict(tmp_path, "S001", "MAYBE")
