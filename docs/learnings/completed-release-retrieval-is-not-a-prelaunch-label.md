# 完成态检索命中不等于启动前标签——先量锚点已经走了多远

- **问题**：15m 双均线密集启动检索用 `t` 及之前的数据提出候选，再用
  `t..t+11` 的完成路径给候选排序。1000 个多空候选全部通过了因果候选门，图上也普遍有
  明显的压缩后释放；但这仍不能回答训练核心是否结束在启动之前。

- **死胡同**：把“候选门不看 `t` 之后”和“这张图可直接作为启动前正例”当成同一件事。
  前者只证明实时到 `t` 可以复算出候选，完成路径排序还会主动把强释放排到前面；它没有证明
  `t` 是蓄势核心的右边界。只抽看头部图也会漏掉排序中尾部的系统性锚点偏晚。

- **有效路径**：在标注前增加独立的锚点时序审计，只读完成的 `t` 及之前 K 线，计算
  `direction × (open[t] - close[t-k]) / ATR14[t]`（`k=3/6/12`）以及
  `direction × (close[t] - open[t]) / ATR14[t]`。全量 1000 张的结果是：
  **40.4% 在 `t` 前 3 根已移动超过 1 ATR，67.3% 的 `t` 本身实体超过 1 ATR**；
  因此这一批只能保持 `PENDING` 人工候选池，不能自动转成训练正例。

- **通用规则**：任何“先因果提议、再用未来完成态排序”的检索，落训练集之前都先量
  **锚点前位移 + 锚点实体**，并让 owner 明确核心右端是否允许包含释放 K。
  因果提议、事后好结果、训练标签是三层不同资格，不能相互代替。

- **牵连**：`scripts/audit_15m_candidate_prelaunch.py`、
  `experiments/active/exp-15m-ma-launch-candidate1000-v1/results/prelaunch_audit.json`、
  `experiments/active/exp-15m-ma-launch-candidate1000-v1/results/review_manifest.jsonl`。
  与 [缩短窗口不等于因果化](window-length-does-not-control-future-visibility.md) 同属“先钉住时间右端”问题。
