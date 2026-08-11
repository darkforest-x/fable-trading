# P1 Owner空头金标中心裁切全量数据集

## 结论

Owner确认“不要Codex重新手割；从最早金标红框中心取几根K线作为橙框”后，已将该合同扩到完整Owner-short母池。

- 1,361行Owner亲自确认的short标注全部成功定位；其中15组是同一市场窗、同一核心框的历史别名，已在split前合并为1,346个独立目标；
- 依赖合并后为1,287个事件块；
- 时间切分后train正例1,143、val正例202、purge丢1；
- train结束到val输入开始相隔162根15m K，高于150根铁律；
- 配套同币、同split、同窗口长度真实空背景train 1,143、val 200；2个val正例所在币种没有任何不碰Owner保护区的安全背景，因此诚实缺2，没有跨币或削弱保护区凑数；
- 未读取holdout、未来收益或模型分数；没有Codex重画；Owner已授权且3060的1:1 easy-negative baseline正在训练，但未promote或部署。

可视化审计：`analysis/html/p1_owner_short_gold_center_dataset_audit_20260811.html`

## 数据来源

| 项 | 数量 |
|---|---:|
| Owner独立框 | 2,525 |
| Owner亲自确认short | 1,361 |
| 独立中心裁切目标（去重后） | 1,346 |
| 重复标注别名 | 15组 / 移除15行 |
| 覆盖币种 | 215 |
| 其中独立精确⭐框 | 70 |
| 源数据缺失 | 0 |
| holdout行 | 0 |

正例源为`analysis/output/owner_side_review/review_sheet.csv`。这里只读取`owner_side=short`；多头框不作为负例，但所有已知Owner框（多空均含）都会形成负例禁入保护区。

## 几何与窗口

| 项 | 结果 |
|---|---:|
| 核心4 / 5 / 6 / 7根 | 213 / 335 / 278 / 519 |
| W12 / 13 / 14 / 15 | 213 / 335 / 278 / 130 |
| W16 / 17 / 18 / 19 | 231 / 86 / 25 / 47 |
| 后文3 / 4 / 5根 | 956 / 317 / 72 |

核心宽度=`ceil(原Owner框宽度/2)`并限制在4–7根，严格取原框中心。前文5–7根，后文3–5根。完整窗口为W12–19，符合“十几根短检测窗”，位置由原框宽度自然变化。

## 时间切分与依赖

同一币种上输入窗口发生重叠的样本先合并为不可拆的`dependency_id`。按依赖块结束时间排序，最后15%为val；其余只有完整窗口结束不晚于`val_start-150 bars`才进入train，中间全部drop。

| 项 | 结果 |
|---|---:|
| dependency blocks | 1,287 |
| train / val / drop blocks | 1,093 / 193 / 1 |
| train / val / drop正例 | 1,143 / 202 / 1 |
| train最后输入结束 | 2026-03-13 18:30 UTC |
| val最早输入开始 | 2026-03-15 11:00 UTC |
| 实际隔离 | 162 bars |
| 跨split依赖块 | 0 |

## 真实空背景

每个正例尝试匹配一个相同symbol、split和W的连续真实K线窗口。候选不得与任何已知Owner框重叠，并在两侧额外留12根保护。负例选择不读取未来收益或模型分数。

2个val正例没有可用的同币安全背景。保留200/202比为了1:1强行复用、跨币或靠近Owner框更可信。交接规范要求至少比较1:1与1:2/1:3两种比例，且后者应以hard negative为主；因此当前1:1 easy-negative只作为首版误火采集基线，不冒充最终负例集，也不直接堆三倍easy-negative。

## 质量检查

- 正例manifest 1,345行；负例manifest 1,343行；
- train图/标签2,286对，val图/标签402对；
- 图片+标签联合SHA重复0组，图片跨split重复0组；
- 同路径连续重建两次，正例manifest SHA均为`8f4119fb…d6d21b`，负例manifest SHA均为`3a32bd61…3c6f0`；
- 正例标签恰好一个class-0框；负例标签为空；
- 所有原始CSV按每币最终所需的pre-holdout索引做前缀读取，审计`holdout_rows_materialized=0`；
- 数据集`production_eligible=false`、`auto_promote=false`；
- 项目测试`627 passed, 2 skipped`；3060与本机版本均为torch 2.8.0 / ultralytics 8.4.89 / numpy 2.0.2；
- 当前用途仅为训练第一版1:1基线并挖hard negatives。

本轮已于2026-08-11 13:29 CST启动训练，但尚未完成、未推理、未回测，因此mAP、precision/recall、AUC、置换p、top-decile收益、胜率、单特征基线与匹配随机对照暂不适用。

## 训练启动

- run：`owner_lsv2_short_gold_center_v1_ft`；
- 初始化：Stage A `best.pt`，SHA-256 `c0e94f47…bf1a`；
- 配方：YOLO11s、imgsz 960、batch 8、seed 0、epochs 40、patience 10；
- 微调：显式`--finetune`，AdamW `lr0=1e-4`、warmup 0.5；
- 增强：flip / mosaic / mixup / HSV全0；
- 远端：`zzc@192.168.1.4` RTX 3060，WMI launch pid 37596；
- 限制：不读holdout、不promote、不改ACTIVE、不部署。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_owner_short_gold_center_dataset.py

PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_build_owner_short_gold_center_dataset.py

FABLE_3060_HOST=zzc@192.168.1.4 \
  bash scripts/train_owner_short_gold_center_on_3060.sh

python3 scripts/md_to_html.py \
  analysis/p1_owner_short_gold_center_dataset_20260811.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- 原始Owner框来自可见长历史图，能够提供Owner形态语义，但不自动证明盘口因果alpha；本模型仍是离线短延迟形态检测器。
- 中心裁切合同已由Owner明确给出，但完整1,345张尚未逐样本重新确认；70个独立⭐框是最高质量子集。
- easy negatives只负责第一版背景安静度，不能替代模型误火产生的hard negatives。
- 当前没有训练结果，不能从数据构建成功推导模型一定准确。
- 2个val匹配负例缺失是安全约束造成，不应通过降低Owner保护区来填平。

## 下一步

等待当前3060固定配方baseline完成并取回权重；不调阈值、不读取holdout。随后冻结该权重，对训练时间块连续窗口推理，收集误火作为hard negatives，构建文档要求的1:2/1:3（hard为主）第二臂，再比较首次识别delay3/4/5、event precision和FP/1000。
