# Owner 要看的预标包不能偷换成研究校准混合包

- **问题**：Owner 要“v10 有框的 200 张图确认效果”，研究方案同时需要随机背景、未来发现候选、隐藏预框和盲重复来测可学习性；两者都叫“校准/预标”时容易被错误合并成一个 HTML。
- **死胡同**：把 216 个混合来源事件和 24 个盲重复直接作为 Owner 审版交付。虽然实验设计更完整，但大多数页面没有 v10 红框，违背“只看 teacher 预标效果”的明确产品口径；额外方法论反而遮住了用户要判断的对象。
- **有效路径**：把产物拆成两层：Owner-facing preview 只含 v10 实际检出的图片，所有预框可见且视觉完全统一；随机背景、来源配额、重复一致性和 outcome 数字只留后台或另立 Gate A 实验。用户先确认 teacher 框和界面，再决定是否导入 Label Studio。
- **通用规则**：接到“给我看 N 张某模型的预标”时，交付集合必须满足 `task_count=N AND every_task.has_model_box=true`；任何对照、空白、隐藏干预或其他候选源都不得混入主 HTML，除非用户明确要求实验包。
- **牵连**：`scripts/build_eth_3m_v10_prebox200.py`、`datasets/eth_3m_v10_prebox200/`、`analysis/p_eth_3m_v10_prebox200.md`；被否决的混合预览 `analysis/output/eth_3m_calibration240_preview/` 仅留审计，不得导入 Label Studio。
