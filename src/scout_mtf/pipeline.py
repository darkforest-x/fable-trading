"""End-to-end: rank pool → multi-TF votes → composite grades."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.scout_mtf.rank import (
    DEFAULT_VOLUME_TOP,
    RankedSymbol,
    build_scan_pool,
    pool_as_dicts,
)
from src.scout_mtf.tf_scan import TIMEFRAMES, composite_from_votes, scan_symbol_all_tf

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT / "data" / "scout_mtf"


def run_scout(
    *,
    top_n: int = 12,
    min_vol_usdt: float = 5_000_000.0,
    include_loss: bool = True,
    max_symbols: int | None = None,
    volume_top: int = DEFAULT_VOLUME_TOP,
    include_majors: bool = True,
    bars: tuple[str, ...] = TIMEFRAMES,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Run one radar pass. Writes latest.json under data/scout_mtf/."""
    pool = build_scan_pool(
        top_n=top_n,
        min_vol_usdt=min_vol_usdt,
        include_loss=include_loss,
        volume_top=volume_top,
        include_majors=include_majors,
        max_symbols=max_symbols,
    )

    results: list[dict[str, Any]] = []
    for item in pool:
        votes = scan_symbol_all_tf(item.inst_id, bars=bars)
        comp = composite_from_votes(
            votes,
            rank_side=item.rank_side,
            rank=item.rank,
            chg24h_pct=item.chg24h_pct,
        )
        results.append(
            {
                "symbol": item.symbol,
                "inst_id": item.inst_id,
                "last": item.last,
                "chg24h_pct": round(item.chg24h_pct, 3),
                "vol24h_usdt": round(item.vol24h_usdt, 0),
                "rank_side": item.rank_side,
                "rank": item.rank,
                "grade": comp["grade"],
                "composite": comp["composite"],
                "detail": comp,
            }
        )

    # Keep majors/volume pinned on top (by grade within group), then movers
    def sort_key(r: dict[str, Any]) -> tuple:
        side = r.get("rank_side") or ""
        pin = 0 if side in {"major", "volume"} else 1
        side_ord = {"major": 0, "volume": 1, "gain": 2, "loss": 3}.get(side, 9)
        return (pin, side_ord, -ord_grade(r["grade"]), -float(r.get("composite") or 0))

    results.sort(key=sort_key)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "branch": "scout_mtf",
        "disclaimer": (
            "Side branch only. Not mainline forward_log / ACTIVE. "
            "Pool = pinned majors (BTC/ETH/SOL…) + top-10 24h volume + 24h gain/loss movers. "
            "Multi-TF votes are rule-dense + regime, not full YOLO+LGB. "
            "Grades A/B/C are research radar, not trade orders."
        ),
        "timeframes": list(bars),
        "pool_size": len(pool),
        "pool": pool_as_dicts(pool),
        "pool_breakdown": {
            "major": sum(1 for x in pool if x.rank_side == "major"),
            "volume": sum(1 for x in pool if x.rank_side == "volume"),
            "gain": sum(1 for x in pool if x.rank_side == "gain"),
            "loss": sum(1 for x in pool if x.rank_side == "loss"),
        },
        "results": results,
        "summary": {
            "A": sum(1 for r in results if r["grade"] == "A"),
            "B": sum(1 for r in results if r["grade"] == "B"),
            "C": sum(1 for r in results if r["grade"] == "C"),
        },
    }

    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    latest = out / "latest.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (out / f"scan_{stamp}.json").write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
    report["output_path"] = str(latest)
    return report


def ord_grade(g: str) -> int:
    return {"A": 3, "B": 2, "C": 1}.get(g, 0)


def format_table(report: dict[str, Any], *, limit: int = 30) -> str:
    lines = [
        f"scout_mtf @ {report.get('generated_at')}  "
        f"A={report['summary']['A']} B={report['summary']['B']} C={report['summary']['C']}",
        f"{'grade':<5} {'symbol':<22} {'side':<5} {'r':>2} {'chg%':>7} {'comp':>6}  "
        f"{'1m':>4} {'3m':>4} {'5m':>4} {'15m':>4} {'30m':>4}  notes",
        "-" * 100,
    ]
    for r in report.get("results", [])[:limit]:
        tf = (r.get("detail") or {}).get("tf") or {}
        def v(bar: str) -> str:
            x = tf.get(bar) or {}
            if not x.get("ok"):
                return "  ·"
            mark = "D" if x.get("dense") else "·"
            return f"{x.get('vote', 0):.2f}"[1:] if False else f"{mark}{int(round(100 * float(x.get('vote') or 0))):02d}"

        # compact: D72 = dense vote 0.72; ·55 = not dense
        def cell(bar: str) -> str:
            x = tf.get(bar) or {}
            if not x.get("ok"):
                return " ·  "
            d = "D" if x.get("dense") else "."
            return f"{d}{int(round(100 * float(x.get('vote') or 0))):02d}"

        notes = []
        for bar in ("15m", "30m"):
            x = tf.get(bar) or {}
            if x.get("note"):
                notes.append(f"{bar}:{x['note']}")
        lines.append(
            f"{r['grade']:<5} {r['symbol']:<22} {r['rank_side']:<5} {r['rank']:>2} "
            f"{r['chg24h_pct']:>+7.2f} {r['composite']:>6.3f}  "
            f"{cell('1m'):>4} {cell('3m'):>4} {cell('5m'):>4} {cell('15m'):>4} {cell('30m'):>4}  "
            f"{';'.join(notes[:2])}"
        )
    lines.append("cell = [D|.] + vote*100  (D=dense hit on that TF)")
    return "\n".join(lines)
