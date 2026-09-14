# ETH 15分钟＋ICT：加入净2R后的含费保本

2026-09-15，Owner明确选择把既有 `cost_be2` 加入后续研究。该分支已经实现并完成历史回测，本轮固定为可复用的研究配置，不重复生成历史结果。

配置文件：[owner_research_choice.json](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v8-ict-exits-20260915-v1/owner_research_choice.json)。原实验的开发期选择仍是 `fixed3r`；本次是看过结果后的Owner选择，不能改写成开发期自动选出的冠军。效果状态仍为不确定。

## 加入的精确规则

1. OKX ETHUSDT.P，15分钟；原V8信号下一根开盘入场，按实际开仓时间筛选纽约02:00—11:00，保留周末，退出全天有效。
2. 初始价格风险R固定。多单用本根最高价、空单用最低价计算有利幅度，减去按初始名义仓位估算的0.2%往返成本；净浮盈达到2R且本根未先触发已有止损时，在收盘确认。
3. 下一根起，多单保护价至少为入场价×1.002，空单至多为入场价×0.998；按0.01 tick向有利方向取整。不是触及2R那一瞬间就回头改变本根止损，也不要求收盘价仍有净2R。
4. 原来的收盘毛浮盈2R激活4ATR跟踪继续运行。保本线和跟踪线取更紧的一条，已有保护不放松；原始V6反向信号仍按下一开盘退出。没有固定止盈上限或新增分批。
5. 保本只覆盖本笔固定成本，不等于赚到2R，更不抵消此前亏损。资金加码、债务重置规则没有改变。

## 已有历史结果对照

以下直接引用原实验 `results/evaluate/summary.csv`，日期为UTC，右端不含；本轮不读取价格、不新跑回测。候选数2026窗21、长历史134；自然交易分别17和123，均无期末持仓。2025验证段40笔，原版净1.93R、加入后3.26R；开发期66笔，原版−34.51R、加入后−38.86R，均见原报告。

| 区间 | 规则 | 笔数 | 总毛R | 总净R | 严格净正比例 | 最长净亏 | 结算回撤R | 匹配随机均净R/笔（覆盖） | Holm p |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|
| 2026-01-01—05-01 | 原版 | 17 | 26.32 | 22.74 | 52.9% | 2 | 3.29 | 0.1111（17/17） | 0.9888 |
| 2026-01-01—05-01 | 加入净2R含费保本 | 17 | 28.26 | 24.68 | 64.7% | 2 | 2.25 | 0.1496（17/17） | 0.9888 |
| 2023-08-01—2026-05-01 | 原版 | 123 | 24.00 | −9.84 | 26.0% | 14 | 38.33 | −0.1369（116/123） | 1.0000 |
| 2023-08-01—2026-05-01 | 加入净2R含费保本 | 123 | 22.92 | −10.92 | 31.7% | 14 | 42.68 | −0.1363（116/123） | 1.0000 |

2026窗改善1.94R，来自两笔亏损变成覆盖成本后的微盈；9笔实质盈利保留，2笔保本取整微盈合计仅0.001184R，另6笔亏损。64.7%不能解释成每次都赚够1R或2R。长历史救回7笔亏损，同时使3笔交易收益变差，合计少1.08R。因此加入是研究选择，不能声称总体更赚钱。

2025年4月17日至7月30日的14连亏仍存在，加入这条规则没有把历史最长连亏缩短。

随机对照沿用同币、方向、实际入场月、纽约小时及周末、此前120根因果波动桶，每单最多5个同退出同成本的随机入场。p为月块9999次置换并在原8组内Holm调整，检验的是相对随机入场的超额，不能冒充两种退出之间的显著性。长历史有7笔缺匹配。AUC、top-decile和单特征排序基线不适用：这里没有预测分数或训练；以原版同入场配对和匹配随机作为对照。

## 配置使用与复核

研究端已经使用 `yoyo.evaluation.spike_partial_exit.replay_partial`。新文件保存完整 `exit_policy`，可作为该函数的关键字参数；它对应原实验 `arms.cost_be2`，通过SHA绑定原配置与已生成统计。后续研究先读取此配置，再沿用既有V8准入与单仓调度；不是修改原8组实验的默认选择，也没有修改TradingView图上的Pine。

```python
import json
from pathlib import Path
from yoyo.evaluation.spike_partial_exit import replay_partial

profile = json.loads(Path(
    "experiments/active/exp-spike-v8-ict-exits-20260915-v1/owner_research_choice.json"
).read_text())
# prepared/signal_i/end_i must come from the approved V8+ICT research window.
# trade = replay_partial(prepared, signal_i, end_i=end_i,
#                        tick=profile["tick"], **profile["exit_policy"])
```

本轮实际执行45项既有检查全部通过，覆盖退出时序、保本、分批引擎委派与ICT准入。复现以下命令只执行合成检查和文档转换，不读取市场价格：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=/Users/zhangzc/fable-trading/.venv/lib/python3.9/site-packages:/Users/zhangzc/fable-trading /usr/bin/python3 -m pytest -q tests/evaluation/test_spike_recovery_exit.py tests/evaluation/test_spike_partial_exit.py tests/test_spike_v8_ict_exit_study.py tests/test_spike_v8_ict.py
/usr/bin/python3 scripts/md_to_html.py analysis/p1_spike_v8_ict_be2_adoption_20260915.md --out-dir analysis/html
```

完整原始回测命令、数据收据及逐笔文件见[原实验报告](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v8_ict_exits_20260915.html)。该报告保留原结果和所有失败方案。

## 风险与诚实声明

- 本轮holdout消耗0次，未读取价格。历史已看过，Owner选择不构成新的盲测。
- 止损跳空按实际观察到的开盘价成交，可能低于成本保本目标；固定0.2%没有另加资金费率和额外滑点。
- 收盘确认与下一根生效不能保证本根曾有2R浮盈就一定保本。TV原生逐笔对齐未完成；本次是Python研究配置固定，未上传Pine或接入实盘。
- 本轮HTML只做静态结构与本地链接检查，不声称浏览器视觉验收。

下一步研究沿用本配置作为比较起点，原版始终保留对照；若继续优化止盈，每次只改一个变量。实际部署及新配置holdout评估仍按项目要求单独取得授权。
