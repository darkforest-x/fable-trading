from __future__ import annotations

import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = (PROJECT_DIR / "src", PROJECT_DIR / "scripts", PROJECT_DIR / ".github")
TEXT_SUFFIXES = {".py", ".sh", ".js", ".ts", ".tsx", ".html", ".yml", ".yaml", ".toml"}
BANNED_PATTERNS = {
    "legacy EMA column": re.compile(r"\bema(?:8|13|21|34|55|144|200)\b", re.IGNORECASE),
    "legacy mainline log": re.compile(r"forward_log\.csv"),
    "legacy H1 log": re.compile(r"forward_log_h1_scaled\.csv"),
    "legacy judgment dataset": re.compile(r"judgment_dataset_v2_(?:strict|expanded)\.csv"),
    "legacy SWAP dataset": re.compile(r"swap_replication/swap_tp5_sl2\.csv"),
    "legacy frozen model": re.compile(r"frozen_tp5_sl2_swap_20260709"),
}


def _runtime_files() -> list[Path]:
    files: list[Path] = []
    for root in RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in TEXT_SUFFIXES:
                continue
            if path.name.startswith("label_audit") and path.suffix == ".html":
                continue
            files.append(path)
    return files


def test_runtime_paths_have_no_legacy_ma_profile_references() -> None:
    violations: list[str] = []
    for path in _runtime_files():
        text = path.read_text(encoding="utf-8")
        for label, pattern in BANNED_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(PROJECT_DIR)}:{line}: {label}: {match.group(0)}")
    assert not violations, "legacy MA runtime references found:\n" + "\n".join(violations)
