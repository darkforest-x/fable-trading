"""Promote the owner detector with the highest FROZEN-eval F1 to owner_best.pt.

Replaces queue14b's broken logic (it picked the best of only the newest run's
two bases, ignoring older-but-better versions). Scores every owner_v* weight
on datasets/owner_eval_frozen — the one ruler — and promotes the winner.
Idempotent; run after any new training.
"""
from __future__ import annotations
import json, shutil
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1

PROJECT_DIR = Path(__file__).resolve().parents[1]
EVAL = PROJECT_DIR / "datasets/owner_eval_frozen"
DST = PROJECT_DIR / "models/owner_best.pt"

def main() -> int:
    weights = sorted(PROJECT_DIR.glob("runs/detect/runs/detect/owner_v*/weights/best.pt"))
    board = []
    for w in weights:
        try:
            best, _ = evaluate_owner_f1(w, EVAL)
            board.append((best["f1"], w, best))
            print(f"{w.parent.parent.name}: frozen-F1 {best['f1']}", flush=True)
        except Exception as e:
            print(f"{w}: ERR {e}", flush=True)
    if not board:
        return 1
    board.sort(reverse=True, key=lambda x: x[0])
    f1, win, best = board[0]
    shutil.copy2(win, DST)
    DST.with_suffix(".json").write_text(json.dumps({
        "source_run": win.parent.parent.name, "frozen_eval_f1": f1,
        "metrics": best, "eval_set": "owner_eval_frozen",
        "leaderboard": [{"run": w.parent.parent.name, "f1": s} for s, w, _ in board],
    }, indent=2))
    print(f"PROMOTED {win.parent.parent.name} (frozen-F1 {f1})", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
