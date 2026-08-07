#!/usr/bin/env python3
"""Assemble preholdout + holdout w20 tip-replay results into analysis report + HTML."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis" / "output"
PRE = OUT / "w20_tip_preholdout.json"
HOLD = OUT / "w20_tip_holdout.json"
MD = PROJECT / "analysis" / "p_w20_midbox_tip_backtest_20260807.md"


def bp(x):
    if x is None:
        return "—"
    return f"{x:+.1f}" if isinstance(x, (int, float)) else str(x)


def load(p: Path) -> dict:
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get("summary", {})


def section(title: str, s: dict) -> str:
    if not s:
        return f"## {title}\n\n**缺失结果文件。**\n"
    return f"""## {title}

| 指标 | 值 |
|------|-----|
| 区间 | {s.get('range')} |
| 币种数 | {s.get('n_symbols')} |
| 扫描 tip 数 | {s.get('bars_scanned')} |
| 原始开火 | {s.get('fired_raw')} ({s.get('fire_per_1k_bars')} /1k bars) |
| 入账成交 | **{s.get('n_trades')}** |
| 胜率 | {s.get('win_rate')} |
| 利润因子 PF | {s.get('profit_factor')} |
| 毛均收益 | {bp(s.get('mean_gross_bp'))} bp |
| 净均收益（扣 maker RT） | **{bp(s.get('mean_net_bp'))} bp** |
| 合计净收益（单位仓） | {s.get('total_net_units')} |
| 结局分布 | `{s.get('outcomes')}` |
| matched 对数 | {s.get('matched_n')} |
| matched lift | **{bp(s.get('matched_lift_bp'))} bp** (se {bp(s.get('matched_lift_se_bp'))}) |
| UTC-week 置换 p | **{s.get('perm_p')}** |
| 成本 | {s.get('cost')} |
| holdout | {s.get('holdout_note')} |

"""


def main() -> int:
    pre = load(PRE)
    hold = load(HOLD)
    if not pre and not hold:
        print("no results yet", file=sys.stderr)
        return 1

    w = (pre or hold).get("weights", "")
    protocol = (pre or hold).get("protocol", "")
    md = f"""# w20 midbox tip 回测裁决 — 2026-08-07

> Owner 2026-08-07 明确批准：ATR 障碍 TP/SL + 全市场 tip 扫描 + matched control 置换 + **holdout**。

## 一句话

见下方 pre-holdout / holdout 表；检测器权重为 cold yolo11s 训在 `dense_owner_w20_midbox` 的 best.pt。

## 协议（固定）

- 权重：`{w}`
- Tip 窗：W=24 右对齐 tip；全历史算 MA 后切片渲染
- conf≥0.15；框右缘落在 tip/tip-1（TIP_EDGE=2）
- 同币 MIN_GAP=18 bar 去重
- 入场：信号 bar 的下一根 open
- 障碍：TP=5×ATR14 / SL=2×ATR14 / 72 bar 超时；同 bar 双触 → SL
- 成本：maker 往返 FORWARD_COST
- tip-stride=2（每隔一根 tip 扫描，诚实声明：非每一根，速度折中）
- matched control：同币 × UTC 月 × atr_pct 五分位随机入场，同障碍同成本
- 置换：UTC-week 整周 sign-flip，n=2000，双侧 p

{protocol}

## Holdout 消耗登记

- **配置名**：`w20_midbox_tip_replay_W24_c0.15`
- **这是该配置第 1 次消耗 holdout**（Owner 2026-08-07 对话明确批准执行 holdout）
- holdout 起点：≥2026-05-04 UTC
- 未改 ACTIVE / 未下单 / 未改障碍默认

{section("Pre-holdout（训练侧外推窗）", pre)}
{section("Holdout（第 1 次消耗）", hold)}

## 解读

"""
    # auto interpretation
    def verdict(s: dict, name: str) -> str:
        if not s or not s.get("n_trades"):
            return f"- **{name}**：无成交或未完成。\n"
        net = s.get("mean_net_bp") or 0
        lift = s.get("matched_lift_bp")
        p = s.get("perm_p")
        lines = [f"- **{name}**：n={s.get('n_trades')}，净 {bp(net)} bp/笔"]
        if lift is not None:
            lines.append(f"，matched lift {bp(lift)} bp")
        if p is not None:
            lines.append(f"，perm p={p}")
        if net > 0 and lift is not None and lift > 0 and p is not None and p < 0.01:
            lines.append(" → **经济边过门（粗）**")
        elif net > 0 and (lift is None or lift <= 0):
            lines.append(" → 毛/净为正但相对对照无增量（可能踩 beta）")
        elif net <= 0:
            lines.append(" → **净亏**；检测器 tip 入场未证明成本后 edge")
        lines.append("\n")
        return "".join(lines)

    md += verdict(pre, "Pre-holdout")
    md += verdict(hold, "Holdout")
    md += """
## 风险与诚实声明

1. tip-stride=2：不是每一根 tip 都扫，可能漏掉半个相位上的信号；密度与收益都可能有偏。
2. W=24 是训练窗 20–30 的中位折中；若最优推理窗不同，本结果不可直接外推。
3. matched control 用月×波动桶，不是完整共时市场组合回测。
4. holdout 已消耗 1 次；同配置再读 holdout 必须重新获批并记第 N 次。
5. 未 promote、未部署、未真下单。

## 复现

```bash
PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/backtest_w20_midbox_tip.py \\
  --weights analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt \\
  --start 2026-03-01 --end 2026-05-03 --tip-stride 2 --tag w20_tip_preholdout --device mps

PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/backtest_w20_midbox_tip.py \\
  --weights analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt \\
  --start 2026-05-04 --end 2026-07-01 --tip-stride 2 \\
  --allow-holdout --holdout-n 1 --tag w20_tip_holdout --device mps
```

## 产物

- `analysis/output/w20_tip_preholdout.json` / `_trades.csv` / `_matched.csv`
- `analysis/output/w20_tip_holdout.json` / `_trades.csv` / `_matched.csv`
- 本报告 + HTML

生成于 {datetime.now(timezone.utc).isoformat()}
"""
    MD.write_text(md, encoding="utf-8")
    # HTML
    subprocess.run(
        [
            sys.executable,
            str(PROJECT / "scripts" / "md_to_html.py"),
            str(MD),
            "--out-dir",
            str(PROJECT / "analysis" / "html"),
        ],
        check=False,
    )
    print(f"wrote {MD}")
    html = PROJECT / "analysis" / "html" / (MD.stem + ".html")
    print(f"html {html} exists={html.exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
