"""Daily digest -> Telegram (the optimized notification: ONE structured
message per day, numbers first, alerts only when something is wrong).

Sections: data freshness | forward signals (new + cumulative scoreboard) |
champion/challenger shadow comparison | system health | pipeline anomalies.
Run after update_okx + forward_track (and optional forward_track_shadows).

Pipeline anomaly glue (Todo 9b): read-only import of webapp
``collect_anomalies`` / ``pipeline_status_payload`` — no writes, no Telegram
side effects from the loader. Prefer ``--dry-run`` until token rotation.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
from src.judgment.shadow_compare import format_comparison_text  # noqa: E402
from src.judgment.forward_types import FORWARD_LOG_H1_SCALED_PATH, FORWARD_LOG_PATH  # noqa: E402
from src.notify import send  # noqa: E402

LOG = FORWARD_LOG_PATH
LOG_H1 = FORWARD_LOG_H1_SCALED_PATH
KLINE_DIR = PROJECT_DIR / "data" / "kline_fetched"

# Severity order for ranking "top" anomalies in the digest (crit first).
_SEV_RANK = {"crit": 0, "warn": 1, "info": 2}


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
    """Mainline + H1 segment plus full registry comparison (unsupported named)."""
    lines = [_forward_segment(LOG, "主线 TP5/SL2")]
    if LOG_H1.exists():
        lines.append(_forward_segment(LOG_H1, "影子 H1 scaled"))
    lines.append(format_comparison_text())
    return "\n".join(lines)


def load_pipeline_anomalies() -> list[dict[str, Any]]:
    """Read-only: pipeline health flags from stage metadata (no I/O writes)."""
    from src.webapp.pipeline_status import pipeline_status_payload

    payload = pipeline_status_payload()
    raw = payload.get("anomalies") or []
    return [a for a in raw if isinstance(a, dict)]


def format_pipeline_anomalies(
    anomalies: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> tuple[str, bool]:
    """Format top pipeline anomaly ids for the digest body.

    Returns (section_text, alert_flag). alert_flag is True when any
    warn/crit flag is present (info-only does not raise the header alert).
    Pure function of the provided list — no I/O; unit-test with injected flags.
    """
    if not anomalies:
        return "管道健康：ok（0 anomalies）", False

    ranked = sorted(
        anomalies,
        key=lambda a: (
            _SEV_RANK.get(str(a.get("severity") or "info"), 9),
            str(a.get("id") or ""),
        ),
    )
    top = ranked[: max(1, int(limit))]
    lines = [f"管道健康：{len(anomalies)} flag(s)（top {len(top)}）"]
    for a in top:
        sev = str(a.get("severity") or "info")
        aid = str(a.get("id") or "?")
        msg = str(a.get("message") or "").strip()
        if len(msg) > 120:
            msg = msg[:117] + "..."
        suffix = f" — {msg}" if msg else ""
        lines.append(f"- {sev} <code>{aid}</code>{suffix}")
    alert = any(str(a.get("severity") or "") in {"warn", "crit"} for a in anomalies)
    return "\n".join(lines), alert


def build_message(
    anomalies: list[dict[str, Any]] | None = None,
) -> tuple[str, bool]:
    """Build digest text. If anomalies is None, load live pipeline flags."""
    fresh, alert = data_freshness()
    flags = load_pipeline_anomalies() if anomalies is None else list(anomalies)
    anom_sec, anom_alert = format_pipeline_anomalies(flags)
    alert = alert or anom_alert
    msg = (
        f"📊 <b>fable-trading 日报</b> {datetime.now():%m-%d %H:%M}\n"
        f"{fresh}\n{forward_board()}\n{system_health()}\n{anom_sec}\n"
        f"看板：http://103.214.174.58:8642"
    )
    if alert:
        msg = "‼️ 有异常需要处理\n" + msg
    return msg, alert


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily digest (Telegram or dry-run).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print message only; never call Telegram send.",
    )
    args = parser.parse_args(argv)
    flags = load_pipeline_anomalies()
    msg, alert = build_message(anomalies=flags)
    if args.dry_run:
        ids = [str(a.get("id") or "") for a in flags if a.get("id")]
        print(msg)
        print(
            f"telegram_send: SKIPPED\n"
            f"alert_flag: {alert}\n"
            f"anomaly_count: {len(flags)}\n"
            f"anomaly_ids: {','.join(ids) if ids else '(none)'}"
        )
        return 0
    ok = send(msg)
    print(f"digest sent: {ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
