# 开火密度必须除以总 symbol-month 暴露量

- **问题**：多币扫描把总开火数换算成“条/币·月”时，先用 `total_bars / n_symbols` 求了单币平均月数，再拿所有币的总开火数去除，结果把密度和相对 owner 的倍数放大了 `n_symbols` 倍。
- **死胡同**：直接相信验收表中的单位标签，或只看召回与密度的相对趋势；趋势可能不变，但绝对工作点、门槛解释和对外结论都会错。
- **有效路径**：先写清分子和分母：`density = total_fires / (total_bars / bars_per_day / days_per_month)`；若每币 bar 数不同，也必须累计每个币的实际暴露量，不能用总事件除以单币平均时间。
- **通用规则**：凡跨币种报告“每币每天/每币每月”，第一步用 `sum(symbol exposure)` 做量纲检查，并用“每币事件均值 ÷ 每币月份均值”作独立复算；两者应一致。
- **牵连**：`scripts/diag_v9_precision_vs_recall.py` 的 `months`/`dens` 换算；`analysis/output/diag_v9_precision_vs_recall.json`、`analysis/output/diag_precision_vs_recall_v10.json`、`analysis/output/after_train.log` 及引用其密度倍数的报告。修正不改变 v10“无召回≥50%且密度≤owner 3倍工作点”的裁决，但会改变所有绝对密度和倍数。
