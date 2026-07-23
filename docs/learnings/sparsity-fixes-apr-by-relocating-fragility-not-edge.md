# 稀疏化能抬 4 月 train PF，靠的是脆弱搬家而不是边增量

- **问题**：unsparsed `spread_expand` short 在 train 上全样本 PF≥1.3，但 2026-04
  系统性翻车；owner 问抬 thr / MIN_GAP 把 n 压到 owner short 量级能否修好。
- **死胡同**：只抬 MIN_GAP（18→96）几乎砍不动 n（~6400 仍远高于 1–2k）——火点
  时间上本就够稀，gap 不是有效单杠杆。两段式「tip 窗 + order≤0 ∧ 已跌 ∧
  expand」在预声明 thr 下 n 仍过 cap；bump 到与 E3 相同强 thr 后，曲线与 E3
  稀疏几乎重合（ΔPF≈−0.05）——硬确认在强 expand 后不加信息，不能当独立胜利。
- **有效路径**：E3 用 **thr-only** 按 **n 带**（非 PF）选阈，把触发压到 ~1.4k；
  train 上 4 月 PF 从 ≪1 抬到 >1.5，但全样本 PF 相对 base **持平略降**，绝对净利
  腰斩，且 2 月/12 月/8 月恶化。读法：稀疏在 train 内重分配了坏月质量，不是
  结构性修好 tip×regime。故发现级可记、**不**够 holdout。
- **通用规则**：稀疏实验必须双看「目标坏月」与「其他月是否接盘」；若 n 校准后
  与另一规则同 thr 同曲线，禁止打包成两个成功。选阈只看 n 仍可能过拟合——
  小样本坏月救援默认可疑。
- **牵连**：`scripts/e3_sparse_and_two_stage.py`；报告
  `analysis/p_e3_sparse_and_two_stage.md`；对照 `p_entry_align_and_regime.md` /
  holdout#7；纪律：勿申请 holdout#8。
