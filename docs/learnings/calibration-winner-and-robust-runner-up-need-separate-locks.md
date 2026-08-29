# 校准第一名与稳健第二名必须分开锁定

- **问题**：六均线 16-bar 门按 2023 两个半年的最差收益胜出，但锁定后 2024 年收益从基线
  109.26% 降到 43.54%；同一 2023 排名的第二名 8-bar 门反而在四个半年都为正。
- **死胡同**：看到第一名校准 PF 高、回撤低就只保存 winner，或看完 2024 后把第二名改称“最优”。
  前者丢掉参数敏感性，后者用确认期重新选参，都会夸大可复现性。
- **有效路径**：排名只使用 2023，预先保存 winner 与 runner-up 两条锁定配置，再一次性展示 2024；
  16-bar 按原选择规则诚实判为跨期失败，8-bar 只能作为 2023 已锁第二名的前向候选。
- **通用规则**：离散窗口/阈值搜索至少保留 winner、runner-up 和邻域表；确认期可以淘汰它们，不能重排
  它们。若第二名更稳，只能建立新的 forward hypothesis，不能回写历史最优。
- **牵连**：`cross_count_gate_search.csv`、`cross_count_tbsl_optimization.json`、
  2023 calibration / 2024 already-inspected development check、holdout 禁读。
