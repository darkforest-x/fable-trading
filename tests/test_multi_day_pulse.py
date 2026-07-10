from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PULSE = ROOT / "scripts" / "multi_day_pulse.sh"


def test_multi_day_pulse_exposes_current_e21b_paths() -> None:
    script = PULSE.read_text(encoding="utf-8")

    assert "FABLE_E21B_RESULTS" in script
    assert "dense_15m_full_s_e21b_hsv0" in script
    assert "p2a_e21b_hsv0_report.md" in script
    assert "FO hard_e21" not in script
