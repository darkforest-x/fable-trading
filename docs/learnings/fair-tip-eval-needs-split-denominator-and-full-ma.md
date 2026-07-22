# tip 公平验收要分母拆开 + full-MA，不能靠 slice tip_hit 翻案

- **问题**：Owner 质疑 tip 验收冤枉 pad200/v15；旧 tip_hit（slice-MA）与 tip-smoke（27 币任意 tip）尺子不公。
- **死胡同**：只改 tip_hit 为 full-MA 就想给 v15 翻案——full-MA 下 v12 也从 0.925→≈0，相对排名变了但 v15 仍不着火；或继续用无条件 0/27 当唯一否决。
- **有效路径**：同一 live 渲染（full-MA）上，用 Owner 已认真 tip 小样把分母拆成应开火 / 空背景 / noise，并分报 raw vs A′；v15 应开火未过线且 empty 贴边误火≈57%（v12 为 0）→ 仍否决 promote，理由更干净。
- **通用规则**：检测验收先对齐训推 MA 协议，再问「分母是不是条件概率」；「尺子不公」≠「模型可上」，要看公平尺下的应开火 hit 与空背景误火。
- **牵连**：`scripts/tip_detectability.py --full-ma`；`scripts/eval_v15_fair_tip.py`；`analysis/p_v15_revalidate_fair.md`；`analysis/p_tip_eval_fairness.md`；`analysis/output/v13_real_tip_preview/`。
