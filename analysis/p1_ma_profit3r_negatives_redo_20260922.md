# MA 3R 训练负例修复：已预注册，尚未训练

Owner 在发现 A/B 训练负例为零后要求“重新做吧”。本轮是两组离线重训的明确授权；不改变生产资格或部署。原 `exp-ma-profit3r-20260922-v1` 失败结果保留。其 plan 把 positive-only 归为 Owner 要求是不正确的解释，“只用原版”仅区分视图增强。

## 冻结方案

新实验 `exp-ma-profit3r-negatives-20260922-v2`，只增加训练反例。保留 1506 个原赢家、全部6812旧PNG/TXT、val1305/test989 的身份/切分/字节；A每事件1图，B每事件2图。保持 YOLO11s、40轮、1280、原AdamW、所有增强关闭、原3060版本契约及同一初始权重。旧1:3障碍、核心+5决策、次开入场、12h标签、20bp成本不变。

目标仍是 `profitable_dense_long/short`，不是通用形态类别。SL/TIMEOUT 非保留事件是收益目标反例；TIMEOUT可能仍盈利，不能统称亏损或形态错误。UNKNOWN/INVALID不作负例。原候选训练总体有7839个已解析非保留事件；预检查保护后7824，最终以渲染、时序与实际文件审计为准。

使用同一冻结候选总体的全部合格负例，不以模型分数或收益大小挑选、不强凑1:1。最长pre11至c+5输入不得碰所有已知父pool正例核心±4h、69参考±4h或221人工原视图区间±4h。旧142人工负例缺当前c+5核心语义，不直接混入训练；旧owner_long只说明单边目标错方向，也不当双边通用负例。

## 预先规定的验收

实际 train_A/train_B 加载清单逐项核对正负标签，任一组负例为0拒绝开训。输入和MA/价格轴只读到各自决策收盘；所有已知数据缺口/重复/保护区排除落账。源SHA、输入ledger、原文件、split、各事件视图同时审计。两组从同一base训练，GPU必须3060；val/test和匹配随机对照沿用上轮，正式分数仍走相同单图predict。

训练后同表报告事件定位/误报、val/test AUC、固定top10毛/净bp与胜率、quality_score单特征、置换p与同币时间波动匹配随机控制。已看过的val/test只作为重做的受控比较，不冒充未接触过的终审。B曝光翻倍、12h标签重叠、内置val padding与单图路径差异保持披露。不依据本轮结果再调参。

## 复现命令

```bash
.venv/bin/python -m pytest tests/test_ma_profit_negative_redo.py tests/test_ma_profit_dataset.py tests/test_train_ma_profit3r.py tests/evaluation/test_ma_profit_input_continuity.py -q
.venv/bin/python -m yoyo.datasets.ma_profit_negative_redo select --plan experiments/active/exp-ma-profit3r-negatives-20260922-v2/plan.json --out experiments/active/exp-ma-profit3r-negatives-20260922-v2/selection
.venv/bin/python -m yoyo.datasets.ma_profit_negative_redo build --plan experiments/active/exp-ma-profit3r-negatives-20260922-v2/plan.json --selection experiments/active/exp-ma-profit3r-negatives-20260922-v2/selection --out datasets/ma_profit3r_owner1500_neg_v5
.venv/bin/python -m yoyo.datasets.ma_profit_negative_redo audit --plan experiments/active/exp-ma-profit3r-negatives-20260922-v2/plan.json --selection experiments/active/exp-ma-profit3r-negatives-20260922-v2/selection --out datasets/ma_profit3r_owner1500_neg_v5
```

## 风险与诚实声明

当前未训练，不能提供新模型收益数字。阶段属于监督总体与输入完整性修复，收益/AUC/p尚不适用；严格对照为旧6812文件及事件/split逐一字节一致、改变未来OHLC或收益标签不改变像素、故意篡改实际标签/loader触发拒绝。重新训练会如实保留失败，不承诺补负例必然赚钱。最终统计和3060实际运行回执补在本报告。
