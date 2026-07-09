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
KLINE_DIR = PROJECT_DIR / "data" / "kline_fetched"


def data_freshness() -> tuple[str, bool]:
    files = sorted(KLINE_DIR.glob("okx_*_USDT_SWAP_15m_*.csv"))
    if not files:
        return "❌ 无合约数据文件", True
    newest = max(f.stat().st_mtime for f in files)
    age_h = (datetime.now().timestamp() - newest) / 3600
    line = f"数据更新：{len(files)} 个合约序列，最新写入 {age_h:.1f} 小时前"
    return (f"⚠️ {line}（疑似断更）", True) if age_h > 26 else (line, False)


def forward_board() -> str:
    if not LOG.exists():
        return "前向日志：尚未生成"
    df = pd.read_csv(LOG, parse_dates=["signal_time"])
    if df.empty:
        return "前向信号：累计 0 笔（等待新密集形成）"
    today = pd.Timestamp.now(tz=timezone.utc).normalize()
    lines = []
    for config, g in df.groupby("config"):
        closed = g[g["outcome"] != "open"].dropna(subset=["realized_ret"])
        new_today = int((g["signal_time"] >= today).sum())
        seg = f"<b>{config}</b>：今日新增 {new_today}，累计 {len(g)} 笔（未平 {int((g['outcome']=='open').sum())}）"
        if len(closed):
            net = closed["realized_ret"] - 0.0006
            seg += f"｜已平净收益 {100*net.sum():+.2f}%，胜率 {100*(net>0).mean():.0f}%，距100笔裁决线 {len(closed)}/100"
        lines.append(seg)
    return "\n".join(lines)


def system_health() -> str:
    notes = []
    for name, log in (("离线管道", "logs/offline_run.log"), ("队列5", "logs/offline_queue5.log")):
        p = PROJECT_DIR / log
        if p.exists():
            tail = p.read_text()[-400:]
            if "Traceback" in tail:
                notes.append(f"⚠️ {name} 日志尾部有异常，需要人看")
    results = PROJECT_DIR / "runs/detect/runs/detect/dense_15m_full_s/results.csv"
    if results.exists():
        import csv
        rows = list(csv.reader(results.open()))
        if len(rows) > 1:
            best = max(float(r[7]) for r in rows[1:] if len(r) > 7)
            notes.append(f"yolo11s：{len(rows)-1} epochs，最佳 mAP50 {best:.3f}（线 0.90）")
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
