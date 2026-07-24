# Short 判断层重构 v1：结构性 short-only 路径 + 特征方向镜像单变量实验

**日期**：2026-07-24  
**性质**：发现级；**不加** `--eval-holdout`；**不** promote / **不**改 TP/SL / 0.2% 成本 / 阈值预设。  
**基线**：`p2b_yolo_owner_side_short_5_6m`（旧判断层特征语义直接套在 tip_v1b short 候选上）。  
**单变量**：仅把方向特征对齐为 short 语义（候选集、标签、障碍、成本、切分不变）。

---

## A. 盘点：旧判断层对「只做空」哪里不合理

| 点 | 问题 |
|---|---|
| 特征语义 | `FEATURE_COLUMNS` 多头取向：`ext_up` / `order_score` / `drawdown24` / `close_vs_ema*` / `ret_*`；short 池此前原样写入（YOLO 5_6m CSV 无 `ext_down`/`runup24`/`down_order_score`） |
| 标签 | 规则/YOLO short 已走 `label_short_candidate`（正确）；但特征未镜像 → 模型读到「上涨动量/多头排列」原义 |
| 评估 | `train.py` 原先不断言 `side`，混边表可被静默训；tag 也不强制含 `short` |
| 文档 | `labeling.py` 模块 docstring 仍写 long-only；short 因果列未在 features 标明 |
| 历史结论 | `docs/learnings/short-mirrors-need-directional-feature-semantics.md`（H10）已要求镜像；本轮把逻辑收进主路径 |

**非本轮改动（需 owner 批）**：换障碍（trend/MA exit / trail）、改 TP/SL、扩币、holdout。

---

## B. 结构性改动（已落地）

1. `src/judgment/features.py`：`align_short_feature_rows` + `extract_feature_rows_for_side`；docstring 标明 short 因果列。  
2. `build_dataset.py` / `yolo_candidate_source.py` / `short_replication.py`：`--side short` 统一走 short 对齐特征。  
3. `train.py`：`--side long|short|auto`；拒绝混边；short 要求 tag 含 `short`；metrics 写 `side`。  
4. `labeling.py`：模块 docstring 分边说明。  
5. `scripts/remap_yolo_short_features.py`：对已有 YOLO short CSV **不重扫检测**，只重算特征（本实验用）。

---

## C. 单变量实验：short 特征方向镜像

**假设**：在同一 tip_v1b 5×6m 候选上，把方向列改成 short 语义，应改善可解释性，并可能抬 top-decile 净收益（裁决看净收益+置换，AUC 仅参考）。

### 复现命令

```bash
# 1) 同池重算 short 对齐特征（标签/realized_ret 不变）
PYTHONPATH=. .venv/bin/python scripts/remap_yolo_short_features.py \
  --in data/judgment_yolo_owner_side_short_5_6m.csv \
  --out data/judgment_yolo_owner_side_short_5_6m_feat_mirror.csv

# 2) 训练（禁止 --eval-holdout）
PYTHONPATH=. .venv/bin/python -m src.judgment.train \
  --data data/judgment_yolo_owner_side_short_5_6m_feat_mirror.csv \
  --tag p2b_yolo_owner_side_short_5_6m_feat_mirror \
  --side short
```

重算 sanity：`n=1240`；`labels_unchanged=true`；方向列 mean\|Δ\|：`order_score≈2.98`，`ext_up≈0.010`，`drawdown24≈0.012`，`ret_4≈0.0086`。

### 数据统计（与基线同池）

| 项 | 值 |
|---|---|
| 池 | tip_v1b YOLO short，5 流动性币 × `[2025-11-04, 2026-05-04)` |
| n | 1240（train 983 / val 248 / holdout 0） |
| 正类率 | 0.296（全集）；val 0.258 |
| 单变量 | 仅特征方向镜像；障碍仍为 YOLO 扫时的 TP5/SL2 |

### 结果对照

| 指标 | 基线 `…_5_6m`（long 语义特征） | 本轮 `…_feat_mirror` |
|---|---:|---:|
| val AUC | 0.599 | 0.590 |
| 置换 p | **0.009** | 0.014 |
| top-decile n | 24 | 24 |
| top-decile 毛收益 | +0.262% | +0.356% |
| top-decile 净（−0.2%） | **+0.062%** | **+0.156%** |
| top-decile 胜率 | 0.375 | 0.375 |
| all_mean_net | −0.141% | −0.141% |
| best_iteration | 5 | 11 |

单特征基线（`ma_spread_pct` logreg）两轮相同：AUC 0.517 / top-decile 净 −0.028%。

镜像后 gain top：`atr_pct`, `volume_ratio`, `drawdown24`(=runup24 语义), `atr_pct_ratio96`, `pre_range168`——波动/量能仍主导，方向列进入前列说明 remapping 被用到。

### 解读

- **经济指标**：top-decile 净从 +0.062% → +0.156%（同 n=24），方向符合「short 语义应对齐」的先验。  
- **统计指标**：AUC 略降；置换 p 从 0.009 → 0.014，**不再过 p&lt;0.01 门**——在 n=24 下两指标都极噪。  
- **诚实**：同一切分、同一标签，特征镜像是干净单变量；但样本仍发现级极薄，**不能**当作确认或晋升依据，也不能据此改障碍/成本。

---

## 风险与诚实声明

1. val top-decile 仅 24 笔；净收益差一个点也像噪声。  
2. 5 币 × 6m 流动性子集，外推全宇宙不可信。  
3. 基线池特征是「long 语义写进 short 表」的历史产物；新扫 short 池会默认镜像——新旧 CSV 不可混比 unless 注明。  
4. 未动 holdout；未 promote。  
5. YOLO 标签仍用 TP5/SL2（与规则默认 TP4/SL2 历史差异保留；改障碍须另批）。

---

## 下一步（需 Owner 决策处已标）

1. **先扩币再优化（建议默认）**：同窗扩至 10 / 更多流动性币，再复跑 feat_mirror vs 基线——否则任何特征优化都可能无意义。  
2. **（需 owner 批）** 换障碍单变量：short trend / MA exit / trail（历史 `fixed-tp-cuts-short-trend-edge`）；**不**在未批准时改 TP/SL 倍数。  
3. 可选：规则 `strict_short` / `expanded_short` 池在新特征路径上重训作对照（骨架已对齐，非本闸必需）。

产物：

| 文件 | 用途 |
|---|---|
| `data/judgment_yolo_owner_side_short_5_6m_feat_mirror.csv` | 镜像特征池 |
| `analysis/output/p2b_yolo_owner_side_short_5_6m_feat_mirror_metrics.json` | 本轮指标 |
| `analysis/output/p2b_yolo_owner_side_short_5_6m_feat_mirror_feature_importance.csv` | gain |
| `docs/learnings/short-judgment-needs-directional-feature-align-on-main-path.md` | 本轮 learning |
