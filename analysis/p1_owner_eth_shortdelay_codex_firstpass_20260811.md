# P1 Owner ETH 短延迟语义 Codex 一审（2026-08-11）

## 结论先行

基于Owner当前唯一明确的ETH空头参考，已逐张复核30张动态短窗校准样本，并形成保守四桶：

| 一审桶 | 数量 | 含义 |
|---|---:|---|
| `short_keep` | 5 | 空头平台形态较干净，旧核心暂可保留 |
| `short_rebox` | 4 | 空头形态可参考，但旧框吃进启动K；已提出新边界 |
| `short_hard_negative` | 4 | 双向波动、核心过乱或无清晰启动，不符合precision-first目标 |
| `mirror_unconfirmed` | 17 | 多头镜像；当前既不是正例也不是负例 |

这不是Owner金标。全部30张仍为`owner_confirmed=false`、`training_eligible=false`、
`production_eligible=false`。本轮价值是把需要Owner回答的问题压缩成一张16例代表板，而不是
擅自扩训练集。

## 一审依据

只使用Owner ETH参考已经表达的语义：

1. 核心是均线密集附近的平台/拒绝/转折段；
2. 核心约4–7根，明显快速启动K应落在框外；
3. 核心后只看3–5根确认，3优先、5封顶；
4. 优先精确度，双向大K、核心过乱或没有清晰离开均降级；
5. 后续涨跌只能作确认上下文，不能反向自动定义正例。

未写任何均线距离、K线幅度或收益阈值。每张是视觉一审，不是规则扫描。

## 为什么增加镜像隔离桶

当前参考只明确了空头形态。若把17张多头镜像混进同一正类，会把红绿K线和方向语义混在一起；
若把它们当空头负例，又会让模型对相同平台结构接受冲突监督。因此在Owner明确下面三种策略前，
它们不进入任何训练集合：

- 仅训练空头单类，多头排除；
- 多头与空头分成两个类别；
- 做有明确颜色/方向变换合同的归一化同类。

## 改框结果

4张`short_rebox`均保留原有pre与post合同，只把核心右端前移，使明显启动侧K进入3–5根确认区。
修正后核心宽仍为4–7根。代表板中：

- 绿色实框：暂保留空头核心；
- 橙色实框：Codex提出的新核心；
- 红色虚框：旧核心中仍可见的启动侧部分；
- 红叉：难负例候选；
- 紫框：多头镜像隔离。

## 数据与安全

| 项目 | 结果 |
|---|---:|
| 输入校准样本 | 30 |
| 一审覆盖 | 30/30 |
| post范围 | 3–5 |
| 改框后core范围 | 4–7 |
| holdout行物化 | 0 |
| Owner确认 | 0/30 |
| 可训练 | 0/30 |

原始CSV继续按每张图最终所需bar做前缀读取；未读取Stage-A val图/标签、模型权重、后续收益或
holdout。未改阈值、成本、障碍、新鲜度、ACTIVE，未promote、未部署、未下单。

## 非适用指标

本轮未训练、未推理、未回测，因此AUC、mAP、置换p值、收益、胜率、匹配随机对照均不适用。
一审数量不是模型precision，也不是母池正类率。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/review_owner_eth_shortdelay_calibration.py

PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_review_owner_eth_shortdelay_calibration.py
```

产物：

- `analysis/output/owner_eth_shortdelay_codex_firstpass_v1/first_pass.jsonl`
- `analysis/output/owner_eth_shortdelay_codex_firstpass_v1/summary.json`
- `analysis/output/owner_eth_shortdelay_codex_firstpass_v1/representative_semantic_calibration16.png`

## 风险与诚实声明

- Codex一审可能理解错Owner的细微语义，尤其`short_keep`与`short_hard_negative`边界。
- 4张新框只是逐图proposal，不应被抽象成固定偏移参数。
- 当前方向范围未最终冻结；17张镜像不能在决策前进入二分类。
- 只有Owner确认后的样本才能进入正式训练集，且仍需独立时间切分与难负例匹配。

## 下一步Owner门

Owner只需先看16例代表板并回答两件事：

1. 绿/橙/红三类对空头“完美平台”的理解是否正确；
2. 多头镜像选择排除、独立类别，还是方向归一化同类。

确认后才把该语义扩到200张审查池，并生成正式短窗正例与同时间块难负例；不得在确认前训练。
