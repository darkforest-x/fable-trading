# 突破＋SPIKE RSI7：纠正为从每笔实际开仓开始计数

日期：2026-09-20，Asia/Shanghai。范围：本机前端模拟联合持仓。源码先提交 `1245ff2776f91f3edebd5637dcaeee4c742ff33b`，再重启既有服务回算。

## 结论与错误归因

Owner指出“xzt 不对啊，从开仓开始算，第七个止盈正好在最高点”。上一版把“连续同色、异色重置”擅自解释为指标全局计数，因而把XTZ入场前的一个大菱形计入，在本仓第6个就提前退出。这是助手对计数起点的理解与实现错误；“开仓不重置”不是Owner确认过的要求。

旧报告 `analysis/p1_spike_joint_rsi_exit_release_20260920.md` 保留为错误版本的历史记录。其中87.57R和“两笔RSI提前退出”只描述V1，不能用于评价Owner指定的入场起点规则。本次新报告纠正其语义归因和结果，未改写历史文件。

已按实际开仓重新计数并上线本机模拟前端。XTZ第7个于北京时间9月19日23:45确认，次根开盘0.3671全平，净52.81781818R；原价格保护为39.36327273R。它在冲高后的高位，但不是最高价成交：最高0.3968出现在23:00开盘的15m bar，不能用该影线价充当退出价。

## 最终规则

- RSI/SAR保留原历史预热；每笔实际入场只重置自己的大菱形计数，初值0。
- 信号bar和入场前不计；实际入场bar收盘开始参与。同周期、仅大菱形，同色累加，异色重置为1；小菱形/圆点/无事件bar不变。
- 当前联合只做多，空头连续恰好第7个收盘确认后，下一根开盘全平；没有盈亏条件，第8个不补触发。下一笔串行持仓重新从0计数。
- 原初始止损、4ATR跟随保护与V9反向保留，先发生先退出。风险参数与0.2%往返成本未改。
- 原全局计数helper保留作审计；执行与前端元数据使用 `counter_start=position_entry`，basis为 `v11_2_box_joint_rsi7_since_entry_same_tf_streak_next_open_v2_net_cost`。未知/缺口不捏造已知计数或成交。

## XTZ逐事件核对

XTZ-USDT-SWAP，15m，2026-09-18 07:00北京实际入场0.2504，初始风险0.0022。下表为大菱形所在bar的**收盘确认时间**；例如23:45确认来自23:30开盘的bar。

| 入场后序号 | 北京收盘确认时间 | 收盘价 |
|---|---|---:|
| 1 | 09-18 09:30 | 0.2531 |
| 2 | 09-18 12:30 | 0.2579 |
| 3 | 09-18 19:00 | 0.2686 |
| 4 | 09-18 23:15 | 0.2697 |
| 5 | 09-19 09:15 | 0.2937 |
| 6 | 09-19 11:30 | 0.3158 |
| 7 | 09-19 23:45 | 0.3670 |

这7次均为空头大菱形，之间无异色大菱形。第7次后的开盘0.3671，模拟净R为 `(0.3671 - 0.2504 - 0.002 * 0.2504) / 0.0022 = 52.81781818`。

## 运行与版本保全

20:24:45启动首次V2扫描，482合约、2410格、136.4秒、0错误；更新全部83条联合performance，没有新增事件。健康接口 `service_alive/market_ready/model_ready/ok=true`。

原价格退出83条归档（19:52:49）逐条保持原payload和保存时间；切换前全局V1的83条performance另冻结为 `previous`（20:24:45），与本轮切换前SQLite备份逐条相等。事件ID和首次发现时间83/83不变。重启不会重写快照。

前端提供“开仓后RSI第7个 · 当前回算”“原价格退出 · 初始快照”“旧全局计数 · 修正前快照”。冻结视图固定时钟、非新鲜；切换加载时清除旧结果。真实浏览器核对XTZ三个版本，最终停在当前版本／XTZ筛选。

| 描述性核对 | 原价格退出快照 | 错误全局V1快照 | 入场起点V2当前回算 |
|---|---:|---:|---:|
| 联合记录 | 83 | 83 | 83 |
| 闭合／活跃／未知 | 68／15／0 | 68／15／0 | 68／15／0 |
| 已闭合净R合计 | 97.3057120893 | 87.5693484530 | 110.7602575439 |
| XTZ 15m净退出R | 39.3632727273 | 29.5905454545 | 52.8178181818 |
| LIGHT 30m净退出R | 0.2665818182 | 0.3029454545 | 0.2665818182 |

修正后相对原价格退出，只有XTZ这笔闭合结果改变，增加13.45454545R；相对错误V1，XTZ增加23.22727273R，LIGHT减少0.03636364R。LIGHT入场后只有3个空头大菱形，仍由V9反向退出。其余闭合原因和净R一致。当前浮动7.0209597759R、原快照浮动6.4715625287R时间截点不同，不拿它们的差值归因规则。

## 复现与验证

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/evaluation/test_spike_joint_rsi_exit.py tests/monitor/test_spike_lines_rsi.py tests/evaluation/test_parabolic_rsi_sar.py tests/evaluation/test_spike_v10_4_increment.py tests/monitor/test_spike_lines.py tests/monitor/test_spike_lines_versions.py tests/boundaries/test_layer_imports.py
node --test tests/monitor/frontend_lines.test.cjs tests/monitor/frontend_cards.test.cjs
```

结果128项Python、22项前端通过。覆盖入场前事件不参与、入场bar参与、恰好第7而非8、异色重置、串行再次入场从0、亏损同样退出、止损优先、元数据前缀因果性、三版本隔离与重启保全。仅已有urllib3/LibreSSL环境警告。

以下复用上一交付冻结的每币1500根OHLC，重跑同一退出引擎；不访问交易所、不写运行账本：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.monitor import spike_lines as lines
from yoyo.evaluation import spike_v10_4_study as study
p = Path('output/qa/joint_rsi_entry_count_20260920')
source = Path('output/qa/joint_rsi_exit_20260920/release_20260920T115620Z')
events = json.loads((p / 'cutover_20260920T122443Z/events.json').read_text())
for symbol, tf, minutes, price, reason in [
    ('XTZ-USDT-SWAP', '15m', 15, .3671, 'rsi_seventh_reverse_next_open'),
    ('LIGHT-USDT-SWAP', '30m', 30, .1687, 'opposite_v6_next_open'),
]:
    event = next(e for e in events if e['symbol'] == symbol and e['timeframe'] == tf)
    bars = pd.read_csv(source / f'{symbol}_{tf}_ohlcv.csv', index_col=0, parse_dates=True)
    facts = study.v9_facts(bars, minutes, event['base_asset'], event['tick'])
    frame = facts['frame']
    i = frame.index.get_loc(pd.Timestamp(event['bar_open_ms'], unit='ms', tz='UTC'))
    fired = np.zeros(len(frame), dtype=bool); fired[i] = True
    got = lines.positions(frame, facts, fired, minutes=minutes, tick=event['tick'], rsi_exit_enabled=True)[i]
    assert got['exit_price'] == price and got['exit_reason'] == reason
    assert got['rsi_counter_start_bar_open_ms'] == got['entry_time_ms']
    print(symbol, got['exit_r'], got['rsi_run_count'])
PY
```

本次是错误修复与功能验收，没有训练/选参样本：val AUC、置换p、top-decile收益、正类率、单特征基线和交易随机对照不适用。等效零假设控制为无RSI触发时与冻结原退出引擎一致，以及入场前添加事件不得改变入场后计数；不把这83条历史投影当收益验证样本。

证据目录 `output/qa/joint_rsi_entry_count_20260920/`：切换前SQLite备份、完整三版本接口、健康/扫描状态、逐事件CSV、两案冻结重放、数据保全收据、浏览器AX/截图及SHA清单。知识库新版本 `https://app.notion.com/p/3e18856479af81189c6dcfce78b327a0` 关联旧V1，保留研究中/想法/非实盘。

## 风险与诚实声明

- 不保证第7个恰好是价格最高点，也不能把同一bar的最高价用作收盘确认后的成交价。
- 这次结果只修复当前前端模拟账本，不证明规则最优或样本外盈利；此前错误推断和失败结果保留。
- 1500根尾窗、完整收盘可用性和缺口删失沿用现有实现；没有建立无限历史的持久化指标状态。
- 保全快照用于审计，因行情截点不同不能作为持续同步收益对照。未改Pine/TradingView脚本、真实账户、ACTIVE、风险或成本参数。

## 下一步

当前功能已完成。若后续评价回吐控制效果，需相同数据截止和成本下的完整串行对照；本报告不替代收益研究。学习记录：`docs/learnings/streak-continuity-does-not-define-its-counting-origin.md`。
