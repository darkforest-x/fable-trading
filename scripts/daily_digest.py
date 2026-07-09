"""Daily digest -> Telegram (the optimized notification: ONE structured
message per day, numbers first, alerts only when something is wrong).

Sections: data freshness | forward signals (new + cumulative scoreboard) |
system health (training/pipeline states). Run after update_okx + forward_track.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
from src.notify import send  # noqa: E402

LOG = PROJECT_DIR / "data" / "forward_log.csv"
LOG_H1 = PROJECT_DIR / "data" / "forward_log_h1_scaled.csv"
KLINE_DIR = PROJECT_DIR / "data" / "kline_fetched"


def data_freshness() -> tuple[str, bool]:
    files = sorted(KLINE_DIR.glob("okx_*_USDT_SWAP_15m_*.csv"))
    if not files:
        return "❌ 无合约数据文件", True
    newest = max(f.stat().st_mtime for f in files)
    age_h = (datetime.now().timestamp() - newest) / 3600
    line = f"数据更新：{len(files)} 个合约序列，最新写入 {age_h:.1f} 小时前"
    return (f"⚠️ {line}（疑似断更）", True) if age_h > 26 else (line, False)


def _forward_segment(path: Path, title: str) -> str:
    if not path.exists():
        return f"{title}：尚未生成"
    df = pd.read_csv(path, parse_dates=["signal_time"])
    if df.empty:
        return f"{title}：累计 0 笔"
    # status column preferred; fall back to outcome==open
    if "status" in df.columns:
        open_n = int((df["status"].astype(str) == "open").sum())
        closed = df[df["status"].astype(str) == "closed"]
    else:
        open_n = int((df.get("outcome", pd.Series(dtype=str)).astype(str) == "open").sum())
        closed = df[df.get("outcome", pd.Series(dtype=str)).astype(str) != "open"]
    closed = closed.dropna(subset=["realized_ret"]) if "realized_ret" in closed.columns else closed
    today = pd.Timestamp.now(tz=timezone.utc).normalize()
    st = df["signal_time"]
    if getattr(st.dt, "tz", None) is None:
        st = st.dt.tz_localize("UTC")
    new_today = int((st >= today).sum())
    seg = f"<b>{title}</b>：今日+{new_today}，累计 {len(df)}（开仓中 {open_n}）"
    if len(closed) and "realized_ret" in closed.columns:
        net = closed["realized_ret"].astype(float) - 0.0006
        seg += (
            f"｜已平净 {100 * net.sum():+.2f}% 胜率 {100 * (net > 0).mean():.0f}% "
            f"裁决 {len(closed)}/100"
        )
    return seg


def forward_board() -> str:
    lines = [_forward_segment(LOG, "主线 TP5/SL2")]
    if LOG_H1.exists():
        lines.append(_forward_segment(LOG_H1, "影子 H1 scaled"))
    return "\n".join(lines)


def system_health() -> str:
    notes = []
    for name, log in (("离线管道", "logs/offline_run.log"), ("队列5", "logs/offline_queue5.log")):
        p = PROJECT_DIR / log
        if p.exists():
            tail = p.read_text()[-400:]
            if "Traceback" in tail:
                notes.append(f"⚠️ {name} 日志尾部有异常，需要人看")
    # Prefer E2.1 retrain curve if present, else legacy full_s
    for label, rel in (
        ("yolo11s E2.1", "runs/detect/runs/detect/dense_15m_full_s_e21/results.csv"),
        ("yolo11s", "runs/detect/runs/detect/dense_15m_full_s/results.csv"),
    ):
        results = PROJECT_DIR / rel
        if not results.exists():
            continue
        import csv

        rows = list(csv.DictReader(results.open()))
        if not rows:
            continue
        best = None
        for r in rows:
            for k, v in r.items():
                if k.strip().endswith("mAP50(B)") and "95" not in k:
                    m = float(v)
                    if best is None or m > best:
                        best = m
        if best is not None:
            import subprocess

            train_alive = False
            try:
                # pgrep returns 0 if any match; ignore self
                train_alive = (
                    subprocess.call(
                        ["pgrep", "-f", "src.detection.train"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    == 0
                )
            except OSError:
                train_alive = False
            state = "训练中" if train_alive else "已结束/未跑"
            notes.append(
                f"{label}：{len(rows)} epochs，最佳 mAP50 {best:.3f}（线 0.90）· {state}"
            )
        break
    return "\n".join(notes) if notes else "系统：无异常"


def main() -> int:
    fresh, alert = data_freshness()
    msg = (f"📊 <b>fable-trading 日报</b> {datetime.now():%m-%d %H:%M}\n"
           f"{fresh}\n{forward_board()}\n{system_health()}\n"
           f"看板：http://103.214.174.58:8642")
    if alert:
        msg = "‼️ 有异常需要处理\n" + msg
    ok = send(msg)
    print(f"digest sent: {ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
