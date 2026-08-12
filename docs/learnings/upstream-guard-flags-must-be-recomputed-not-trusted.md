# 上游的"护栏已检查"标记要重算，不能直接信

- **问题**：六个 Owner 审核包的 1,200 条事件里，`touches_owner_box_guard` **全部为 false**，
  看起来"没有任何事件压在金标框上"。把其中的 Owner-NO 直接当硬负例用，本来是顺理成章的。

- **死胡同**：信这个字段。它是构建审核包时算的，但算的是**那一轮自己的候选池口径**——
  不同包的金标集合、护栏根数、甚至坐标系都不完全一样。字段名叫 guard，不等于它守的是
  你现在这批金标。

- **有效路径**：从 `positive_manifest` 的 `source_owner_global` + `win_start` + `start_time`
  重建每个 Owner 原框的**绝对时间区间**，加 12 根护栏，再与每个审核事件的
  `[decision_time - (window_len-1)*15min, decision_time]` 做区间相交。结果抓出 **100 个重叠**：
  85 个是 YES（模型重新检出已知金标，是好现象），15 个是 NO（Owner 在含金标框的窗口上说 NO，
  真实分歧）。那 15 个若不拦，就会变成压在确认正例上的硬负例。

- **通用规则**：跨轮次复用别人算好的布尔标记前，先问"它是拿哪一版事实算的"。
  能从原始坐标重算的约束，一律重算——尤其是**正负例互斥**这种一旦错就直接毒化训练集的约束。
  重算成本通常只有几十行区间相交。

- **牵连**：`yoyo/datasets/legacy_audit.py` 的 `owner_box_spans` / `intersects_owner_gold_box`、
  `tools/audit_legacy_labels.py`；
  `analysis/p3_yoyo_dataset_v3_gold_core_prereview_20260812.md`。
  相关：[negative-ratio-must-not-weaken-gold-exclusion.md](negative-ratio-must-not-weaken-gold-exclusion.md)、
  [reproducibility-is-per-axis-not-a-boolean.md](reproducibility-is-per-axis-not-a-boolean.md)。
