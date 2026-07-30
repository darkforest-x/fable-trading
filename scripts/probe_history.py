#!/usr/bin/env python3
"""Historical single-symbol YOLO+judgment probe → HTML signal report.

Default: last 365 days of 15m bars (local cache + optional tip refresh),
mode=full (stride walk, not tip-only live schedule). Read-only: never writes
forward_log / ACTIVE / kline_fetched (klines only read from disk; tip fetch
is memory-only like check_symbol).

Usage:
  PYTHONPATH=. OMP_NUM_THREADS=1 .venv/bin/python scripts/probe_history.py ETH --days 365
  PYTHONPATH=. .venv/bin/python scripts/probe_history.py BTC --days 90 --json
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.check_symbol import (  # noqa: E402
    _score_candidates,
    fetch_bars_memory,
    load_local_bars,
    normalize_symbol,
    resolve_probe_weights,
)
from src.data.loader import OHLCV_COLUMNS  # noqa: E402
from src.judgment.candidates import WARMUP_BARS  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from src.judgment.yolo_candidates import WINDOW, load_yolo_model, scan_series_with_yolo  # noqa: E402

OUT_DIR = PROJECT / "analysis" / "output" / "probe_history"
BARS_PER_DAY = 24 * 4  # 15m


def _slice_days(frame: pd.DataFrame, days: int) -> pd.DataFrame:
    if frame.empty or days <= 0:
        return frame
    t_end = pd.to_datetime(frame["open_time"], utc=True).iloc[-1]
    t0 = t_end - pd.Timedelta(days=int(days))
    # keep warmup bars before window so indicators/YOLO windows are valid
    times = pd.to_datetime(frame["open_time"], utc=True)
    warmup_cut = t0 - pd.Timedelta(minutes=15 * (WARMUP_BARS + WINDOW + 5))
    return frame[times >= warmup_cut].reset_index(drop=True)


def _attach_outcomes(enriched: pd.DataFrame, signal_i: int) -> dict:
    """Best-effort TP5/SL2 paper outcome (no orders)."""
    from src.judgment.forward_scan import resolve_forward_exit  # noqa: PLC0415

    try:
        ex = resolve_forward_exit(enriched, signal_i)
    except Exception:  # noqa: BLE001
        return {"status": "error", "outcome": "", "realized_ret": None}
    if ex is None:
        return {"status": "skip", "outcome": "", "realized_ret": None}
    ret = float(ex.realized_ret) if ex.realized_ret == ex.realized_ret else None  # NaN check
    return {
        "status": ex.status,
        "outcome": ex.outcome or "",
        "realized_ret": None if ret is None or not np.isfinite(ret) else round(ret, 6),
        "exit_offset": int(ex.exit_offset) if ex.exit_offset else None,
        "exit_time": ex.exit_time or "",
    }


def run_history(symbol: str, *, days: int = 365, conf: float = 0.30) -> dict:
    now = pd.Timestamp.now(tz="UTC")
    result: dict = {
        "kind": "probe_history",
        "symbol": symbol,
        "days": int(days),
        "generated_at": str(now),
        "error": None,
        "signals": [],
        "report_html": None,
        "report_json": None,
    }

    local = load_local_bars(symbol)
    after_ts = int(local["open_time"].max().timestamp() * 1000) if not local.empty else None
    fetch_warning = ""
    try:
        # Only pull a tip tail for freshness; history body is local cache.
        fresh = fetch_bars_memory(symbol, after_ts=after_ts, min_bars=500)
    except Exception as exc:  # noqa: BLE001
        fresh = pd.DataFrame(columns=OHLCV_COLUMNS)
        fetch_warning = f"OKX tip 拉取失败({exc}), 仅用本地缓存"
    frame = pd.concat([local, fresh], ignore_index=True)
    frame = (
        frame.drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    if frame.empty:
        result["error"] = f"无本地 K 线: {symbol}（请先 fetch / 确认 kline_fetched）"
        return result

    frame = _slice_days(frame, days)
    times = pd.to_datetime(frame["open_time"], utc=True)
    t_end = times.iloc[-1]
    t_start = t_end - pd.Timedelta(days=int(days))
    # scan range for reporting
    n_in_window = int((times >= t_start).sum())
    result["local_bars"] = int(len(local))
    result["frame_bars"] = int(len(frame))
    result["window_bars"] = n_in_window
    result["window_start"] = str(t_start)
    result["window_end"] = str(t_end)
    result["fetch_warning"] = fetch_warning
    result["approx_days"] = round((t_end - times.iloc[0]).total_seconds() / 86400, 1)

    if len(frame) < WARMUP_BARS + WINDOW + 2:
        result["error"] = f"数据不足: {len(frame)} bars"
        return result

    weights = resolve_probe_weights()
    model = load_yolo_model(weights)
    try:
        result["detector"] = str(weights.relative_to(PROJECT))
    except ValueError:
        result["detector"] = str(weights)

    with tempfile.TemporaryDirectory(prefix="probe_hist_") as td:
        tmp = Path(td) / "win.png"
        # full history stride scan (not live tip-only)
        indices = scan_series_with_yolo(
            frame,
            model,
            conf=conf,
            mode="full",
            tmp_png=tmp,
            signal_time_lo=t_start,
            signal_time_hi=t_end + pd.Timedelta(minutes=15),
        )

    artifact, enriched, feature_rows, scores = _score_candidates(frame, indices)
    result["artifact"] = artifact.relative_model_path
    result["threshold"] = float(artifact.threshold)
    result["conf"] = float(conf)
    result["n_raw_fires"] = int(len(indices))

    signals = []
    for n, signal_i in enumerate(indices):
        signal_time = pd.Timestamp(enriched["open_time"].iloc[signal_i])
        score = float(scores[n])
        passed = score >= artifact.threshold
        atr_pct = float(feature_rows["atr_pct"].iloc[n])
        atr_ok = bool(pd.notna(atr_pct) and atr_pct >= ATR_PCT_MIN)
        outc = _attach_outcomes(enriched, signal_i)
        entry_i = signal_i + 1
        entry_price = (
            float(enriched["open"].iloc[entry_i])
            if entry_i < len(enriched)
            else float(enriched["close"].iloc[signal_i])
        )
        signals.append(
            {
                "i": n + 1,
                "signal_i": int(signal_i),
                "signal_time": str(signal_time),
                "entry_price": round(entry_price, 8),
                "score": round(score, 6),
                "passed": bool(passed),
                "atr_pct": round(atr_pct, 5) if pd.notna(atr_pct) else None,
                "atr_ok": atr_ok,
                "eligible": bool(passed and atr_ok),
                "status": outc.get("status"),
                "outcome": outc.get("outcome") or "",
                "realized_ret": outc.get("realized_ret"),
                "exit_time": outc.get("exit_time") or "",
            }
        )

    result["signals"] = signals
    result["n_eligible"] = sum(1 for s in signals if s["eligible"])
    result["n_passed_score"] = sum(1 for s in signals if s["passed"])
    closed = [s for s in signals if s.get("status") == "closed" and s.get("realized_ret") is not None]
    if closed:
        rets = np.array([float(s["realized_ret"]) for s in closed], dtype=float)
        result["closed_n"] = int(len(closed))
        result["closed_mean_ret"] = round(float(rets.mean()), 6)
        result["closed_win_rate"] = round(float((rets > 0).mean()), 4)
        by_oc: dict[str, int] = {}
        for s in closed:
            by_oc[s["outcome"] or "?"] = by_oc.get(s["outcome"] or "?", 0) + 1
        result["closed_outcomes"] = by_oc
    else:
        result["closed_n"] = 0
        result["closed_mean_ret"] = None
        result["closed_win_rate"] = None
        result["closed_outcomes"] = {}

    # persist
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    stem = f"{symbol}_{days}d_{stamp}"
    json_path = OUT_DIR / f"{stem}.json"
    html_path = OUT_DIR / f"{stem}.html"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(result), encoding="utf-8")
    result["report_json"] = str(json_path.relative_to(PROJECT))
    result["report_html"] = str(html_path.relative_to(PROJECT))
    result["report_url"] = f"/debug-artifacts/probe_history/{html_path.name}"
    return result


def render_html(r: dict) -> str:
    sym = html.escape(str(r.get("symbol") or ""))
    thr = r.get("threshold")
    rows_html = []
    for s in r.get("signals") or []:
        ret = s.get("realized_ret")
        ret_s = "—" if ret is None else f"{100 * float(ret):+.2f}%"
        cls = ""
        if ret is not None:
            cls = "pos" if float(ret) > 0 else ("neg" if float(ret) < 0 else "")
        rows_html.append(
            "<tr>"
            f"<td>{s.get('i')}</td>"
            f"<td class='mono'>{html.escape(str(s.get('signal_time') or ''))}</td>"
            f"<td class='num'>{s.get('entry_price')}</td>"
            f"<td class='num'>{s.get('score')}</td>"
            f"<td>{'✓' if s.get('passed') else '·'}</td>"
            f"<td>{'✓' if s.get('eligible') else '·'}</td>"
            f"<td>{html.escape(str(s.get('outcome') or s.get('status') or ''))}</td>"
            f"<td class='num {cls}'>{ret_s}</td>"
            "</tr>"
        )
    if not rows_html:
        rows_html.append("<tr><td colspan='8' class='empty'>窗口内无 YOLO 信号</td></tr>")

    outcomes = r.get("closed_outcomes") or {}
    oc_txt = " · ".join(f"{k} {v}" for k, v in outcomes.items()) or "—"
    mean_ret = r.get("closed_mean_ret")
    mean_s = "—" if mean_ret is None else f"{100 * float(mean_ret):+.2f}%"
    wr = r.get("closed_win_rate")
    wr_s = "—" if wr is None else f"{100 * float(wr):.1f}%"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>历史盘口检测 · {sym}</title>
<style>
body {{ font: 15px/1.6 -apple-system, "PingFang SC", system-ui, sans-serif;
  max-width: 960px; margin: 0 auto; padding: 1.5rem 1rem 3rem; color: #111; }}
h1 {{ font-size: 1.4rem; margin: 0 0 .4rem; }}
.sub {{ color: #666; margin-bottom: 1.2rem; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px,1fr)); gap: 8px; margin: 1rem 0; }}
.tile {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; background: #fafafa; }}
.tile b {{ display: block; font-size: 1.15rem; margin-top: 2px; }}
.tile span {{ color: #6b7280; font-size: 12px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ border-bottom: 1px solid #eee; padding: 6px 8px; text-align: left; }}
th {{ background: #f3f4f6; position: sticky; top: 0; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.mono {{ font-family: ui-monospace, Menlo, monospace; font-size: 12px; }}
.pos {{ color: #059669; }} .neg {{ color: #dc2626; }}
.empty {{ color: #9ca3af; text-align: center; }}
.note {{ color: #6b7280; font-size: 13px; margin-top: 1rem; }}
.warn {{ background: #fff7ed; border-left: 3px solid #f59e0b; padding: 8px 10px; margin: 8px 0; }}
</style></head><body>
<h1>历史盘口检测 · {sym}</h1>
<p class="sub">近 {html.escape(str(r.get('days')))} 天 · YOLO full 扫描 + 判断层打分 · 只读报告 · 生成于 {html.escape(str(r.get('generated_at')))}</p>
{"<div class='warn'>" + html.escape(str(r.get('fetch_warning'))) + "</div>" if r.get("fetch_warning") else ""}
{"<div class='warn'>" + html.escape(str(r.get('error'))) + "</div>" if r.get("error") else ""}
<div class="tiles">
  <div class="tile"><span>窗口</span><b>{html.escape(str(r.get('window_start',''))[:16])} → {html.escape(str(r.get('window_end',''))[:16])}</b></div>
  <div class="tile"><span>YOLO 开火</span><b>{r.get('n_raw_fires', 0)}</b></div>
  <div class="tile"><span>过阈值</span><b>{r.get('n_passed_score', 0)}</b></div>
  <div class="tile"><span>合格(分+ATR)</span><b>{r.get('n_eligible', 0)}</b></div>
  <div class="tile"><span>已平仓笔数</span><b>{r.get('closed_n', 0)}</b></div>
  <div class="tile"><span>平仓均收益</span><b>{mean_s}</b></div>
  <div class="tile"><span>平仓胜率</span><b>{wr_s}</b></div>
  <div class="tile"><span>阈值</span><b>{thr}</b></div>
</div>
<p class="note">检测器: {html.escape(str(r.get('detector')))} · 判断: {html.escape(str(r.get('artifact')))} · 结果分布: {html.escape(oc_txt)}</p>
<p class="note">障碍: TP5/SL2/72bar（纸面回放，非实盘）；合格 = score≥阈值 且 atr_pct 过下限。</p>
<table>
  <thead><tr>
    <th>#</th><th>信号时间 (UTC)</th><th class="num">入场价≈</th><th class="num">score</th>
    <th>过阈</th><th>合格</th><th>结果</th><th class="num">实现收益</th>
  </tr></thead>
  <tbody>
    {''.join(rows_html)}
  </tbody>
</table>
<p class="note">免责：本地历史探针，不写 forward_log、不下单、不 promote。与 VPS 主线互不影响。</p>
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="历史盘口检测 → HTML 信号报告")
    ap.add_argument("symbol", help="币种，如 ETH / BTC_USDT_SWAP")
    ap.add_argument("--days", type=int, default=365, help="回溯天数（默认 365）")
    ap.add_argument("--conf", type=float, default=0.30, help="YOLO conf")
    ap.add_argument("--json", action="store_true", help="stdout 最后一行 JSON")
    args = ap.parse_args()
    symbol = normalize_symbol(args.symbol)
    result = run_history(symbol, days=max(7, int(args.days)), conf=float(args.conf))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not result.get("error") else 1
    if result.get("error"):
        print("ERROR:", result["error"])
        return 1
    print(f"{symbol} days={result['days']} fires={result['n_raw_fires']} "
          f"eligible={result['n_eligible']} closed={result['closed_n']}")
    print(f"HTML: {result.get('report_html')}")
    print(f"URL:  {result.get('report_url')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
