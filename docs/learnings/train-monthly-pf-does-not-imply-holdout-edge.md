# Train 月度过线 ≠ holdout 可交易边

- **问题**：`spread_expand` short + 趋势出场（no_tp / trail4）在 train 月度口径
  PF@maker ≥1.3，holdout#7 是否仍过线？
- **死胡同**：用「月 top2＜60%、多月各自 ≥1.3」当稳健证据，容易把制度内相关月份
  误当成可迁移边；2026-04 train 翻车被写成张力而非否决信号。
- **有效路径**：预注册两档同测 holdout——两档同步塌到 PF≈1.0（0.997 / 0.969），
  净≈0 / 略亏；证伪的是底座，不是单一出场参数。
- **通用规则**：train 过线 + 月度分散，仍只是申请 holdout 的门槛，不是上线证据；
  同一规则族 holdout 证伪后勿再烧同一配置。
- **牵连**：`scripts/short_trend_ab.py --eval-holdout`；
  `analysis/p_short_trend_holdout7.md`；对照 `p_short_trend_ab.md`。
