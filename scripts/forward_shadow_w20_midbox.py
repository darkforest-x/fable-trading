#!/usr/bin/env python3
"""Shadow forward book for w20 midbox hardneg detector (option B).

Isolated from mainline:
  - never writes data/forward_log.csv
  - never touches models/ACTIVE or models/owner_best.pt
  - never calls the executor

Protocol:
  - L1 only: tip window W=24, conf>=0.30, tip-edge TIP_EDGE_BARS, MIN_GAP
  - weights: hardneg_c1 best.pt (override with --weights)
  - long TP5/SL2/H72 on ATR14 at signal bar; entry = next open
  - score = YOLO conf; no L2 judgment (w20 has no trained L2)
  - ledger: data/forward_log_w20_midbox_shadow.csv

Target: accumulate 100 closed trades for prospective detector gate.

Usage:
  PYTHONPATH=.:$HOME/yoyo-trading YOYO_DATA_ROOT=. \\
    .venv/bin/python scripts/forward_shadow_w20_midbox.py --once
  bash scripts/forward_pulse_w20_shadow.sh   # optional loop wrapper
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
_YOYO = Path.home() / "yoyo-trading"
for p in (PROJECT, _YOYO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
os.environ.setdefault("YOYO_DATA_ROOT", str(PROJECT))

from yoyo.contracts.forward_log import (  # noqa: E402
    FORWARD_COLUMNS,
    FORWARD_LOG_PATH,
    merge_forward_log,
    open_keys,
    read_forward_log,
    write_forward_log,
)
from yoyo.data.loader import list_series, load_series  # noqa: E402
from yoyo.layers.l1_detection.candidates import (  # noqa: E402
    TIP_EDGE_BARS,
    load_yolo_model,
    map_box_to_signal,
    right_edge_to_bar,
)
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from src.costs import FORWARD_COST  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS  # noqa: E402
from src.judgment.labeling import HORIZON_BARS  # noqa: E402

SHADOW_LOG = PROJECT / "data" / "forward_log_w20_midbox_shadow.csv"
STATUS_JSON = PROJECT / "analysis" / "output" / "w20_shadow_status.json"
DEFAULT_WEIGHTS = (
    PROJECT / "analysis/output/w20_overnight/cycle_hardneg_c1/weights/best.pt"
)
WINDOW = 24
DEFAULT_CONF = 0.30
TP_MULT, SL_MULT = 5.0, 2.0
LIVE_TAIL = 800
STRATEGY_ID = "w20_midbox_hardneg_shadow"
PROTOCOL_VERSION = "w20_midbox_shadow_detector_only_20260807"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atr14(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()


def resolve_long_exit(df: pd.DataFrame, signal_i: int) -> dict | None:
    """Entry next open; TP5/SL2; same-bar → SL; timeout after HORIZON_BARS."""
    entry_i = signal_i + 1
    if entry_i >= len(df):
        return None
    atr = float(df["atr14"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(df["open"].iloc[entry_i])
    last_i = min(entry_i + HORIZON_BARS - 1, len(df) - 1)
    highs = df["high"].to_numpy()[entry_i : last_i + 1]
    lows = df["low"].to_numpy()[entry_i : last_i + 1]
    upper, lower = entry + TP_MULT * atr, entry - SL_MULT * atr
    hit_up = highs >= upper
    hit_dn = lows <= lower
    up1 = int(np.argmax(hit_up)) if hit_up.any() else len(highs)
    dn1 = int(np.argmax(hit_dn)) if hit_dn.any() else len(highs)
    if up1 < dn1:
        outcome, ret, off = "tp", upper / entry - 1.0, up1
        exit_i = entry_i + up1
    elif dn1 <= up1 and hit_dn.any():
        outcome, ret, off = "sl", lower / entry - 1.0, dn1
        exit_i = entry_i + dn1
    elif last_i - entry_i + 1 >= HORIZON_BARS:
        outcome, ret, off = "timeout", float(df["close"].iloc[last_i]) / entry - 1.0, last_i - entry_i
        exit_i = last_i
    else:
        return {
            "status": "open",
            "entry_i": entry_i,
            "entry_price": entry,
            "entry_time": str(df["open_time"].iloc[entry_i]),
            "atr": atr,
            "outcome": "",
            "exit_offset": None,
            "exit_time": None,
            "realized_ret": None,
            "label": None,
        }
    return {
        "status": "closed",
        "entry_i": entry_i,
        "entry_price": entry,
        "entry_time": str(df["open_time"].iloc[entry_i]),
        "atr": atr,
        "outcome": outcome,
        "exit_offset": int(off),
        "exit_time": str(df["open_time"].iloc[exit_i]),
        "realized_ret": float(ret),
        "label": 1 if outcome == "tp" else 0,
    }


def tip_fire_at(
    fr: pd.DataFrame,
    model,
    *,
    end_i: int,
    conf: float,
    window: int,
    device: str,
    tmp_png: Path,
) -> tuple[int, float] | None:
    """Return (signal_i, conf) if window ending at end_i has tip-edge box."""
    n = len(fr)
    if end_i < window - 1 or end_i >= n:
        return None
    start_i = end_i - window + 1
    win = fr.iloc[start_i : end_i + 1]
    try:
        img, tf = render_chart(win, out_path=None)
        cv2_imwrite = __import__("cv2").imwrite
        tmp_png.parent.mkdir(parents=True, exist_ok=True)
        cv2_imwrite(str(tmp_png), img)
        res = model.predict(str(tmp_png), conf=conf, verbose=False, device=device)
    except Exception:
        return None
    r0 = res[0] if res else None
    if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
        return None
    best_c = 0.0
    best_sig = None
    for row, cf in zip(r0.boxes.xywhn.cpu().numpy(), r0.boxes.conf.cpu().numpy()):
        cx, _, w, _ = map(float, row[:4])
        m = map_box_to_signal(
            cx=cx,
            w=w,
            tf=tf,
            window_start_i=start_i,
            n_bars=window,
            frame_length=n,
            latest_closed_i=end_i,
            tip_edge_bars=TIP_EDGE_BARS,
            apply_tip_edge=True,
            max_global_tip_age_bars=TIP_EDGE_BARS,
            allow_pending_entry=True,
        )
        if not m.accepted:
            continue
        if float(cf) > best_c:
            best_c = float(cf)
            best_sig = int(m.mapped_signal_i)
    if best_sig is None:
        return None
    return best_sig, best_c


def tip_fire(
    fr: pd.DataFrame,
    model,
    *,
    conf: float,
    window: int,
    device: str,
    tmp_png: Path,
) -> tuple[int, float] | None:
    """Return (signal_i, conf) if current tip window has tip-edge box."""
    return tip_fire_at(
        fr, model, end_i=len(fr) - 1, conf=conf, window=window, device=device, tmp_png=tmp_png
    )


def empty_record(**kwargs) -> dict:
    row = {c: "" for c in FORWARD_COLUMNS}
    row.update(
        {
            "source": "okx",
            "status": "open",
            "score": float("nan"),
            "threshold": float("nan"),
            "signal_i": -1,
            "maker_filled": True,
            "outcome": "",
            "label": "",
            "exit_offset": "",
            "exit_time": "",
            "realized_ret": "",
            "atr_pct": float("nan"),
            "dense_run_len": "",
            "tier": "",
            "size_mult": 1.0,
            "side": "long",
            "protocol_version": PROTOCOL_VERSION,
            "strategy_id": STRATEGY_ID,
            "feature_semantics": "detector_conf_only",
            "execution_eligible": False,  # shadow never executable
            "model_sha256": "",
            "detector_sha256": "",
            "candidate_detected_at": "",
            "signal_closed_at": "",
            "entry_mode": "next_open_research",
            "entry_status": "paper_filled",
            "entry_requested_at": "",
            "fill_source": "research_next_open",
            "fill_at": "",
            "fill_px": "",
            "reference_px": "",
            "research_status": "open",
            "research_outcome": "",
            "research_label": "",
            "dataset_sha256": "w20_midbox_hardneg_shadow",
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "decision_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    row.update(kwargs)
    return row


def series_pool() -> list[tuple[str, pd.DataFrame]]:
    groups: dict[tuple[str, str], list[Path]] = {}
    for d in (PROJECT / "data" / "kline_cache", PROJECT / "data" / "kline_fetched"):
        if d.is_dir():
            part = list_series(cache_dir=d, bar="15m")
            for k, paths in part.items():
                groups.setdefault(k, []).extend(paths)
    out = []
    for (_src, sym), paths in sorted(groups.items()):
        if not sym.endswith("_USDT_SWAP") or is_stockish(sym) or is_eval_symbol(sym):
            continue
        try:
            df = load_series(paths)
        except Exception:
            continue
        if len(df) < WINDOW + 100:
            continue
        out.append((sym, df.tail(LIVE_TAIL).reset_index(drop=True)))
    return out


def resolve_open_rows(log: pd.DataFrame, series_map: dict[str, pd.DataFrame]) -> list[dict]:
    updates = []
    if log.empty:
        return updates
    open_df = log[log["status"].astype(str) != "closed"]
    for _, row in open_df.iterrows():
        sym = str(row["symbol"])
        if sym not in series_map:
            continue
        df = series_map[sym]
        if "atr14" not in df.columns:
            df = df.copy()
            df["atr14"] = atr14(df)
        times = pd.to_datetime(df["open_time"], utc=True)
        sig_t = pd.Timestamp(row["signal_time"])
        if sig_t.tzinfo is None:
            sig_t = sig_t.tz_localize("UTC")
        else:
            sig_t = sig_t.tz_convert("UTC")
        idxs = np.where(times == sig_t)[0]
        if len(idxs) == 0:
            # nearest
            i = int(np.searchsorted(times, sig_t))
            i = min(max(i, 0), len(df) - 1)
        else:
            i = int(idxs[0])
        resolved = resolve_long_exit(df, i)
        if resolved is None or resolved["status"] != "closed":
            continue
        updates.append(
            empty_record(
                symbol=sym,
                signal_time=str(row["signal_time"]),
                status="closed",
                score=float(row.get("score", float("nan"))),
                threshold=float(row.get("threshold", DEFAULT_CONF)),
                model_path=str(row.get("model_path", "")),
                signal_i=int(row.get("signal_i", i)),
                entry_time=resolved["entry_time"],
                entry_price=resolved["entry_price"],
                outcome=resolved["outcome"],
                label=resolved["label"],
                exit_offset=resolved["exit_offset"],
                exit_time=resolved["exit_time"],
                realized_ret=resolved["realized_ret"],
                atr_pct=float(resolved["atr"] / resolved["entry_price"])
                if resolved["entry_price"]
                else float("nan"),
                research_status="closed",
                research_outcome=resolved["outcome"],
                research_label=resolved["label"],
                fill_at=resolved["entry_time"],
                fill_px=resolved["entry_price"],
                reference_px=resolved["entry_price"],
                detector_sha256=str(row.get("detector_sha256", "")),
            )
        )
    return updates


def discover_new(
    series_list: list[tuple[str, pd.DataFrame]],
    model,
    *,
    conf: float,
    window: int,
    device: str,
    existing_keys: set,
    weights_path: Path,
    det_sha: str,
    last_signal: dict[str, pd.Timestamp],
    bootstrap_days: int = 0,
    tip_stride: int = 1,
) -> list[dict]:
    new_rows = []
    tmp = Path(tempfile.mkdtemp(prefix="w20_shadow_")) / "tip.png"
    for i, (sym, df) in enumerate(series_list, 1):
        fr = add_mas(df)
        fr = fr.copy()
        fr["atr14"] = atr14(fr)
        times = pd.to_datetime(fr["open_time"], utc=True)
        n = len(fr)
        if bootstrap_days and bootstrap_days > 0:
            t_hi = times.iloc[-1]
            t_lo = t_hi - pd.Timedelta(days=int(bootstrap_days))
            mask = (times >= t_lo) & (times <= t_hi)
            idxs = np.flatnonzero(mask.to_numpy())
            if len(idxs) == 0:
                continue
            ends = list(range(int(idxs[0]), int(idxs[-1]) + 1, max(1, tip_stride)))
        else:
            ends = [n - 1]
        for end_i in ends:
            hit = tip_fire_at(
                fr, model, end_i=end_i, conf=conf, window=window, device=device, tmp_png=tmp
            )
            if hit is None:
                continue
            sig_i, conf_v = hit
            sig_t = times.iloc[sig_i]
            prev = last_signal.get(sym)
            if prev is not None:
                gap_bars = int(abs((sig_t - prev).total_seconds()) / 900)
                if gap_bars < MIN_GAP_BARS:
                    continue
            key = f"okx|{sym}|{sig_t}"
            if key in existing_keys:
                continue
            resolved = resolve_long_exit(fr, sig_i)
            if resolved is None:
                continue
            last_signal[sym] = sig_t
            row = empty_record(
                symbol=sym,
                signal_time=str(sig_t),
                status=resolved["status"],
                score=conf_v,
                threshold=conf,
                model_path=str(weights_path),
                signal_i=sig_i,
                entry_time=resolved["entry_time"],
                entry_price=resolved["entry_price"],
                atr_pct=float(resolved["atr"] / resolved["entry_price"])
                if resolved["entry_price"]
                else float("nan"),
                detector_sha256=det_sha,
                signal_closed_at=str(sig_t),
                candidate_detected_at=datetime.now(timezone.utc).isoformat(),
                fill_at=resolved["entry_time"]
                if resolved["status"] == "closed" or resolved["entry_time"]
                else "",
                fill_px=resolved["entry_price"] if resolved["entry_price"] else "",
                reference_px=resolved["entry_price"] if resolved["entry_price"] else "",
            )
            if resolved["status"] == "closed":
                row["outcome"] = resolved["outcome"]
                row["label"] = resolved["label"]
                row["exit_offset"] = resolved["exit_offset"]
                row["exit_time"] = resolved["exit_time"]
                row["realized_ret"] = resolved["realized_ret"]
                row["research_status"] = "closed"
                row["research_outcome"] = resolved["outcome"]
                row["research_label"] = resolved["label"]
            new_rows.append(row)
            existing_keys.add(key)
        if i % 20 == 0 or i == len(series_list):
            print(f"  discover {i}/{len(series_list)} new={len(new_rows)}", flush=True)
    return new_rows


def summarize(log: pd.DataFrame) -> dict:
    if log is None or log.empty:
        return {
            "n_rows": 0,
            "n_open": 0,
            "n_closed": 0,
            "closed_toward_100": 0,
            "mean_net_bp": None,
            "win_rate": None,
            "profit_factor": None,
        }
    closed = log[log["status"].astype(str) == "closed"].copy()
    open_n = int((log["status"].astype(str) != "closed").sum())
    rets = pd.to_numeric(closed.get("realized_ret"), errors="coerce").dropna()
    # net after maker RT (research gross in realized_ret for this shadow)
    if len(rets):
        net = rets - FORWARD_COST
        wins = float(net[net > 0].sum())
        losses = float(net[net < 0].sum())
        pf = float(wins / -losses) if losses < 0 else None
        wr = float((net > 0).mean())
        mean_bp = float(net.mean() * 1e4)
    else:
        pf = wr = mean_bp = None
    return {
        "n_rows": int(len(log)),
        "n_open": open_n,
        "n_closed": int(len(closed)),
        "closed_toward_100": int(len(closed)),
        "mean_net_bp": None if mean_bp is None else round(mean_bp, 2),
        "win_rate": None if wr is None else round(wr, 4),
        "profit_factor": None if pf is None else round(pf, 3),
        "outcomes": closed["outcome"].value_counts().to_dict() if len(closed) else {},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single pulse (default)")
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--window", type=int, default=WINDOW)
    ap.add_argument("--out", type=Path, default=SHADOW_LOG)
    ap.add_argument("--device", default=None)
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument(
        "--bootstrap-days",
        type=int,
        default=0,
        help="if >0, scan every tip in the last N days (causal) to seed closed trades",
    )
    ap.add_argument("--tip-stride", type=int, default=1, help="tip stride when bootstrap-days>0")
    args = ap.parse_args()

    out = args.out.resolve()
    mainline = FORWARD_LOG_PATH.resolve()
    if out == mainline:
        raise SystemExit("refusing to write mainline forward_log.csv — use shadow path")
    if "forward_log.csv" == out.name and "w20" not in str(out):
        raise SystemExit(f"suspicious path {out}; shadow must be isolated")

    if not args.weights.exists():
        raise SystemExit(f"missing weights: {args.weights}")

    device = args.device
    if device is None:
        import torch

        if torch.cuda.is_available():
            device = "0"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

    det_sha = _sha256(args.weights)
    print(
        f"w20_shadow: weights={args.weights} conf={args.conf} window={args.window} "
        f"device={device} out={out}",
        flush=True,
    )
    print(
        "w20_shadow: execution_eligible=false ACTIVE/owner_best untouched",
        flush=True,
    )

    existing = read_forward_log(out)
    tracked = open_keys(existing)
    # last signal times for gap
    last_signal: dict[str, pd.Timestamp] = {}
    if not existing.empty:
        for _, r in existing.iterrows():
            sym = str(r["symbol"])
            t = pd.Timestamp(r["signal_time"])
            if t.tzinfo is None:
                t = t.tz_localize("UTC")
            prev = last_signal.get(sym)
            if prev is None or t > prev:
                last_signal[sym] = t

    pool = series_pool()
    if args.max_symbols > 0:
        pool = pool[: args.max_symbols]
    series_map = {s: f for s, f in pool}
    print(f"w20_shadow: series={len(pool)} existing_rows={len(existing)}", flush=True)

    # 1) resolve open
    for s, df in list(series_map.items()):
        if "atr14" not in df.columns:
            d2 = df.copy()
            d2["atr14"] = atr14(d2)
            series_map[s] = d2
    updates = resolve_open_rows(existing, series_map)
    print(f"w20_shadow: closed_updates={len(updates)}", flush=True)

    # 2) discover tip fires
    model = load_yolo_model(args.weights)
    # existing keys set
    keys = set()
    if not existing.empty:
        for _, r in existing.iterrows():
            keys.add(f"okx|{r['symbol']}|{r['signal_time']}")
    new_rows = discover_new(
        [(s, series_map[s]) for s, _ in pool],
        model,
        conf=args.conf,
        window=args.window,
        device=device,
        existing_keys=keys,
        weights_path=args.weights,
        det_sha=det_sha,
        last_signal=last_signal,
        bootstrap_days=int(args.bootstrap_days),
        tip_stride=int(args.tip_stride),
    )
    print(f"w20_shadow: new_signals={len(new_rows)}", flush=True)

    # 3) merge write
    to_merge = updates + new_rows
    if to_merge:
        # convert to ForwardRecord-like dicts — merge expects mapping rows
        from yoyo.contracts.forward_log import ForwardRecord  # type: ignore

        records = []
        for r in to_merge:
            # ensure required keys
            records.append(r)  # TypedDict structural
        merged = merge_forward_log(existing, records)  # type: ignore[arg-type]
        write_forward_log(out, merged.frame)
        log = merged.frame
    else:
        log = existing

    summ = summarize(log)
    status = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "shadow": True,
        "execution_eligible": False,
        "active_untouched": True,
        "owner_best_untouched": True,
        "mainline_forward_log_untouched": True,
        "weights": str(args.weights.resolve()),
        "conf": args.conf,
        "window": args.window,
        "log_path": str(out),
        "pulse": {
            "new_signals": len(new_rows),
            "closed_updates": len(updates),
            "series": len(pool),
        },
        "book": summ,
        "gate": {
            "target_closed": 100,
            "closed": summ["n_closed"],
            "remaining": max(0, 100 - summ["n_closed"]),
            "ready_for_verdict": summ["n_closed"] >= 100,
        },
        "protocol": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
    }
    STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(status, indent=2, ensure_ascii=False))
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
