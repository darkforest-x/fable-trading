"""Audit: does box_right_frac≈0.5 wrongly contradict Owner's「框=tip」?

Owner claims the labeled boxes mark tips (not confirmation). Prior reports cite
median box_right_frac≈0.50 as if that disproves tip intent. That metric only
says where the box sits *inside the stored training image window* — it does not
say whether the market state at the box's RIGHT EDGE (cut_global) is tip-like.

This script separates three questions (all pre-holdout, no promote):

  A. Geometry of the stored image: where is box right edge in the PNG window?
  B. Market state AT cut_global: still dense? already expanding?
  C. Relative timing: is cut at local density trough, or after expansion onset?

If A is mid-window but B/C look tip-like, the 0.50 stat was unfair to Owner.
If B/C look confirmatory, intent≠mechanical tip under our FAST/FULL defs
(still not a license to dismiss Owner's words — report the gap honestly).

Source: owner_side_review/review_sheet.csv (L/S labeled). Holdout barred.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import iter_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
WARMUP = 288
WINDOW = 200
FAST_MAX, FULL_MAX = 0.0028, 0.0055
SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
OUT = PROJECT / "analysis" / "output" / "tip_mapping_owner_intent_audit.json"


def _q(s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return {"n": 0}
    return {
        "n": int(len(s)),
        "median": round(float(s.median()), 4),
        "p25": round(float(s.quantile(0.25)), 4),
        "p75": round(float(s.quantile(0.75)), 4),
        "mean": round(float(s.mean()), 4),
        "frac_ge_0.9": round(float((s >= 0.9).mean()), 4),
        "frac_ge_0.8": round(float((s >= 0.8).mean()), 4),
    }


def main() -> int:
    sheet = pd.read_csv(SHEET, dtype=str).fillna("")
    sheet["owner_side"] = sheet["owner_side"].str.strip().str.lower()
    labeled = sheet[sheet["owner_side"].isin(["long", "short"])].copy()
    for c in ("cut_global", "bar_b0", "bar_b1", "width_bars", "box_right_frac",
              "yolo_xc", "yolo_w", "spread_chg8", "fast_spread", "full_spread"):
        if c in labeled.columns:
            labeled[c] = pd.to_numeric(labeled[c], errors="coerce")

    # A — image geometry (no market needed)
    geom = {
        "n_labeled": int(len(labeled)),
        "box_right_frac": _q(labeled["box_right_frac"]),
        "width_bars": _q(labeled["width_bars"]),
        "yolo_xc": _q(labeled["yolo_xc"]),
        "note": (
            "box_right_frac=(b1+0.5)/WINDOW on the STORED image; "
            "mid-window ≠ 'Owner meant confirmation'. cut = win_start+b1."
        ),
    }

    need = set(labeled["symbol"].unique())
    series_by: dict[str, pd.DataFrame] = {}
    for _src, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if symbol in need:
            series_by[symbol] = frame
    print(f"loaded {len(series_by)}/{len(need)} symbols", flush=True)

    # Precompute indicators once per symbol (add_features for spread_chg8).
    from src.judgment.features import add_features  # noqa: E402

    ind_by: dict[str, pd.DataFrame] = {}
    for sym, df in series_by.items():
        ind_by[sym] = add_features(add_indicators(df))
    print(f"indicators ready for {len(ind_by)} symbols", flush=True)

    rows = []
    skips = {"no_series": 0, "holdout": 0, "oob": 0, "bad_ind": 0}
    for _, r in labeled.iterrows():
        sym = r["symbol"]
        cut = r["cut_global"]
        if sym not in ind_by or not np.isfinite(cut):
            skips["no_series"] += 1
            continue
        ind = ind_by[sym]
        times = pd.to_datetime(ind["open_time"], utc=True)
        cut_i = int(cut)
        if cut_i < WARMUP or cut_i >= len(ind) - 8:
            skips["oob"] += 1
            continue
        if times.iloc[cut_i] >= HOLDOUT_START:
            skips["holdout"] += 1
            continue
        fast = ind["fast_spread"].to_numpy(dtype=float)
        full = ind["full_spread"].to_numpy(dtype=float)
        chg8 = ind["spread_chg8"].to_numpy(dtype=float)
        if not np.isfinite(fast[cut_i]) or not np.isfinite(full[cut_i]):
            skips["bad_ind"] += 1
            continue
        # local trough of fast_spread in [cut-24, cut]
        lo = max(WARMUP, cut_i - 24)
        window_fast = fast[lo:cut_i + 1]
        trough_offset = int(cut_i - (lo + int(np.nanargmin(window_fast))))  # 0 = at trough
        # bars until expansion: first bar after cut where fast > FAST_MAX * 1.5
        expand_i = None
        for j in range(cut_i, min(cut_i + 24, len(fast))):
            if np.isfinite(fast[j]) and fast[j] > FAST_MAX * 1.5:
                expand_i = j
                break
        bars_to_expand = (expand_i - cut_i) if expand_i is not None else None
        dense_now = bool(fast[cut_i] <= FAST_MAX and full[cut_i] <= FULL_MAX)
        dense_recent = bool(
            ((fast[max(0, cut_i - 8):cut_i + 1] <= FAST_MAX)
             & (full[max(0, cut_i - 8):cut_i + 1] <= FULL_MAX)).any()
        )
        sc = float(chg8[cut_i]) if np.isfinite(chg8[cut_i]) else np.nan
        rows.append({
            "symbol": sym,
            "owner_side": r["owner_side"],
            "box_right_frac": float(r["box_right_frac"]) if np.isfinite(r["box_right_frac"]) else np.nan,
            "width_bars": float(r["width_bars"]) if np.isfinite(r["width_bars"]) else np.nan,
            "fast_at_cut": float(fast[cut_i]),
            "full_at_cut": float(full[cut_i]),
            "spread_chg8_at_cut": sc,
            "dense_at_cut": dense_now,
            "dense_in_prior_8": dense_recent,
            "trough_offset_bars": trough_offset,
            "bars_to_expand": bars_to_expand,
            "implied_tip_aligned_right_frac": round((WINDOW - 0.5) / WINDOW, 4),
        })

    pos = pd.DataFrame(rows)
    print(f"audit rows={len(pos)} skips={skips}", flush=True)

    def rate(mask) -> float:
        return round(float(mask.mean()), 4) if len(mask) else float("nan")

    expanding = pos["spread_chg8_at_cut"].dropna()
    market = {
        "n": int(len(pos)),
        "skips": skips,
        "dense_at_cut_rate": rate(pos["dense_at_cut"]),
        "dense_in_prior_8_rate": rate(pos["dense_in_prior_8"]),
        "trough_offset_bars": _q(pos["trough_offset_bars"]),
        "bars_to_expand": _q(pos["bars_to_expand"].dropna()),
        "spread_chg8_at_cut": _q(expanding),
        "spread_chg8_gt_0_rate": rate(expanding > 0) if len(expanding) else None,
        "fast_at_cut": _q(pos["fast_at_cut"]),
        "full_at_cut": _q(pos["full_at_cut"]),
        "by_side": {},
    }
    for side, g in pos.groupby("owner_side"):
        market["by_side"][side] = {
            "n": int(len(g)),
            "dense_at_cut_rate": rate(g["dense_at_cut"]),
            "spread_chg8_gt_0_rate": rate(g["spread_chg8_at_cut"].dropna() > 0),
            "box_right_frac_median": round(float(g["box_right_frac"].median()), 4),
        }

    # Cross: mid-image boxes that are still dense at cut (= tip intent surviving A)
    mid = pos["box_right_frac"].between(0.35, 0.65)
    market["mid_image_and_dense_at_cut"] = {
        "n_mid": int(mid.sum()),
        "dense_rate_among_mid": rate(pos.loc[mid, "dense_at_cut"]),
        "interpretation": (
            "If high: Owner tip cut sits mid-PNG only because images aren't tip-cropped; "
            "market tip state still true. If low: cut is confirmatory even when mid-image."
        ),
    }

    # Verdict gates (honest, mechanical)
    dense_rate = market["dense_at_cut_rate"]
    chg_pos = market["spread_chg8_gt_0_rate"]
    if dense_rate >= 0.7 and (chg_pos is None or chg_pos <= 0.45):
        verdict = "METRIC_UNFAIR_TO_OWNER"
        blurb = (
            "Most cuts still mechanically dense and not yet expanding → "
            "box_right_frac≈0.5 was about image crop, not confirmation intent."
        )
    elif dense_rate <= 0.55 and chg_pos is not None and chg_pos >= 0.55:
        verdict = "MECHANICAL_TIP_GAP"
        blurb = (
            "Under FAST/FULL defs, cuts look confirmatory (many already expanding). "
            "Does NOT license dismissing Owner's tip claim — means our tip proxy ≠ his eye, "
            "or labels mix tip+confirm. Mapping/threshold audit, not intent denial."
        )
    else:
        verdict = "MIXED"
        blurb = (
            "Partial dense / partial expand — neither clean tip nor clean confirm under "
            "current thresholds. Do not weaponize box_right_frac against Owner."
        )

    out = {
        "holdout": "FORBIDDEN",
        "source_sheet": str(SHEET),
        "A_image_geometry": geom,
        "B_C_market_at_cut": market,
        "verdict": verdict,
        "blurb": blurb,
        "owner_claim_policy": (
            "Respect 框=tip. Contradictory metrics → audit mapping/thresholds first."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"verdict": verdict, "dense_at_cut": dense_rate,
                      "chg8_gt0": chg_pos, "out": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
