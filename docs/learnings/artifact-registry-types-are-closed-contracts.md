# 资产注册表类型是封闭契约，不是自由标签

- **问题**：HTML 交付物在 `artifacts/registry.yaml` 中被登记为看似清晰的 `report_html`，但注册表加载器只接受固定的 `ARTIFACT_TYPES`；任意消费者加载整份注册表时都会在到达目标资产前失败。
- **死胡同**：只校验 YAML、文件哈希和该实验的专项测试不够。它们证明了内容与登记值一致，却没有证明登记值属于跨项目契约；用更“语义化”的新字符串反而绕开了类型守门。
- **有效路径**：先让真实注册表加载器和边界测试复现，再把 HTML 仍登记为合法的 `report`，用 `role: owner_delivery` 与文件扩展名表达媒介差异。这样不扩张契约，也不需要让所有消费者理解一个新枚举值。
- **通用规则**：新增资产前先查 `yoyo.contracts.artifacts.ARTIFACT_TYPES`，类型只从封闭集合选择；需要更细的语义优先放进 `role`。登记后至少运行一次整表加载和依赖它的边界测试，不能只做 YAML/hash 检查。
- **牵连**：`artifacts/registry.yaml`、`yoyo/contracts/artifacts.py`、`yoyo/artifacts/registry.py`、`tests/boundaries/test_teacher_proposals_are_isolated.py`；任何非法行都会让整份注册表 fail-closed。
