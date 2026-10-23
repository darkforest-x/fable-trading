# 首4小时策略规格复核：close-only不能代表完整实体版本

2026-09-20，Owner追问“确定逻辑没问题吗”。这是既有`exp-four-hour-range-20260920-v1`的规格审计补充，不是参数实验或修正版回测。旧报告、原始失败结果、代码与run_v1全部保留。

## 结论

确认当前实现与Owner最初“实体必须完全收在区间之外”的文字存在实质差异。代码使用close越界，完整实体条件应同时约束open和close。原逐笔独立核算验证的是close-only执行契约，并未证明忠实实现原始文字。当前净亏结论仅属于close-only、纽约00–04、日末强平及20bp成本的固定版本；严格实体版收益尚未知。

此前记录将视频字幕解释为close-based，不能将此解释当作Owner取消完整实体约束的授权。本轮没有重新观看视频，也不裁定视频所有主观案例的含义。

## 确认偏差与最小反例

`yoyo/evaluation/four_hour_range.py:319`附近仅使用close计算outside_side和inside。完整向上突破应为`min(open, close) > H`；向下为`max(open, close) < L`。

参考区间[99,101]；第一根O=100、H=103、L=100、C=102，第二根O=102、H=102、L=100、C=100。第一根实体穿越上沿，从未完全在外；第二根收盘回区间。现代码在下一根开盘100做空，SL=103、TP=94，实际产生1笔。严格完整实体突破前提不成立，应为0笔。这是从原始需求独立构造的反例，不依赖收益或事后选取行情。

现有首项测试同样以open仍在区间内、close在外的bar触发交易，故测试通过只证明当前解释自洽。只读Terra High审查独立确认这一差异。

## 原账本影响范围（描述性，不是删单回测）

原正式范围纽约2023-09-20至2026-09-19（不含），每币315360根5m、1095天。逐笔检查原excursion_start_i至signal_i之前，是否出现至少一根对应方向完整实体越界bar。

| 原账本 | 成交数 | 完全没有完整实体越界bar | 占比 |
|---|---:|---:|---:|
| BTC-USDT-SWAP | 2335 | 485 | 20.77% |
| ETH-USDT-SWAP | 2246 | 543 | 24.18% |

这些交易在当前事件范围内不满足严格突破前提。其余交易也不能视为修正版成交：突破开始、极值窗口、回归时间和单仓占用可能一起改变，必须重新串行回放。不能把删掉485/543笔后的旧账本当作新策略。

## 其他边界

- 回归当前也只检查close。原文“实体收回区间”是否要求整个实体都在内需要与“收盘回归”分开，不能静默决定。原账本每币只有1根确认bar的整个实体严格在内；这符合首次收盘回归通常仍从区间外开盘的事实，但不是直接认定其余全部错误的依据。
- 纽约00–04本地墙钟聚合是一个明确实现选择，不等同于把图表显示时区改成纽约后选交易所原生首根4H。夏令时切换日当前区间包含3或5小时。TradingView说明图表时区只改变显示，不改变Pine时间计算：[官方时间文档](https://www.tradingview.com/pine-script-docs/concepts/time/)。
- 同日出现回归不等于仓位必须日末平掉。当前约20%原交易使用日末退出，这属于原始文字未定义的补充假设。
- 目前极值窗口包含当前已定义突破起始bar、期间各bar以及回归bar的完整影线，没有证据证明漏掉该窗口内影线。更早影线是否属于“本次突破”属于事件定义，不能凭直觉认定为止损bug。
- 跨越整个区间直接收在对侧外时，当前代码重置为对侧事件，也是已披露补充定义。

## 复现命令

环境沿用仓库.venv；以下只读冻结行情、账本，并在内存构造最小输入，不改变策略或生成新经济回测。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python - <<'PY'
from pathlib import Path
import pandas as pd
from yoyo.evaluation.four_hour_range import BAR, prepare, simulate
idx = pd.date_range('2025-01-06 05:00', periods=288, freq='5min', tz='UTC')
x = pd.DataFrame({'open':100., 'high':101., 'low':99., 'close':100., 'volume':1.}, index=idx)
x.loc['2025-01-06 09:00', ['open','high','low','close']] = [100.,103.,100.,102.]
x.loc['2025-01-06 09:05', ['open','high','low','close']] = [102.,102.,100.,100.]
trades = simulate(prepare(x), idx[0], idx[-1]+BAR)['trades']
print('Current trades:', len(trades), '; strict-breakout expected: 0')
root = Path('experiments/active/exp-four-hour-range-20260920-v1')
for symbol in ['BTC-USDT-SWAP','ETH-USDT-SWAP']:
    bars = pd.read_csv(root/'data'/symbol/'bars.csv.gz', float_precision='round_trip')
    trades = pd.read_csv(root/'run_v1'/symbol/'trades.csv', float_precision='round_trip')
    missing = 0
    for t in trades.itertuples(index=False):
        event = bars.iloc[int(t.excursion_start_i):int(t.signal_i)]
        body = event[['open','close']]
        outside = body.max(axis=1) < t.range_low if t.side == 1 else body.min(axis=1) > t.range_high
        missing += int(not outside.any())
    print(symbol, len(trades), missing, missing/len(trades))
PY
```

## 风险与诚实声明

这是规格一致性审计，无训练、标签、预测排序或新收益实验；val AUC、置换p、top-decile收益、随机入场收益对照不适用。相应严格反例对照是相同OHLC下两个独立布尔前提：close-only允许1笔，完整实体前提不成立应为0笔。数据影响计数不用于推断修正后的净收益。

本次没有改代码、成本、止损倍数、Pine或生产配置；不证明修正后一定赚钱，也没有再次声称系统完全无bug。历史原始净亏账本依然有效，只限定其代表的版本。后续应先明确完整实体回归与原生4H锚点，再按版本修正、单独回放；不得用本次诊断筛选旧账本冒充新回测。
