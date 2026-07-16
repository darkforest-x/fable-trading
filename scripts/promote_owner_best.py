"""Promote the owner detector with the highest FROZEN-eval F1 to owner_best.pt.

Replaces queue14b's broken logic (it picked the best of only the newest run's
two bases, ignoring older-but-better versions). Scores every eligible owner_v*
weight on datasets/owner_eval_frozen — the one ruler — and promotes the winner.
Idempotent; run after any new training.

Eligibility (2026-07-16): a run may only enter the leaderboard if we can PROVE
its training set held no frozen-eval symbol. Until today this file only *said*
so in a comment while the code globbed every run and took the top score — it
would have promoted v5_from_v4, whose 0.663 is inflated because 11% of its
training images are eval symbols. Unverifiable runs are excluded rather than
trusted: assuming a model was clean is exactly how that 0.663 was believed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import yaml

from src.detection.owner_eval import evaluate_owner_f1

PROJECT_DIR = Path(__file__).resolve().parents[1]
EVAL = PROJECT_DIR / "datasets/owner_eval_frozen"
DST = PROJECT_DIR / "models/owner_best.pt"


def is_eval_symbol(sym: str) -> bool:
    """Frozen-eval membership: same sha1%7 rule as scripts/frozen_eval_set.py."""
    return int(hashlib.sha1(sym.encode()).hexdigest(), 16) % 7 == 0


def dataset_of(run_dir: Path) -> Path | None:
    """The dataset a run actually trained on, per its own args.yaml.

    Runs fitted on the LAN 3060 record a Windows path (C:\\fable\\datasets\\x)
    that means nothing here, so fall back to matching the dataset by NAME under
    datasets/. The name is the identity: scripts/train_on_3060.sh ships that exact
    directory and the receipt is checked on arrival (image/label/box counts must
    match), so a same-named local dataset is the same data the remote trained on.
    """
    args = run_dir / "args.yaml"
    if not args.exists():
        return None
    try:
        data = yaml.safe_load(args.read_text()).get("data")
    except Exception:
        return None
    if not data:
        return None
    p = Path(str(data).replace("\\", "/"))
    p = p.parent if p.name.endswith(".yaml") else p
    if p.exists():
        return p
    local = PROJECT_DIR / "datasets" / p.name
    return local if local.exists() else None


def eval_leak_count(dataset: Path) -> int | None:
    """How many training images belong to frozen-eval symbols. None = can't tell."""
    if dataset is None or not dataset.exists():
        return None
    n = 0
    seen_any = False
    for split in ("train", "val"):
        d = dataset / "images" / split
        if not d.exists():
            continue
        for img in d.glob("*.png"):
            seen_any = True
            if is_eval_symbol(img.stem.rsplit("_", 1)[0]):
                n += 1
    return n if seen_any else None


def main() -> int:
    weights = sorted(PROJECT_DIR.glob("runs/detect/runs/detect/owner_v*/weights/best.pt"))
    board, rejected = [], []
    for w in weights:
        run_dir = w.parent.parent
        run = run_dir.name
        leak = eval_leak_count(dataset_of(run_dir))
        if leak is None:
            rejected.append((run, "训练集无法验证(args.yaml 或数据集已不在)"))
            continue
        if leak > 0:
            rejected.append((run, f"训练集含 {leak} 张 eval 币种图 -> F1 虚高"))
            continue
        try:
            best, _ = evaluate_owner_f1(w, EVAL)
        except Exception as e:  # noqa: BLE001 -- a broken weight must not stop promotion
            rejected.append((run, f"评估失败: {e}"))
            continue
        board.append((best["f1"], w, best))
        print(f"  ✅ {run}: frozen-F1 {best['f1']:.3f}", flush=True)

    for run, why in rejected:
        print(f"  ⛔ {run}: {why}", flush=True)

    if not board:
        print("没有合格模型 —— 不动 owner_best", flush=True)
        return 1

    board.sort(reverse=True, key=lambda x: x[0])
    f1, win, best = board[0]
    shutil.copy2(win, DST)
    DST.with_suffix(".json").write_text(
        json.dumps(
            {
                "source_run": win.parent.parent.name,
                "frozen_eval_f1": f1,
                "metrics": best,
                "eval_set": "owner_eval_frozen(47币种从未参训)",
                "eligibility": "仅限已验证训练集不含 eval 币种的 run",
                "leaderboard": [{"run": w.parent.parent.name, "f1": s} for s, w, _ in board],
                "rejected": [{"run": r, "reason": why} for r, why in rejected],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nPROMOTED {win.parent.parent.name} (frozen-F1 {f1:.3f})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
