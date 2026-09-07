# 报告表面契约必须用真实验证器预检

- **问题**：V39 的纯 Markdown canonical report 包在合成测试中保留了全部正文，却被真实验证器拒绝：report 表面至少需要一张 chart block。
- **死胡同**：沿用字段形状和用 mock 检查正文完整，不等于满足实际交付契约；数据和回测正确也不能替代呈现层验证。
- **有效路径**：保留早期包与拒绝收据，新增独立 delivery 构建器，从已保存交易核对半年均值后增加有用的稳定性对照图。先提交新构建器，再生成 artifact_final；不重跑经济结果、不覆盖失败证据。
- **通用规则**：选定表面后先将最小真实 payload 送入实际校验器。只对字段和 mock 做单测，容易漏掉表面级必需项。
- **牵连**：`owner_k1k2_delayed_entry_delivery.py`、V39 原 `artifact.json` 与新增 `artifact_final.json`，源 MD 最终复现命令同步更新。
