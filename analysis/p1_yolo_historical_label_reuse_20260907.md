# P1 · 历史人工标注库存与复用顺序（2026-09-07）

## 结论

旧人工标注仍有用，应先恢复其来源和确认状态，再决定需要补标多少。当前 4,172 个事件的
Label Studio 空白项目已经交付，但不应要求 Owner 把它作为第一步全部重画。
本轮只做来源核查与工作顺序修正，没有更改 Label Studio 任务、训练标签或模型。

两条来源链必须区分：旧人工工作收窄成 **1,345 张 short 训练正例**；当前 Grade-A 则由
规则与参考图自动筛出 **1,043 个正事件**，裁出 8,000 张正图，再配 24,000 张负图。
这不是同一批“一千多”，也不能把自动扩充样本冒充 Owner 逐张金标。

## 库存与证据强度

| 资产 | 数量 | 确认到哪一步 | 本轮依据 |
|---|---:|---|---|
| canonical LS 历史人工审核 | 12,565 张唯一图 / 6,291 矩形框 | 5,946 图有框，6,619 图为背景 | 引用已有 canonical export 审计，未重读全部原始标注 |
| 旧训练主源 dense_owner_v11 | 11,730 图 / 5,831 框 | 曾用于旧检测训练 | 历史报告 |
| Owner 方向复核池 | 2,525 框 / 2,391 图 | short 1,361、long 1,152、skip 12 | 本轮重算 CSV 数量、时间和 SHA |
| 旧 short 正例 | 1,345 张 | 1,361 short 去 15 别名，再 purge 1；原框和方向有人工依据 | 本轮重算 manifest 行数和 SHA；历史 split 为 1,143 train / 202 val |
| Gold500 对应批次 | 500 条方向复核 | short 257、long 236、skip 7；不等于 500 个精选正框 | 本轮核对 in_sample=1 与 257 个 short 引用集合相等 |
| 原 ⭐ 标杆登记 | 176 图 | 独立的标杆登记；不是上述 500 的同义词 | 本轮读取登记数量与来源引用；归档清单记 161 原图 / 15 缺失 |
| 旧 short 的 A 档 | 385 张 | 机器排序，不是新增逐图确认 | 预筛报告与摘要 |
| 当前 Grade-A 正例 | 1,043 事件 / 8,000 图 | 自动筛选和裁剪扩充，非逐事件 Owner Gold | 当前 builder、冻结协议及构建摘要 |

历史 golden_pool 的另一库存口径是 12,567 stems / 6,229 框，与 canonical exports 相差
2 stems、62 框。本轮未重做两个上游的完整集合联结，因此不将它们强行合并，也不把
12,567 误写成框数。各行有重叠且计量单位不同，不应相加。

方向表 cut_time 范围为 **2025-06-05 16:45～2026-05-03 11:30 UTC**。本轮没有打开原图、
行情或未知时间的原始 LS 标注，没有运行模型或新评估；引用历史总量不代表全部历史样本
都已重新验证时间资格。

主要来源：[旧人工谱系审计](../p1_ma_rope_prefilter_20260821.md)、
[旧 short 数据集](../p1_owner_short_gold_center_dataset_20260811.md)、
[原图精筛包](../p1_owner_short_positive_refilter_20260821.md)、
[原始星标交集](../p1_owner_gold_center_crop_review_20260811.md)。
Gold500 元数据位于 `datasets/fixed_w10_gold500_short_ownerhn_v1/`。

## 为什么旧人工标签没有等价地进入当前配方

当前自动 10,000 样本来自 Owner 接受过的 v7 50 张参考族，再按形态规则与距离检索。
严格二筛的 perfect/good 锚点只有 #44/#42，50 张参考族并非 50 个 perfect 锚点。
Grade-A 扩充沿用这套合同，加入新 Binance 历史候选，再按事件裁出 7–8 个变体。
增加裁剪数量能改变图上位置和上下文，不能增加独立的人工判断。

`yoyo/datasets/ma_launch_owner_grade_a8000.py` 读取参考模板和自动候选，并按核心范围生成框；
它没有直接导入旧 1,345 张的原 LS 逐框标签。当前训练记录使用官方 `yolo11s.pt` 初始化，
也不能声称通过旧权重完整继承了旧人工训练。当前事件与旧事件可能重合，**本轮没有完成
跨来源逐事件交集**，不能声称交集为零。

因此旧人工资产的价值是补上独立的人类形态判断、更多形态种类，以及有明确语义的反例。
现有证据支持改变数据整理顺序，尚未证明这样做能提高多少准确率。

来源：[自动 10k](../p0_15m_ma_launch_owner_autofill10000_20260827.md)、
[严格二筛](../p0_15m_ma_launch_owner_perfect_filter10000_20260828.md)、
[Grade-A 扩充](../p1_15m_ma_launch_owner_grade_a8000_20260828.md)。

## 建议的复用顺序

1. **先恢复人工主索引。** 以原 export、图身份、原框坐标和 Owner 方向裁决联结。保留
   原框、⭐身份和历史确认，不让 Owner 从空白重画已有框。当前 `owner_annotation_ids`
   是 stem__bN 别名，不能冒充 Label Studio 原生 task/annotation/region ID。
2. **先整理 ⭐，再覆盖完整多空方向池。** 1,345 张只是旧空头子集，另有 1,152 个已确认
   long 框值得核对当前协议。按时间和事件去重，并记录哪些已用于训练、选模板或调参。
   已用过的标杆可作训练候选、规范参考或回归检查，不得再冒充未见过的独立测试集。
3. **只把变化和分歧放进人工队列。** 原框取中央一半并限制 4–7 根，是规则派生；
   当前 4/5 根核心的类别和边界不能自动继承逐框 Gold 身份。已有正确原框可保留；
   需要改变核心位置或当前语义不一致的，显示原标注并让 Owner 保留、调整或暂不确定。
   再审核新增事件、人工与机器不一致，以及模型明显漏检/误检的例子。
4. **保留有用反例，分清“不完美”和“无目标”。** 旧背景和 Owner 否决记录值得复用，
   但拒绝一个框可能只是边界错误、等级不够或方向错误，不能自动把整图当背景。
   明确无目标、近似但不合格、有合格目标、暂不确定分别保留，避免制造互相矛盾的标签。
5. **最后才形成下一版训练候选。** 人工确认与自动弱标签分开统计，按独立事件计算样本量，
   不让同一事件的 7–8 张裁剪被误当成 7–8 份独立证据。保留正常合格形态和困难反例，
   不只收事后走势最漂亮的图。训练/验证按时间与依赖块隔离，不自动变更资格。

审核继续允许右侧未来 40 根，未来图独立存储。形态类别、边界与事后走势评价要分开；
未来看起来赚钱不能单独决定正例。要用于即时识别的输入只能看到对应决策时刻，完成态
研究样本不自动转成实盘 tip 样本。

| 工作顺序 | 上一轮交付后的默认路线 | 本轮建议 |
|---|---|---|
| 第一优先 | 4,172 个事件从空白重画 | 找回已有人工框和确认状态，先对齐旧标注 |
| 人工精力 | 对全部事件重复给出完整框 | 先修语义变化、派生核心、冲突和缺失 |
| 当前 LS 76/74 | 全量项目与候选项目 | 保留为可用任务池；本轮不删除、改名或回填 |
| 质量判断 | 自动 Grade-A 容易被当作人工精选 | 人工确认、规则派生和机器候选明确区分 |

## 本轮验证与复现

方向 CSV 与旧正例 manifest 的现存 SHA 均与 2026-08-21 谱系报告完全相同。这证明这两份
关键资产没有在此后悄悄换成别的内容；不单凭 SHA 宣称所有图像、split 与新语义已合格。

| 文件 | SHA-256 |
|---|---|
| review_sheet.csv | bb7081e7e1821c5f791486fae0f29caf18307b104bbb07156c35883781071c9a |
| positive_manifest.jsonl | 8f4119fbf634ec976077e8eb50b36e57ae3aa0471759cad04f2eaeaeacd6d21b |
| benchmark_exemplars.json | 5acc5d8f68d2671ecd9351bcfc417bc1d27fa7f201d8cd83ec1293b7b231a762 |
| gold500_short_box_ids.json | 67324c39d7c6259dd655f71b9c7114e0143eb8421c2b3aa7f50da293c32f077f |

本轮是库存与谱系核对，未产生收益或预测。因此 val AUC、收益置换 p、top-decile 毛净收益、
胜率、单特征与匹配随机入场对照不适用。谱系零假设是“名称相近的池可以互换”：用旧报告
冻结 SHA 与精确来源 ID 集合对照，而不是按名字或总数认定同一资产；当前 1,043 与旧 1,345
来源合同不同，不能互代。已有 A385 预筛的独立反证 AUC=0.4887、p=0.6352 也明确不支持
自动筛成 Gold，本轮没有重跑该实验。

以下为只读库存核对和报告转换命令；不调用历史训练或宽范围行情加载器：

```bash
cd /Users/zhangzc/fable-trading
python3 - <<'PY'
import csv, hashlib, json
from collections import Counter
from pathlib import Path
p = Path('analysis/output/owner_side_review/review_sheet.csv')
rows = list(csv.DictReader(p.open()))
print(len(rows), dict(Counter(r['owner_side'] for r in rows)))
print(min(r['cut_time'] for r in rows), max(r['cut_time'] for r in rows))
sample = [r for r in rows if r['in_sample'] == '1']
print(len(sample), dict(Counter(r['owner_side'] for r in sample)))
q = Path('datasets/owner_short_gold_center_v1/positive_manifest.jsonl')
print('short positive rows', sum(bool(line.strip()) for line in q.open()))
z = Path('data/benchmark_exemplars.json')
print('star registry count', json.loads(z.read_text())['count'])
g = Path('datasets/fixed_w10_gold500_short_ownerhn_v1/lineage/gold500_short_box_ids.json')
for path in (p, q, z, g):
    print(path, hashlib.sha256(path.read_bytes()).hexdigest())
PY
python3 scripts/md_to_html.py analysis/p1_yolo_historical_label_reuse_20260907.md --out-dir analysis/html
```

## 风险与诚实声明

- 12,565 / 6,291 来自已有完整导出审计，本轮没有重新打开全量原始 LS 标注。旧 golden_pool
  与 canonical exports 的差额仍需后续精确解释。
- 176 是登记图数量，不是 176 个已重验当前语义的核心框；原图归档存在缺失。旧 short
  精确 ⭐ 交集、原图恢复与去重训练的 71 / 69 / 70 是不同口径，不强行相加。
- Gold500 确实存在，但其 500 条方向优先样本不能称作 500 个精选正例；后来生成的固定
  W10 分类数据也不能直接复制成当前 YOLO 框。
- 旧 1,345 原人工框有价值，但派生核心仍有未逐框确认的部分。当前数据与历史数据的
  市场事件重叠、当前渲染映射和新增复标的最小数量尚未完成计算。
- 本轮没有改数据、回填 LS、训练或评估新配置；训练/生产资格仍为 false。

## 下一条允许的动作

先完成旧人工标注与当前事件的覆盖/冲突清单，给出可直接复用、仅需调整和必须新增标注
各有多少。完成清单后再安排 Label Studio 中的实际复核次序。具体保留/重分类/新核心
边界由真实 Owner 标注确认；不是以本报告自动修改标签。现有 76/74 项目和历史答案保留。
