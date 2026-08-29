# ROADMAP — 阶段与门

> 当前真相看 [`HANDOFF.md`](HANDOFF.md) 顶部。历史路线图看
> [`PROJECT_PLAN.md`](PROJECT_PLAN.md)（保留原样，记录的是当时的计划）。
> 本文件是单仓收敛后的固定阶段表。

阶段是顺序的。**代码存在不等于阶段开始**——前一阶段必须先过它自己的门。

## P0 — 语义稳定性 ← **当前**

Owner 对"完美平台/启动形态"的定义，以及重复标注下的自身一致性。

门：
- 盲重复审核的 κ 与重复一致率有数字
- 核心边界（core start/end）的定义能被第二个人复现
- 未来信息在图片生成阶段物理隔离（不是 CSS 隐藏）

**为什么先做这个**：标签的可学习性从未在盘口条件下验证过。
`datasets/label_live_tip_1000/`（1000 张右缘=tip、无后文的窗口）已就绪但从未开标，
在它被标注之前，"模型学不会"和"信息不在因果窗里"无法区分。

（2026-08-30 撤回：此处原先引用的 499/2 「画在盘口」统计已删除。那个指标算的是
框落在 200 根固定渲染窗内的位置，不是标注者是否使用了未来信息——形态出现在图中段
就必然算出「右边还有 97 根」，与判断过程无关。结论不成立，勿再引用。）

## P1 — Gold Dataset

门：
- 每个样本可逐框追溯到原 Label Studio 坐标 + owner 方向裁决
- DIRECT 抽检错误率**有数字**（现存 fixed W10 金标卡在这里：DIRECT=0，
  所以 `training_eligible=false`，见 `experiments/historical/yoyo_trading/`）
- 正负样本在"除标签之外的一切维度"上同分布，或按 source 分层各报一次
- `visible_end_at <= decision_at` 全样本通过（`yoyo/contracts/pattern.py`）

## P2 — L1 Causal Onset

门：
- 因果 onset 有人给的 warrant，不是从框右边界推的
  （`yoyo/contracts/pattern.py` 规则 4）
- Future Mutation Test 通过
- 报告 delay 3/4/5 的首次命中，precision 优先

## P3 — L2 Economic Judgment

门（三项全要，缺一即否）：
- top-decile 扣 0.2% 往返成本后净收益 > 0
- 置换检验 p < 0.01
- **跑赢匹配随机对照**（同币 × 同时间桶 × 同波动桶 × 同 horizon × 同成本）

由 `yoyo/evaluation/economic_gates.py` 执行。AUC 是参考量，不是成功标准。

## P4 — Paper Forward

门：前向 100 笔新鲜裁决；事后检出剔除；新鲜度三门同值。

## P5 — Execution

门：只加载已 promote 的冻结 bundle；真金操作逐次 owner 授权。

---

## 收敛后的当前限制

**只允许继续 P0 / P1。** 在两者通过前禁止：

```
新 YOLO 训练 · 新 Onset 模型训练 · 新 LightGBM 训练 · 多周期扩展
强化学习 · 仓位优化 · 执行层重构 · 模型 promote · 实盘替换
```

新实验一律进 `experiments/active/<experiment_id>/` 并注册到
`experiments/registry.yaml`，**不开新仓**（`yoyo-eth-v2` / `yolo-new` / `fable-next` 一律不建）。
