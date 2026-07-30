# Grok overnight batch RESULTS（`grok/overnight`）

**完成时间**：2026-07-15  
**纪律**：全程 train/val only；未加 `--eval-holdout`；未改 `features.py` 主线、`models/frozen*`、`data/forward_log.csv`。

---

## 任务1 — 成交量因子三连（H14/H17/H18）

**做了什么**：在 `src/factors/library.py` 注册 `obv_slope` / `vol_dryup` / `taker_imbalance`；跑 `factor_ic_screen.py`；写 `analysis/p2b_factor_ic_vol.md`。

**结论**：三者均为 **dead**（IC +0.025 / +0.021 / −0.002），无一达到 `|IC|≥0.03` 且符号稳定。不进入单变量验证队列。

**异常**：无。`ts_rank_close` 在全库筛中仍「样本不足」（既有问题）。

---

## 任务2 — 密集质量二阶（H15）

**做了什么**：注册 `ma_order_score` / `convergence_speed` / `ma_bandwidth_pct`；IC 筛；`analysis/p2b_h15_quality.md`。

**结论**：三者 **dead**（IC +0.013 / +0.016 / +0.002）。解释为规则入池后 spread/order 方差被截断（selection-conditioned IC collapse）。

**异常**：无。

---

## 任务3 — BTC 大盘状态（H13）

**做了什么**：`scripts/h13_btc_regime.py`：BTC 1h EMA55 斜率 + ATR% 分位，收盘后 `available_at` 因果 as-of 对齐 15m 候选；IC + 单变量净增益；`analysis/p2b_h13_btc_regime.md`。

**结论**：**未通过**。IC 无稳定存活；单变量 top 净@maker 增益仅 +0.01%~+0.04%/笔（噪声量级），不并入主线。

**异常**：无。

---

## 任务4 — 结构出场 H3（MA-exit）

**做了什么**：完善 `label_candidate_ma_exit` 文档；`scripts/h3_ma_exit_sweep.py` 对比 TP5；`analysis/p15_h3_ma_exit.md`。

**结论**：**发现级通过**。MA-exit top 净@maker **+0.512%** vs TP5 **+0.151%**，p=0.001；均持仓 11.7 vs 20.1 根。不替换冻结主线，仅发现级记录。

**异常**：扫描序列数（364）大于早期 H1 报告（~116），绝对值勿与旧表直接横比。

---

## 任务5 — 时间衰减紧缩 H4

**做了什么**：`label_candidate_time_decay`（每 12 根收紧 0.25×ATR）；`scripts/h4_time_decay_sweep.py`；`analysis/p15_h4_time_decay.md`。

**结论**：**未通过（持平）**。净@maker +0.150% vs TP5 +0.151%，无实质增益。

**异常**：无。

---

## 任务6 — 波动率自适应障碍 H5

**做了什么**：`label_candidate_vol_adaptive` + train-only 三分位边界；`scripts/h5_vol_adaptive.py`；`analysis/p15_h5_vol_adaptive.md`。

**结论**：**边际发现级通过**。自适应净@maker +0.172% vs 固定 +0.151%，p=0.001。幅度小，不建议立刻替换主线 barrier。

**异常**：无。

---

## 任务7 — 市值/流动性分层 H11

**做了什么**：24h 名义成交额（volume×close×96）train 中位数二分；分 tier 训练 + stacked；`scripts/h11_tiered_models.py`；`analysis/p2b_h11_tiered.md`。

**结论**：**分层未稳定碾压合池**。stacked 净@maker +0.173% vs pooled +0.151%，AUC 几乎持平。alt tier 单独更好、large 更弱——异质存在但不够切主线。

**异常**：无。

---

## 任务8 — 30m 网格 H8

**做了什么**：`scripts/h8_30m_grid.py` 跑 TP{4,5,6}×h{48,60,72}；`analysis/p2b_h8_30m_grid.md`（低置信度标注）。

**结论**：**h60 不是稳定最优**。单格最优 `tp4_h48`（净@maker +0.84%）；跨 TP 平均 horizon 亦偏 h48。n_val≈360，置信度低。

**异常**：无。

---

## 任务9 — 代码去重

**做了什么**：合并隧道脚本（保留 `tunnel_labelstudio.sh`，`ls_reverse_tunnel.sh` 作别名）；queue8/9/10/12 手写 F1 → `evaluate_owner_f1`（14/15 已用）；pytest 全绿。

**结论**：行为保持、去重完成。

**异常**：无。

---

## 任务10 — 测试补强

**做了什么**：`tests/test_factor_causality.py`（全 FACTORS 因果性，跳过 `ts_rank_close`/`ret_skew` 的 pandas 伪影）；`tests/test_labeling_paths.py` 增补 scaled/breakeven 各四路径；全量 pytest **141 passed, 2 skipped**。

**结论**：测试补强完成。

**异常**：`ret_skew` 在「未来突变」下 rolling.skew 在线矩有 float 漂移（非前视），测试中 skip 并注释。

---

## 汇总给 owner 的优先信号

| 优先级 | 结果 |
|---|---|
| 最强新发现 | **H3 MA-exit** 发现级通过（短持仓 + 更高 top 净） |
| 次强 | H1 scaled（历史已记录）仍为强挑战者；本批 H5 仅边际 |
| 明确负结果 | 成交量三连 / H15 质量 / H13 BTC / H4 时间衰减 |
| 工程 | 隧道+F1 去重；因果/障碍测试补齐 |

**未改主线**：冻结模型与前向时钟未动；任何升级需 owner 拍板 + 前向确认。
