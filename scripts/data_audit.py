"""Data-quality audit (P2-12): gaps, zero-volume, OHLC integrity, staleness.

Scans every bar the loader knows about (5m/15m/30m/1H) plus orphan .part.csv
fetch leftovers. Writes:

  analysis/output/data_audit.csv
  analysis/output/data_audit_summary.json
  analysis/p2_data_audit_report.md

Blacklist recommendations are advisory only — do not mutate loader.BLOCKED_BASES
without owner approval.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.data.bars import BAR_CHOICES
from src.data.loader import FETCHED_DIR, iter_series

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_DIR / "analysis" / "output"
OUT_CSV = OUT_DIR / "data_audit.csv"
OUT_JSON = OUT_DIR / "data_audit_summary.json"
OUT_REPORT = PROJECT_DIR / "analysis" / "p2_data_audit_report.md"

BAR_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1H": 60}

# Advisory thresholds (documented in report). Series above these become
# blacklist *candidates*, not automatic exclusions.
MAX_GAPS = 5
MAX_ZERO_VOL = 0.02
# Crypto 15m wicks >25% are common; only chronic spikiness is structural.
MAX_SPIKES_FLAG = 3          # report / structural flag
MAX_SPIKES_BLACKLIST = 8     # blacklist candidate bar
MAX_OHLC_BAD = 0
STALE_HOURS = 48.0  # last bar older than this vs "now" is flagged
# Equity/tokenized-stock style names often thin on OKX swap; surface them.
STOCKISH_HINTS = (
    "AAPL", "ADBE", "AMAT", "AMD", "AMZN", "ASML", "AVGO", "COIN", "CRCL",
    "GOOGL", "HOOD", "INTC", "META", "MSFT", "MSTR", "NVDA", "PLTR", "TSLA",
    "QQQ", "SPY", "ALAB", "APLD", "MU", "ORCL", "NFLX", "BABA",
)


def audit_frame(frame: pd.DataFrame, *, bar: str) -> dict:
    """Compute quality metrics for one OHLCV series."""
    minutes = BAR_MINUTES[bar]
    dt = frame["open_time"].diff().dt.total_seconds() / 60.0
    gaps = int((dt > minutes * 1.5).sum()) if len(dt) > 1 else 0
    max_gap_h = round(float(dt.max() / 60.0), 2) if len(dt) > 1 and pd.notna(dt.max()) else 0.0
    zero_vol = round(float((frame["volume"].fillna(0) <= 0).mean()), 4)
    ret = frame["close"].pct_change().abs()
    spikes = int((ret > 0.25).sum())
    ohlc_bad = int(
        (
            (frame["high"] < frame["low"])
            | (frame["high"] < frame["open"])
            | (frame["high"] < frame["close"])
            | (frame["low"] > frame["open"])
            | (frame["low"] > frame["close"])
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        ).sum()
    )
    last = frame["open_time"].iloc[-1]
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    age_h = (datetime.now(timezone.utc) - last.to_pydatetime()).total_seconds() / 3600.0
    return {
        "n_bars": len(frame),
        "first": str(frame["open_time"].iloc[0])[:10],
        "last": str(frame["open_time"].iloc[-1])[:16],
        "age_hours": round(age_h, 1),
        "n_gaps": gaps,
        "max_gap_hours": max_gap_h,
        "zero_vol_share": zero_vol,
        "spike_bars": spikes,
        "ohlc_bad": ohlc_bad,
    }


def flag_reasons(row: dict) -> list[str]:
    reasons: list[str] = []
    if row["n_gaps"] > MAX_GAPS:
        reasons.append(f"gaps>{MAX_GAPS}")
    if row["zero_vol_share"] > MAX_ZERO_VOL:
        reasons.append(f"zero_vol>{MAX_ZERO_VOL}")
    if row["spike_bars"] >= MAX_SPIKES_FLAG:
        reasons.append(f"spikes>={MAX_SPIKES_FLAG}")
    if row["ohlc_bad"] > MAX_OHLC_BAD:
        reasons.append("ohlc_bad")
    if row["age_hours"] > STALE_HOURS:
        reasons.append(f"stale>{STALE_HOURS}h")
    return reasons


def is_blacklist_candidate(row: dict) -> bool:
    """Stricter than flagged: only issues worth permanent exclusion review."""
    if row.get("ohlc_bad", 0) > MAX_OHLC_BAD:
        return True
    if row.get("n_gaps", 0) > MAX_GAPS:
        return True
    if row.get("zero_vol_share", 0) > 0.05:
        return True
    if row.get("spike_bars", 0) >= MAX_SPIKES_BLACKLIST:
        return True
    base = str(row.get("symbol", "")).split("_", 1)[0]
    if base in STOCKISH_HINTS and row.get("zero_vol_share", 0) > MAX_ZERO_VOL:
        return True
    return False


def scan_part_files(fetched_dir: Path = FETCHED_DIR) -> list[dict]:
    """Incomplete fetch leftovers (loader ignores these by filename regex)."""
    if not fetched_dir.is_dir():
        return []
    out = []
    for path in sorted(fetched_dir.glob("*.part.csv")):
        try:
            n = max(sum(1 for _ in path.open()) - 1, 0)
        except OSError:
            n = -1
        out.append({"path": path.name, "approx_rows": n, "size_bytes": path.stat().st_size})
    return out


def run_audit(*, min_bars: int = 300) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    for bar in BAR_CHOICES:
        if bar not in BAR_MINUTES:
            continue
        for source, symbol, frame in iter_series(bar=bar, min_bars=min_bars):
            metrics = audit_frame(frame, bar=bar)
            row = {"bar": bar, "source": source, "symbol": symbol, **metrics}
            reasons = flag_reasons(row)
            row["flagged"] = bool(reasons)
            row["reasons"] = "|".join(reasons)
            row["blacklist_candidate"] = is_blacklist_candidate(row)
            rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["bar", "symbol", "source"]).reset_index(drop=True)
    parts = scan_part_files()
    flagged = df[df["flagged"]] if not df.empty else df
    # Structural = quality issues excluding pure staleness.
    structural = flagged[
        flagged["reasons"].str.contains("gaps|zero_vol|spikes|ohlc_bad", na=False)
    ] if not flagged.empty else flagged
    bl = df[df["blacklist_candidate"]] if not df.empty else df
    swap15_bl = bl[(bl["bar"] == "15m") & (bl["symbol"].str.contains("_SWAP"))] if not bl.empty else bl
    swap15 = df[(df["bar"] == "15m") & (df["symbol"].str.contains("_SWAP")) & (df["source"] == "okx")] if not df.empty else df
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "series_total": int(len(df)),
        "by_bar": df.groupby("bar").size().to_dict() if not df.empty else {},
        "flagged": int(len(flagged)),
        "structural_flagged": int(len(structural)),
        "blacklist_candidate_n": int(len(bl)),
        "okx_swap15_n": int(len(swap15)),
        "okx_swap15_stale": int((swap15["age_hours"] > STALE_HOURS).sum()) if not swap15.empty else 0,
        "part_files": parts,
        "worst_gaps": (
            df.nlargest(8, "n_gaps")[["bar", "source", "symbol", "n_gaps", "max_gap_hours"]]
            .to_dict("records")
            if not df.empty
            else []
        ),
        "spike_series": (
            structural[structural["spike_bars"] >= MAX_SPIKES_FLAG][
                ["bar", "source", "symbol", "spike_bars"]
            ]
            .sort_values("spike_bars", ascending=False)
            .head(15)
            .to_dict("records")
            if not structural.empty
            else []
        ),
        "zero_vol_series": (
            structural[structural["zero_vol_share"] > MAX_ZERO_VOL][
                ["bar", "source", "symbol", "zero_vol_share"]
            ]
            .sort_values("zero_vol_share", ascending=False)
            .head(15)
            .to_dict("records")
            if not structural.empty
            else []
        ),
        "ohlc_bad_series": (
            structural[structural["ohlc_bad"] > 0][
                ["bar", "source", "symbol", "ohlc_bad"]
            ]
            .head(15)
            .to_dict("records")
            if not structural.empty
            else []
        ),
        "blacklist_candidates_swap15": (
            swap15_bl[["source", "symbol", "n_gaps", "zero_vol_share", "spike_bars", "ohlc_bad", "reasons"]]
            .sort_values(["zero_vol_share", "spike_bars"], ascending=False)
            .head(40)
            .to_dict("records")
            if not swap15_bl.empty
            else []
        ),
        "thresholds": {
            "max_gaps": MAX_GAPS,
            "max_zero_vol": MAX_ZERO_VOL,
            "max_spikes_flag": MAX_SPIKES_FLAG,
            "max_spikes_blacklist": MAX_SPIKES_BLACKLIST,
            "stale_hours": STALE_HOURS,
        },
    }
    return df, summary


def write_report(df: pd.DataFrame, summary: dict) -> str:
    lines = [
        "# P2-12 数据质量审计",
        "",
        f"**日期**：{summary['generated_at']}",
        "**纪律**：只读扫描；不改 loader 黑名单、不碰 holdout、不调参。",
        "",
        "## 复现命令",
        "",
        "```bash",
        "PYTHONPATH=. python3 scripts/data_audit.py",
        "python3 -m pytest tests/test_data_audit.py -q",
        "```",
        "",
        "## 覆盖统计",
        "",
        f"| 项 | 数值 |",
        f"|---|---:|",
        f"| 序列总数 | {summary['series_total']} |",
        f"| 触发任一阈值 | {summary['flagged']} |",
        f"| 结构性问题（缺口/零量/尖刺/OHLC） | {summary['structural_flagged']} |",
        f"| 黑名单候选（全宇宙） | {summary['blacklist_candidate_n']} |",
        f"| OKX SWAP 15m 序列 | {summary['okx_swap15_n']} |",
        f"| OKX SWAP 15m stale | {summary['okx_swap15_stale']} |",
        f"| 未完成 `.part.csv` | {len(summary['part_files'])} |",
        "",
        "分 bar：",
        "",
    ]
    for bar, n in sorted(summary.get("by_bar", {}).items()):
        lines.append(f"- `{bar}`: {n}")
    lines += [
        "",
        "## 阈值",
        "",
        f"- 缺口数 > {MAX_GAPS}（间隔 > 1.5×bar）",
        f"- 零成交量占比 > {MAX_ZERO_VOL:.0%} 记入 flagged；>5% 才进黑名单候选",
        f"- 单 bar \\|ret\\| > 25% 计 spike；≥{MAX_SPIKES_FLAG} 才 structural，≥{MAX_SPIKES_BLACKLIST} 才黑名单",
        f"- OHLC 逻辑错误（high<low / 越界 / 非正价）> 0",
        f"- 末 bar 距今 > {STALE_HOURS:.0f}h → 标 stale（优先跑 `update_okx`，不是黑名单）",
        f"- 股票类 SWAP（AAPL/NVDA/…）在 zero_vol>{MAX_ZERO_VOL:.0%} 时直接进黑名单候选",
        "",
        "## 最差缺口 Top",
        "",
        "| bar | source | symbol | n_gaps | max_gap_h |",
        "|---|---|---|---:|---:|",
    ]
    for r in summary.get("worst_gaps", []):
        lines.append(
            f"| {r['bar']} | {r['source']} | {r['symbol']} | {r['n_gaps']} | {r['max_gap_hours']} |"
        )
    lines += [
        "",
        "## 尖刺 / 零量 / OHLC 坏样本",
        "",
        "### spikes",
        "",
    ]
    if summary.get("spike_series"):
        lines.append("| bar | source | symbol | spikes |")
        lines.append("|---|---|---|---:|")
        for r in summary["spike_series"]:
            lines.append(
                f"| {r['bar']} | {r['source']} | {r['symbol']} | {r['spike_bars']} |"
            )
    else:
        lines.append("（无）")
    lines += ["", "### zero_vol", ""]
    if summary.get("zero_vol_series"):
        lines.append("| bar | source | symbol | zero_vol_share |")
        lines.append("|---|---|---|---:|")
        for r in summary["zero_vol_series"]:
            lines.append(
                f"| {r['bar']} | {r['source']} | {r['symbol']} | {r['zero_vol_share']} |"
            )
    else:
        lines.append("（无）")
    lines += ["", "### ohlc_bad", ""]
    if summary.get("ohlc_bad_series"):
        lines.append("| bar | source | symbol | ohlc_bad |")
        lines.append("|---|---|---|---:|")
        for r in summary["ohlc_bad_series"]:
            lines.append(
                f"| {r['bar']} | {r['source']} | {r['symbol']} | {r['ohlc_bad']} |"
            )
    else:
        lines.append("（无）")

    lines += [
        "",
        "## 未完成拉取（`.part.csv`）",
        "",
    ]
    if summary["part_files"]:
        lines.append("| file | approx_rows | size_bytes |")
        lines.append("|---|---:|---:|")
        for r in summary["part_files"]:
            lines.append(f"| `{r['path']}` | {r['approx_rows']} | {r['size_bytes']} |")
        lines.append("")
        lines.append(
            "这些文件**不会**被 loader 读入。重跑 `python3 -m src.data.fetch_okx` "
            "对应币种可续传；不要手改文件名假装完成。"
        )
    else:
        lines.append("无。")

    lines += [
        "",
        "## 黑名单候选（SWAP 15m 结构性问题）",
        "",
        "> 仅建议，**未写入** `loader.BLOCKED_BASES`。owner 确认后再改。",
        "",
    ]
    cands = summary.get("blacklist_candidates_swap15") or []
    if cands:
        lines.append("| symbol | gaps | zero_vol | spikes | ohlc_bad | reasons |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for r in cands:
            lines.append(
                f"| {r['symbol']} | {r['n_gaps']} | {r['zero_vol_share']} | "
                f"{r['spike_bars']} | {r['ohlc_bad']} | {r['reasons']} |"
            )
    else:
        lines.append("当前 SWAP 15m 无结构性黑名单候选（仅 stale/轻微问题则不列）。")

    # Gate vs OKX note
    if not df.empty:
        gate_n = int(((df["source"] == "gate") & (df["bar"] == "15m")).sum())
        okx_n = int(((df["source"] == "okx") & (df["bar"] == "15m")).sum())
        lines += [
            "",
            "## 解读",
            "",
            f"- 15m 序列：okx={okx_n}，gate={gate_n}。主线宇宙是 **OKX SWAP**；gate 与 spot 仅作对照。",
            "- 单 bar >25% 尖刺在山寨上偶发，可能是真实插针；列入候选时需人工 spot-check K 线。",
            "- stale 优先跑每日 `update_okx`，不要当成永久坏币。",
            "",
            "## 风险与诚实声明",
            "",
            "- 缺口检测用 `diff > 1.5×bar`，节假日/停牌造成的真实空洞也会被计数。",
            "- 旧 cache 与 kline_fetched 合并后的序列会一起审计；决策应以 OKX fetched 为准。",
            "- 本审计不修改任何训练数据或黑名单。",
            "",
            "## 下一步（需 owner 决策的标为决策）",
            "",
            "1. 对上表 SWAP 15m 黑名单候选逐币 spot-check（决策）。",
            "2. 清掉或续传 `.part.csv` 未完成币种。",
            "3. 确认每日 `update_okx` 仍在跑，stale 应在 24h 内消失。",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-bars", type=int, default=300)
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, summary = run_audit(min_bars=args.min_bars)
    df.to_csv(OUT_CSV, index=False)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    report = write_report(df, summary)
    OUT_REPORT.write_text(report, encoding="utf-8")
    print(json.dumps({
        "series_total": summary["series_total"],
        "flagged": summary["flagged"],
        "structural_flagged": summary["structural_flagged"],
        "part_files": len(summary["part_files"]),
        "csv": str(OUT_CSV),
        "report": str(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
