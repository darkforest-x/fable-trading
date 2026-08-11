# P1 原始空头金标中心裁切审核

## 结论

当前61张Codex逐图目测橙框不再作为下一版标签来源。新的审核包直接联结两份Owner事实：

1. 最早在Label Studio标为`⭐标杆`的原始手画框；
2. Owner后来逐框确认的`short`方向。

精确联结得到71个框，其中69个仍能找到未加工原始PNG并完成重裁，2个历史原图缺失而跳过。新橙框不是模型预测，也不是Codex目测：它只取原始Owner红框的中心部分，宽度为原框约一半并限制在4–7根。外层训练输入缩为W12–17，框后仅3–4根。未来48根单独进入人工审核图，不进入训练图片或标签。

审核HTML：`analysis/html/p1_owner_gold_center_crop_owner_gate_20260811.html`

## 数据血缘

| 层 | 来源 | 数量 | 说明 |
|---|---|---:|---|
| 独立Owner框 | `analysis/output/owner_side_review/review_sheet.csv` | 2,525 | 非tip克隆 |
| Owner亲自确认short | 同上`owner_side=short` | 1,361框 / 1,317图 | 时间到2026-05-02；无holdout |
| ⭐原始标杆注册 | `data/benchmark_exemplars.json` | 176图 | 原Label Studio框坐标 |
| 精确框交集 | ⭐坐标IoU=1.000且Owner short | 71框 / 69图 | 逐框联结，不是按图模糊联结 |
| 可恢复原始PNG | `analysis/output/star_benchmark_originals/raw/` | 69框 | 2框原始PNG缺失，未替代 |

本轮没有读取Stage-A val图/标签，没有读取holdout，没有读取模型权重，也没有按未来收益筛选几何。

## 几何合同

对每个原始Owner框`[source_start, source_end]`：

1. 中心核心宽度=`ceil(原框宽度/2)`，限制到4–7根；
2. 核心在原框内取严格中心，左右剩余相差不超过1根；
3. 前文由原框左余量导出并限制到5–7根；
4. 后文由原框右余量导出并限制到3–5根；
5. 橙框纵向只由核心K线的high/low计算；
6. 未来48根另行读取和渲染，不参与上述任一步。

这保留了Owner原始语义，同时把200根历史大图压缩成十几根可训练输入。它不再要求Codex判断“哪一根像启动首根”。

## 结果

| 项 | 结果 |
|---|---:|
| 成功渲染 | 69 |
| 原框宽度 | 5–15根；中位约9根 |
| 新核心4 / 5 / 6 / 7根 | 24 / 16 / 12 / 17 |
| 外层W12 / 13 / 14 / 15 / 16 / 17 | 24 / 16 / 12 / 4 / 10 / 3 |
| 后文3 / 4 / 5根 | 56 / 13 / 0 |
| holdout读取 | 0 |
| Codex手动画框 | 0 |
| 模型预测框 | 0 |
| 当前训练资格 | 0 |

## 与上一方案对照

| 项 | 61张Codex重框 | 本轮原始金标中心裁切 |
|---|---|---|
| 内框来源 | Codex逐图视觉判断 | Owner原手框的中心K线 |
| 未来参与几何 | 否 | 否 |
| 主观二次标注 | 有 | 无 |
| 5根框集中度 | 56/61 | 16/69 |
| 原始Owner坐标约束 | 仅作旧框参考 | 逐框IoU=1.000硬联结 |
| 当前能否训练 | 否 | 否，待Owner确认合同 |

## 验证

- 单元测试覆盖：中心框始终位于源框内、宽度4–7、左右余量差≤1、后文3–5、相同YOLO框IoU=1；
- 所有训练原始数据读取只到训练窗末，逐行审计`holdout_rows_materialized=0`；
- 未来审核数据使用独立读取审计与独立目录`review_future_only/`；
- 输出不存在训练`labels/`目录，Owner未确认前不生成YOLO标签；
- 69张训练短图与69张未来审核图均记录SHA256。

本轮未训练、未推理、未回测，因此mAP、precision/recall、AUC、置换p、top-decile收益、胜率、单特征基线与匹配随机对照均不适用。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_owner_gold_center_crop_review.py

PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_build_owner_gold_center_crop_review.py

python3 scripts/md_to_html.py \
  analysis/p1_owner_gold_center_crop_review_20260811.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- 69张是最高标准的`⭐ ∩ short`种子，不是完整1,361个空头Owner框；Owner确认裁切合同后才扩全量。
- 原始金标是在长历史图上产生，可能带事后可见性；本轮只证明几何来自Owner，不证明盘口因果alpha。
- “取原框中心”是Owner本轮明确给出的派生合同，但每张橙框仍未逐样本确认，训练资格保持false。
- 2个历史原图缺失；没有用pad200或训练裁图伪装成原图。
- 后文3–5是最大延迟合同，不是要求模型必须等到第3根；最终仍以最早可靠识别为目标。

## 下一步

Owner先查看69张三联图，判断“原框中心→橙框”的合同是否正确。若合同认可，下一轮把同一机械规则扩到1,361个Owner-short框，按时间切分并做依赖块去重，再单独构建真实背景与难负例；仍不读取holdout、不自动开训。
