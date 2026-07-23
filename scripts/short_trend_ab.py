#!/usr/bin/env python3
"""A/B: short-only trend-exit robustness + owner-short oracle vs rule.

Default (no ``--eval-holdout``): train only (``open_time < 2026-05-04``).
  A — Fixed entry ``spread_expand_chg8`` short (next_open), sweep trend exits;
      monthly/quarterly PF & net; costs maker 0.06% + legacy 0.20%.
  B — Owner ``owner_side=short`` cuts as oracle entries + same exits; causal
      rule = same spread_expand short scan (reuse A). Compare oracle vs rule.

``--eval-holdout`` (owner approval + consumption accounting required):
  A causal short only; pre-registered exits ``no_tp_sl2_h144`` + ``trail4_atr_h144``;
  signals with ``open_time >= 2026-05-04``. No long / oracle / grid / promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/short_trend_ab.py --n-symbols 20
  PYTHONPATH=. .venv/bin/python scripts/short_trend_ab.py --n-symbols 0 \\
      --tag short_trend_ab
  PYTHONPATH=. .venv/bin/python scripts/short_trend_ab.py --eval-holdout \\
      --n-symbols 0 --tag short_trend_holdout7
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from scripts.direction_select_base_rate import (  # noqa: E402
    HOLDOUT_START,
    MIN_GAP_BARS,
    SL_MULT,
    SPREAD_CHG8_THR,
    TP_MULT,
    WARMUP,
    collect_signals,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import (  # noqa: E402
    ATR_PCT_MIN,
    HORIZON_BARS,
    BarrierOutcome,
    EntryMode,
    label_candidate,
    label_candidate_ma_exit,
    label_candidate_sl_only,
    label_candidate_time_stop,
    label_candidate_trailing,
    label_short_candidate,
    label_short_candidate_ma_exit,
    label_short_candidate_trailing,
)

SUCCESS_PF_MAKER = 1.3
ENTRY_RULE = "spread_expand_chg8"
ENTRY_FILL: EntryMode = "next_open"
TREND_HORIZON = 144
DEFAULT_SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"

ExitResolver = Callable[[pd.DataFrame, int, int], Optional[BarrierOutcome]]


def _trail(mult: float) -> ExitResolver:
    def _fn(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
        if d > 0:
            return label_candidate_trailing(
                frame, i, trail_mult=mult, horizon=TREND_HORIZON, entry=ENTRY_FILL
            )
        return label_short_candidate_trailing(
            frame, i, trail_mult=mult, horizon=TREND_HORIZON, entry=ENTRY_FILL
        )

    return _fn


def _ma(col: str) -> ExitResolver:
    def _fn(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
        if d > 0:
            return label_candidate_ma_exit(
                frame, i, ma_col=col, horizon=TREND_HORIZON, entry=ENTRY_FILL
            )
        return label_short_candidate_ma_exit(
            frame, i, ma_col=col, horizon=TREND_HORIZON, entry=ENTRY_FILL
        )

    return _fn


def _baseline(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    if d > 0:
        return label_candidate(
            frame, i, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS, entry=ENTRY_FILL
        )
    return label_short_candidate(
        frame, i, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS, entry=ENTRY_FILL
    )


def _sl_only(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    return label_candidate_sl_only(
        frame, i, direction=d, sl_mult=SL_MULT, horizon=TREND_HORIZON, entry=ENTRY_FILL
    )


def _time(hold: int) -> ExitResolver:
    def _fn(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
        return label_candidate_time_stop(
            frame, i, direction=d, hold_bars=hold, entry=ENTRY_FILL
        )

    return _fn


EXIT_ORDER = [
    "baseline_tp5_sl2_h72",
    "no_tp_sl2_h144",
    "trail2_atr_h144",
    "trail3_atr_h144",
    "trail4_atr_h144",
    "ma_ema20_h144",
    "ma_ema55_h144",
    "time_stop_48",
    "time_stop_96",
]

EXIT_RESOLVERS: dict[str, ExitResolver] = {
    "baseline_tp5_sl2_h72": _baseline,
    "no_tp_sl2_h144": _sl_only,
    "trail2_atr_h144": _trail(2.0),
    "trail3_atr_h144": _trail(3.0),
    "trail4_atr_h144": _trail(4.0),
    "ma_ema20_h144": _ma("ema20"),
    "ma_ema55_h144": _ma("ema55"),
    "time_stop_48": _time(48),
    "time_stop_96": _time(96),
}

EXIT_RULES = {
    "baseline_tp5_sl2_h72": "fixed TP5/SL2, horizon 72 (mainline control)",
    "no_tp_sl2_h144": f"SL{SL_MULT:g} only, no TP; timeout {TREND_HORIZON}",
    "trail2_atr_h144": f"ATR trailing 2×ATR14(signal); timeout {TREND_HORIZON}",
    "trail3_atr_h144": f"ATR trailing 3×ATR14(signal); timeout {TREND_HORIZON}",
    "trail4_atr_h144": f"ATR trailing 4×ATR14(signal); timeout {TREND_HORIZON}",
    "ma_ema20_h144": f"exit on close cross against ema20; timeout {TREND_HORIZON}",
    "ma_ema55_h144": f"exit on close cross against ema55; timeout {TREND_HORIZON}",
    "time_stop_48": "force flat at close of path bar 48; no TP/SL",
    "time_stop_96": "force flat at close of path bar 96; no TP/SL",
}

# Headline exits for long one-row control (A focuses short).
LONG_CONTROL_EXITS = ("baseline_tp5_sl2_h72", "no_tp_sl2_h144", "trail3_atr_h144", "ma_ema55_h144")

# Holdout#7 pre-registered A causal exits only (owner 2026-07-23).
HOLDOUT_A_EXITS = ("no_tp_sl2_h144", "trail4_atr_h144")
HOLDOUT_CONSUMPTION_N = 7


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    """add_indicators + ema20 (not in CLUSTER_EMAS; owner asked EMA20 exit)."""
    out = add_indicators(frame)
    if "ema20" not in out.columns:
        out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    return out


def _stats(gross: list[float], holds: list[int], cost: float) -> dict:
    if not gross:
        return {
            "n": 0,
            "win_rate": None,
            "mean_gross": None,
            "mean_net": None,
            "sum_net": None,
            "profit_factor": None,
            "mean_hold_bars": None,
        }
    g = np.asarray(gross, dtype=float)
    net = g - cost
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n": int(len(g)),
        "win_rate": round(float((net > 0).mean()), 4),
        "mean_gross": round(float(g.mean()), 5),
        "mean_net": round(float(net.mean()), 5),
        "sum_net": round(float(net.sum()), 5),
        "profit_factor": round(float(w / -l), 3) if l < 0 else None,
        "mean_hold_bars": round(float(np.mean(holds)), 2) if holds else None,
    }


def _period_blocks(
    times: list[pd.Timestamp],
    gross: list[float],
    holds: list[int],
    cost: float,
    *,
    freq: str,
) -> dict[str, dict]:
    """Bucket trades by month (YYYY-MM) or quarter (YYYYQn)."""
    bags: dict[str, dict[str, list]] = {}
    for t, g, h in zip(times, gross, holds):
        ts = pd.Timestamp(t)
        if freq == "M":
            key = f"{ts.year:04d}-{ts.month:02d}"
        else:
            q = (ts.month - 1) // 3 + 1
            key = f"{ts.year:04d}Q{q}"
        bag = bags.setdefault(key, {"gross": [], "hold": []})
        bag["gross"].append(g)
        bag["hold"].append(h)
    return {k: _stats(v["gross"], v["hold"], cost) for k, v in sorted(bags.items())}


def _concentration(period_stats: dict[str, dict], *, min_n: int = 30) -> dict:
    """How much of sum_net sits in the best 1–2 periods; count periods PF≥1.3."""
    rows = [(k, v) for k, v in period_stats.items() if v.get("n", 0) >= min_n]
    if not rows:
        rows = list(period_stats.items())
    nets = [(k, float(v["sum_net"] or 0.0), v.get("profit_factor")) for k, v in rows]
    total = sum(n for _, n, _ in nets)
    ranked = sorted(nets, key=lambda x: x[1], reverse=True)
    top1 = ranked[0][1] if ranked else 0.0
    top2 = sum(n for _, n, _ in ranked[:2]) if ranked else 0.0
    n_ge = sum(1 for _, _, pf in nets if pf is not None and pf >= SUCCESS_PF_MAKER)
    n_pos = sum(1 for _, n, _ in nets if n > 0)
    return {
        "n_periods": len(nets),
        "n_periods_pf_ge_1_3": n_ge,
        "n_periods_sum_net_gt_0": n_pos,
        "sum_net_total": round(total, 5),
        "top1_period": ranked[0][0] if ranked else None,
        "top1_share_of_sum_net": round(top1 / total, 3) if total > 0 else None,
        "top2_share_of_sum_net": round(top2 / total, 3) if total > 0 else None,
        "fragile_if_top2_gt_0_6": (
            bool(total > 0 and (top2 / total) > 0.6) if ranked else None
        ),
    }


def _trade_bag() -> dict[str, list]:
    return {"gross": [], "hold": [], "time": [], "symbol": []}


def _append_trade(
    bag: dict[str, list], *, gross: float, hold: int, t: pd.Timestamp, symbol: str
) -> None:
    bag["gross"].append(gross)
    bag["hold"].append(hold)
    bag["time"].append(t)
    bag["symbol"].append(symbol)


def _summarize_bag(bag: dict[str, list]) -> dict:
    maker = _stats(bag["gross"], bag["hold"], FORWARD_COST)
    legacy = _stats(bag["gross"], bag["hold"], LEGACY_P0_ROUND_TRIP)
    monthly = _period_blocks(
        bag["time"], bag["gross"], bag["hold"], FORWARD_COST, freq="M"
    )
    quarterly = _period_blocks(
        bag["time"], bag["gross"], bag["hold"], FORWARD_COST, freq="Q"
    )
    return {
        "maker_0.06pct": maker,
        "legacy_0.20pct": legacy,
        "monthly_maker": monthly,
        "quarterly_maker": quarterly,
        "concentration_monthly": _concentration(monthly, min_n=30),
        "concentration_quarterly": _concentration(quarterly, min_n=50),
    }


def _load_owner_shorts(sheet: Path) -> pd.DataFrame:
    df = pd.read_csv(sheet)
    side = df["owner_side"].astype(str).str.strip().str.lower()
    out = df[side == "short"].copy()
    out["cut_time"] = pd.to_datetime(out["cut_time"], utc=True)
    # Train only — never touch holdout cuts even if sheet mixes splits.
    out = out[out["cut_time"] < HOLDOUT_START].reset_index(drop=True)
    return out


def _oracle_cuts_for_symbol(
    enriched: pd.DataFrame, items: list[dict]
) -> list[int]:
    tt = pd.to_datetime(enriched["open_time"], utc=True)
    cuts: list[int] = []
    for it in items:
        t = pd.Timestamp(it["cut_time"])
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
        hits = np.where(tt == t)[0]
        if len(hits):
            cuts.append(int(hits[0]))
            continue
        # Fallback: cut_global if in range (same series indexing as pack builder).
        cg = it.get("cut_global")
        if cg is not None and not (isinstance(cg, float) and np.isnan(cg)):
            ci = int(cg)
            if 0 <= ci < len(enriched):
                cuts.append(ci)
    cuts = sorted(set(cuts))
    deduped: list[int] = []
    last = -10**9
    for c in cuts:
        if c - last < MIN_GAP_BARS:
            continue
        deduped.append(c)
        last = c
    return deduped


def _write_main_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _write_period_csv(path: Path, rows: list[dict]) -> None:
    _write_main_csv(path, rows)


def run_holdout_a(*, n_symbols: int, tag: str) -> int:
    """Holdout#7: A causal short only, pre-registered no_tp + trail4.

    Keeps pre-holdout bars for indicator warmup; only settles signals with
    ``open_time >= HOLDOUT_START``. Never runs long / oracle / other exits.
    """
    a_short: dict[str, dict[str, list]] = {e: _trade_bag() for e in HOLDOUT_A_EXITS}
    n_sym = 0
    t_min = t_max = None
    n_short_fires = 0
    max_h = TREND_HORIZON

    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        # Need pre-holdout history for indicators; keep full series.
        if int((times >= HOLDOUT_START).sum()) < 10:
            continue
        if len(frame) < WARMUP + max_h + 50:
            continue

        enriched = _enrich(frame)
        t = pd.to_datetime(enriched["open_time"], utc=True)
        sigs, _ = collect_signals(enriched)
        pairs = sigs.get(ENTRY_RULE, [])

        for i, d in pairs:
            if d >= 0:
                continue
            ti = t.iloc[i]
            if ti < HOLDOUT_START:
                continue
            n_short_fires += 1
            if t_min is None or ti < t_min:
                t_min = ti
            if t_max is None or ti > t_max:
                t_max = ti
            for name in HOLDOUT_A_EXITS:
                out = EXIT_RESOLVERS[name](enriched, i, -1)
                if out is None:
                    continue
                _append_trade(
                    a_short[name],
                    gross=float(out.realized_ret),
                    hold=int(out.exit_offset),
                    t=ti,
                    symbol=symbol,
                )

        n_sym += 1
        if n_sym % 40 == 0:
            print(f"  scanned {n_sym} symbols …")
        if n_symbols and n_sym >= n_symbols:
            break

    a_short_sum = {e: _summarize_bag(a_short[e]) for e in HOLDOUT_A_EXITS}

    def _cleared(block: dict) -> bool:
        pf = block["maker_0.06pct"]["profit_factor"]
        return pf is not None and pf >= SUCCESS_PF_MAKER

    cleared = []
    for e in HOLDOUT_A_EXITS:
        blk = a_short_sum[e]
        if not _cleared(blk):
            continue
        m = blk["maker_0.06pct"]
        cleared.append(
            {
                "exit": e,
                "pf_maker": m["profit_factor"],
                "pf_legacy": blk["legacy_0.20pct"]["profit_factor"],
                "sum_net_maker": m["sum_net"],
                "n": m["n"],
                "win_rate": m["win_rate"],
                "mean_hold_bars": m["mean_hold_bars"],
                "concentration_monthly": blk["concentration_monthly"],
            }
        )

    # Verdict: PF@maker ≥1.3 preference; report net too.
    if len(cleared) == len(HOLDOUT_A_EXITS):
        verdict = "过线"
    elif cleared:
        verdict = "擦线"
    else:
        # Any PF in [1.15, 1.3) or positive net with PF near line → 擦线; else 证伪
        near = False
        for e in HOLDOUT_A_EXITS:
            m = a_short_sum[e]["maker_0.06pct"]
            pf = m["profit_factor"]
            if pf is not None and pf >= 1.15:
                near = True
            if (m["sum_net"] or 0) > 0 and pf is not None and pf >= 1.0:
                near = True
        verdict = "擦线" if near else "证伪"

    main_rows: list[dict] = []
    for e in HOLDOUT_A_EXITS:
        m = a_short_sum[e]["maker_0.06pct"]
        leg = a_short_sum[e]["legacy_0.20pct"]
        conc = a_short_sum[e]["concentration_monthly"]
        main_rows.append(
            {
                "section": "A_short_holdout",
                "exit": e,
                "n": m["n"],
                "win_rate_maker": m["win_rate"],
                "sum_net_maker": m["sum_net"],
                "mean_net_maker": m["mean_net"],
                "pf_maker": m["profit_factor"],
                "pf_legacy_0.20": leg["profit_factor"],
                "mean_hold_bars": m["mean_hold_bars"],
                "top2_share_monthly": conc.get("top2_share_of_sum_net"),
                "n_months_pf_ge_1_3": conc.get("n_periods_pf_ge_1_3"),
                "fragile_top2": conc.get("fragile_if_top2_gt_0_6"),
                "exit_rule": EXIT_RULES[e],
            }
        )

    period_rows: list[dict] = []
    for e in HOLDOUT_A_EXITS:
        for period, st in a_short_sum[e]["monthly_maker"].items():
            period_rows.append(
                {
                    "section": "A_short_monthly",
                    "exit": e,
                    "period": period,
                    "n": st["n"],
                    "win_rate": st["win_rate"],
                    "sum_net_maker": st["sum_net"],
                    "pf_maker": st["profit_factor"],
                    "mean_hold_bars": st["mean_hold_bars"],
                }
            )

    out = {
        "tag": tag,
        "holdout_consumption_n": HOLDOUT_CONSUMPTION_N,
        "holdout_note": (
            f"这是该配置第 {HOLDOUT_CONSUMPTION_N} 次消耗 holdout"
            "（owner 批准：只测 A 因果空边 no_tp / trail4）"
        ),
        "question": (
            "On holdout (>=2026-05-04), does A causal spread_expand_chg8 short "
            "+ pre-registered no_tp_sl2_h144 / trail4_atr_h144 clear PF@maker≥1.3?"
        ),
        "verdict": verdict,
        "cleared_exits": cleared,
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": True,
            "eval_holdout": True,
            "entry_rule": ENTRY_RULE,
            "entry_fill": ENTRY_FILL,
            "direction": "short_only",
            "exits": list(HOLDOUT_A_EXITS),
            "costs": {"swap_maker": FORWARD_COST, "legacy_p0": LEGACY_P0_ROUND_TRIP},
            "atr_pct_min": ATR_PCT_MIN,
            "success_pf_maker": SUCCESS_PF_MAKER,
            "scope_forbidden": [
                "long",
                "oracle/owner_side",
                "new rules",
                "param grid",
                "promote",
                "ACTIVE",
                "live orders",
            ],
            "theme": "holdout#7 A causal short trend-exit confirmation",
        },
        "data": {
            "n_symbols": n_sym,
            "time_range": [
                str(t_min) if t_min is not None else None,
                str(t_max) if t_max is not None else None,
            ],
            "n_short_entry_fires": n_short_fires,
            "spread_chg8_thr": SPREAD_CHG8_THR,
        },
        "a_short": {
            e: {**a_short_sum[e], "exit_rule": EXIT_RULES[e]} for e in HOLDOUT_A_EXITS
        },
        "train_reference": {
            "source": "analysis/p_short_trend_ab.md",
            "no_tp_sl2_h144": {
                "n": 6166,
                "pf_maker": 1.415,
                "pf_legacy": 1.222,
                "sum_net_maker": 21.81,
                "win_rate": 0.207,
            },
            "trail4_atr_h144": {
                "n": 6166,
                "pf_maker": 1.359,
                "pf_legacy": 1.141,
                "sum_net_maker": 15.38,
                "win_rate": 0.420,
            },
        },
        "comparable_to": [
            "analysis/p_short_trend_ab.md",
            "analysis/p_trend_exit_base_rate.md",
        ],
    }

    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{tag}.json"
    main_csv = out_dir / f"{tag}_main.csv"
    period_csv = out_dir / f"{tag}_periods.csv"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    _write_main_csv(main_csv, main_rows)
    _write_period_csv(period_csv, period_rows)

    print(
        f"HOLDOUT#{HOLDOUT_CONSUMPTION_N} n_symbols={n_sym} "
        f"window=>={HOLDOUT_START.date()} short_fires={n_short_fires}"
    )
    print(f"holdout_note={out['holdout_note']}")
    print(f"verdict={verdict}")
    print(
        f"{'exit':24s} {'n':>6} {'win':>6} {'sum@m':>10} {'PF@m':>6} "
        f"{'PF@0.2':>6} {'hold':>6}"
    )
    for e in HOLDOUT_A_EXITS:
        m = a_short_sum[e]["maker_0.06pct"]
        leg = a_short_sum[e]["legacy_0.20pct"]
        if m["n"] == 0:
            print(f"{e:24s} {0:6d}")
            continue
        print(
            f"{e:24s} {m['n']:6d} {(m['win_rate'] or 0):6.1%} "
            f"{(m['sum_net'] or 0):+10.5f} {(m['profit_factor'] or 0):6.3f} "
            f"{(leg['profit_factor'] or 0):6.3f} {(m['mean_hold_bars'] or 0):6.1f}"
        )
        for period, st in a_short_sum[e]["monthly_maker"].items():
            print(
                f"    {period} n={st['n']:4d} PF@m={st['profit_factor']} "
                f"sum_net={st['sum_net']:+.4f}"
            )
    print(f"\nwrote {out_path}")
    print(f"wrote {main_csv}")
    print(f"wrote {period_csv}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=0, help="0 = all SWAP")
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--tag", default="short_trend_ab")
    ap.add_argument(
        "--eval-holdout",
        action="store_true",
        help=(
            "Consume holdout (>=2026-05-04): A causal short only, "
            "exits no_tp_sl2_h144 + trail4_atr_h144. Requires owner approval."
        ),
    )
    args = ap.parse_args()

    if args.eval_holdout:
        tag = args.tag if args.tag != "short_trend_ab" else "short_trend_holdout7"
        return run_holdout_a(n_symbols=args.n_symbols, tag=tag)

    owner_short = _load_owner_shorts(args.sheet)
    by_sym: dict[str, list[dict]] = {}
    for _, r in owner_short.iterrows():
        by_sym.setdefault(str(r["symbol"]), []).append(r.to_dict())
    need_oracle = set(by_sym)

    # A bags: short / long-control per exit
    a_short: dict[str, dict[str, list]] = {e: _trade_bag() for e in EXIT_ORDER}
    a_long: dict[str, dict[str, list]] = {e: _trade_bag() for e in LONG_CONTROL_EXITS}
    # B bags
    b_oracle: dict[str, dict[str, list]] = {e: _trade_bag() for e in EXIT_ORDER}
    b_rule: dict[str, dict[str, list]] = {e: _trade_bag() for e in EXIT_ORDER}

    n_sym = 0
    t_min = t_max = None
    n_short_fires = 0
    n_long_fires = 0
    n_oracle_fires = 0
    max_h = max(TREND_HORIZON, HORIZON_BARS, 96)

    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < WARMUP + max_h + 50:
            continue

        enriched = _enrich(frame)
        t = pd.to_datetime(enriched["open_time"], utc=True)
        sigs, _ = collect_signals(enriched)
        pairs = sigs.get(ENTRY_RULE, [])

        for i, d in pairs:
            ti = t.iloc[i]
            if t_min is None or ti < t_min:
                t_min = ti
            if t_max is None or ti > t_max:
                t_max = ti
            if d < 0:
                n_short_fires += 1
                for name, resolve in EXIT_RESOLVERS.items():
                    out = resolve(enriched, i, -1)
                    if out is None:
                        continue
                    _append_trade(
                        a_short[name],
                        gross=float(out.realized_ret),
                        hold=int(out.exit_offset),
                        t=ti,
                        symbol=symbol,
                    )
                    # B rule = same A short fires
                    _append_trade(
                        b_rule[name],
                        gross=float(out.realized_ret),
                        hold=int(out.exit_offset),
                        t=ti,
                        symbol=symbol,
                    )
            elif d > 0:
                n_long_fires += 1
                for name in LONG_CONTROL_EXITS:
                    out = EXIT_RESOLVERS[name](enriched, i, +1)
                    if out is None:
                        continue
                    _append_trade(
                        a_long[name],
                        gross=float(out.realized_ret),
                        hold=int(out.exit_offset),
                        t=ti,
                        symbol=symbol,
                    )

        if symbol in need_oracle:
            cuts = _oracle_cuts_for_symbol(enriched, by_sym[symbol])
            for ci in cuts:
                n_oracle_fires += 1
                ti = t.iloc[ci]
                for name, resolve in EXIT_RESOLVERS.items():
                    out = resolve(enriched, ci, -1)
                    if out is None:
                        continue
                    _append_trade(
                        b_oracle[name],
                        gross=float(out.realized_ret),
                        hold=int(out.exit_offset),
                        t=ti,
                        symbol=symbol,
                    )

        n_sym += 1
        if n_sym % 40 == 0:
            print(f"  scanned {n_sym} symbols …")
        if args.n_symbols and n_sym >= args.n_symbols:
            break

    # Summaries
    a_short_sum = {e: _summarize_bag(a_short[e]) for e in EXIT_ORDER}
    a_long_sum = {e: _summarize_bag(a_long[e]) for e in LONG_CONTROL_EXITS}
    b_oracle_sum = {e: _summarize_bag(b_oracle[e]) for e in EXIT_ORDER}
    b_rule_sum = {e: _summarize_bag(b_rule[e]) for e in EXIT_ORDER}

    def _pf(block: dict, cost_key: str = "maker_0.06pct") -> float | None:
        return block[cost_key]["profit_factor"]

    def _cleared(block: dict) -> bool:
        pf = _pf(block)
        return pf is not None and pf >= SUCCESS_PF_MAKER

    # A verdict: which short exits clear 1.3 AND not single-month propped
    a_cleared = []
    for e in EXIT_ORDER:
        blk = a_short_sum[e]
        if not _cleared(blk):
            continue
        conc = blk["concentration_monthly"]
        a_cleared.append(
            {
                "exit": e,
                "pf_maker": _pf(blk),
                "pf_legacy": _pf(blk, "legacy_0.20pct"),
                "sum_net_maker": blk["maker_0.06pct"]["sum_net"],
                "n": blk["maker_0.06pct"]["n"],
                "win_rate": blk["maker_0.06pct"]["win_rate"],
                "mean_hold_bars": blk["maker_0.06pct"]["mean_hold_bars"],
                "concentration_monthly": conc,
                "robust_pass": (
                    not conc.get("fragile_if_top2_gt_0_6")
                    and (conc.get("n_periods_pf_ge_1_3") or 0) >= 2
                ),
            }
        )

    robust_pass_exits = [r for r in a_cleared if r["robust_pass"]]
    if robust_pass_exits:
        a_verdict = "A 稳健过线（PF≥1.3 且非少数月份独撑）"
    elif a_cleared:
        a_verdict = "A 全样本过线但月份集中/不稳（张力）"
    else:
        a_verdict = "A 未过线"

    # B: oracle vs rule under same exits
    b_rows = []
    for e in EXIT_ORDER:
        o = b_oracle_sum[e]["maker_0.06pct"]
        r = b_rule_sum[e]["maker_0.06pct"]
        o_pf, r_pf = o["profit_factor"], r["profit_factor"]
        delta = (
            round(float(o_pf) - float(r_pf), 3)
            if o_pf is not None and r_pf is not None
            else None
        )
        b_rows.append(
            {
                "exit": e,
                "oracle_n": o["n"],
                "oracle_pf_maker": o_pf,
                "oracle_sum_net_maker": o["sum_net"],
                "oracle_win_rate": o["win_rate"],
                "oracle_mean_hold": o["mean_hold_bars"],
                "rule_n": r["n"],
                "rule_pf_maker": r_pf,
                "rule_sum_net_maker": r["sum_net"],
                "delta_oracle_minus_rule_pf": delta,
                "oracle_pf_legacy": b_oracle_sum[e]["legacy_0.20pct"]["profit_factor"],
                "rule_pf_legacy": b_rule_sum[e]["legacy_0.20pct"]["profit_factor"],
            }
        )

    # Significant: oracle PF clearly above rule AND oracle ≥1.3 on ≥1 trend exit
    sig_exits = [
        row
        for row in b_rows
        if row["oracle_pf_maker"] is not None
        and row["rule_pf_maker"] is not None
        and row["oracle_pf_maker"] >= SUCCESS_PF_MAKER
        and (row["delta_oracle_minus_rule_pf"] or 0) >= 0.15
    ]
    if sig_exits:
        b_verdict = "B 手标 short+趋势出显著好于规则"
    elif any(
        (row["delta_oracle_minus_rule_pf"] or 0) > 0.05
        and row["oracle_pf_maker"] is not None
        and row["oracle_pf_maker"] >= SUCCESS_PF_MAKER
        for row in b_rows
    ):
        b_verdict = "B oracle 过线且略优于规则（差距有限）"
    elif any(
        row["oracle_pf_maker"] is not None and row["oracle_pf_maker"] >= SUCCESS_PF_MAKER
        for row in b_rows
    ):
        b_verdict = "B oracle 过线但相对规则优势不显著"
    else:
        b_verdict = "B 手标 short+趋势出未显著好于规则"

    # CSV rows
    main_rows: list[dict] = []
    for e in EXIT_ORDER:
        m = a_short_sum[e]["maker_0.06pct"]
        leg = a_short_sum[e]["legacy_0.20pct"]
        conc = a_short_sum[e]["concentration_monthly"]
        main_rows.append(
            {
                "section": "A_short",
                "exit": e,
                "n": m["n"],
                "win_rate_maker": m["win_rate"],
                "sum_net_maker": m["sum_net"],
                "mean_net_maker": m["mean_net"],
                "pf_maker": m["profit_factor"],
                "pf_legacy_0.20": leg["profit_factor"],
                "mean_hold_bars": m["mean_hold_bars"],
                "top2_share_monthly": conc.get("top2_share_of_sum_net"),
                "n_months_pf_ge_1_3": conc.get("n_periods_pf_ge_1_3"),
                "fragile_top2": conc.get("fragile_if_top2_gt_0_6"),
                "exit_rule": EXIT_RULES[e],
            }
        )
    for e in LONG_CONTROL_EXITS:
        m = a_long_sum[e]["maker_0.06pct"]
        leg = a_long_sum[e]["legacy_0.20pct"]
        main_rows.append(
            {
                "section": "A_long_control",
                "exit": e,
                "n": m["n"],
                "win_rate_maker": m["win_rate"],
                "sum_net_maker": m["sum_net"],
                "mean_net_maker": m["mean_net"],
                "pf_maker": m["profit_factor"],
                "pf_legacy_0.20": leg["profit_factor"],
                "mean_hold_bars": m["mean_hold_bars"],
                "top2_share_monthly": None,
                "n_months_pf_ge_1_3": None,
                "fragile_top2": None,
                "exit_rule": EXIT_RULES[e],
            }
        )
    for row in b_rows:
        main_rows.append(
            {
                "section": "B_oracle_vs_rule",
                "exit": row["exit"],
                "n": row["oracle_n"],
                "win_rate_maker": row["oracle_win_rate"],
                "sum_net_maker": row["oracle_sum_net_maker"],
                "mean_net_maker": None,
                "pf_maker": row["oracle_pf_maker"],
                "pf_legacy_0.20": row["oracle_pf_legacy"],
                "mean_hold_bars": row["oracle_mean_hold"],
                "top2_share_monthly": None,
                "n_months_pf_ge_1_3": None,
                "fragile_top2": None,
                "exit_rule": (
                    f"oracle_n={row['oracle_n']} rule_n={row['rule_n']} "
                    f"rule_pf={row['rule_pf_maker']} Δpf={row['delta_oracle_minus_rule_pf']}"
                ),
            }
        )

    period_rows: list[dict] = []
    for e in EXIT_ORDER:
        for period, st in a_short_sum[e]["monthly_maker"].items():
            period_rows.append(
                {
                    "section": "A_short_monthly",
                    "exit": e,
                    "period": period,
                    "n": st["n"],
                    "win_rate": st["win_rate"],
                    "sum_net_maker": st["sum_net"],
                    "pf_maker": st["profit_factor"],
                    "mean_hold_bars": st["mean_hold_bars"],
                }
            )
        for period, st in a_short_sum[e]["quarterly_maker"].items():
            period_rows.append(
                {
                    "section": "A_short_quarterly",
                    "exit": e,
                    "period": period,
                    "n": st["n"],
                    "win_rate": st["win_rate"],
                    "sum_net_maker": st["sum_net"],
                    "pf_maker": st["profit_factor"],
                    "mean_hold_bars": st["mean_hold_bars"],
                }
            )

    # holdout suggestion (advice only — do not run)
    suggest_holdout = bool(robust_pass_exits) or bool(sig_exits)
    holdout_note = (
        "值得申请 holdout#7 对照一次（仅建议，本脚本不跑）"
        if suggest_holdout
        else "暂不建议消耗 holdout：train 稳健性/oracle 优势不足"
    )

    out = {
        "tag": args.tag,
        "question_a": (
            "On fixed spread_expand_chg8 short (next_open), which trend exits "
            "keep train PF@maker≥1.3 without single-month concentration?"
        ),
        "question_b": (
            "Do owner_side=short oracle entries + same trend exits beat the "
            "causal spread_expand short rule under identical exits?"
        ),
        "verdict_a": a_verdict,
        "verdict_b": b_verdict,
        "holdout_suggestion": holdout_note,
        "a_cleared_short_exits": a_cleared,
        "a_robust_pass_exits": robust_pass_exits,
        "b_compare_rows": b_rows,
        "b_significant_exits": sig_exits,
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": False,
            "entry_rule": ENTRY_RULE,
            "entry_fill": ENTRY_FILL,
            "entry_fill_note": (
                "p_entry_timing_close_vs_next: close≈next for spread-short; keep next_open"
            ),
            "costs": {"swap_maker": FORWARD_COST, "legacy_p0": LEGACY_P0_ROUND_TRIP},
            "atr_pct_min": ATR_PCT_MIN,
            "success_pf_maker": SUCCESS_PF_MAKER,
            "fragile_rule": "top2 months share of sum_net > 0.6 OR <2 months with PF≥1.3",
            "b_rule": "spread_expand_chg8 short fires (same as A)",
            "oracle_sheet": str(args.sheet),
            "n_owner_short_labels_train": int(len(owner_short)),
            "theme": "A robustness + B oracle vs rule; short focus",
        },
        "data": {
            "n_symbols": n_sym,
            "time_range": [
                str(t_min) if t_min is not None else None,
                str(t_max) if t_max is not None else None,
            ],
            "n_short_entry_fires": n_short_fires,
            "n_long_entry_fires": n_long_fires,
            "n_oracle_short_fires_deduped": n_oracle_fires,
            "spread_chg8_thr": SPREAD_CHG8_THR,
        },
        "a_short": {
            e: {**a_short_sum[e], "exit_rule": EXIT_RULES[e]} for e in EXIT_ORDER
        },
        "a_long_control": {
            e: {**a_long_sum[e], "exit_rule": EXIT_RULES[e]} for e in LONG_CONTROL_EXITS
        },
        "b_oracle": {
            e: {**b_oracle_sum[e], "exit_rule": EXIT_RULES[e]} for e in EXIT_ORDER
        },
        "b_rule": {
            e: {**b_rule_sum[e], "exit_rule": EXIT_RULES[e]} for e in EXIT_ORDER
        },
        "comparable_to": [
            "analysis/p_trend_exit_base_rate.md",
            "analysis/p_entry_timing_close_vs_next.md",
            "analysis/p_owner_side_rich_features_verdict.md",
        ],
    }

    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.tag}.json"
    main_csv = out_dir / f"{args.tag}_main.csv"
    period_csv = out_dir / f"{args.tag}_periods.csv"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    _write_main_csv(main_csv, main_rows)
    _write_period_csv(period_csv, period_rows)

    print(
        f"n_symbols={n_sym} window=<{HOLDOUT_START.date()} "
        f"short_fires={n_short_fires} oracle_fires={n_oracle_fires}"
    )
    print(f"verdict_A={a_verdict}")
    print(f"verdict_B={b_verdict}")
    print(f"holdout_suggestion={holdout_note}")
    print(
        f"{'exit':24s} {'n':>6} {'win':>6} {'sum@m':>10} {'PF@m':>6} "
        f"{'PF@0.2':>6} {'hold':>6} {'top2':>6} {'m≥1.3':>5}"
    )
    for e in EXIT_ORDER:
        m = a_short_sum[e]["maker_0.06pct"]
        leg = a_short_sum[e]["legacy_0.20pct"]
        conc = a_short_sum[e]["concentration_monthly"]
        if m["n"] == 0:
            print(f"{e:24s} {0:6d}")
            continue
        print(
            f"{e:24s} {m['n']:6d} {(m['win_rate'] or 0):6.1%} "
            f"{(m['sum_net'] or 0):+10.5f} {(m['profit_factor'] or 0):6.3f} "
            f"{(leg['profit_factor'] or 0):6.3f} {(m['mean_hold_bars'] or 0):6.1f} "
            f"{(conc.get('top2_share_of_sum_net') or 0):6.1%} "
            f"{(conc.get('n_periods_pf_ge_1_3') or 0):5d}"
        )
    print("\nB oracle vs rule (PF@maker):")
    for row in b_rows:
        print(
            f"  {row['exit']:24s} oracle n={row['oracle_n']:4d} "
            f"PF={row['oracle_pf_maker']}  rule n={row['rule_n']:5d} "
            f"PF={row['rule_pf_maker']}  Δ={row['delta_oracle_minus_rule_pf']}"
        )
    print(f"\nwrote {out_path}")
    print(f"wrote {main_csv}")
    print(f"wrote {period_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
