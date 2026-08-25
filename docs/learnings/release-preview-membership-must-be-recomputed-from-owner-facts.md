# Release preview 的成员资格必须从 Owner 事实重算

- **问题**：P1 release planner 收到的 `review_joined.jsonl` 已带
  `eligible_for_later_owner_release_preview`。若直接按这个布尔值取行，一条错误的 `false` 就会让
  Owner 已确认的 SHORT KEEP 静默漏出 release preview；错误的 `true` 则可能把 LONG 或非 KEEP
  带进数据集计划。

- **死胡同**：把上游 summarizer 已通过测试等同于下游可以信任派生布尔值。哈希只能证明收到的是
  哪些字节，不能证明派生字段仍与 `direction`、`decision` 和几何确认事实一致；下游门若不重算，
  就把一次上游 bug 变成可传播的训练谱系错误。

- **有效路径**：release planner 从原子事实重新计算成员条件：仅
  `direction=SHORT AND decision=KEEP` 可以进入 preview，并同时重算几何确认、方向协议状态以及
  非 KEEP 必须无几何。上游布尔值只作为一致性断言；与重算结果不同就 fail closed，而不是据它
  选择成员。

- **通用规则**：跨阶段传递的 eligibility、guard、release、split 等布尔字段都不是新的事实源。
  下游在边界处必须用最小原子事实重算，再把上游字段当校验和；尤其是会改变正负样本集合的字段。

- **牵连**：`yoyo/datasets/candidate_dataset_release.py`、
  `tests/test_candidate_dataset_release.py`、SHORT-only P1 release 门；相关：
  [upstream-guard-flags-must-be-recomputed-not-trusted.md](upstream-guard-flags-must-be-recomputed-not-trusted.md)。
