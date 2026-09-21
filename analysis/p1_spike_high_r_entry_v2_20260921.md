# SPIKE High-R Entry V2：20根突破未提升高R命中率

2026-09-21。**拒绝作为有效升级。** 原V9多头净盈利胜率29.33%→30.21%，但净R>10比例0.8801%→0.8186%。后段胜率26.43%→26.84%，95%差值区间含0；后段净R>10比例0.8214%→0.7786%，平均净bp仍−42.40，匹配随机超额−0.05337R、p=0.89136。

## 数据与固定规则

原V9全部3,531条行情流，Binance/OKX/Gate的30m/1h/4h，2024-09-10至2026-09-10；原V9准入候选96,200（多空合计）。本版仅在原做多信号close严格超过此前20根连续已完成K线high最大值时准入，当前high不进历史最大值。原V9止损/2R启用4ATR追踪/20bp成本/空头及原反向退出不变。缺历史显式未知拒绝，信号收盘可知、次开执行。

2025-09-10时间切分；早段入场与退出都在切点前，跨界单列。原/新后段闭合多单25,567/12,843、删失114/59，尾部正类率0.8214%/0.7786%。全部亏单保留，无训练，无按收益挑币或未来数据入场。

## 与旧版及匹配随机同表

率为0至1比例，成本已扣；R总和不是账户收益率。匹配随机同stream/方向/月/ATR桶，原退出、固定seed、不重抽失败路径。

| arm | period | closed | win_rate | gt10 | gt10_rate | total_r | mean_net_bp | paired_excess_r | random_p |
|---|---|---|---|---|---|---|---|---|---|
| baseline | full | 41700 | 0.2933 | 367 | 0.0088 | 1697.5902 | 2.8211 | 0.0580 | 0.1339 |
| baseline | earlier | 15807 | 0.3301 | 146 | 0.0092 | 3353.0703 | 67.4076 | 0.1898 | 0.0704 |
| baseline | later | 25567 | 0.2643 | 210 | 0.0082 | -2328.7619 | -43.3545 | -0.0207 | 0.7321 |
| high_r_entry_v2 | full | 21988 | 0.3021 | 180 | 0.0082 | 1151.9068 | 3.8324 | 0.0281 | 0.3157 |
| high_r_entry_v2 | earlier | 8925 | 0.3385 | 75 | 0.0084 | 1901.1331 | 59.0773 | 0.1494 | 0.1174 |
| high_r_entry_v2 | later | 12843 | 0.2684 | 100 | 0.0078 | -1167.9084 | -42.3967 | -0.0534 | 0.8914 |

全期闭合单减少47.27%，旧367笔高R仅保留145、漏掉222，另有35笔新进入高R，合计180。后段胜率提高0.407个百分点，月块bootstrap95%差值区间[−0.4423,+1.2445]个百分点；高R比例差−0.0427个百分点，区间[−0.1615,+0.0646]。改善不稳定，主验收失败。

## 分数与参照

连续突破分数=(close-prior20high)/ATR。后段净R>10 AUC=0.48524；按该因果分数排序的前10%2,557笔，普通胜率28.55%、27笔净>10R、均值净60.49bp、随机超额p=0.21045。该批量排名是诊断，不是本版下单阈值，也不能据此追调参数。

预列单特征参照：负的信号参考风险比例后段AUC=0.61868，前10%有49/2,557笔净>10R（1.9163%）、胜率28.35%、均值净20.27bp，随机超额p=0.10986；原RV后段AUC=0.51771、前10%均值净−60.52bp。所有参照均在已暴露数据上，不是独立验证；风险距离方向另建V2.1探索，旧V2失败不改写。

## 验证与复现

builder b2e5a24dbd8dcfd5ab0019866a533c5280dfa79a先于回放；3,531流211.1秒、0失败、原V9逐笔一致、原cache运行前后哈希不变。20专项测试与108相关边界/因果/数值检查通过。独立静态复核发现并修复空样本必须拒绝、配置退出/入场描述矛盾；额外结果证据见evidence_audit.json。代码审阅未重复运行市场或测试。独立数值复核确认19,192条共同事件经济字段逐项相等、63,596条已匹配控制实际month/bin相等。362条流未出现在多头聚合表，经主任务逐一核对receipt确为两版均零多头事件；不代表零空头或零候选，见zero_long_streams_audit.json。

```bash
.venv/bin/python -m pytest tests/evaluation/test_spike_high_r_entry_v2.py tests/evaluation/test_spike_high_r_entry_study.py -q
.venv/bin/python -m pytest tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_experiment_isolation.py tests/causality/test_continuity_and_availability.py tests/parity/test_numeric_baseline_parity.py -q
.venv/bin/python -m yoyo.evaluation.spike_high_r_entry_study --output experiments/active/exp-spike-high-r-entry-20260921-v2/run_reproduce --workers 3
.venv/bin/python -m yoyo.evaluation.spike_high_r_entry_report --run experiments/active/exp-spike-high-r-entry-20260921-v2/run_reproduce --output experiments/active/exp-spike-high-r-entry-20260921-v2/statistics_reproduce
```

## 风险与诚实声明

- 历史已被多次查看，不是盲验证；三组分数诊断不作为规则胜出证据。
- 高R数量大幅下降；不能用少交易少亏代替单位交易能力。净盈利与净>10R是两种命中率。
- 固定20bp沿用旧研究，没有杠杆、账户保证金、资金费、历史标记价强平或实际滑点模拟，不能换算成100U滚仓金额。
- 串行空头规则保留，不将本回放冒称纯多头单仓资金组合。跨币并发R总和不是资金曲线。
- 输出有pandas空表concat未来行为提示，当前严格解析及数值检查通过，警告保留在日志。
- 原源码、所有失败结果保留；Pine/TV、监控、ACTIVE、训练和账户均未变更。

## 下一步

独立V2.1检验过去3完整月的风险距离分位阈值；其方向来自本轮已暴露诊断，必须如实披露选择过程与全部失败。实盘准入、默认参数切换和真钱操作仍需Owner另行授权。
