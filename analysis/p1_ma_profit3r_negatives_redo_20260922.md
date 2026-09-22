# MA 3R 训练负例修复：数据审计完成，准备3060训练

Owner 在发现 A/B 训练负例为零后要求“重新做吧”。本轮是两组离线重训的明确授权；不改变生产资格或部署。原 `exp-ma-profit3r-20260922-v1` 失败结果保留。其 plan 把 positive-only 归为 Owner 要求是不正确的解释，“只用原版”仅区分视图增强。

## 冻结方案

新实验 `exp-ma-profit3r-negatives-20260922-v2`，只增加训练反例。保留 1506 个原赢家、全部6812旧PNG/TXT、val1305/test989 的身份/切分/字节；A每事件1图，B每事件2图。保持 YOLO11s、40轮、1280、原AdamW、所有增强关闭、原3060版本契约及同一初始权重。旧1:3障碍、核心+5决策、次开入场、12h标签、20bp成本不变。

目标仍是 `profitable_dense_long/short`，不是通用形态类别。SL/TIMEOUT 非保留事件是收益目标反例；TIMEOUT可能仍盈利，不能统称亏损或形态错误。UNKNOWN/INVALID不作负例。原候选训练总体有7839个已解析非保留事件；预检查保护后7824，最终以渲染、时序与实际文件审计为准。

使用同一冻结候选总体的全部合格负例，不以模型分数或收益大小挑选、不强凑1:1。最长pre11至c+5输入不得碰所有已知父pool正例核心±4h、69参考±4h或221人工原视图区间±4h。旧142人工负例缺当前c+5核心语义，不直接混入训练；旧owner_long只说明单边目标错方向，也不当双边通用负例。

## 预先规定的验收

实际 train_A/train_B 加载清单逐项核对正负标签，任一组负例为0拒绝开训。输入和MA/价格轴只读到各自决策收盘；所有已知数据缺口/重复/保护区排除落账。源SHA、输入ledger、原文件、split、各事件视图同时审计。两组从同一base训练，GPU必须3060；val/test和匹配随机对照沿用上轮，正式分数仍走相同单图predict。

训练后同表报告事件定位/误报、val/test AUC、固定top10毛/净bp与胜率、quality_score单特征、置换p与同币时间波动匹配随机控制。已看过的val/test只作为重做的受控比较，不冒充未接触过的终审。B曝光翻倍、12h标签重叠、内置val padding与单图路径差异保持披露。不依据本轮结果再调参。

## Owner 查看入口与验证集口径

Owner 追问先看负例及验证集后，按实际 PNG 和 TXT 复核：验证集175正/1130负，共1305；测试集160正/829负，共989。两组共享原验证和测试清单，训练结束后的测试分数不参与选权重。时间边界沿用 UTC 2026-01-01/2026-05-01，同一事件的视图不跨集合。

这些正负标签表示当前3R收益目标，不是逐张由Owner确认的形态金标。旧人工标注及真实tip验收是另一项证据；本轮core+5研究窗口不得冒充tip输入或以内部mAP替代那项验收。

人工221条账本在本仓 `datasets/manifests/dataset_v3_2_reviewed_core_v1_rows.jsonl`；原图实际位于只读归档 `/Users/zhangzc/yoyo-trading/datasets/dataset_v3_2_reviewed_core_v1/`，不能把账本相对路径直接拼成本仓不存在的图片目录。该套原人工val为28张（8正/20负），train为193张（71正/122负）。子代理核对221张PNG存在、可解码且图片SHA与账本一致，主任务复核目录计数并抽查一张val图片SHA。28条flip_pos_to_neg记录的label_sha仍为翻转前旧值，而实际TXT为空；若将来复用这些标签，需另行建立正确的派生标签清单，不可声称原标签哈希全部通过。本轮仅以其时间窗口作保护，未使用这些标签训练或改写旧归档。

本地查看目录为 `datasets/ma_profit3r_owner1500_neg_v5/review/`，含train_positive_A、train_negatives_A、val_positive、val_negative、test_positive、test_negative。它们仅为指向原图的查看软链接，不在训练加载清单，不上传3060。训练负例生成中时该目录仅是已生成图片的快照，完整计数以最终manifest和实际加载清单审计为准。

## 复现命令

```bash
.venv/bin/python -m pytest tests/test_ma_profit_negative_redo.py tests/test_ma_profit_dataset.py tests/test_train_ma_profit3r.py tests/evaluation/test_ma_profit_input_continuity.py -q
.venv/bin/python -m yoyo.datasets.ma_profit_negative_redo select --plan experiments/active/exp-ma-profit3r-negatives-20260922-v2/plan.json --out experiments/active/exp-ma-profit3r-negatives-20260922-v2/selection
.venv/bin/python -m yoyo.datasets.ma_profit_negative_redo build --plan experiments/active/exp-ma-profit3r-negatives-20260922-v2/plan.json --selection experiments/active/exp-ma-profit3r-negatives-20260922-v2/selection --out datasets/ma_profit3r_owner1500_neg_v5
.venv/bin/python -m yoyo.datasets.ma_profit_negative_redo audit --plan experiments/active/exp-ma-profit3r-negatives-20260922-v2/plan.json --selection experiments/active/exp-ma-profit3r-negatives-20260922-v2/selection --out datasets/ma_profit3r_owner1500_neg_v5
```

审计结果保存为实验目录 `local_audit.json`；运行 `python -m yoyo.datasets.ma_profit_negative_preview` 后实际查看contact sheet与原图，再填写 `visual_review/review.json`，不能自动假定人工检查已发生。当前已冻结版本接续命令如下，已有产物时拒绝覆盖：

```bash
.venv/bin/python -m scripts.research.run_ma_profit_negative_redo freeze
.venv/bin/python -m scripts.research.run_ma_profit_negative_redo stage
.venv/bin/python -m scripts.research.run_ma_profit_negative_redo start
.venv/bin/python -m scripts.research.run_ma_profit_negative_redo watch
.venv/bin/python -m yoyo.evaluation.ma_profit_negative_delivery
```

## 构建与开训前证据

Builder提交 `bfcd47fb8d` 先于构建。原7839个训练SL/TIMEOUT候选中15个与保护范围冲突，选定7824个；实际构建全部7824，无额外跳过。最终30284张物理PNG，11624个独立事件；A/B共用val/test，B训练每事件两视图。实际标签及加载清单审计通过60568个PNG/TXT，保留父集6812图/标签及其事件、split、manifest行。

| 集合 | 正事件 | 负事件 | A图片 | B图片 |
|---|---:|---:|---:|---:|
| train | 1506 | 7824 | 9330 | 18660 |
| val | 175 | 1130 | 1305 | 1305 |
| test | 160 | 829 | 989 | 989 |

新增训练负例5595 SL、2229 TIMEOUT；最新决策UTC2025-12-31 11:11，最晚标签结束23:11，均早于2026-01-01切点。独立Luna核对event_id、cluster_id、origin_event_id无跨集合冲突，记录 `independent_split_review.json`。分周期/方向数量在 `population_strata.json`，并非每个周期方向都有充足正样本；不能用总体指标覆盖稀少或无正例的分层。

渲染检查为28格代表性contact sheet和3张原尺寸图，保持1280×742、六均线、审核框不进入原图；它不是Owner逐样本金标确认。原图和全部负例的查看软链接已完整生成。

专项测试原46项通过；后续收集/交付的冻结评估器和对照输入绑定测试通过，自包含fixture验证错组评估回执被拒。全仓相关门曾运行468通过/8失败：1个既有归档路径引用、5个既有注册表source_commit缺失、2个L1迁移哈希差异。没有掩盖或修复其他任务的变更，也不宣称全仓全绿；本轮离线授权不改变生产资格。

## 风险与诚实声明

当前尚未取得新训练结果，不能提供新模型收益数字。阶段属于监督总体与输入完整性修复，收益/AUC/p尚不适用；严格对照为旧6812文件及事件/split逐一字节一致、改变未来OHLC或收益标签不改变像素、故意篡改实际标签/loader触发拒绝。重新训练会如实保留失败，不承诺补负例必然赚钱。3060实际运行回执和结果完成后补在本报告。
