#!/usr/bin/env python3
"""Single-variable entry timing: next_open vs signal_close (train only).

Reuses the direction_select / launch side-causal tip base and rules. Only the
fill price convention changes; TP5/SL2/72 and costs stay fixed. No TP/SL grid.

Base: emergence tip (fast≤0.0028 & full≤0.0055, run==5) + causal direction
rules from scripts/direction_select_base_rate.py (long|short rows).

Entry modes:
  next_open     — fill at open of bar i+1 (project default; no same-print fill)
  signal_close  — fill at close of signal bar i; barrier path still from i+1

Train window only (<2026-05-04). No holdout. Does not touch ACTIVE.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/entry_timing_close_vs_next.py --n-symbols 20
  PYTHONPATH=. .venv/bin/python scripts/entry_timing_close_vs_next.py --n-symbols 0 \\
      --tag entry_timing_close_vs_next
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from scripts.direction_select_base_rate import (  # noqa: E402
    DIR_RULES,
    HOLDOUT_START,
    SUCCESS_PF_MAKER,
    TP_MULT,
    SL_MULT,
    VARIANT_ORDER,
    WARMUP,
    _pf,
    _resolve_gross,
    _side_block,
    _write_csv,
    collect_signals,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import (  # noqa: E402
    ATR_PCT_MIN,
    ENTRY_MODES,
    HORIZON_BARS,
    EntryMode,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=60, help="0 = all SWAP")
    ap.add_argument("--tag", default="entry_timing_close_vs_next")
    args = ap.parse_args()

    bags: dict[str, dict[str, dict[str, list]]] = {
        e: {v: {"gross": [], "dir": []} for v in VARIANT_ORDER} for e in ENTRY_MODES
    }
    n_sym = 0
    t_min = t_max = None
    arrange_skips_total = 0
    arrange_tips_raw = 0

    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < WARMUP + HORIZON_BARS + 50:
            continue

        enriched = add_indicators(frame)
        sigs, audit = collect_signals(enriched)
        arrange_skips_total += audit["arrange_skips"]
        arrange_tips_raw += audit["tips_raw"]

        t = pd.to_datetime(enriched["open_time"], utc=True)
        for name, pairs in sigs.items():
            for i, d in pairs:
                for entry in ENTRY_MODES:
                    entry_m: EntryMode = entry  # type: ignore[assignment]
                    g = _resolve_gross(enriched, i, d, entry=entry_m)
                    if g is None:
                        continue
                    bags[entry][name]["gross"].append(g)
                    bags[entry][name]["dir"].append(d)
                ti = t.iloc[i]
                if t_min is None or ti < t_min:
                    t_min = ti
                if t_max is None or ti > t_max:
                    t_max = ti

        n_sym += 1
        if n_sym % 40 == 0:
            print(f"  scanned {n_sym} symbols …")
        if args.n_symbols and n_sym >= args.n_symbols:
            break

    by_entry: dict[str, dict] = {}
    for entry in ENTRY_MODES:
        variants_out = {}
        for name in VARIANT_ORDER:
            block = _side_block(bags[entry][name]["gross"], bags[entry][name]["dir"])
            block["dir_rule"] = DIR_RULES[name]
            variants_out[name] = block
        best_side = None
        best_pf = None
        for name, block in variants_out.items():
            for side in ("long_only", "short_only"):
                pf = _pf(block, side, "maker_0.06pct")
                if pf is None:
                    continue
                if best_pf is None or pf > best_pf:
                    best_pf = pf
                    best_side = {"variant": name, "side": side, "pf_maker": pf}
        by_entry[entry] = {
            "best_side": best_side,
            "cleared_1_3": best_pf is not None and best_pf >= SUCCESS_PF_MAKER,
            "variants": variants_out,
        }

    # Flat comparison rows: variant × side × entry
    compare_rows = []
    for name in VARIANT_ORDER:
        for side, label in (("long_only", "long"), ("short_only", "short")):
            row = {"variant": name, "side": label, "dir_rule": DIR_RULES[name]}
            for entry in ENTRY_MODES:
                m = by_entry[entry]["variants"][name][side]["maker_0.06pct"]
                leg = by_entry[entry]["variants"][name][side]["legacy_0.20pct"]
                row[f"n_{entry}"] = m["n"]
                row[f"win_{entry}"] = m["win_rate"]
                row[f"net_maker_{entry}"] = m["mean_net"]
                row[f"pf_maker_{entry}"] = m["profit_factor"]
                row[f"pf_legacy_{entry}"] = leg["profit_factor"]
            pn = row.get("pf_maker_next_open")
            pc = row.get("pf_maker_signal_close")
            if pn is not None and pc is not None:
                row["delta_pf_maker_close_minus_next"] = round(pc - pn, 3)
            else:
                row["delta_pf_maker_close_minus_next"] = None
            compare_rows.append(row)

    best_close = by_entry["signal_close"]["best_side"]
    best_next = by_entry["next_open"]["best_side"]
    any_cleared = by_entry["next_open"]["cleared_1_3"] or by_entry["signal_close"]["cleared_1_3"]
    verdict = (
        "入场约定改变结论（至少一档过 1.3）"
        if any_cleared
        else "入场约定未救出可交易边（两档皆 <1.3）"
    )

    out = {
        "tag": args.tag,
        "question": (
            "Same causal direction-select rules / TP5/SL2/72 / costs; only entry "
            "fill convention changes (next_open vs signal_close). Does either "
            "side clear PF@maker ≥ 1.3?"
        ),
        "success_criterion": {
            "pf_maker_ge": SUCCESS_PF_MAKER,
            "single_variable": "entry fill only; TP/SL not swept",
        },
        "verdict": verdict,
        "rule_base": (
            "direction_select_base_rate / launch emergence tip + causal "
            "direction rules (ctrl_fixed / arrange / range_break / spread_expand)"
        ),
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": False,
            "entries_compared": list(ENTRY_MODES),
            "exit": f"TP{TP_MULT:g}/SL{SL_MULT:g}/{HORIZON_BARS}bar",
            "tp_sl_swept": False,
            "costs": {"swap_maker": FORWARD_COST, "legacy_p0": LEGACY_P0_ROUND_TRIP},
            "atr_pct_min": ATR_PCT_MIN,
            "path_bars": "always from signal_i+1 for both entry modes",
        },
        "data": {
            "n_symbols": n_sym,
            "time_range": [
                str(t_min) if t_min is not None else None,
                str(t_max) if t_max is not None else None,
            ],
            "arrange_tips_raw": arrange_tips_raw,
            "arrange_skips_raw": arrange_skips_total,
        },
        "best_by_entry": {
            "next_open": best_next,
            "signal_close": best_close,
        },
        "by_entry": {
            e: {
                "best_side": by_entry[e]["best_side"],
                "cleared_1_3": by_entry[e]["cleared_1_3"],
                "variants": by_entry[e]["variants"],
            }
            for e in ENTRY_MODES
        },
        "compare_rows": compare_rows,
        "tp_sl_note": (
            "Triple-barrier TP/SL optimization is a separate variable; not run. "
            "Awaiting owner approval before any TP/SL grid."
        ),
    }

    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.tag}.json"
    csv_path = out_dir / f"{args.tag}_compare.csv"
    next_csv = out_dir / f"{args.tag}_next_open.csv"
    close_csv = out_dir / f"{args.tag}_signal_close.csv"

    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(compare_rows[0].keys()) if compare_rows else [])
        if compare_rows:
            w.writeheader()
            w.writerows(compare_rows)
    _write_csv(next_csv, by_entry["next_open"]["variants"])
    _write_csv(close_csv, by_entry["signal_close"]["variants"])

    print(
        f"n_symbols={n_sym}  window=<{HOLDOUT_START.date()}  "
        f"entry=next_open|signal_close  TP5/SL2/72  verdict={verdict}"
    )
    print(f"best next_open  = {best_next}")
    print(f"best signal_close = {best_close}")
    hdr = (
        f"{'variant':22s} {'side':5s} "
        f"{'n_n':>5} {'PF@n':>6} {'PF@c':>6} {'Δc-n':>6}"
    )
    print(hdr)
    for row in compare_rows:
        print(
            f"{row['variant']:22s} {row['side']:5s} "
            f"{row['n_next_open']:5d} "
            f"{(row['pf_maker_next_open'] or 0):6.3f} "
            f"{(row['pf_maker_signal_close'] or 0):6.3f} "
            f"{(row['delta_pf_maker_close_minus_next'] or 0):+6.3f}"
        )
    print(f"\nwrote {out_path}")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
