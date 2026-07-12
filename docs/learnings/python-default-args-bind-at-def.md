# Python 默认参数在 def 时绑定——猴子补丁模块常量对已定义函数无效

- **问题**：E3 实验用 `AL.FAST_SPREAD_MAX = x*0.85` 想让 `find_dense_segments`
  按缩放阈值重扫，结果所有框都被判为"core"，边界类为空。
- **死胡同**：以为补丁没执行、以为 IoU 匹配写错——都不是。
- **有效路径**：`def find_dense_segments(*, fast_max=FAST_SPREAD_MAX)` 的默认值
  在**函数定义那一刻**就求值固定了，之后改模块常量毫无作用。修法：显式传参。
- **通用规则**：要参数化第三方/同仓函数的行为，永远显式传 kwargs，不改模块常量；
  另一半教训：**实验闸门必须校验每个分类非空**——我的 gate 在空的 boundary 类上
  "差距50pp"放行，烧了 2 小时无效训练才被人工止损。
- **牵连**：scripts/e3_margin_experiment.py（已弃用其 monkeypatch 路径）、
  scripts/fit_rules_to_golden.py（正确的显式传参示范）。
