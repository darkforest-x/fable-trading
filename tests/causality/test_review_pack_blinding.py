"""The review page must not leak what it is asking the reviewer to reconstruct.

A page that shows the original box, the quality grade, or which items are
repeats has answered its own question. Blinding is enforced at render time --
the values never enter the page or the manifest, only the separate truth file.
"""
import json

from yoyo.layers.l1_detection.onset.events.review_pack import (
    PROTOCOL_VERSION,
    STAGE_CHOICES,
    build_pack,
    frame_plan,
)


def _events(n=4):
    out = []
    for i in range(n):
        out.append({
            "event_id": f"evt_{i:06d}",
            "source_pattern_id": f"dense_{i:05d}",
            "source": "golden_pool",
            "symbol": "ETH_USDT_SWAP",
            "timeframe": "15m",
            "original_box": {"xywhn": [0.75, 0.5, 0.12, 0.2],
                             "box_start_i": 8830 + i, "box_end_i": 8850 + i},
            "quality_label": "A",
        })
    return out


def _fake_render(symbol, tip_i, window_bars):
    return f"frames/{symbol}_{tip_i}.png"


def test_frame_plan_starts_before_box_and_ends_after():
    tips = frame_plan(box_start_i=100, box_end_i=120, lead_bars=12, trail_bars=6)
    assert tips[0] == 88
    assert tips[-1] == 126
    assert tips == sorted(tips)


def test_page_and_manifest_contain_no_answers(tmp_path):
    """Checks the payload, not the whole file.

    The manifest's top-level `blinding` note names the withheld fields on
    purpose, so a raw substring scan of the file flags itself. What matters is
    that no item the page actually reads carries an answer.
    """
    build_pack(_events(4), _fake_render, tmp_path, repeat_frac=0.5, seed=1)
    page = (tmp_path / "index.html").read_text()
    items = json.loads((tmp_path / "manifest.json").read_text())["items"]
    payload = json.dumps(items, ensure_ascii=False)
    for leak in ("box_start_i", "box_end_i", "quality_label", "signal_i",
                 "golden_pool", "is_repeat_of", "source_pattern_id", "evt_0000"):
        assert leak not in page, f"page leaks {leak}"
        assert leak not in payload, f"manifest items leak {leak}"


def test_truth_file_holds_the_answers(tmp_path):
    build_pack(_events(3), _fake_render, tmp_path, repeat_frac=0.0, seed=1)
    truth = json.loads((tmp_path / "_truth_do_not_open.json").read_text())
    assert truth
    any_row = next(iter(truth.values()))
    for k in ("event_id", "quality_label", "box_start_i", "box_end_i", "is_repeat_of"):
        assert k in any_row


def test_review_id_does_not_encode_event_id(tmp_path):
    """Adjacent rv_xxx / rv_xxx_r would tell the reviewer two items are the same."""
    build_pack(_events(3), _fake_render, tmp_path, repeat_frac=1.0, seed=2)
    truth = json.loads((tmp_path / "_truth_do_not_open.json").read_text())
    for rid, row in truth.items():
        assert row["event_id"] not in rid
        assert rid.startswith("rv_")


def test_repeats_are_present_but_indistinguishable_in_page(tmp_path):
    build_pack(_events(4), _fake_render, tmp_path, repeat_frac=1.0, seed=3)
    truth = json.loads((tmp_path / "_truth_do_not_open.json").read_text())
    n_rep = sum(1 for r in truth.values() if r["is_repeat_of"])
    assert n_rep > 0
    page = (tmp_path / "index.html").read_text()
    assert "is_repeat" not in page and "repeat_of" not in page


def test_no_frame_extends_past_its_own_tip(tmp_path):
    """Each frame is rendered for one tip; later bars must not exist in it."""
    seen = []

    def rec(symbol, tip_i, window_bars):
        seen.append((symbol, tip_i, window_bars))
        return f"frames/{symbol}_{tip_i}.png"

    build_pack(_events(1), rec, tmp_path, lead_bars=5, trail_bars=2, seed=4,
               repeat_frac=0.0)
    tips = [t for _, t, _ in seen]
    assert tips == sorted(tips)
    # the renderer is asked for one tip at a time; nothing requests a future span
    assert len(set(tips)) == len(tips)


def test_stage_choices_are_the_five_from_the_protocol():
    assert STAGE_CHOICES == ("NOT_YET", "FORMING", "ONSET_NOW", "INVALID", "UNCERTAIN")
    assert PROTOCOL_VERSION == "causal_onset_review_v1"


def test_localstorage_key_is_namespaced_per_pack(tmp_path):
    """Shared keys made round 2 of the quality packs report round 1's progress."""
    build_pack(_events(2), _fake_render, tmp_path, seed=5, repeat_frac=0.0)
    page = (tmp_path / "index.html").read_text()
    assert f"causal_onset::{tmp_path.name}" in page


def test_commit_keys_carry_direction(tmp_path):
    """F and D both commit an onset; they differ only in which way it breaks.

    Direction cannot be recovered later -- backfill reaches a third of events and
    geometry is circular -- so the page has to capture it at commit time.
    """
    build_pack(_events(), _fake_render, tmp_path, repeat_frac=0.0)
    page = (tmp_path / "index.html").read_text()
    assert "setOnset('short')" in page
    assert "setOnset('long')" in page
    assert "k==='f'" in page and "k==='d'" in page


def test_export_records_side_and_its_source(tmp_path):
    build_pack(_events(), _fake_render, tmp_path, repeat_frac=0.0)
    page = (tmp_path / "index.html").read_text()
    assert "side:a.side??null" in page
    assert 'side_source:a.side?"causal_onset_review":null' in page


def test_side_is_not_in_the_manifest_or_the_page_data(tmp_path):
    """Blinding still holds: the page must not be told a direction up front."""
    build_pack(_events(), _fake_render, tmp_path, repeat_frac=0.0)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    for item in manifest["items"]:
        assert "side" not in item
