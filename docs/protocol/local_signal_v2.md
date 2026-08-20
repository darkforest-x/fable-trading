# Local Signal V2 研究规格

> **Owner 2026-08-11 口径，逐字保留，未经 owner 批准不得改写。**
> 从 `CLAUDE.md` 铁律 12 搬出（2026-08-20）：那条规则当时有 30 行，
> 混了实盘纪律、本研究规格、以及一段历史勘误——三类东西各有各的生命周期。
> **纪律留在 CLAUDE.md，规格在这里。** 铁律 12 保留指向本文的指针。
>
> 这里写的是**这项研究怎么做**；`CLAUDE.md` 写的是**什么绝对不能做**。
> 两者冲突时以 CLAUDE.md 为准。

## Owner 口径原文

**Local Signal V2 研究主目标已改为短延迟事后形态检测**：标签是“完美平台/启动形态”语义，
不是固定裁剪模板。Owner 的 ETH 参考中，核心约 4–7 根，边界是两条竖线之间的平台/转折段；
红框不得包入右侧快速下跌。输入窗口不固定为 20–30 根，必须从最短充分上下文开始动态变化；
当前首轮只试约 14–22 根，并继续按 precision 向更短收缩。核心结束后只允许 **3–5 根**确认：
3 根优先，5 根为硬上限，6–10 根撤出。红框位置随最短充分上下文自然变化，不得固定最右或
正中。验收分别报告 delay 3/4/5 的首次命中，精确度优先。不得因为后面已经上涨/下跌就自动
把核心框判成正例。旧 W20–30 派生框只能作复核来源；但能够逐框追溯到Label Studio坐标、
又经Owner亲自确认short方向的原始金标，是新合同的几何源。外层可重裁，内框只能按Owner批准
的中心截取规则从原坐标派生，禁止Codex或模型二次目测重画；未经Owner确认裁切合同不得训练。
Stage A 数据和权重继续保留作表征底座。该研究不再以严格因果 Stage B + 真 tip 作为离线检测器唯一验收；但
`production_eligible=false`，直到 owner 单独批准生产用途。今天 ETH 图是语义参考尺，不是
坐标模板，不得据此删除或替代既有 Stage A 数据、权重、日志和候选池。当前ETH参考只冻结
空头语义；Owner未明确多头镜像策略前，一律标为`mirror_unconfirmed`，既不进正例也不进负例。
Owner确认类别协议/代表板只设置`owner_protocol_confirmed=true`，不得批量推导
`sample_owner_confirmed=true`；协议级确认与逐样本金标必须分层记录。

## 与铁律的关系

本规格受 `CLAUDE.md` 铁律 12 约束，不能覆盖它：

- 实盘执行路径**仍只扫 tip / tip-1 / tip-2 因果窗**
- 任何使用核心形态之后 K 线的模型**不得冒充新鲜盘口信号**，
  不得直接进入 tip-smoke、forward、ACTIVE 或部署
- 本研究的全部产物 `production_eligible=false`，直到 owner 单独批准生产用途

## 相关 learnings

- `docs/learnings/dynamic-recrop-does-not-repair-label-semantics.md`
- `docs/learnings/original-gold-geometry-beats-secondary-manual-reboxing.md`
- `docs/learnings/unconfirmed-mirror-is-neither-positive-nor-negative.md`
- `docs/learnings/protocol-confirmation-is-not-sample-confirmation.md`
- `docs/learnings/human-review-future-context-must-be-physically-separated-from-training-input.md`
