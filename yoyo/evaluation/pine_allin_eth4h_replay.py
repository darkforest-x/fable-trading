"""Reproduce the frozen owner ETH 4h request and publish auditable ledgers.

No network or live exchange writes. Read only the checksum-identified Binance
2020-2026-04 archive. Source math and event clock are documented in the replay
module; comparisons are preregistered in PROJECT_PLAN.md before execution.
Controls use prior-252-bar ATR% bins, UTC month and HK six-hour block; their
future path is an outcome, never used in selection. No realized-horizon match.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.permutation import permutation_test
from yoyo.layers.l3_backtest.pine_allin_eth4h import POLICIES, Replay, aggregate_4h, features
from yoyo.layers.l3_backtest.pine_allin_v7 import SignalParameters, auc_from_scores, profit_factor

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-pine-allin-eth4h-20260907-v1"
OUT = EXP / "results"
SOURCE = ROOT / "data/kline_preholdout_binance_um15m/series/binance_um_ETHUSDT_15m_221952.csv"
EXPECTED_SHA = "07013f996a13a0b205f9df6df2af5deab5d601e7e34b42bfc308dc13aef400bd"
END = pd.Timestamp("2026-05-01T00:00Z")
HOLDOUT = pd.Timestamp("2026-05-04T00:00Z")
NAMES = {"original_zero_cost": "原版·零成本", "original_cost20": "原版·计成本",
         "entry_stop_cost20": "仅提前首根止损·计成本", "one_x_cost20": "仅改固定1倍·计成本",
         "sma_only_cost20": "仅改均线交叉·计成本"}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(x) for x in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def save_json(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2) + "\n")


def load_source():
    # Hash comparison is prior to parsing the known pre-holdout-only file.
    actual = digest(SOURCE)
    if actual != EXPECTED_SHA:
        raise ValueError("Source identity changed; refuse to parse an unknown dataset")
    df = pd.read_csv(SOURCE)
    df["open_time"] = pd.to_datetime(df.open_time, utc=True)
    assert df.open_time.min() == pd.Timestamp("2020-01-01T00:00Z")
    assert (df.open_time + pd.Timedelta(minutes=15) <= END).all() and END < HOLDOUT
    four = aggregate_4h(df)
    assert len(df) == 221952 and len(four) * 16 == len(df)
    receipt = {"path": str(SOURCE.relative_to(ROOT)), "sha256": actual,
               "venue": "Binance USD-M ETHUSDT perpetual", "source_rows": len(df),
               "four_hour_rows": len(four), "first": four.open_time.min(),
               "end_exclusive": END, "gaps": 0, "all_groups_have_16_bars": True,
               "holdout_consumed": False}
    return features(four), receipt


def controls(frame, runner, trades, start, end):
    months = frame.open_time.dt.strftime("%Y-%m").to_numpy()
    blocks = (frame.hk_hour // 6).to_numpy()
    bins = frame.vol_bin.to_numpy()
    pool = {}
    for i in range(start, end - 1):
        if runner.allowed[i] and not runner.raw[i] and bins[i] >= 0:
            pool.setdefault((months[i], blocks[i], bins[i]), []).append(i)
    records, matches = [], []
    for trade_id, trade in trades.iterrows():
        i = int(trade.signal_i)
        eligible = [j for j in pool.get((months[i], blocks[i], bins[i]), []) if abs(i - j) > 12]
        # Selection key excludes the policy so single-variable arms are paired.
        key = f"allin-eth4h-v1|{i}|{int(trade.direction)}"
        choices = sorted(eligible, key=lambda j: hashlib.sha256(f"{key}|{j}".encode()).digest())[:3]
        values = []
        for j in choices:
            result = runner.run(j, end, injected=(j, int(trade.direction)))
            closed = not result["trades"].empty
            row = {"trade_id": int(trade_id), "case_signal_i": i, "control_signal_i": j,
                   "month": months[i], "hk_6h_block": int(blocks[i]), "vol_bin": int(bins[i]),
                   "direction": int(trade.direction), "closed": closed}
            if closed:
                control = result["trades"].iloc[0]
                row.update({"control_gross_return": control.gross_return,
                            "control_net_return": control.net_return,
                            "control_entry_i": int(control.entry_i), "control_exit_i": int(control.exit_i),
                            "control_entry_price": control.entry_price, "control_exit_price": control.exit_price,
                            "control_exit_reason": control.exit_reason})
                values.append(control.net_return)
            records.append(row)
        matched = len(choices) > 0 and len(values) == len(choices)
        matches.append({"trade_id": int(trade_id), "n_controls": len(choices), "matched": matched,
                        "control_mean": np.mean(values) if matched else np.nan})
    match = pd.DataFrame(matches).set_index("trade_id") if matches else pd.DataFrame()
    enriched = trades.join(match) if len(match) else trades.assign(n_controls=0, matched=False, control_mean=np.nan)
    enriched["paired_excess"] = enriched.net_return - enriched.control_mean
    return enriched, pd.DataFrame(records)


def trade_stats(t):
    if t.empty:
        return {"trades": 0}
    matched = t[t.matched]
    wins = t.net_pnl > 0
    return {"trades": len(t), "win_rate_pct": 100 * wins.mean(),
            "gross_bp": t.gross_return.mean() * 1e4, "net_bp": t.net_return.mean() * 1e4,
            "pf_currency": profit_factor(t.net_pnl), "pf_unit": profit_factor(t.net_return),
            "matched_trades": len(matched), "coverage_pct": len(matched) / len(t) * 100,
            "matched_case_bp": matched.net_return.mean() * 1e4,
            "random_control_bp": matched.control_mean.mean() * 1e4,
            "excess_bp": matched.paired_excess.mean() * 1e4,
            "avg_hold_hours": t.holding_bars.mean() * 4,
            "max_gross_trade_pct": t.gross_return.max() * 100,
            "min_gross_trade_pct": t.gross_return.min() * 100,
            "largest_5_share_of_positive_pnl_pct": t.net_pnl.nlargest(5).clip(lower=0).sum() / t.loc[wins, "net_pnl"].sum() * 100}


def inference(t):
    if len(t) < 2:
        return {"n": len(t)}
    scores = t.score.to_numpy()
    gross, net = t.gross_return.to_numpy(), t.net_return.to_numpy()
    rank = np.argsort(scores)[::-1][:max(1, math.ceil(.1 * len(t)))]
    perm = permutation_test(scores, net, n_permutations=10000, seed=20260907)
    matched = t[t.matched].copy()
    grouped = matched.groupby(matched.signal_time.dt.strftime("%Y-%m")).paired_excess.sum().to_numpy()
    rng = np.random.default_rng(20260907)
    obs = float(matched.paired_excess.mean()) if len(matched) else np.nan
    # Explicit summation avoids spurious Accelerate matmul floating warnings
    # observed on this Mac; independent recomputation agreed within 1.4e-17.
    signs = rng.choice([-1, 1], (10000, len(grouped)))
    null = (signs * grouped[None, :]).sum(axis=1) / max(1, len(matched))
    pair_p = (1 + (null >= obs).sum()) / 10001 if len(matched) else np.nan
    return {"n": len(t), "auc_abs_osc_vs_net_positive": auc_from_scores(scores, net > 0),
            "top_decile_n": len(rank), "top_decile_gross_bp": gross[rank].mean() * 1e4,
            "top_decile_net_bp": net[rank].mean() * 1e4,
            "top_decile_control_bp": t.iloc[rank].control_mean.mean() * 1e4,
            "top_decile_paired_excess_bp": t.iloc[rank].paired_excess.mean() * 1e4,
            "ranking_permutation_p": perm.p_value,
            "matched_month_cluster_signflip_p": pair_p, "matched_months": len(grouped),
            "permutations": 10000,
            "interpretation": "diagnostic only; reused history, non-independent controls, not held-out acceptance"}


def table(rows, columns):
    def fmt(x):
        if x is None or (isinstance(x, (float, np.floating)) and not np.isfinite(x)):
            return "不适用/不可估"
        return f"{x:.2f}" if isinstance(x, (float, np.floating)) else str(x)
    lines = ["| " + " | ".join(label for _, label in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    def cell(row, key):
        value = row.get(key)
        if key.endswith("_p") and value is not None and np.isfinite(value):
            return f"{value:.4f}"
        return fmt(value)
    lines.extend("| " + " | ".join(cell(row, key) for key, _ in columns) + " |" for row in rows)
    return "\n".join(lines)


def report(payload, all_results):
    main = [r for r in payload["summary"] if r["window"] == "continuous_2021_2026apr"]
    recent = [r for r in payload["summary"] if r["window"] == "reset_2025_2026apr"]
    cols = [("name", "版本"), ("return_pct", "权益收益%"), ("max_drawdown_path_pct", "路径回撤%"),
            ("trades", "平仓笔数"), ("win_rate_pct", "净胜率%"), ("pf_currency", "资金PF"),
            ("net_bp", "每笔净bp"), ("matched_case_bp", "匹配病例净bp"),
            ("random_control_bp", "随机净bp"), ("excess_bp", "配对超额bp")]
    yearcols = [("year", "年份"), ("return_pct", "年内权益收益%"), ("trades", "当年入场已平仓数"),
                ("net_bp", "每笔净bp"), ("random_control_bp", "随机净bp"), ("excess_bp", "配对超额bp")]
    rankcols = [("window", "窗口"), ("auc_abs_osc_vs_net_positive", "AUC"), ("top_decile_n", "前10%笔数"),
                ("top_decile_gross_bp", "前10%毛bp"), ("top_decile_net_bp", "前10%净bp"),
                ("top_decile_control_bp", "前10%随机净bp"), ("top_decile_paired_excess_bp", "前10%配对超额bp"),
                ("ranking_permutation_p", "排序p"), ("matched_month_cluster_signflip_p", "月簇配对p")]
    primary = next(x for x in main if x["arm"] == "original_cost20")
    zero = next(x for x in main if x["arm"] == "original_zero_cost")
    fixed = next(x for x in main if x["arm"] == "entry_stop_cost20")
    one = next(x for x in main if x["arm"] == "one_x_cost20")
    verification_text = "独立复核尚未执行。"
    if "independent_validation" in payload:
        qa = payload["independent_validation"]
        first = qa["firstbar"]["continuous_2021_2026apr"]
        verification_text = (
            f"独立复核 {sum(qa['checks'].values())}/{len(qa['checks'])} 项通过，包含两边手续费、"
            f"总账权益、信号时仓位、下一根开盘价格及精确对照分层。原版计成本有 "
            f"{first['unprotected_firstbar_stop_touches']} 笔交易在未挂止损的入场首根已经触及原定止损，"
            f"其中 {first['later_winners']} 笔后来盈利。首轮矩阵乘法出现浮点警告，"
            "改用逐项求和核对后 p 值不变；原始交易、账户和费用结果均未重跑。"
        )
    text = f"""# ALLIN V7：ETHUSDT 永续 4H 原码回放

生成时间：{payload['generated_at']}。这是独立 Python 历史回放，尚未与 TradingView 原生交易清单逐笔对齐。

## 主要结果

币安 ETHUSDT 永续，2021-01-01 至 2026-04-30，初始 500 USDT。
原版零成本权益收益 {zero['return_pct']:.2f}%；按每边 0.1% 名义手续费计算后 {primary['return_pct']:.2f}%，
原版计成本资金曲线路径回撤 {primary['max_drawdown_path_pct']:.2f}%，已平仓 {primary['trades']} 笔。
仅让初始止损从成交首根生效，权益收益变为 {fixed['return_pct']:.2f}%；仅改固定 1 倍则为 {one['return_pct']:.2f}%。
这些是预先冻结的单变量检查，没有调参择优。
原版计成本的净盈利交易只有 {primary['win_rate_pct']:.2f}%；最大 5 笔盈利占全部正盈利金额 {primary['largest_5_share_of_positive_pnl_pct']:.2f}%。
2023 年权益亏损约 80.17%，所以累计盈利不能解释成各年稳定。2026 年只含 1–4 月。

## 数据与复现范围

- 数据来自 Binance 官方 USD-M 月度公开档案，本机只读，SHA256 {payload['data']['sha256']}。
- 原始 15m {payload['data']['source_rows']} 根，聚合 4h {payload['data']['four_hour_rows']} 根；每组恰好 16 根、时间连续、无缺口，普通 OHLC。
- 2020 年只作指标预热；连续资金账户从 2021 年开始。另从 2025 年空仓、500 USDT、冷却清零重新启动，检验起点敏感性。
- 2026-05-01 起没有进入数据；仓库 holdout 从 2026-05-04 起，本配置消耗次数为 0。没有训练、特征选择或部署。
- 连续期原始 V7 候选 {payload['candidate_counts']['v7_raw']}，日历和波动过滤后 {payload['candidate_counts']['v7_allowed']}；单纯交叉 {payload['candidate_counts']['cross_raw']}。
- 正类定义为已平仓净收益 >0；正类率即表中净胜率。AUC 用 abs(osc) 作诊断分数，没有训练出的概率。
- 2025 年以后是事先划定的后段诊断，不宣称此前没有被人看过，也不算独立最终验证。

## 连续资金结果及同币随机对照

{table(main, cols)}

权益包含末端未平仓浮盈亏；胜率、PF、每笔均值只含已平仓交易。资金 PF 按实际美元盈亏计算；与不加仓权重的每笔 PF 可不同。
路径回撤依照 4h OHLC 的开盘接近极值顺序记账，是模型内回撤，不是逐笔市场实测或 TradingView 同定义复刻。
bp 为万分之一。随机对照每个事件独立入场，不能把这些重叠事件的均值当成可交易组合收益。

## 2025 年重新启动账户

{table(recent, cols)}

## 原版计成本的逐年表现

{table(payload['yearly'], yearcols)}

年度权益包含跨年持仓的盯市变化；交易统计按入场年份归属且可跨年退出，因此两列不强求会计相等。

## 原版计成本的多空分解

{table(payload['directions'], [('direction','方向'),('trades','笔数'),('win_rate_pct','净胜率%'),('net_bp','每笔净bp'),('matched_case_bp','匹配病例净bp'),('random_control_bp','随机净bp'),('excess_bp','配对超额bp')])}

## 排序与对照检验

{table(payload['ranking'], rankcols)}

上表仅针对原版计成本。原策略交易全部合格交叉，没有前 10% 选单规则；不能用该诊断倒推重新筛选交易。
详细 p 值保留在 summary.json，表中 p 值保留四位小数，其他指标保留两位。全程匹配病例 67/69，后段 12/12；各版本覆盖率另见 summary.csv。
匹配同币、UTC 月份、香港 6 小时时间块、因果 ATR% 五分位、同方向，排除距病例信号 12 根以内；候选按固定哈希顺序选最多 3 个。
对照用自己的入场价和 ATR、相同止损/保本/原始信号退出规则和成本，独立空仓状态启动；没有复制病例实现后的持有期限。
冷却路径依赖于各自账户历史，独立事件对照不复制主账户先前盈利造成的计数；这是解释边界。
缺匹配和未闭合事件保留在 ledgers/summary，不把它们当作零收益。配对 p 使用月度簇符号翻转，10,000 次，未修正全部探索比较的多重检验。

## 执行语义核对

{verification_text}

- 历史信号在收盘计算、下一根开盘成交；初始止损价格锚定信号 close，并按原码延后提交。修正分支只改变初始止损生效时点。
- 保本门槛严格 >1.5%，锁定价格变化 0.1%；收盘更新只能影响后续价格，不能在同根历史 K 线上追溯成交。
- 保留原码先求 is_cooling_down 再更新计数的顺序；同向信号虽不加仓仍重置共享 sl_price；反向信号先修改共享止损再翻仓。
- 日历禁入和周日用 UTC；周四仓位倍增用 UTC+8。UTC 对齐 4h 开盘小时不落在 3 点或 21–22 点，所以凌晨加成与小时禁入没有触发。
- 原版平时约 4 倍、香港周四约 8 倍；每次按信号收盘权益和价格计算数量。13 倍只是上限，本次时间格不会达到。
- 相反方向 strategy.entry、旧仓 strategy.exit、strategy.close 在同一时刻的队列，采用合并反向成交模型；未验证原生交易清单的一致性。
- 无限保证金假设沿用 Pine v5 原码未指定保证金的口径；若路径权益 <=0 会显式标记，不能把这样的收益解释为可执行。

## 风险与诚实声明

- 没有运行 TradingView Pine 编译器；这份结果是代码语义的 Python 复现，不是原生策略测试器导出。截图使用的交易所、回测窗口和属性仍可能不同。
- 费用为每边成交额的 0.1%，名义往返 0.2%；没有另外添加滑点或永续资金费，也没有模拟交易所强平、最低数量、数量步长、价格取整。
- calc_on_every_tick 只在实时执行能看到盘中信号，此次复现历史收盘行为；不等同于实时警报绩效。
- 原码 strategy() 的 alert_message 属于声明语法疑点；实际 TV 使用版本仍需核对。可视化和警报文字没有参与收益计算。
- 旧 V7 的 15m、多币种、修正执行结果不是本次 4h 原码的可比上一版本，所以没有拼接成同口径历史基线；本次五行在同数据同窗口对照。
- 交易可能集中在少数大行情。单一品种、重复使用历史、随机对照重叠及未做原生对齐，均限制盈利证据强度。

## 资金曲线

![资金和回撤](../experiments/active/exp-pine-allin-eth4h-20260907-v1/results/equity.png)

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/test_pine_allin_eth4h.py -q
.venv/bin/python -m yoyo.evaluation.pine_allin_eth4h_replay
.venv/bin/python -m yoyo.evaluation.pine_allin_eth4h_verify
.venv/bin/python scripts/md_to_html.py analysis/p0_pine_allin_eth4h_20260907.md --out-dir analysis/html
```

先具备源 CSV 并核对固定 SHA256；来源文档与逐月下载校验收据在 data/kline_preholdout_binance_um15m/audits/ETHUSDT.json。
结果目录保留各窗口/版本交易、随机对照、权益曲线、状态事件、源码和数据哈希及验证收据。重复执行禁止覆盖现有结果，应先另行归档并注册新运行。

## 下一步

先核对 TradingView 同交易所、同日期、同佣金和计算设置的逐笔交易。确认原生一致性后再讨论是否继续；新的调参或 holdout 检验需要单独决策。本次不改原策略，不推进实盘。

来源：[TradingView 策略执行规则](https://www.tradingview.com/pine-script-docs/v5/concepts/strategies/)、[Pine 时间语义](https://www.tradingview.com/pine-script-docs/v5/concepts/time/)、[Binance 官方公开数据](https://github.com/binance/binance-public-data)。
"""
    md = ROOT / "analysis/p0_pine_allin_eth4h_20260907.md"
    md.write_text(text)
    # Required immediately after the Markdown report is completed.
    subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/md_to_html.py"), str(md),
                    "--out-dir", str(ROOT / "analysis/html")], check=True)


def main():
    if (OUT / "summary.json").exists():
        raise RuntimeError("Results already exist; no silent overwrite")
    OUT.mkdir(parents=True, exist_ok=True)
    config = json.loads((EXP / "config.json").read_text())
    if config["policies"] != [asdict(p) for p in POLICIES] or config["parameters"] != asdict(SignalParameters()):
        raise ValueError("Frozen JSON configuration and executed policies disagree")
    code = [Path(__file__), ROOT / "yoyo/layers/l3_backtest/pine_allin_eth4h.py",
            ROOT / "yoyo/layers/l3_backtest/pine_allin_v7.py", EXP / "source.pine", EXP / "config.json"]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for path in code:
        # Require that the exact bytes are already tracked in HEAD before replay.
        frozen = subprocess.check_output(["git", "show", f"HEAD:{path.relative_to(ROOT)}"], cwd=ROOT)
        if hashlib.sha256(frozen).hexdigest() != digest(path):
            raise RuntimeError(f"uncommitted builder/input: {path}")
    frame, receipt = load_source()
    payload = {"generated_at": datetime.now(timezone.utc), "builder_commit": commit, "data": receipt,
               "code_sha256": {str(p.relative_to(ROOT)): digest(p) for p in code},
               "holdout_consumed": False, "training_eligible": False, "production_eligible": False,
               "summary": [], "yearly": [], "directions": [], "ranking": []}
    save_json(OUT / "pre_run_receipt.json", payload)
    mask = frame.open_time.ge(pd.Timestamp("2021-01-01T00:00Z"))
    raw = frame.v7_long | frame.v7_short
    payload["candidate_counts"] = {"v7_raw": int((raw & mask).sum()),
                                   "v7_allowed": int((raw & mask & frame.entry_allowed).sum()),
                                   "cross_raw": int(((frame.cross_long | frame.cross_short) & mask).sum())}
    results = {}
    checks = {"source_hash": True, "source_cadence_15m": True, "aggregation_complete": True,
              "all_data_before_holdout": bool(frame.open_time.max() + pd.Timedelta(hours=4) <= END < HOLDOUT)}
    for window, date in (("continuous_2021_2026apr", "2021-01-01"), ("reset_2025_2026apr", "2025-01-01")):
        start = int(frame.open_time.searchsorted(pd.Timestamp(date, tz="UTC")))
        for policy in POLICIES:
            print(f"Replay {window} {policy.name}", flush=True)
            runner = Replay(frame, policy)
            out = runner.run(start, len(frame))
            trades, ctrl = controls(frame, runner, out["trades"], start, len(frame))
            stem = f"{window}_{policy.name}"
            trades.to_csv(OUT / f"{stem}_trades.csv", index=False)
            ctrl.to_csv(OUT / f"{stem}_controls.csv", index=False)
            out["equity"].to_csv(OUT / f"{stem}_equity.csv", index=False)
            out["events"].to_csv(OUT / f"{stem}_events.csv", index=False)
            summary = {"window": window, "arm": policy.name, "name": NAMES[policy.name],
                       **trade_stats(trades), "return_pct": (out["final_equity"] / 500 - 1) * 100,
                       "final_equity": out["final_equity"], "max_drawdown_path_pct": out["max_drawdown_path"] * 100,
                       "max_drawdown_close_pct": out["max_drawdown_close"] * 100,
                       "fees": out["fees"], "open_position": out["open_position"],
                       "nonpositive_equity_seen": out["nonpositive_equity_seen"],
                       "exposure_fraction": out["exposure_fraction"],
                       "control_censored_count": int((~ctrl.closed).sum()) if len(ctrl) else 0,
                       "events": out["events"].event.value_counts().to_dict()}
            payload["summary"].append(summary)
            results[(window, policy.name)] = (out, trades)
            if len(trades):
                gross = trades.direction * (trades.exit_price / trades.entry_price - 1)
                checks[stem + "_ledger"] = bool(np.allclose(gross, trades.gross_return) and
                    np.allclose(trades.net_pnl, trades.gross_pnl - trades.fees) and
                    (trades.entry_i == trades.signal_i + 1).all() and
                    (trades.exit_i >= trades.entry_i).all() and
                    (trades.exit_time < END).all())
                if len(ctrl):
                    checks[stem + "_controls"] = bool((abs(ctrl.control_signal_i - ctrl.case_signal_i) > 12).all())
            if policy.name == "original_cost20":
                payload["ranking"].append({"window": window, **inference(trades)})
                if window.startswith("continuous"):
                    eq = out["equity"].copy()
                    eq["year"] = (eq.time - pd.Timedelta(nanoseconds=1)).dt.year
                    previous = 500.
                    for year, group in eq.groupby("year"):
                        value = float(group.equity.iloc[-1])
                        subset = trades[trades.entry_time.dt.year == year]
                        payload["yearly"].append({"year": int(year), "return_pct": (value / previous - 1) * 100,
                                                  **trade_stats(subset)})
                        previous = value
                    for side, subset in trades.groupby("direction"):
                        payload["directions"].append({"direction": "多" if side > 0 else "空", **trade_stats(subset)})
            print(json.dumps(clean({k:summary[k] for k in ("arm", "return_pct", "trades", "max_drawdown_path_pct", "net_bp")})), flush=True)
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    colors = ["#3264ba", "#cf493c", "#23947d", "#a073bc"]
    for policy, color in zip(POLICIES[:4], colors):
        eq = results[("continuous_2021_2026apr", policy.name)][0]["equity"]
        axes[0].plot(eq.time, eq.equity, label=policy.name, color=color, lw=1.1)
        axes[1].plot(eq.time, 100 * (eq.equity / eq.equity.cummax().clip(lower=500) - 1), color=color, lw=.8)
    axes[0].set_ylabel("Equity (USDT)")
    if all((r[0]["equity"].equity > 0).all() for r in results.values()):
        axes[0].set_yscale("log")
    axes[0].legend(fontsize=8)
    axes[0].set_title("ETHUSDT perpetual 4h | independent Python replay | 2021-Apr 2026")
    axes[1].set_ylabel("Close-mark drawdown (%)")
    for ax in axes:
        ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(OUT / "equity.png", dpi=150)
    plt.close(fig)
    payload["validation"] = {"passed": all(checks.values()), "checks": checks,
                             "native_tradingview_parity": "not_performed", "live_execution": "not_tested"}
    save_json(OUT / "summary.json", payload)
    save_json(OUT / "validation.json", payload["validation"])
    pd.DataFrame(payload["summary"]).drop(columns=["open_position", "events"]).to_csv(OUT / "summary.csv", index=False)
    report(payload, results)
    print("Completed", flush=True)


if __name__ == "__main__":
    main()
