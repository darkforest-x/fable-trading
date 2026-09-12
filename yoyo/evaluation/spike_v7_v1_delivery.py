"""Build a Chinese, artifact-bound delivery report for the frozen V7/V1 replay.

The builder never replays, scores, fetches, tunes, or promotes.  It only reads
the completed replay/post/figure artifacts and renders their recorded numbers.
By default it refuses any run other than the frozen 3,531 comparable streams.
``--allow-partial`` creates a visibly labelled smoke draft in a separate
delivery directory; it cannot be passed off as the final report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1"
CONFIG = EXPERIMENT / "config.json"
FINAL_REPORT = ROOT / "analysis/p1_spike_v7_v1_compare_20260912.md"


def sha256(path: Path) -> str:
    """Return one artifact's immutable byte identity."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _read_csv(folder: Path, name: str, *, gzip: bool = False) -> pd.DataFrame:
    path = folder / name
    if not path.is_file():
        raise ValueError(f"missing delivery input: {path}")
    return pd.read_csv(path, compression="gzip" if gzip else "infer")


def _relative(target: Path, base: Path) -> str:
    """Use absolute local paths so links survive conversion into analysis/html."""
    del base
    return target.resolve().as_posix()


def _num(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return "—"
    return f"{float(value):.{digits}f}"


def _pct(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return "—"
    return f"{100 * float(value):.{digits}f}%"


def _integer(value: object) -> str:
    return "—" if value is None or pd.isna(value) else str(int(value))


def _markdown_table(frame: pd.DataFrame, columns: Iterable[tuple[str, str, str]]) -> str:
    """Render selected artifact fields without synthesizing any result numbers."""
    cols = list(columns)
    header = "| " + " | ".join(label for _, label, _ in cols) + " |"
    divider = "| " + " | ".join("---" for _ in cols) + " |"
    rows = [header, divider]
    for row in frame.itertuples(index=False):
        values = []
        for field, _, style in cols:
            value = getattr(row, field)
            values.append(_pct(value) if style == "pct" else _num(value) if style == "num"
                          else _num(value, 4) if style == "p4"
                          else _integer(value) if style == "int" else str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def _account_summary(accounts: pd.DataFrame) -> pd.DataFrame:
    """Describe independent 1x closed balances without a cross-market curve."""
    required = {"variant", "timeframe_min", "entries", "closed", "insolvency_or_invalid_return_events",
                "net_return_closed_balance", "max_drawdown_closed_balance"}
    if not required.issubset(accounts):
        raise ValueError("independent account artifact lacks required columns")
    rows: list[dict[str, object]] = []
    for (variant, minutes), part in accounts.groupby(["variant", "timeframe_min"], sort=True):
        closed = pd.to_numeric(part.closed, errors="raise")
        entries = pd.to_numeric(part.entries, errors="raise")
        failed = pd.to_numeric(part.insolvency_or_invalid_return_events, errors="raise")
        eligible = part.loc[closed.gt(0) & failed.eq(0)].copy()
        returns = pd.to_numeric(eligible.net_return_closed_balance, errors="coerce")
        drawdowns = pd.to_numeric(eligible.max_drawdown_closed_balance, errors="coerce")
        if not returns.notna().all() or not drawdowns.notna().all():
            raise ValueError("closed non-insolvent account requires finite balance metrics")
        noninvalid = part.loc[failed.eq(0)].copy()
        all_with_zero_return = pd.to_numeric(noninvalid.net_return_closed_balance, errors="coerce").fillna(0.0)
        all_with_zero_dd = pd.to_numeric(noninvalid.max_drawdown_closed_balance, errors="coerce").fillna(0.0)
        rows.append({"variant": variant, "timeframe_min": int(minutes), "total_streams": len(part),
                     "streams_with_closed_trades": int(closed.gt(0).sum()),
                     "no_closed_trade_streams": int(closed.eq(0).sum()),
                     "zero_entry_streams": int(entries.eq(0).sum()),
                     "censored_only_streams": int((entries.gt(0) & closed.eq(0)).sum()),
                     "insolvency_or_invalid_streams": int(failed.gt(0).sum()),
                     "eligible_closed_streams": len(eligible),
                     "closed_balance_return_median": returns.median(),
                     "closed_balance_return_p90": returns.quantile(.9),
                     "closed_balance_dd_median": drawdowns.median(),
                     "closed_balance_dd_p90": drawdowns.quantile(.9),
                     "closed_balance_dd_max": drawdowns.max(),
                     "all_noninvalid_streams": len(noninvalid),
                     "all_noninvalid_zero_return_median": all_with_zero_return.median(),
                     "all_noninvalid_zero_return_p90": all_with_zero_return.quantile(.9),
                     "all_noninvalid_zero_dd_median": all_with_zero_dd.median(),
                     "all_noninvalid_zero_dd_p90": all_with_zero_dd.quantile(.9)})
    return pd.DataFrame(rows)


def _validate_scope(config: dict, raw_manifest: dict, post_manifest: dict, *, allow_partial: bool) -> str:
    expected = int(config["expected_coverage"]["comparable_cells"])
    frozen = int(raw_manifest.get("covered_streams_frozen", raw_manifest.get("comparable_streams_frozen", -1)))
    completed = int(raw_manifest.get("completed_streams", -1))
    post_available = int(post_manifest.get("available_streams", -1))
    if frozen != expected or int(post_manifest.get("frozen_streams", -1)) != expected:
        raise ValueError("delivery input does not preserve frozen 3531 comparable-stream scope")
    complete = completed == expected and post_available == expected and bool(post_manifest.get("complete"))
    if not complete and not allow_partial:
        raise ValueError(f"final delivery requires exactly {expected} completed streams; got replay={completed}, post={post_available}")
    return "完整 3531 流结果" if complete else f"部分 smoke 草稿：仅 {post_available}/{expected} 个冻结可比流；不可作正式结论"


def _variant_table(metrics: pd.DataFrame, variants: tuple[str, ...]) -> str:
    subset = metrics.loc[metrics.variant.isin(variants)].copy()
    return _markdown_table(subset, [
        ("timeframe_min", "周期(分钟)", "int"), ("variant", "执行臂", "text"),
        ("entries", "进场", "int"), ("closed", "已平仓", "int"), ("censored", "删失", "int"),
        ("win_rate", "胜率", "pct"), ("event_pf", "PF", "num"), ("mean_net_r", "平均净R", "num"),
        ("sum_net_r", "净R合计", "num"), ("net_ge_10r", "真实兑现≥10R", "int"),
        ("mfe_ge_10r", "MFE≥10R", "int"),
    ])


def build(raw: Path, post: Path, figures: Path, report: Path, delivery_dir: Path, *, allow_partial: bool,
          interpretation: Path | None = None) -> Path:
    """Render a source-identified markdown report from immutable result artifacts."""
    config = _read_json(CONFIG)
    raw_manifest = _read_json(raw / "manifest.json")
    post_manifest = _read_json(post / "post_manifest.json")
    figures_manifest_path = figures / "figures_manifest.json"
    figure_rows = json.loads(figures_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(figure_rows, list):
        raise ValueError("figure manifest must be a list")
    config_hash = sha256(CONFIG)
    if raw_manifest.get("config_sha256") != config_hash or post_manifest.get("config_sha256") != config_hash:
        raise ValueError("replay/post configuration hash differs from frozen config")
    if post_manifest.get("raw_manifest_sha256") != sha256(raw / "manifest.json"):
        raise ValueError("post artifact is not bound to supplied replay manifest")
    scope = _validate_scope(config, raw_manifest, post_manifest, allow_partial=allow_partial)
    interpretation_text = interpretation.read_text(encoding="utf-8").strip() if interpretation else ""
    interpretation_option = " --interpretation " + shlex.quote(str(interpretation.resolve())) if interpretation else ""

    overall = _read_csv(post, "metrics_overall.csv")
    timeframe = _read_csv(post, "metrics_timeframe.csv")
    yearly = _read_csv(post, "metrics_year.csv")
    venue = _read_csv(post, "metrics_venue.csv")
    side = _read_csv(post, "metrics_side.csv")
    signals = _read_csv(post, "signal_counts.csv")
    reasons = _read_csv(post, "filter_reasons.csv")
    tails = _read_csv(post, "exact_entry_tail_retention.csv")
    controls = _read_csv(post, "matched_control_metrics.csv")
    conflict_path = post / "v1_conflict_counts.csv"
    conflicts = pd.read_csv(conflict_path) if conflict_path.is_file() else pd.DataFrame()
    native = _read_csv(post, "historical_v1_native_metrics.csv")
    accounts = _read_csv(post, "independent_stream_closed_balance_metrics.csv.gz", gzip=True)
    native_accounts = _read_csv(post, "historical_v1_native_independent_accounts.csv.gz", gzip=True)
    account_summary = _account_summary(accounts)
    native_account_summary = _account_summary(native_accounts)

    delivery_dir.mkdir(parents=True, exist_ok=True)
    account_summary.to_csv(delivery_dir / "independent_account_summary.csv", index=False)
    native_account_summary.to_csv(delivery_dir / "historical_v1_native_independent_account_summary.csv", index=False)
    report.parent.mkdir(parents=True, exist_ok=True)
    raw_link, post_link, figure_link = (_relative(raw, report.parent), _relative(post, report.parent),
                                        _relative(figures, report.parent))
    long_variants = ("v1_common_execution_long", "v6_unfiltered_long", "v7_bb_long",
                     "v1_common_ready_long", "v6_common_ready_long")
    both_variants = ("v6_unfiltered_both", "v7_bb_both", "v6_common_ready_both")
    account_columns = [
        ("timeframe_min", "周期(分钟)", "int"), ("variant", "执行臂", "text"),
        ("total_streams", "总流数", "int"), ("streams_with_closed_trades", "有已平仓交易流", "int"),
        ("no_closed_trade_streams", "无已平仓交易流", "int"), ("zero_entry_streams", "零进场流", "int"),
        ("censored_only_streams", "仅删失流", "int"), ("insolvency_or_invalid_streams", "破产/异常流", "int"),
        ("eligible_closed_streams", "中位/P90样本", "int"),
        ("closed_balance_return_median", "余额收益中位", "pct"), ("closed_balance_return_p90", "余额收益P90", "pct"),
        ("closed_balance_dd_median", "余额DD中位", "pct"), ("closed_balance_dd_p90", "余额DD P90", "pct"),
        ("closed_balance_dd_max", "最差单流余额DD", "pct"),
    ]
    details = [
        ("按年度", yearly, [("timeframe_min", "周期", "int"), ("year_block", "年度段", "text"),
                              ("variant", "执行臂", "text"), ("closed", "已平仓", "int"),
                              ("win_rate", "胜率", "pct"), ("event_pf", "PF", "num"), ("mean_net_r", "平均净R", "num"),
                              ("net_ge_10r", "兑现≥10R", "int")]),
        ("按交易所", venue, [("venue", "交易所", "text"), ("timeframe_min", "周期", "int"),
                               ("variant", "执行臂", "text"), ("closed", "已平仓", "int"),
                               ("win_rate", "胜率", "pct"), ("event_pf", "PF", "num"), ("mean_net_r", "平均净R", "num")]),
        ("按方向", side, [("timeframe_min", "周期", "int"), ("side", "方向", "text"),
                            ("variant", "执行臂", "text"), ("closed", "已平仓", "int"),
                            ("win_rate", "胜率", "pct"), ("event_pf", "PF", "num"), ("mean_net_r", "平均净R", "num")]),
    ]
    detail_markdown = "\n\n".join(
        f"### {title}完整拆分\n\n{_markdown_table(table, columns)}"
        for title, table, columns in details
    )
    figure_markdown = "\n\n".join(
        f"![{item['selection']}：{item['venue']} {item['symbol']} {item['timeframe_min']}m]"
        f"({_relative(figures / item['figure'], report.parent)})\n\n"
        f"[{item['figure']}]({_relative(figures / item['figure'], report.parent)})：{item['selection']}；"
        f"{item['venue']} {item['symbol']} {item['timeframe_min']}m，"
        f"{'多' if int(item['side']) == 1 else '空'}，仅供事后审阅，含未来 K 线。"
        for item in figure_rows
    ) or "- 本次产物没有可链接图例。"
    code_id_rows = pd.DataFrame(sorted(raw_manifest.get("source_code_sha256", {}).items()), columns=["source", "sha256"])
    reproduce_root = EXPERIMENT / "results/replay_two_year_20260912_final"
    reproduce_post = EXPERIMENT / "results/post_two_year_20260912_final"
    reproduce_figures = EXPERIMENT / "results/figures_two_year_20260912_final"
    reproduce_delivery = EXPERIMENT / "results/delivery_two_year_20260912_final"
    command = f"""TASK_PYTHON=.venv/bin/python
REPLAY={reproduce_root}
POST={reproduce_post}
FIGURES={reproduce_figures}
DELIVERY={reproduce_delivery}

$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_compare "$REPLAY"
$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_report "$REPLAY" "$POST" --controls
$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_figures "$REPLAY" "$POST" "$FIGURES"
$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_delivery --raw "$REPLAY" --post "$POST" --figures "$FIGURES" \\
  --report {FINAL_REPORT} --delivery-dir "$DELIVERY"{interpretation_option}
$TASK_PYTHON scripts/md_to_html.py --out-dir analysis/html {FINAL_REPORT}"""
    body = f"""# SPIKE V7 BB 背景准入与归档 V1：结果交付

> **状态：{scope}。** 本文只读取已经完成的 replay、post 与 figures 产物；不重跑、不调参、不拉取数据。

{interpretation_text}

## 规则与口径

V7 B 是既定的 V6 原始事件准入背景：先以当前及此前收盘价计算 BB200（中轨为 SMA200，宽度为 `4 × population_std(close, 200) / abs(SMA200)`）；阈值是**前 500 个**宽度的 P10。信号前 12 根都必须已有阈值，且这 12 根中完整出现过连续 3 根压缩；信号根不进入记忆。要求连续历史至少 712 根。没有要求当前带宽扩张，也没有使用 RSI。V7 只过滤新开仓；未经滤除的反向 V6 原始事件仍在下一根开盘平仓。

共同执行模型固定为：信号后下一根开盘进场；**含信号根的最近 5 根**极值、0.2 ATR 缓冲、最小 2 ATR 风险；2R 后启动 4 ATR 跟踪；往返成本 0.2%。V1 原版历史账本是**原有只多头执行**，与 V1 共同执行、V6、V7 不能混称为同一策略。V1 共同执行的空头 V6 信号仅作反向平仓；同根冲突以平仓优先。表中 PF 为已平仓**净事件收益**的正收益和除以负收益绝对值和，不是 R 的盈亏比。

## 覆盖与身份

- 冻结可比流：{config['expected_coverage']['comparable_cells']}；原评估流：{config['expected_coverage']['evaluated_cells']}；当前目录完成：{raw_manifest.get('completed_streams')}。
- 覆盖 Binance / OKX / Gate 的已评估当前目录子集，只含 30m、1H、4H；这不是全部历史上所有币种的池。
- 评估确认窗口：{config['window']['start']} 至 {config['window']['end_exclusive']}（不含末点）；指标预热从 {config['window']['warmup_start']} 开始，仅供历史特征计算。
- 三个 OKX SATS 流（30m/1H/4H）因冻结 catalog tick=0 排除，未用历史 raw tickSz 替代。
- 配置 SHA256：`{config_hash}`；Pine SHA256：`{config['pine_sha256']}`；replay manifest SHA256：`{sha256(raw / 'manifest.json')}`；post manifest SHA256：`{sha256(post / 'post_manifest.json')}`。

### replay 源码身份

{_markdown_table(code_id_rows, [('source', '文件', 'text'), ('sha256', 'SHA256', 'text')])}

## 原版 V1 历史执行（独立参考）

下表来自归档 V1 原版**已覆盖子集**历史账本。{'当前共同执行仅为部分 smoke，故这里的数值不得与下方共同执行收益直接比较。' if allow_partial else '完整共同执行完成后才与下方同池口径并列解读。'}

{_markdown_table(native, [('timeframe_min', '周期(分钟)', 'int'), ('entries', '进场', 'int'), ('closed', '已平仓', 'int'), ('censored', '删失', 'int'), ('win_rate', '胜率', 'pct'), ('event_pf', 'PF', 'num'), ('mean_net_r', '平均净R', 'num'), ('net_ge_10r', '真实兑现≥10R', 'int')])}

## 共同执行：多头臂

{_variant_table(timeframe, long_variants)}

`*_common_ready_*` 仅施加与 V7 相同的 BB 历史就绪门，不要求压缩，用于区分冷启动历史限制与压缩准入本身。

## 共同执行：双向臂

{_variant_table(timeframe, both_variants)}

## 共同执行拆分

方向字段 `1` 表示多头，`-1` 表示空头。以下表格是同一已平仓事件指标的年度、交易所和方向拆分；不把不同流复利成全市场组合。

{detail_markdown}

## 信号、准入与尾部逐笔留存

V7 拒绝原因是先验门，而不是事后收益筛选。`insufficient_bb_history` 表示 712 根连续历史不足；`ready_without_recent_compression` 表示 BB 历史充分但此前 12 根没有完整 3 根压缩。

{_markdown_table(reasons, [('timeframe_min', '周期', 'int'), ('side', '方向', 'text'), ('raw_v6', '原始V6', 'int'), ('insufficient_bb_history', '历史不足', 'int'), ('ready_without_recent_compression', '就绪但无压缩', 'int'), ('v7_admitted', 'V7准入', 'int')])}

### 各臂候选与准入

{_markdown_table(signals, [('variant', '执行臂', 'text'), ('minutes', '周期', 'int'), ('side', '方向', 'text'), ('entry_candidates', '候选', 'int'), ('entry_admitted', '准入', 'int'), ('raw_v6_short_exit_feed', '仅作反向退出的空头原始事件', 'int')])}

逐笔同 entry identity 的真实兑现 ≥10R 留存如下；这是已平仓交易的精确联结，未把 MFE 当兑现。这里的“同 entry 未留存”只表示 V7 没有在该 V6 进场根实际进场，**不等于整段行情漏掉**：V7 仍可能在另一根进场。本轮没有计算行情段级召回率；请同时查看 V7 自身的“真实兑现≥10R”笔数。

{_markdown_table(tails, [('baseline', '基线', 'text'), ('target', 'V7目标', 'text'), ('timeframe_min', '周期', 'int'), ('baseline_closed', '基线已平仓', 'int'), ('baseline_net_ge_10r', '基线兑现≥10R', 'int'), ('same_entry_tails_retained', '同 entry 留存', 'int'), ('same_entry_tails_missed', '同 entry 未留存', 'int')])}

### V1 同根冲突的退出优先

{_markdown_table(conflicts, [('venue', '交易所', 'text'), ('timeframe_min', '周期', 'int'), ('suppressed_v1_long_entries', '被退出优先抑制的V1多头进场', 'int')]) if len(conflicts) else '当前 post 产物没有 `v1_conflict_counts.csv`：这不等于冲突为零，只表示该聚合文件未产出。原版 V1 历史账本始终未改。'}

## 单币独立 1x 已平仓余额与回撤

每个市场/周期/segment 单独按 1x notional 已平仓净收益复利；没有跨币、跨交易所或跨流资金曲线。主表的收益/DD 中位与 P90 **只取有已平仓交易且无破产/无效收益事件的流**，因此同时报告总流数、有已平仓交易流、无已平仓交易流、零进场流、仅删失流和异常流，避免样本分母被静默改变后误读回撤。

{_markdown_table(account_summary, account_columns)}

### 含零交易流的附表

这里先排除破产/无效收益流，再把其余无已平仓交易流的余额收益与 DD 记为 0；表中分母单列，仅用于展示无已平仓交易流对分布的影响，不能与主表混读。

{_markdown_table(account_summary, [('timeframe_min', '周期', 'int'), ('variant', '执行臂', 'text'), ('all_noninvalid_streams', '非异常分母', 'int'), ('all_noninvalid_zero_return_median', '余额收益中位（含零）', 'pct'), ('all_noninvalid_zero_return_p90', '余额收益P90（含零）', 'pct'), ('all_noninvalid_zero_dd_median', 'DD中位（含零）', 'pct'), ('all_noninvalid_zero_dd_p90', 'DD P90（含零）', 'pct')])}

原版 V1 独立账户汇总在 [`historical_v1_native_independent_account_summary.csv`]({_relative(delivery_dir / 'historical_v1_native_independent_account_summary.csv', report.parent)})；它遵循上方原版 V1 的同一范围警告。

## 匹配随机对照

每个已实现样本在读取结果前按 entry identity SHA256 选取，每个流/臂/方向至多 16 笔；匹配同 venue+symbol、周期、日历月、因果前 120 根波动分位与方向，seed=0。共同 ready / V7 臂的随机候选还须满足同一 BB 历史就绪门。配对差是事件层，不是共享资本组合收益；本研究复用既往审阅过的数据，**不是盲测**。少于六个月 block 时不报告 p 值。

{_markdown_table(controls, [('timeframe_min', '周期', 'int'), ('variant', '执行臂', 'text'), ('sampled_targets', '预定样本', 'int'), ('matched_targets', '成功配对', 'int'), ('matched_months', '月block', 'int'), ('paired_mean_net_r_difference', '配对平均净R差', 'num'), ('equal_month_mean_net_r_difference', '等权月平均R差', 'num'), ('exploratory_month_block_sign_flip_p', '探索性sign-flip p', 'p4')])}

AUC 不适用：这里没有分类器概率或排序模型，只有规则事件与成对随机入场对照。

## 图例与逐笔账本

{figure_markdown}

- [共同执行逐笔账本 CSV]({_relative(post / 'common_execution_trades.csv.gz', report.parent)})
- [匹配随机样本 CSV]({_relative(post / 'sampled_matched_controls.csv.gz', report.parent)})
- [信号/准入统计 CSV]({_relative(post / 'signal_counts.csv', report.parent)})
- [独立账户汇总 CSV]({_relative(delivery_dir / 'independent_account_summary.csv', report.parent)})

## 风险与诚实声明

- V7 B 是固定规则，未在本次输出后重新调参；此前已查看的重叠数据不构成新的盲测。
- 只读取指定已覆盖子集；没有补数据、填缺口、变更目录或以当前赢家筛选资产。
- BB 门需要完整前史 712 根；成本固定为往返 0.2%，未建模 funding、冲击、滑点差异或交易容量。
- 未平仓交易标为删失，不参与已平仓胜率、PF、净R或独立余额；不能以 MFE 替代真实兑现。
- PF 与事件净R是单笔描述；独立余额/DD 也只在单流内计算。本文没有全市场组合收益。
- 历程包含失败尝试：`f02fbf0` 的预检因 SATS tick=0 未产生新交易输出；`aaa2767`/v2 在首流产生部分交易后于汇总失败；`d88205a`/v3 先完成 smoke 再续跑全池。v3 只复用 v2 已校验的输入清单，未复用其交易产物；因此不能称这批数据首次被查看或为盲测。
- {'本 smoke 草稿只含一个流，任何好坏数字都不能推及 3,531 流或用于选择“最佳”V7。' if allow_partial else '完整覆盖也只能评价这个预注册的历史配置，不能自动 promote、训练或进入生产。'}

## 复现

```bash
{command}
```

输入目录：[`raw`]({raw_link})、[`post`]({post_link})、[`figures`]({figure_link})。报告 builder SHA256 在 delivery manifest 中记录；所有表格数字直接读取上述产物。
"""
    report.write_text(body, encoding="utf-8")
    manifest = {"delivery_code_sha256": sha256(Path(__file__)), "config_sha256": config_hash,
                "raw_manifest_sha256": sha256(raw / "manifest.json"), "post_manifest_sha256": sha256(post / "post_manifest.json"),
                "figures_manifest_sha256": sha256(figures_manifest_path), "report": str(report.resolve()),
                "report_sha256": sha256(report), "scope": scope, "allow_partial": allow_partial,
                "inputs": {"raw": str(raw.resolve()), "post": str(post.resolve()), "figures": str(figures.resolve())}}
    if interpretation:
        manifest["interpretation"] = {"path": str(interpretation.resolve()), "sha256": sha256(interpretation)}
    (delivery_dir / "delivery_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True, help="completed replay result directory")
    parser.add_argument("--post", type=Path, required=True, help="postprocess result directory")
    parser.add_argument("--figures", type=Path, required=True, help="figure result directory")
    parser.add_argument("--report", type=Path, default=FINAL_REPORT, help="Markdown report path")
    parser.add_argument("--delivery-dir", type=Path, required=True, help="builder receipt and derived account tables")
    parser.add_argument("--allow-partial", action="store_true", help="create a labelled smoke draft; never a final report")
    parser.add_argument("--interpretation", type=Path, help="reviewed, source-bound narrative included with its hash")
    args = parser.parse_args()
    result = build(args.raw, args.post, args.figures, args.report, args.delivery_dir,
                   allow_partial=args.allow_partial, interpretation=args.interpretation)
    print(result)


if __name__ == "__main__":
    main()
