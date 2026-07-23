# 空边趋势出场：月度过线仍可能季度集中

- **问题**：train 上空边趋势出场已过 PF≥1.3，是否只是少数月份撑起来？手标 short
  加同出场是否真比因果规则强？
- **死胡同**：只看全样本 PF/净合计会宣布「稳健过线」；忽略月/季分解会漏掉
  2026-04 翻车与 Q1 独占净利。把 owner short oracle 的 PF6–16 当成可部署边，
  会重复「事后确认态 ≠ tip 因果」的坑。
- **有效路径**：固定同一空边入场，出场扫完后**强制月/季 PF 与 top2 净利占比**；
  稳健线 = 全样本≥1.3 且月 top2≤60% 且≥2 个月各自≥1.3。结果：no_tp/trail3–4/ema55
  **月度过线且非单月独撑**，但季度头两块常 >60% 净利。B 侧 oracle 显著好于规则
  （ΔPF5–15），胜率鸿沟证明是 hindsight，裁决仍认 spread_expand 规则行。
- **通用规则**：宣称「稳健过线」必须同时交月（或季）分解与集中度；oracle vs 规则
  对照时，胜率数量级差本身就是 hindsight 警报，不要只报 ΔPF。
- **牵连**：`scripts/short_trend_ab.py`；`analysis/p_short_trend_ab.md`；对照
  `p_trend_exit_base_rate.md`、`owner-label-oracle-alpha-is-not-causal-tip-alpha.md`。
  Holdout 只建议测 A 因果，不测 oracle。
