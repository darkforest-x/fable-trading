# Owner 对齐抬召回不抬 Jaccard，因果边仍死

- **问题**：归因认定入场与手标 short Jaccard≈0.05 / 召回≈25%；E1 想从 owner
  short 因果特征重写入场，抬重叠并保住可扫描边；E2 想用 BTC/ATR 门修 2026-04。
- **死胡同**：①用召回当对齐成功——R2/R3 召回 94%+ 过线，但火点膨胀 10–25×，
  Jaccard 从 0.045 **降到** 0.017–0.018，精度更差；②把 trail4 擦过 1.3 写成入场
  胜利——同规则 baseline≈1.14，抬升是出场；③以为 `not_btc_up` 能修翻车——btc_up
  片占比极低，门≈空转且 4 月更差；④atr 高波门抬全样本 PF 却仍救不过 4 月到 1.0。
- **有效路径**：重叠与 PF **分栏**；同时报召回、精度、Jaccard。裁决：**抬召回 ≠
  集合对齐**；宽确认规则 no_tp≈1.14 相对 spread 1.415 **倒退**；atr 门是 train
  放大器不是 regime 补丁。不申请 holdout#8。
- **通用规则**：对齐实验的成功线必须含 Jaccard/精度，禁止只报召回；regime 门要
  同时看全样本 ΔPF、坏月 ΔPF、n_frac——「切片里好看」≠「加门可部署」。
- **牵连**：`analysis/p_entry_align_and_regime.md`；`scripts/entry_align_and_regime.py`；
  `p_chain_failure_attribution`；`docs/learnings/chain-failure-is-regime-plus-entry-mismatch.md`。
