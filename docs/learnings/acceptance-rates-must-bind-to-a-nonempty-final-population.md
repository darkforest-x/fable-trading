# 验收比例必须绑定非空的最终总体与逐条证据

- **问题**：同一个 dataset manifest 引用了最终 Gold 的 SHA，却用迁移前 rows 计算出
  `DIRECT=0`；实现又把 `n_direct == 0` 当作 15% 抽检门通过，导致错误总体和空总体都能看起来
  “只差一个错误率”。
- **死胡同**：继续在旧报告里补一个 `direct_error_rate` 标量没有用。它既不能证明被审的是
  哪些样本，也不能证明这些样本属于最终 DIRECT，总体换了仍可能沿用同一个数字。
- **有效路径**：验收只加载一次最终 Gold；盲审 scorer 输出逐条 `gold_id + review_label`，门禁
  回连当前快照后自己重算分子、分母和归属。总体为 0、快照内容不同、非 DIRECT 声称计入
  DIRECT、行缺失或重复都 fail-closed；Owner 批准再设为独立的最后一道门。
- **通用规则**：任何“错误率/覆盖率/抽检比例”先问三件事：总体是哪份内容哈希、分子能否逐条
  回连、总体为空时是否明确失败。缺一项就不是可晋级证据。
- **牵连**：`yoyo/datasets/legacy_gold_migration/audit.py`、fixed-W10 final Gold、blind review
  evidence、`training_eligible` Owner 门；不得读取 holdout。
