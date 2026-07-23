# tip 上因果择向（排列/突破/散开）救不出 ≥1.3 的边

- **问题**：分边启动已证明机械启动两边都 <1.3；Owner 再批「择向」——同一密集 tip 上用因果规则选多或空（不够跳过），看能否救出一边 PF@maker ≥1.3。
- **死胡同**：把 `order_score` 当「错边过滤器」——跳过约 43% tip 后空边只从固定空 1.068 → 1.116，多边更差；跳过率高≠选出可交易边。小样（20 币）spread-short 曾过 1.38，全量回落到 1.245——勿用冒烟过线叙事。
- **有效路径**：底座锁死与 launch 分边同值（emergence tip + judgment bundle + TP5/SL2）；四类规则各用一套预声明默认、主表强制 long|short。结果：最好仍是 spread-short **1.245**（与 launch 同格复述）；排列/突破均未过线 → 标「择向未救出可交易边」。
- **通用规则**：在 tip 自带空头偏置的底座上，择向是**选边**不是**造 alpha**；对照固定空/固定多必须同行。小样过线必须以全宇宙复核后再写裁决句。
- **牵连**：`scripts/direction_select_base_rate.py`、`analysis/p_direction_select_base_rate.md`、
  [启动分边仍薄](mechanical-launch-entry-lifts-pf-but-not-past-1.3.md)、
  [多空必须分边](long-short-must-be-split-in-base-rate-tables.md)。
