#!/usr/bin/env python3
"""Build the executed companion notebook for the Pine ALLIN-V7 audit.

Inputs are the frozen pre-holdout replay outputs under
``experiments/active/exp-pine-allin-v7-preholdout-v1/results``.  The script
does not read market data or the repository holdout; it only derives compact
diagnostics from the already validated trade ledger and executes a notebook
that makes those derivations reviewable from top to bottom.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence

import nbformat
import pandas as pd
from nbclient import NotebookClient


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/active/exp-pine-allin-v7-preholdout-v1"
RESULTS = EXPERIMENT / "results"
NOTEBOOKS = EXPERIMENT / "notebooks"


def _aggregate(frame: pd.DataFrame, keys: Sequence[str]) -> List[Dict[str, object]]:
    """Return compact unit-return diagnostics for the requested grouping."""

    grouped = frame.groupby(list(keys), dropna=False)
    summary = grouped.agg(
        trades=("net_return", "size"),
        gross_bp=("gross_return", lambda values: values.mean() * 10_000),
        net_bp=("net_return", lambda values: values.mean() * 10_000),
        win_rate_pct=("net_return", lambda values: (values > 0).mean() * 100),
        holding_bars_median=("holding_bars", "median"),
    ).reset_index()
    return summary.round(4).to_dict("records")


def build_diagnostics(trades: pd.DataFrame) -> Dict[str, object]:
    """Derive stability and attribution diagnostics without tuning a parameter."""

    risk = trades.loc[trades["arm"] == "v7_risk_2pct"].copy()
    diagnostics: Dict[str, object] = {
        "by_direction": _aggregate(risk, ["split", "direction"]),
        "by_exit_reason": _aggregate(risk, ["split", "exit_reason"]),
        "stability": {},
    }

    for split in ("development", "validation"):
        selected = risk.loc[risk["split"] == split].copy()
        selected["month"] = selected["entry_time"].dt.strftime("%Y-%m")
        monthly = selected.groupby("month")["net_return"].mean() * 10_000
        by_symbol = selected.groupby("symbol")["net_return"].mean() * 10_000
        diagnostics["stability"][split] = {
            "months": int(monthly.size),
            "positive_months": int((monthly > 0).sum()),
            "monthly_net_bp_median": round(float(monthly.median()), 4),
            "monthly_net_bp_min": round(float(monthly.min()), 4),
            "monthly_net_bp_max": round(float(monthly.max()), 4),
            "symbols": int(by_symbol.size),
            "positive_symbols": int((by_symbol > 0).sum()),
            "symbol_net_bp_q25": round(float(by_symbol.quantile(0.25)), 4),
            "symbol_net_bp_median": round(float(by_symbol.median()), 4),
            "symbol_net_bp_q75": round(float(by_symbol.quantile(0.75)), 4),
        }

    boosted = trades.loc[trades["arm"] == "v7_fixed_boost"].copy()
    boosted["time_multiplier"] = (boosted["leverage"] / 4.0).round(2)
    diagnostics["by_time_multiplier"] = _aggregate(
        boosted, ["split", "time_multiplier"]
    )

    schedule_keys = ["symbol", "direction", "signal_time", "entry_time"]
    base_schedules = risk[schedule_keys].drop_duplicates().assign(_base=True)
    no_skip = trades.loc[trades["arm"] == "v7_risk_2pct_no_skip"].copy()
    incremental = no_skip.merge(base_schedules, on=schedule_keys, how="left")
    incremental = incremental.loc[incremental["_base"].isna()]
    diagnostics["no_skip_incremental"] = _aggregate(incremental, ["split"])
    return diagnostics


def make_notebook() -> nbformat.NotebookNode:
    """Create a concise notebook whose cells run against generated evidence."""

    cells = [
        nbformat.v4.new_markdown_cell(
            "# Pine ALLIN-V7 预留验证审计\n\n"
            "**TL;DR：** V7 进场相对同币、同月、同时间块、同 ATR 桶的随机入场有显著优势，"
            "但验证期每笔毛收益 18.37 bp 小于冻结的 20 bp 往返成本。2% 风险预算把资金曲线"
            "最大回撤从 67.66% 降至 19.20%，却不会把负的单位期望变正。"
        ),
        nbformat.v4.new_code_cell(
            "from pathlib import Path\n"
            "import json\n"
            "import pandas as pd\n\n"
            "ROOT = Path.cwd()\n"
            "EXP = ROOT / 'experiments/active/exp-pine-allin-v7-preholdout-v1'\n"
            "RESULTS = EXP / 'results'\n"
            "config = json.loads((EXP / 'config.json').read_text())\n"
            "validation = json.loads((RESULTS / 'validation.json').read_text())\n"
            "diagnostics = json.loads((RESULTS / 'diagnostics.json').read_text())\n"
            "summary = pd.read_csv(RESULTS / 'summary.csv')\n"
            "quality = pd.read_csv(RESULTS / 'data_quality.csv')\n"
            "assert validation['status'] == 'pass'\n"
            "assert config['eligibility']['holdout_consumed'] is False\n"
            "print(f\"验证器: {len(validation['checks'])}/{len(validation['checks'])} checks pass\")\n"
            "print(f\"安全分析截止: {config['safe_end_exclusive']}；holdout: {config['holdout_start']}\")"
        ),
        nbformat.v4.new_markdown_cell(
            "## Context & Methods\n\n"
            "这是用户所给 Pine V7.2 的因果 Python 翻译：信号在 15 分钟 bar 收盘确认，"
            "下一根开盘成交，初始止损从成交时生效，保本更新在确认 bar 后生效。"
            "开发期为 2023–2024，预留验证期为 2025-01-01 至 2026-03-01（不含）。"
            "成本固定为每笔 20 bp。匹配随机对照复制方向、持有 bar 数、止损/保本和成本，"
            "并精确匹配币种、月份、香港 6 小时时间块和 ATR 五分位。"
        ),
        nbformat.v4.new_code_cell(
            "data_overview = pd.DataFrame({\n"
            "    '指标': ['币种', '原始 bars', '缺口', '最早 bar', '最晚 bar', 'V7 原始信号'],\n"
            "    '值': [len(quality), int(quality.rows.sum()), int(quality.gap_count.sum()),\n"
            "           quality.first_bar.min(), quality.last_bar.max(), int(quality.v7_raw_signals.sum())]\n"
            "})\n"
            "data_overview"
        ),
        nbformat.v4.new_markdown_cell("## Results"),
        nbformat.v4.new_code_cell(
            "cols = ['arm', 'n_trades', 'gross_bp_per_trade', 'net_bp_per_trade', 'win_rate',\n"
            "        'profit_factor', 'average_leverage', 'portfolio_return',\n"
            "        'portfolio_max_drawdown', 'matched_control_net_bp', 'paired_excess_bp',\n"
            "        'paired_signflip_p', 'top_decile_net_bp', 'permutation_p', 'auc_net_positive']\n"
            "view = summary.loc[summary.split.eq('validation'), cols].copy()\n"
            "for col in ['win_rate', 'portfolio_return', 'portfolio_max_drawdown']:\n"
            "    view[col] = (view[col] * 100).round(2)\n"
            "view.round(4)"
        ),
        nbformat.v4.new_markdown_cell(
            "## Stability and attribution\n\n"
            "以下切片只用于解释已冻结结果，不据此改阈值。方向收益发生翻转、时间倍率分组不稳定，"
            "说明不能根据开发期最好分组做日历择时。"
        ),
        nbformat.v4.new_code_cell("pd.DataFrame(diagnostics['by_direction'])"),
        nbformat.v4.new_code_cell("pd.DataFrame(diagnostics['by_exit_reason'])"),
        nbformat.v4.new_code_cell("pd.DataFrame(diagnostics['by_time_multiplier'])"),
        nbformat.v4.new_code_cell("pd.DataFrame(diagnostics['no_skip_incremental'])"),
        nbformat.v4.new_markdown_cell(
            "## Takeaways\n\n"
            "1. **拒绝当前经济假设。** V7 验证期净收益 -1.63 bp/笔，top-decile -32.00 bp/笔，"
            "不能上线。\n"
            "2. **保留风险预算作为工程改进。** 它把最坏资金路径大幅压低，但不是 alpha。\n"
            "3. **保留 skip，关闭日历倍率。** skip 的增量交易验证期 -15.10 bp/笔；日历倍率"
            "开发/验证表现翻转。\n"
            "4. **下一轮只允许单变量。** 优先测试进场后确认或低换手门；改变 ATR/止损、成本或"
            "读取 holdout 必须先由 owner 批准。"
        ),
        nbformat.v4.new_code_cell(
            "val = summary.set_index(['split', 'arm'])\n"
            "fixed = val.loc[('validation', 'v7_fixed_boost')]\n"
            "risk = val.loc[('validation', 'v7_risk_2pct')]\n"
            "assert risk.net_bp_per_trade < 0\n"
            "assert risk.portfolio_max_drawdown < fixed.portfolio_max_drawdown\n"
            "assert risk.paired_signflip_p < 0.01\n"
            "assert risk.top_decile_net_bp < 0 and risk.permutation_p > 0.95\n"
            "print('All decision assertions passed; holdout consumption remains 0.')"
        ),
    ]
    return nbformat.v4.new_notebook(
        cells=cells,
        metadata={
            "kernelspec": {
                "display_name": "Fable Pine Eval",
                "language": "python",
                "name": "fable-pine-eval",
            },
            "language_info": {"name": "python", "version": "3.9"},
        },
    )


def main() -> None:
    """Write diagnostics, then build and execute the companion notebook."""

    NOTEBOOKS.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(
        RESULTS / "trades.csv",
        parse_dates=["signal_time", "entry_time", "exit_time"],
    )
    diagnostics = build_diagnostics(trades)
    (RESULTS / "diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    notebook = make_notebook()
    output = NOTEBOOKS / "pine_allin_v7_preholdout_audit.ipynb"
    client = NotebookClient(
        notebook,
        timeout=600,
        kernel_name="fable-pine-eval",
        resources={"metadata": {"path": str(ROOT)}},
        record_timing=False,
    )
    executed = client.execute()
    nbformat.write(executed, output)
    print(f"wrote {RESULTS / 'diagnostics.json'}")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
