# Walk-forward 不得把不同模型的 raw scores 拼成一个排名

- **问题**：5 个时间折各自训练模型、各自校准分数尺度后，把 OOS raw scores 直接拼接再取全局 top-decile，会得到一个看似很强的聚合结果；但逐折 top-decile 可能只有 1/5 为正。
- **死胡同**：认为“都是 LightGBM 预测的净收益”就天然同尺度。不同训练窗、best iteration 和 regime 会改变分数的位置与尺度，全局排序会让某一折整体占据 top 桶，而不是在每折检验排名能力。
- **有效路径**：exact top-decile 和 AUC/Spearman 先在每个 fold 内计算，再按 test rows 或 selected effective-n 加权；只有已经通过各折 calibration threshold 得到的布尔 fixed-gate 集合可以跨折合并收益。
- **通用规则**：看到 walk-forward 的 pooled score metric，第一步先问“这些 score 是否来自同一个冻结模型和同一个 calibration 尺度”；若不是，只聚合 fold metrics / decisions，不聚合 raw scores。
- **牵连**：`scripts/run_p2_l2_20260803.py`、`tests/test_p2_l2.py`、P2 exact-top / rank 汇总；不影响逐折 fixed gate、matched control 或最终 REJECTED 裁决。
