# 一个非法 artifact 行会让整个注册表失效

- **问题**：artifact registry 中少数实验把“第几次 holdout 消耗”或“预注册阶段”写成了新的 `holdout_status` 字面量，并使用了未定义的 `bundle` 类型；注册表加载器逐行强校验，因此任何一个非法行都会阻断所有正常 artifact 的解析。
- **死胡同**：只修测试首先报出的第一种非法值。加载器会在下一条非法记录再次 fail-closed，看似出现一串不同故障，实际上是同一个全表 schema 污染问题。
- **有效路径**：先用 `ArtifactRecord.from_mapping()` 对全部记录做一次枚举式审计，再把状态归一到契约允许的 `pre_holdout` / `holdout_consumed`，把消费次数保留在 notes；图包按现有注册表惯例登记为 `dataset`。修复后重新逐行实例化全部记录，再运行依赖注册表的边界测试。
- **通用规则**：注册表变更必须在提交前验证**整张表**，不能只验证新增 YAML 可解析；描述性细节放 notes，契约字段只能使用中央 schema 的枚举。
- **牵连**：`artifacts/registry.yaml`、`yoyo/contracts/artifacts.py::ArtifactRecord`、teacher artifact 解析以及所有依赖 artifact registry 的守门测试。
