# P1 · 15m 均线密集启动 10,000 正例 + 30,000 负例 YOLO 数据集

日期：2026-08-27（UTC+8）

实验：`exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2`

数据构建代码：`a4315a6952a50ccd555dd365ae585ff988bee504`

扩容审计代码：`e76bc7d43ea48297f5b48d117b067f273a03372e`

## 结论先行

Owner 的“对啊，那你应该搞 3w 张啊”已完成为一套新的、未覆盖 v1 的本地 YOLO 数据集：

- 正例 10,000 张，LONG 5,000 / SHORT 5,000；
- 负例 30,000 张，hard 19,922 / easy 10,078；
- 旧版 10,000 张负例全部保留，并新增 20,000 张互不重叠的负例；
- 共 40,000 张 1280×742 无框、无损 PNG，40,000 个独立标签文件；
- 训练可见 train 32,644 张、val 7,260 张；切点 purge 内 96 张仅保留谱系，不暴露给 `data.yaml`；
- 全量重新解码 40,000 张图片、核验 80,000 个文件；图片 SHA 40,000/40,000 唯一，负标签 30,000/30,000 字节为空，模型底图精确红框像素为 0；
- v1 的 10,000 正例 + 10,000 负例共 20,000 行，在 v2 中图片 SHA 与标签 SHA 全部逐行一致；
- 每个正例均有 slot 1/2/3 三个不同负窗，30,000/30,000 同币、同源文件、同半年、同 split、同窗口几何；378 个源文件内所有负样本依赖区间互不重叠；
- 重新保护全部 14,117 个严格正候选，holdout OHLCV 读取 0；训练、3060、权重、ACTIVE、frozen、forward、部署、交易状态变更均为 0。

训练入口：`datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2/data.yaml`。

负样本实际输入抽样：`experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results/actual_negative_inputs_seed50_added50.html`。前 50 张来自保留的 v1 负样本，后 50 张来自新增 20,000 张；两组都按 hard/easy 各 25 张确定性抽样，页面直接读取数据集实际 PNG。

本轮只修正负样本数量并构建数据集，**没有启动训练**。正标签仍是 Owner 对整批的使用授权，不冒充逐样本 Gold，因此 `training_eligible=false / production_eligible=false` 保持不变。

## 与上一版同表对照

| 项目 | v1：10k 正 + 10k 负 | v2：10k 正 + 30k 负 | 变化 |
|---|---:|---:|---:|
| 正例 | 10,000 | 10,000 | 0，图片与标签逐字节不变 |
| LONG / SHORT | 5,000 / 5,000 | 5,000 / 5,000 | 0 |
| 负例 | 10,000 | 30,000 | +20,000 |
| hard / easy 负例 | 4,999 / 5,001 | 19,922 / 10,078 | +14,923 / +5,077 |
| train 正例 | 8,161 | 8,161 | 0 |
| train 负例 | 8,161 | 24,483 | +16,322 |
| val 正例 | 1,815 | 1,815 | 0 |
| val 负例 | 1,815 | 5,445 | +3,630 |
| excluded 正 / 负 | 24 / 24 | 24 / 72 | 每个正例仍按 1:3 保留谱系 |
| `data.yaml` 暴露图片 | 19,952 | 39,904 | +19,952 |
| 实际图片 / 标签文件 | 20,000 / 20,000 | 40,000 / 40,000 | 各 +20,000 |
| 数据集字节数 | 951,638,666 | 1,873,726,866 | +922,088,200 |
| holdout OHLCV | 0 | 0 | 0 |
| 训练 / 权重 | 0 / 0 | 0 / 0 | 0 |

## 3 万负样本怎么组成

v2 不是重新随机洗一套 30,000 张：

1. 读取并锁定 v1 的 `negative_plan.jsonl`，SHA256 为 `32d9de2752c5764e42a6014dd8497ebfd180e8455fe78f4670c6bebaa48ef7dc`；
2. 把 v1 每个正例已有的负样本作为 slot 1，重新计算 hard/easy 特征、split、窗口几何、正候选保护和区间占用，不能只因为旧 JSON 哈希正确就直接信任；
3. 在相同 source / symbol / calendar half-year / split 内，为同一正例再找 slot 2、slot 3；
4. 新负窗与所有严格正候选、已选正例、旧负窗、新负窗均保持隔离，负窗依赖区间两侧再留 2 根 K；
5. 每张负图沿用对应正例的 core 根数、前文根数、后文根数，因此 hard/easy 不会靠不同图幅或 K 线宽度形成捷径。

负标签的 no-launch 门保持 v1 原值：core 后 +2/+3/+5 的绝对收盘进度分别不超过 0.85/1.10/1.35 ATR，未来 1–5 根双向 high/low excursion 不超过 1.75 ATR。未来 K 只用于判定负标签，渲染窗口及依赖区间均严格停在 holdout 前。

目标配额是每个正例 2 hard + 1 easy。安全约束优先于种类比例；局部 hard 容量不足时，只允许在完全相同匹配块内用已冻结定义的 easy 回退，不能缩小金标禁入区、降低 no-launch 门、跨币、跨半年或复用窗口。

| split | hard | easy | hard 占比 |
|---|---:|---:|---:|
| train | 16,303 | 8,180 | 66.59% |
| val | 3,572 | 1,873 | 65.60% |
| excluded | 47 | 25 | 65.28% |
| 全部 | 19,922 | 10,078 | 66.41% |

按每个正例的三个负窗看：9,938 个正例精确拿到 2 hard + 1 easy；46 个为 1 hard + 2 easy；16 个为 0 hard + 3 easy。后两类共消耗 78 次 hard→easy 安全回退。YOLO 训练把每张负图视为独立空标签样本，不使用 triplet 损失；因此配对的作用是控制同币、时间块、split 与窗口几何分布，而不是要求每一组三张在损失函数中绑定。全局及 train/val 的 hard 比例仍超过预注册的 60% 下限。

## 正例完全没有变化

v2 仍从原 OHLCV 调用同一个 `render_chart` 重新生成 10,000 张干净 PNG，并使用原审核 manifest 的 `cx/cy/w/h` 写独立 YOLO 标签。验证分两层：

- 正确标签框临时叠回干净图后，10,000/10,000 与 Owner 已认可审核 PNG 逐字节一致；
- v2 对照 v1 数据集 manifest，10,000 张正图和 10,000 个正标签的 SHA 全部一致。

因此没有重新框、移动框、压缩图、缩放图或改变红绿 K 线/均线颜色。v1 的 10,000 张种子负图及其空标签也同样 10,000/10,000 SHA 一致。

## 时间切分与无前视

- 全局 cutoff：2025-12-01 00:00 UTC；
- 两侧各 purge 150 根 15m K（37.5 小时）；
- split 判断覆盖完整渲染窗口 + 负标签最晚 core+5 的依赖区间；
- train：8,161 正 + 24,483 负；
- val：1,815 正 + 5,445 负；
- excluded：24 正 + 72 负，不被 `data.yaml` 暴露；
- 时间范围仍为 2021-09-03 至 2026-05-03，229 个币、378 个源文件；
- holdout 起点 2026-05-04，实际读取 0 行。

正例检索和负标签都使用 core 后已完成行情，因此这是 completed-history 视觉任务，不能冒充 tip / tip-1 / tip-2 新鲜盘口检测器，也没有进入 forward、ACTIVE 或部署。

## 全量 QA 与零假设对照

| 检查 | 结果 |
|---|---:|
| 实际图片解码 | 40,000 / 40,000 |
| 1280×742 | 40,000 / 40,000 |
| 图片 SHA 唯一 | 40,000 / 40,000 |
| 图片 + 标签文件 SHA | 80,000 / 80,000 |
| 正例标签可解析 | 10,000 / 10,000 |
| 负例标签字节为空 | 30,000 / 30,000 |
| 模型输入精确红框像素 | 0 |
| 正确框叠回匹配审核 PNG | 10,000 / 10,000 |
| v1 图片/标签字节谱系一致 | 20,000 / 20,000 |
| 每个正例 slot 1/2/3 齐全 | 10,000 / 10,000 |
| 同源/币/半年/split/几何 | 30,000 / 30,000 |
| 源文件内负依赖区间互斥 | 378 / 378 |
| 保护严格正候选 | 14,117 |
| holdout OHLCV | 0 |
| 本仓正式测试 | 1,715 passed / 4 skipped |

这是非方向性数据集审计，没有预测分数或交易收益，因此 val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征基线以及同币×时间块×波动桶随机入场对照均不适用，不能编造。

同等严格的零假设对照有两项：

1. 对 1,000 张正图循环使用下一张图的错误框，叠回后匹配审核 PNG 为 0/1,000；正确框为 10,000/10,000。说明正例一致性来自逐图真实几何，不是“同一渲染器总能对上”。
2. 把“代码相同所以旧数据应该相同”视为未经验证的假设，实际逐行对比 v1/v2 图片与标签 SHA；20,000/20,000 完全相同，才接受“只增加负样本、旧数据未变”的结论。

## 数据与产物

- 数据目录：`datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2`；
- `data.yaml` SHA256：`94376651f00a7dc5be3192f181109d9b67d4fed92931c5af8c70cf0b5787ef25`；
- manifest SHA256：`6e601034ab15765a74b788cc6d094e9326c3044c1fb615c908ef9de897d6e0af`；
- negative plan SHA256：`4c2a48174636f02998f65f8d53ca9bd986375ddd668144401c1d667a8fb2cb86`；
- build receipt SHA256：`1deeebc93c94902a67ef1dcdcad0c4593a53b7e7627db3778b7541d2ceb8766a`；
- expansion audit SHA256：`347343455e14872e23c410292a97b0dd5915be9f9217137a1a0e2ab748349d5d`；
- 数据集字节数：1,873,726,866（`du -sh` 约 1.9G）；
- 实际负样本抽样 HTML：`results/actual_negative_inputs_seed50_added50.html`；
- 新增 20,000 张中的 50 张联系表：`results/actual_added_negative_inputs_sample50.jpg`。

大体积 PNG、标签、manifest、逐行 plan 与 JPG 联系表均为本地可重建产物，不进 git；预注册、紧凑 receipts、`data.yaml`、抽样 HTML、本报告与注册记录进 git。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

# 构建器和预注册必须先提交在 main；未提交时会 fail-closed。
.venv/bin/python -m pytest -q tests/test_ma_launch_owner_yolo_dataset.py

# 在干净副本或新的空输出路径中先规划 3 万负样本。
.venv/bin/python scripts/build_15m_ma_launch_owner_yolo_dataset10000.py \
  --prereg experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/preregistration.json \
  --results experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results \
  --dataset datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2 \
  --plan-only

# 读取冻结 plan，生成 4 万张图片和 4 万个标签并全量验收。
.venv/bin/python scripts/build_15m_ma_launch_owner_yolo_dataset10000.py \
  --prereg experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/preregistration.json \
  --results experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results \
  --dataset datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2

# 独立复核 1:3 配对、区间隔离、旧/新增谱系并生成实际输入抽样。
.venv/bin/python scripts/audit_15m_ma_launch_owner_yolo_neg30000.py

# 全仓正式测试与报告转换。
.venv/bin/python -m pytest -q tests
python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_owner_yolo_neg30000_20260827.md \
  --out-dir analysis/html
```

构建器拒绝覆盖已有 final dataset，复现应在干净副本或新的显式空目录执行；不能删除当前唯一完成产物来“试跑”。若本地缺少 v1 的 seed plan，应先按 v1 报告重建其冻结计划，并要求 SHA 与预注册值一致。

## 风险与诚实声明

1. **正例仍是批量授权 weak labels，不是逐样本 Gold**；扩负样本没有提升正框语义本身的确认层级。
2. **负例也是规则弱标签**；它们严格满足 frozen no-launch 与隔离门，但没有逐张 Owner NO 裁决。
3. **78 个 hard 配额安全回退为 easy**；其中 16 个正例的三张配对负图均为 easy。没有为追求漂亮的 20,000/10,000 精确比值而放松安全门。YOLO 不使用配对 triplet 损失，但若以后改成 pair-aware 训练，需要重新考虑这 62 组偏离配额的样本。
4. **正例纵向长框风险原样保留**：136 张 `h_norm > 0.5`，其中 7 张大于 0.7，最大 0.9043。为保证 v1/v2 字节一致，本轮没有二次裁框。
5. **不是新鲜盘口模型数据**：正例保留 core 后 K，检索及负标签看至 core+5；不得进入 tip/forward/ACTIVE。
6. 本轮没有训练、没有上传 3060、没有生成权重、没有评估 holdout、没有 promote 或改生产状态。

## 下一步

本轮目标“3 万负样本”已经完成，不需要 Owner 再做人工逐图审核。若下一步要训练，应另开训练实验并明确授权；在此之前建议直接打开实际输入抽样 HTML，确认看到的是 v1 保留负样本与 v2 新增负样本各 50 张，而不是带框展示图。
