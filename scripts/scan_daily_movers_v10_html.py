"""Build a v10 review gallery for each day's 20 largest absolute movers.

Sources and windows
-------------------
Only local OKX USDT-SWAP 15m OHLCV is read through ``src.data.loader``.  A
daily move uses the first open and last close of a complete UTC day, requiring
at least 90 of 96 bars.  For every selected symbol-day, v10 is run causally on
each bar with a 200-bar OHLCV + SMA/EMA window ending at that bar.  A detection
is kept only when its right edge lands in tip/tip-1/tip-2, with an eight-bar
display dedupe.  Forward TP/SL outcome is drawn only as review context.

The daily mover ranking itself sees the whole day, so this is deliberately a
post-hoc visual review pack, not a causal selection backtest.  It writes only
under analysis/output and never changes ACTIVE, owner_best, holdout, forward
logs, thresholds, or trading configuration.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/scan_daily_movers_v10_html.py
  PYTHONPATH=. .venv/bin/python scripts/scan_daily_movers_v10_html.py --days 1 --top 2 --out /tmp/v10_movers_smoke
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)
from scripts.scan_recent_multi_tf import draw, simulate  # noqa: E402

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v10/weights/best.pt"
DEFAULT_OUT = PROJECT / "analysis/output/v10_daily_movers_10d_report"
MIN_DAY_BARS = 90
MIN_GAP = 8


def choose_device(requested: str | None) -> str:
    if requested:
        return requested
    try:
        import torch

        return "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def universe() -> dict[str, list[Path]]:
    return {
        symbol: paths
        for (source, symbol), paths in list_series(bar="15m").items()
        if source == "okx"
        and symbol.endswith("_USDT_SWAP")
        and not is_stockish(symbol)
    }


def target_days(series: dict[str, list[Path]], n_days: int) -> list[pd.Timestamp]:
    latest: pd.Timestamp | None = None
    for paths in series.values():
        frame = load_series(paths)
        if frame.empty:
            continue
        end = pd.Timestamp(frame["open_time"].iloc[-1])
        latest = end if latest is None or end > latest else latest
    if latest is None:
        raise RuntimeError("no usable 15m series")
    # The date containing the latest bar is incomplete; stop one UTC day before it.
    last_complete = latest.floor("D") - pd.Timedelta(days=1)
    return [last_complete - pd.Timedelta(days=i) for i in reversed(range(n_days))]


def rank_days(
    series: dict[str, list[Path]], days: list[pd.Timestamp], top: int
) -> list[dict]:
    wanted = set(days)
    candidates: dict[pd.Timestamp, list[dict]] = {day: [] for day in days}
    for k, (symbol, paths) in enumerate(sorted(series.items()), 1):
        frame = load_series(paths)
        if frame.empty:
            continue
        day_key = frame["open_time"].dt.floor("D")
        for day, group in frame[day_key.isin(wanted)].groupby(day_key[day_key.isin(wanted)]):
            day = pd.Timestamp(day)
            if day not in wanted or len(group) < MIN_DAY_BARS:
                continue
            open_px = float(group["open"].iloc[0])
            close_px = float(group["close"].iloc[-1])
            if not np.isfinite(open_px) or open_px <= 0 or not np.isfinite(close_px):
                continue
            candidates[day].append(
                {
                    "day": day,
                    "symbol": symbol,
                    "return": close_px / open_px - 1,
                    "bars": int(len(group)),
                    "open": open_px,
                    "close": close_px,
                }
            )
        if k % 50 == 0:
            print(f"排名数据 {k}/{len(series)}", flush=True)

    ranked: list[dict] = []
    for day in days:
        rows = sorted(candidates[day], key=lambda row: -abs(row["return"]))[:top]
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
            ranked.append(row)
        print(
            f"{day:%Y-%m-%d}: 有效币 {len(candidates[day])}, "
            f"Top{len(rows)} 阈值 |涨跌|={abs(rows[-1]['return'])*100:.2f}%"
            if rows
            else f"{day:%Y-%m-%d}: 无有效币",
            flush=True,
        )
    return ranked


def scan_selected(
    series: dict[str, list[Path]], ranked: list[dict], out: Path, conf: float, device: str
) -> list[dict]:
    selected: dict[str, list[dict]] = defaultdict(list)
    for row in ranked:
        selected[row["symbol"]].append(row)

    model = load_yolo_model(str(WEIGHTS))
    image_dir = out / "img"
    image_dir.mkdir(parents=True, exist_ok=True)
    tmp = out / "_window.png"
    signals: list[dict] = []
    total_symbol_days = len(ranked)
    done = 0
    t0 = time.perf_counter()

    for symbol, day_rows in sorted(selected.items()):
        base = load_series(series[symbol])
        if base.empty:
            continue
        frame = add_mas(base)
        indicators = add_indicators(frame)
        last_signal = -10**9
        open_times = pd.to_datetime(frame["open_time"], utc=True)

        for daily in sorted(day_rows, key=lambda row: row["day"]):
            day = daily["day"]
            day_end = day + pd.Timedelta(days=1)
            indices = np.flatnonzero((open_times >= day) & (open_times < day_end))
            fires: list[tuple[int, tuple[int, int], float]] = []
            for t in indices:
                t = int(t)
                if t < WINDOW - 1:
                    continue
                try:
                    _, transform = render_chart(
                        frame.iloc[t - WINDOW + 1 : t + 1], out_path=tmp
                    )
                    predictions = model.predict(
                        str(tmp), conf=conf, verbose=False, device=device
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"WARN {symbol} {open_times.iloc[t]}: {exc}", flush=True)
                    continue
                result = predictions[0] if predictions else None
                if result is None or result.boxes is None or len(result.boxes) == 0:
                    continue

                accepted: list[tuple[float, int, int]] = []
                xywh = result.boxes.xywhn.cpu().numpy()
                scores = result.boxes.conf.cpu().numpy()
                for box_row, score in zip(xywh, scores):
                    cx, width = float(box_row[0]), float(box_row[2])
                    right = right_edge_to_bar(cx, width, transform, n_bars=WINDOW)
                    if right < WINDOW - 1 - TIP_EDGE_BARS:
                        continue
                    left = right_edge_to_bar(
                        cx - width / 2, 0.0, transform, n_bars=WINDOW
                    )
                    accepted.append((float(score), int(left), int(right)))
                if not accepted:
                    continue

                score, left, right = max(accepted)
                signal_i = t - (WINDOW - 1 - right)
                signal_day = open_times.iloc[signal_i].floor("D")
                if signal_day != day or signal_i - last_signal < MIN_GAP:
                    continue
                box_start = t - (WINDOW - 1 - max(0, left))
                fires.append((signal_i, (box_start, signal_i), score))
                last_signal = signal_i

            for signal_i, box, score in fires:
                trade = simulate(indicators, signal_i)
                if trade.get("status") == "no_entry":
                    continue
                signal_time = open_times.iloc[signal_i]
                filename = (
                    f"{day:%Y%m%d}_{symbol}_{signal_time:%H%M}_r{daily['rank']:02d}.png"
                )
                draw(
                    indicators,
                    symbol,
                    "15m",
                    signal_i,
                    box,
                    score,
                    trade,
                    image_dir / filename,
                )
                signals.append(
                    {
                        "day": day,
                        "rank": int(daily["rank"]),
                        "symbol": symbol,
                        "daily_return": float(daily["return"]),
                        "signal_time": signal_time,
                        "conf": float(score),
                        "status": trade["status"],
                        "outcome": trade["why"],
                        "gross": float(trade["gross"]),
                        "net_taker": float(trade["gross"] - SWAP_TAKER),
                        "held_bars": int(trade["exit_i"] - trade["entry_i"]),
                        "png": f"img/{filename}",
                    }
                )

            done += 1
            elapsed = (time.perf_counter() - t0) / 60
            eta = elapsed / done * (total_symbol_days - done) if done else 0
            print(
                f"[{done}/{total_symbol_days}] {day:%m-%d} "
                f"#{daily['rank']:02d} {symbol:<20} "
                f"日涨跌 {daily['return']*100:+6.2f}% 扫 {len(indices):>2} "
                f"开火 {len(fires):>2}  elapsed={elapsed:.1f}m eta={eta:.1f}m",
                flush=True,
            )

    tmp.unlink(missing_ok=True)
    return signals


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def write_outputs(
    out: Path,
    days: list[pd.Timestamp],
    ranked: list[dict],
    signals: list[dict],
    conf: float,
    device: str,
    wall_min: float,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rank_df = pd.DataFrame(ranked)
    if not rank_df.empty:
        rank_df.assign(day=rank_df["day"].astype(str)).to_csv(
            out / "daily_rankings.csv", index=False
        )
    sig_df = pd.DataFrame(signals)
    if not sig_df.empty:
        sig_df.assign(
            day=sig_df["day"].astype(str),
            signal_time=sig_df["signal_time"].astype(str),
        ).to_csv(out / "signals.csv", index=False)

    by_day_signal: dict[pd.Timestamp, list[dict]] = defaultdict(list)
    for row in signals:
        by_day_signal[row["day"]].append(row)
    by_day_rank: dict[pd.Timestamp, list[dict]] = defaultdict(list)
    for row in ranked:
        by_day_rank[row["day"]].append(row)

    chunks = [
        '<!doctype html><meta charset="utf-8">',
        "<title>v10 · 最近10日每日涨跌幅 Top20</title>",
        """<style>
body{background:#0f1113;color:#e9e9e9;font:15px/1.65 -apple-system,"PingFang SC",sans-serif;margin:0;padding:18px}
h1{font-size:20px;margin:0 0 6px}h2{font-size:17px;margin:38px 0 8px;border-bottom:1px solid #272c31;padding-bottom:6px}
.c{color:#8b949e;font-size:14px;margin:2px 0 14px}.nav a{color:#80cbc4;margin-right:12px}.w{background:#1a1410;border-left:3px solid #ffab40;padding:12px 14px;border-radius:6px;margin:14px 0}
table{border-collapse:collapse;width:100%;margin:8px 0 18px;font-size:13.5px}th,td{border-bottom:1px solid #272c31;padding:6px 8px;text-align:right}th:nth-child(2),td:nth-child(2){text-align:left}.up{color:#4caf50}.dn{color:#ef5350}.muted{color:#8b949e}.none{color:#8b949e;padding:8px 0 20px}
figure{margin:0 0 26px}figcaption{color:#a8b2bb;padding:6px 2px;font-size:13.5px}img{width:100%;border-radius:6px;background:#fff}</style>""",
        f"<h1>v10 检测器 · 最近 {len(days)} 个完整 UTC 日 · 每日 |涨跌幅| Top20</h1>",
        f'<p class="c">{days[0]:%Y-%m-%d} ～ {days[-1]:%Y-%m-%d} · '
        f"共 {len(ranked)} 个币日 · v10 信号 {len(signals)} 条 · conf {conf:.2f} · "
        f"设备 {html.escape(device)} · 用时 {wall_min:.1f} 分钟</p>",
        '<div class="w"><b>看之前先知道这三件事</b><br>'
        '1. <b>日榜按全天收盘后才知道的绝对涨跌幅选币</b>，所以这是事后审图包，不是可交易的因果回测。<br>'
        '2. <b>每一次 v10 推理仍是因果的</b>：图只到当时的 bar，预测框必须落在 tip/tip-1/tip-2。<br>'
        '3. <b>v10 尚未通过检测晋升门</b>；修正单位后，conf 0.30 的自由开火约 9.62 条/币·月，仍显著高于 owner 的 0.18～0.36。页面只表示“v10 在这里画了框”。</div>',
        '<p class="nav">'
        + "".join(f'<a href="#{day:%Y%m%d}">{day:%m-%d}</a>' for day in days)
        + "</p>",
    ]

    for day in reversed(days):
        ranks = sorted(by_day_rank[day], key=lambda row: row["rank"])
        fires = sorted(by_day_signal[day], key=lambda row: row["signal_time"])
        count_by_symbol: dict[str, int] = defaultdict(int)
        max_conf: dict[str, float] = defaultdict(float)
        for row in fires:
            count_by_symbol[row["symbol"]] += 1
            max_conf[row["symbol"]] = max(max_conf[row["symbol"]], row["conf"])
        chunks.append(
            f'<h2 id="{day:%Y%m%d}">{day:%Y-%m-%d} UTC</h2>'
            f'<p class="c">Top{len(ranks)} · v10 信号 {len(fires)} 条 · 按 |日涨跌幅| 排名</p>'
        )
        chunks.append("<table><thead><tr><th>#</th><th>币种</th><th>日涨跌</th><th>完整bars</th><th>v10信号</th><th>最高conf</th></tr></thead><tbody>")
        for row in ranks:
            cls = "up" if row["return"] >= 0 else "dn"
            symbol = html.escape(row["symbol"].replace("_USDT_SWAP", ""))
            n = count_by_symbol[row["symbol"]]
            mc = f"{max_conf[row['symbol']]:.2f}" if n else "—"
            chunks.append(
                f"<tr><td>{row['rank']}</td><td><b>{symbol}</b></td>"
                f'<td class="{cls}">{pct(row["return"])}</td><td>{row["bars"]}</td>'
                f"<td>{n}</td><td>{mc}</td></tr>"
            )
        chunks.append("</tbody></table>")
        if not fires:
            chunks.append('<p class="none">当日 Top20 没有通过 tip 门的 v10 信号。</p>')
            continue
        for row in fires:
            outcome = row["outcome"] if row["status"] == "closed" else "持仓中"
            color = {"TP": "#4caf50", "SL": "#ef5350"}.get(outcome, "#ffab40")
            symbol = html.escape(row["symbol"].replace("_USDT_SWAP", ""))
            chunks.append(
                "<figure><figcaption>"
                f"<b>#{row['rank']:02d} {symbol}</b> · 日涨跌 {pct(row['daily_return'])} · "
                f"信号 {row['signal_time']:%H:%M} UTC · conf {row['conf']:.2f} · "
                f'<b style="color:{color}">{html.escape(outcome)}</b> · '
                f"毛 {pct(row['gross'])} · 净@taker {pct(row['net_taker'])}"
                f'</figcaption><img src="{html.escape(row["png"])}" loading="lazy"></figure>'
            )

    (out / "index.html").write_text("\n".join(chunks), encoding="utf-8")
    payload = {
        "weights": str(WEIGHTS),
        "days": [str(day) for day in days],
        "ranking": "absolute daily return, first open to last close, >=90 bars",
        "conf": conf,
        "device": device,
        "n_symbol_days": len(ranked),
        "n_signals": len(signals),
        "wall_min": round(wall_min, 2),
        "ranked": [
            {**row, "day": str(row["day"])} for row in ranked
        ],
        "signals": [
            {
                **row,
                "day": str(row["day"]),
                "signal_time": str(row["signal_time"]),
            }
            for row in signals
        ],
    }
    (out / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF)
    parser.add_argument("--device", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.days < 1 or args.top < 1:
        parser.error("--days and --top must be positive")
    if not WEIGHTS.exists():
        print(f"missing v10 weights: {WEIGHTS}")
        return 2

    t0 = time.perf_counter()
    series = universe()
    days = target_days(series, args.days)
    print(
        f"v10={WEIGHTS.name} universe={len(series)} days={days[0]:%Y-%m-%d}.."
        f"{days[-1]:%Y-%m-%d} top={args.top} conf={args.conf:.2f}",
        flush=True,
    )
    ranked = rank_days(series, days, args.top)
    device = choose_device(args.device)
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"扫描 {len(ranked)} 个币日，设备 {device}", flush=True)
    signals = scan_selected(series, ranked, args.out, args.conf, device)
    wall_min = (time.perf_counter() - t0) / 60
    write_outputs(args.out, days, ranked, signals, args.conf, device, wall_min)
    print(
        f"完成: 币日 {len(ranked)} · 信号 {len(signals)} · {wall_min:.1f} 分钟 "
        f"-> {args.out / 'index.html'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
