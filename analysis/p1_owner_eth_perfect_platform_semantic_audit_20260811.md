# ETH 完美平台语义审查：短延迟、多位置、不自动贴标签

> **已被同日二次修正版取代。** Owner 随后用两条竖线明确核心边界，并把确认延迟收紧为
> 3–5 根（3 优先、5 封顶）；20–30 根固定输入和 delay 6–10 均退出新训练合同。当前以
> `analysis/p1_owner_eth_shortdelay_boundary_contract_20260811.md` 为准。

日期：2026-08-11  
状态：**200 张 Owner 语义审查页已完成；尚未生成新训练标签，尚未训练新模型**

## Executive Summary

- **目标合同已经纠正。** `11根前文 + 中间6根 + 11根后文` 只是曾用于解释的一张示意，
  不是训练模板。当前合同是：20–30 根真实 K、核心约 4–7 根、位置自然分散、框后 0–10 根；
  **10 根是最大容忍延迟，不是目标延迟，越早可靠检出越好。**
- **旧资产不丢，而且找到了更正确的复用方式。** 昨晚 Stage A 的 event_id/时间 split 与旧
  `dense_owner_w20_midbox` 原始 Owner 框 2,378/2,378 完整对应。Stage A 负责宽位置表征；
  原始 manifest 恢复真实 5/7 根 Owner 框，避免继续使用机械生成的 4/5 根 Mode-C 框。
- **可复用训练期语义母池为 1,120 张。** 它们全部来自 Stage-A train、窗口 20–30 根、原始
  Owner 框 5/7 根、框后 0–10 根；358 张 Stage-A val 全部隔离，没有参与筛选。
- **现在不能直接训练。** 1,120 张只是“过去 Owner 框 + 正确几何/时间合同”的候选母池，尚未
  按今天 ETH 终极目标重新确认语义。已生成 200 张可交互审查页；在 Owner 裁决前全部标记
  `semantic_status=unreviewed / training_eligible=false`。

[查看最新训练与延迟合同示意图](../reference/owner_ethusdt_15m_semantic_delay_contract_20260811.png)

## 固定坐标不是标签，完美平台语义才是标签

Owner 的目标不是训练出一个“看到红框在某个 X 坐标就开火”的模型，而是让 YOLO 在不同真实
上下文中都能识别同一种完美平台。窗口长度、框位置和后文根数是模型必须抵抗的干扰变量；
核心平台是否成立才是正例语义。

因此本轮明确撤销两条错误做法：

1. 不再用“框中心 40%–60%”筛正例；框可以自然偏右、中右或靠中，但不能整批固定在一个位置。
2. 不再用“框后 8–12 根”筛正例；0 是最早检测，10 是最大容忍，超过 10 不进入当前母池。

同样，本轮没有按后续跌幅、未来收益或昨晚模型置信度给候选排序。后面已经走出来只能帮助测
延迟，不能反过来自动证明红框里的平台语义正确。

## 原始 5/7 根 Owner 框比 Stage A 机械 4/5 根更接近目标

| 证据 | 结果 | 影响 |
|---|---:|---|
| Stage A 事件数 | 2,378 | 修复后的 event_id、时间切分与 pre-holdout 资格继续复用 |
| 与旧 Owner manifest 联结 | 2,378 / 2,378 | 无 orphan、无重复，能够恢复原始框语义 |
| Stage A 当前框宽 | 4 根 1,188；5 根 1,190 | 来自 `anchor-2..decision` 机械规则，不是原始 Owner 框 |
| 原始 Owner 框宽 | 5 根 1,249；7 根 1,129 | 完整覆盖 Owner 早先真正画过的两种核心宽度 |
| 当前训练期、delay≤10 母池 | **1,120** | 可进入语义复核，但尚不能直接成为新训练金标 |

这是本轮最重要的数据质量发现：昨晚 Stage A 权重仍然有价值，因为它通过了位置不变性诊断；
但下一轮精调不应继续把机械 4/5 根框当最终语义。正确做法是用 Stage A `best.pt` 作初始化，
并把原始 5/7 根 Owner 核心重新置于修复后的时间 split 下。

## 1,120 张母池支持短延迟和多位置，不需要固定中间

框后根数在 0–10 之间接近均匀覆盖：

| 框后真实 K | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 训练期候选 | 95 | 101 | 99 | 94 | 107 | 115 | 107 | 95 | 120 | 86 | 101 |

框中心自然位置分布：

| 位置带 | 数量 | 占 1,120 |
|---|---:|---:|
| 中点左侧 | 123 | 10.98% |
| 中间带 | 360 | 32.14% |
| 右侧带 | 376 | 33.57% |
| 远右带 | 261 | 23.30% |

这些位置不是人为验收阈值，只用于证明母池没有被固定成“全部最右”或“全部正中”。短延迟样本
自然更偏右，长一点的确认样本自然向中部移动；训练目标是让模型跨位置保持语义识别，而不是
强行把各带变成相同数量。

## 200 张审查页已经把“语义确认”与“位置/延迟覆盖”分开

审查页采用可复现的 `delay × box-width × position` 分层抽样，不做形态自动评分：

| 审查组 | 数量 | 用途 |
|---|---:|---|
| 0–2 根后文 | 80 | 优先审查最早可见形态，避免默认依赖后文 |
| 3–5 根后文 | 70 | 审查短确认后是否更稳定，同时保持较低延迟 |
| 6–10 根后文 | 50 | 覆盖难例和最大容忍区间，不把它当默认目标 |
| 合计 | **200** | 110 个币种，2025-06-08 至 2026-03-18 |

页面支持“完美平台 / 接近待定 / 不是”、分组筛选、localStorage 暂存和 JSON 导出。导出的
Owner 选择才是后续语义标签；当前候选分组本身不是标签。

审查页：`analysis/output/owner_eth_target_review_v1/index.html`。

## 数据质量裁决

| 发现 | 证据 | 严重度 | 置信度 | 裁决 |
|---|---|---|---|---|
| 固定居中 + 8–12 后文合同错误 | Owner 明确 10 为最大延迟，位置不得固定 | Critical | 高 | 已撤销并更新活文档 |
| Stage A 框宽丢失部分原始语义 | 4/5 机械框 vs 5/7 Owner 原框 | High | 高 | 精调恢复原始 5/7 框 |
| 位置 shortcut 已明显改善 | Stage A 四桶 recall spread 14.72pp、Spearman=-0.134 | Medium | 中高 | 保留 Stage A `best.pt` 和宽位置 replay |
| 精确度仍不可用 | conf=0.10 precision 22.66%；conf=0.20 precision 30.67% 但 recall 13.97% | High | 高 | 需语义清洗 + hard negatives，不能抬阈值解决 |
| 当前母池语义纯度未知 | 200/1,120 尚未 Owner 复核 | Critical | 高 | Owner 导出裁决前禁止训练 |
| hard negatives 缺失 | Stage A hard negative=0 | High | 高 | 语义正例确认后再构建匹配难负例 |

## 下一步：先完成语义裁决，再做单变量精调

1. Owner 打开 200 张审查页，优先看 0–2 根后文组，并导出 JSON。
2. 导入裁决后，按 delay、框宽、位置和时间段统计 `yes/no/uncertain` 纯度；如果某组语义污染
   明显，修的是标签来源，不是随手改 YOLO 阈值。
3. 构建新的 precision fine-tune 集：确认正例使用原始 5/7 根框；保留一部分 Stage A 宽位置
   replay，防止重新学会最右/正中 shortcut；负例按相同窗口、延迟和位置分布匹配。
4. 第一轮只加入经过确认的 hard negatives，固定 Stage A `best.pt`、seed 和训练配方，遵守
   单变量纪律。
5. 在独立 pre-holdout 时间块渲染 delay 0..10 的同事件序列，报告每一档 event precision、
   recall、FP/1000 和首次命中 delay；选择满足精确度要求的最短延迟，而不是把 10 写死。

## Further questions

- 当前 ETH 参考明确展示空头启动。如果“完美平台”还包括多头镜像，后续必须明确是同一类的
  镜像归一化，还是两个独立类别；不能在没有声明的情况下把多空语义混进一个正类。
- precision 的最低验收值仍需在新训练前由 Owner 冻结；本轮只确认“精确度优先”，没有擅自
  发明数值门。

## Caveats and assumptions

- 本轮只读取既有 manifest、标签与 pre-holdout PNG；没有打开原始行情、holdout、Stage-A val
  图片、模型权重或未来收益。
- 旧 `dense_owner_w20_midbox` 的历史 split 和 246 条 holdout 资格问题仍然存在；本轮没有直接
  恢复该数据集训练，而是只取与修复后 Stage-A train event_id 对应的 1,120 张候选。
- 200 张代表抽样能测语义纯度和分组污染，但不能在 Owner 裁决前声称整个 1,120 张母池已经
  合格。
- AUC、置换检验、top-decile 收益、胜率和匹配随机对照组均为 N/A：本轮是 YOLO 检测层的
  数据语义审查，不是判断层或方向性回测。
- `production_eligible=false`；未训练新模型、未读取 holdout、未 promote、未部署、未下单。

## 复现命令

```bash
python3 scripts/build_owner_eth_target_review.py
python3 scripts/md_to_html.py \
  analysis/p1_owner_eth_perfect_platform_semantic_audit_20260811.md \
  --out-dir analysis/html
```

机器可读产物：

- `analysis/output/owner_eth_target_review_v1/summary.json`
- `analysis/output/owner_eth_target_review_v1/contract.json`
- `analysis/output/owner_eth_target_review_v1/candidates.jsonl`
- `analysis/output/owner_eth_target_review_v1/candidates.csv`
