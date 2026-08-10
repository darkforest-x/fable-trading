# HANDOFF — 给下一个会话/模型的执行路线图

> 文档地图：`docs/DOC_MAP.md` · 本周计划：`analysis/week_plan_20260720.md` · 纪律：`CLAUDE.md`

## ⚡ 当前真相（2026-08-11 — Owner 发现 B2/P2 固定最右位置 shortcut；P2 训练已停）

**直接裁决：三张 200 样本审计图不是显示问题。B2 固定 30 根因果窗的正框中心只落在
0.931034 / 0.948276 两个 X 比例，100% 集中于最右带，未满足交接规范 Stage-B 65%–95%
且不得固定 95% 的要求。现有 P2 hard-negative 路线建立在错误几何上，已作废，不能继续训练。**

- Owner 在 600 样本 montage 中直接指出“信号框怎么全是在最右边”；代码审计确认
  `visible_end=decision`、固定 `W=30`、`confirm_delay=1/2` 必然只产生上述两个位置。
- Windows 3060 的 P2 训练已精确停止；远端 `best.pt`、`last.pt`、`results.csv` 均不存在，
  因而没有可误用权重。远端数据保留，未删除其他任务或文件。
- 不能把位置修复塞进冻结 P2：那会同时改变布局与 hard negatives，违反单变量纪律。
- 已预注册独立位置臂：固定 B2 的 30 根可见 K、事件、split、seed、标签和 easy negatives，
  唯一变量为右侧 0–12 个纯空白画布槽位；不追加未来 K，目标框中心覆盖 65%–95%。
- 旧 renderer 像素合同不改；新增 opt-in Local-Signal V2 renderer，0 空白时必须逐像素等同旧版。
- 未读 holdout、未改阈值/成本/障碍/ACTIVE，未 promote、未部署、未下单。

独立 `local_signal_v2_p1_causal_blank_w30_v3` 已在 builder 提交后全量重建：2,388 正例 +
2,388 easy negatives；九道 P0 门全绿。框中心 0.6585–0.9483，四桶 732/625/572/459，
正负均覆盖 0–12 全部空白槽；0 future、0 holdout、0 跨 split、0 越界、4,776 文件守恒。
同 seed 二次重建后正/负 manifest SHA 分别稳定为 `f82a4910…43a1` / `83575284…94c8`。

下一步：该数据臂已具备训练前提，但尚未启动训练。需保持旧阈值、seed、训练配方和事件尺，
只评估位置布局这一个变量；通过后再从其冻结权重重新做 P2 hard-negative mining。

## ⚡ 当前真相（2026-08-11 — B2 候选密度失败；3,880 不是订单）

**直接裁决：上一版把 3,880 写成“交易/开单”是口径错误；它们是 B2 在 v10 预筛
proposal ledger 上的 L1 fire rows，不是订单。但密度复核也证实当前 B2 确实放得过宽，
因此停在 P2 hard-negative mining，P3 判断层不得提前启动。**

- P1 统一尺是 715 个平衡抽样 endpoints（358 正例 + 357 easy negatives），不是连续市场暴露。
  conf=0.35 在 easy negatives 上命中 56/357 = **15.69%**。
- v10 short-L2 pool 是已经预筛且同币至少间隔 18 bars 的 proposal ledger，不是订单流。
  B2 命中 3,880/7,795 = **49.78%**，按 ledger 跨度为 **88.27 L1 fires/日**。
- 3,880 个 fire rows 只去重成 3,715 个 outcome event groups，减少 4.25%；candidate_id 唯一，
  同币最小间隔 18 bars，edge2=edge3，数组/PNG 8 样本推理完全一致。高计数不是重复、edge
  或图像传输 bug。
- 不能靠抬 conf 修：0.45 时密度降到 8.35 fires/日，但验证召回从 73.46% 塌到 6.98%。
- 连续市场逐币×逐盘口 endpoint 尚未扫描，真实 L1 fires/日与可执行订单数均未知；禁止把
  88.27 或 3,880 外推为生产订单。
- 把每个 fire row 强行当 short 的反事实收益仍为负：10bp 后 -9.19bp、PF 0.893；匹配
  超额 +2.18bp 但 p=0.890625。该结果不是订单回测。
- 未读 holdout、未改阈值/成本/障碍/新鲜度，未 promote、未部署、未下单。

交付物：

- `analysis/html/p1_b2_short_l2_backtest_20260811.html`
- `analysis/p1_b2_short_l2_backtest_20260811.md`
- `analysis/output/p1_b2_short_l2_backtest_20260811.json`
- `analysis/output/p1_b2_density_diagnostic_20260811.json`
- `analysis/output/p1_b2_short_l2_backtest_20260811_{rows,selected,matched}.csv`
- `analysis/output/p1_b2_short_l2_backtest_report_20260811/{daily,symbol}.csv`

**下一方向：按交接规范做 P2 hard-negative mining + 连续因果 tip 密度回放。** 固定 B2
30 根窗口、事件尺和训练配方，只增加难负例；先冻结并验证 L1 密度门、event 匹配和去重规则。
只有 P2 密度与事件门通过后才进入 P3 LightGBM/规则判断层。禁止用提高 conf 代替重训。

## ⚡ 当前真相（2026-08-11 — Local Signal V2 P1 历史发现级通过，B2 胜出）

**直接裁决：30 根固定因果窗 B2 通过冻结事件门并成为 P1 候选；只接受历史发现，不具备生产资格。**

- 统一尺 715 endpoints（358 正事件 + 357 easy negatives），最大时间 2026-05-03 10:45 UTC；
  holdout 消耗 0。
- 冻结绝对门：Event Precision≥0.50、Recall≥0.50、FP/1000≤250。A 旧模型最大 Recall
  仅 0.0754，无法建立同 Recall 相对门；绝对门在候选结果前冻结。
- B2 fixed-30 @ conf=0.35：P=0.8193、R=0.7346、F1=0.7747、FP/1000=81.12、
  duplicates/event=0.0076，PASS / selected。
- C3 range-20–30 @ conf=0.45：P=0.7471、R=0.7095、F1=0.7278、FP/1000=120.28，PASS。
- B1 fixed-24 没有合格工作点：best-F1 @ 0.10 时 P=0.3543、R=0.9916、FP/1000=904.90，FAIL。
- dataset seed=20260807；三臂实际 training seed=0。字段歧义已勘误，trainer/3060 wrapper
  现在显式传递 seed；没有 seed sweep。
- 568 tests passed / 2 skipped。未读 holdout、未改 ACTIVE、未 promote、未部署、未下单。

交付物：

- `analysis/html/p1_local_signal_v2_report_20260811.html`
- `analysis/p1_local_signal_v2_report_20260811.md`
- `reports/P1_EXPERIMENT_REPORT.md`
- `reports/ACCEPTANCE_DECISION.json`
- `analysis/output/p1_local_signal_v2/comparison.json`
- `analysis/output/p1_local_signal_v2/training/B2/weights/best.pt`

**停止点：P1。** 下一步由 owner 决定是否以 B2 30 根窗为固定基线，只增加 hard-negative
mining 这一变量进入 P2；禁止自动 promote、读 holdout、部署或下单。

## ⚡ 当前真相（2026-08-10 — Local Signal V2 P0 修复通过，停在 owner gate）

**直接裁决：旧 Stage-B V1 的 P0 全绿是误报；strict-negative V2 已修复并通过 P0。**

- V1 positives 按时间切分，但 negatives 只继承 split 名称、候选来自全段历史：317 条 train
  negatives 晚于 train 截止，296 条 val negatives 早于 val 起点。
- 原 auditor 只审 positives，现已改为检查每个正/负窗口完整 `[start, end]`；V1 命令返回 1，
  strict-negative V2 八道门全绿。
- 新数据集 2,388 positive + 2,388 easy negative；train 4,060、val 716；0 holdout、0 event
  跨 split、0 label 越界、4,776 image/label/manifest 守恒、100% market-bar 可追溯。
- 同 seed 原地全量重跑两次，positive manifest SHA `6814b86c…b047`、negative manifest SHA
  `2cdcf889…13ba` 均逐字节不变；24-event preview 覆盖 24 个不同 symbol。
- Builder/auditor 已先提交为 `471f854`，数据随后从该 HEAD 全量重建，满足“builder 先入 Git”纪律。
- 旧 `owner_lsv2_stageb_cold` 绑定 V1 且训练时 HSV 非零，已 invalidated；不得作为新 V2 候选。
- 未来 3060 训练入口会下发仓库内 `src/detection/train.py`，不再调用远端未跟踪 trainer；
  flip/mosaic/mixup/HSV 全关。
- 未训练、未读 holdout、未改 ACTIVE、未部署、未下单。

交付物：

- `analysis/html/p0_local_signal_v2_stageb_strictneg_v2_report.html`
- `analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json`
- `datasets/local_signal_v2_stageb_strictneg_v2/manifest.jsonl`
- `reports/ACCEPTANCE_DECISION.json`

**停止点：P0。** 按交接规范 §14 等 owner 决定是否启动 P1 A/B/C 对照；不自动训练。

## ⚡ 当前真相（2026-08-03 — P2-M 只读机制审计已完成，必须停止）

**直接裁决：raw return IC 大部分含 ATR/barrier 尺度成分，但有小幅 scale-robust 残余；
P2 仍为 REJECTED，禁止据此选 feature 或训练。** P2-M 唯一数据源是 P1 immutable dataset。

- TP / SL gross return 在 ATR 单位上精确为 **+5 / -2**，确认 raw return 同时编码 outcome
  probability 与 ATR-scaled payout magnitude。
- P2-R frozen stable 20 features 中，14/20（70%）在 TP label、ATR-normalized gross、折内
  ATR quintile net IC 三条控制线上都衰减到 raw IC 的 50% 内；但未达到预注册 75% 全局门，
  所以 `global_mechanical_dominance=false`。
- 8/20 在三条控制线上仍满足 4/5 折同号且 abs median rho≥0.03，
  `global_scale_robust_signal=true`；其中 3 个同时 mechanical+robust。scale-robust 只是残余关联，
  不等于因果、经济 edge 或 feature shortlist。
- 五折 rows 2,937 / 2,918 / 2,996 / 2,944 / 3,000；P1 18,103 rows / 230 symbols；
  max signal 2026-05-03 05:15 UTC，max label end 2026-05-03 22:45 UTC，0 holdout。
- P2-M 专项 7 passed；完整 tests 513 passed / 2 skipped / 14 warnings / 0 failed。
- 未训练、未拟合、未选 feature、未调 threshold、未读 holdout、未改 ACTIVE、未部署、未下单；
  ACTIVE / forward log / ledger hash 不变，active bundle 不存在。

交付物：

- `analysis/html/p2m_readonly_mechanism_audit_20260803.html`
- `analysis/p2m_readonly_mechanism_audit_20260803.md`
- `analysis/output/p2m_mechanism_prereg_20260803.json`
- `analysis/output/p2m_mechanism_audit_20260803.json`
- `analysis/output/p2m_feature_mechanism_20260803.csv`
- `analysis/output/p2m_fold_target_mechanism_20260803.csv`
- `analysis/output/p2m_test_results_20260803.json`
- `analysis/output/p2m_hashes_20260803.sha256`

**停止点：P2-M。** `training_allowed=false`、`threshold_change_supported=false`。未来若 Owner
另行授权，只能先选一个单变量问题（target mechanism / one feature family / fresh-forward），
不得把相同 P1 的自适应结果包装成独立 confirmation。

## ⚡ 当前真相（2026-08-03 — P2-R 只读根因审计已完成，必须停止）

**直接裁决：P2 仍为 REJECTED；失败不是只改 q90 可以修复。** P2-R 只读取 P1 immutable
dataset 与 hash 冻结的 P2 产物，未训练、未读 holdout、未改 ACTIVE、未部署、未下单。

- 独立重建五个 test folds，rows 精确复现 2,937 / 2,918 / 2,996 / 2,944 / 3,000；P1
  18,103 行，max signal 2026-05-03 05:15 UTC，max label end 2026-05-03 22:45 UTC，0 holdout。
- fold-local exact-top 4/5 折 pressure-net≤0；加权 **-15.91bp**。同期整池 **-15.33bp**，
  exact-top 相对整池 **-0.59bp**，所以 ranking 没有证明增量，调 fixed threshold 不能救。
- matched control 从冻结 CSV 独立复算：1,051 pairs、12 UTC-week blocks、lift +0.74bp、
  exact sign-flip `p=0.4836`；pair ID / delta 完整性全过。
- outcome regime 明显漂移：TP-before-SL 五折 range 19.52pp，整池 pressure range 84.79bp；
  fold 2 / 4 都 collapse 到 best_iteration=1 / 15 distinct scores，fixed pass 有 4/5 折脱离
  8%–12%。这些是 contributor，不单独构成因果证明。
- 28 features 无 missing / inf；20 个满足预注册的跨折 Spearman 稳定规则。但 P2-R 已查看
  全部 feature × outcome；今后从中挑 feature 在相同 P1 重跑只能标 exploratory，不能重新
  作为独立 P2 acceptance。
- P2-R 专项 7 passed；完整 tests 506 passed / 2 skipped / 14 warnings / 0 failed。
- ACTIVE / forward log / ledger SHA 不变；active bundle 不存在；holdout 消耗 0。

交付物：

- `analysis/html/p2r_readonly_root_cause_audit_20260803.html`
- `analysis/p2r_readonly_root_cause_audit_20260803.md`
- `analysis/output/p2r_root_cause_prereg_20260803.json`
- `analysis/output/p2r_root_cause_audit_20260803.json`
- `analysis/output/p2r_feature_ic_20260803.csv`
- `analysis/output/p2r_fold_diagnostics_20260803.csv`
- `analysis/output/p2r_test_results_20260803.json`
- `analysis/output/p2r_hashes_20260803.sha256`

**停止点：P2-R。** 不调 threshold、不继续训练、不读 holdout、不创建/修改 ACTIVE bundle、
不部署、不下单。未来若 Owner 另行授权，只能先立新的单变量 exploratory 预注册；相同 P1
不能再提供独立确认，确认需要预注册后未参与选择的新鲜前向样本。

## ⚡ 当前真相（2026-08-03 — P2-L2 已完成且 REJECTED，必须停止）

**直接裁决：P2-L2 训练/验证流程完成，策略门失败。** 只使用 P1 immutable dataset；
artifact integrity audit accepted，但 strategy verdict 是 **rejected**。

- 主模型 best_iteration=1、1 tree、15 distinct scores；calibration q90 `>=` 实际 pass
  85.51%，threshold equality 81.23%，模型/selector health 全失败。
- 5-fold fixed runtime gate 只有 1/5 折 pressure-net>0；聚合 4,723 selected、pass 31.92%、
  pressure-net **-39.33bp**、PF 0.641。单特征 baseline 为 -22.67bp，反而少亏 16.66bp。
- 逐折 exact-top 也只有 1/5 为正；按 fold top-n 正确加权后为 **-15.91bp**。
- matched candidate control 1,051 pairs / coverage 22.25%；lift +0.74bp，UTC-week exact
  block permutation `p=0.4836`，未过 0.01。
- 初版曾错误 pooling 不同 fold 模型 raw scores 得到 +9.04bp；独立审计发现后未重训，改为
  foldwise aggregation，结论 -15.91bp；该纠错不影响 fixed gate 或最终 rejected。
- full tests 499 passed / 2 skipped；独立产物审计 17/17 true。
- ACTIVE / forward log / ledger SHA 不变；active bundle 不存在；未读 holdout、未部署、未访问
  trading client、未下单。

交付物：

- `analysis/html/p2_l2_preholdout_validation_20260803.html`
- `analysis/p2_l2_preholdout_validation_20260803.md`
- `analysis/output/p2_l2_results_20260803.json`
- `analysis/output/p2_l2_independent_audit_20260803.json`
- `analysis/output/p2_l2_selector_manifest_20260803.json`（research-only / execution=false）
- `analysis/output/p2_l2_dataset_binding_20260803.json`
- `analysis/output/p2_l2_hashes_20260803.sha256`
- `analysis/output/p2_l2_test_results_20260803.json`

**停止点：P2.7。** 不进入 P3，不改模型/threshold/cost，不读 holdout，不做 ACTIVE/bundle、
deploy 或 order。后续任何动作需要 Owner 新指令与新预注册。

## ⚡ 当前真相（2026-08-03 — P2.0 审计通过，P2.1 Owner 门已批准）

**P2 尚未训练。** 只读审计重新加载了唯一 P1 immutable dataset，并完成时间三段、完整
label interval / event-group purge。Owner 已以“批准”确认成本压力线和 fixed gate，机器
预注册当前为 `status=accepted`、`p2_training_allowed=true`。

- dataset SHA `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a`；
  18,103 行 / 230 币 / 15,604 event groups；holdout signal / interval 均为 0。
- 固定三段在完整 event-group purge 后为 train 10,940、early-stop 3,498、calibration
  3,623；42 行 / 32 组被 purge，跨段 event group=0。
- fixture 发现并修复“穿越边界行删除后，同组邻居仍留在下一段”的依赖泄漏；现在触边会
  清除整个连接分量。
- 预注册推荐：LightGBM regression、target=`net_ret_swap_taker`、28 frozen features、无参数
  扫描、5-fold expanding walkforward、matched candidate control、UTC-week economic block
  permutation；AUC 不作成功裁判。
- Owner 已批准：①实际成本总 RT 0.15%，即 P1 taker-net 再减 5bp，P1-only 范围不含
  funding；②固定 gate 为 calibration q90、`>=`、可分边界取中点、并列整块通过、pass
  8%–12%、equal≤2%、不切 ties。
- 未训练、未在真实分数上校准 threshold、未读 holdout、未改 ACTIVE、未建 active bundle、
  未部署、未访问交易 client、未下单。

交付物：

- `analysis/p2_l2_audit_and_prereg_20260803.md`
- `analysis/html/p2_l2_audit_and_prereg_20260803.html`
- `analysis/output/p2_l2_audit_20260803.json`
- `analysis/output/p2_l2_prereg_20260803.json`
- `analysis/output/p2_prereg_test_results_20260803.json`（492 passed / 2 skipped）

**下一动作**：先跑 fixture 与小样本 dry-run，二者通过后才执行 full P2 训练验证；仍禁止
holdout、ACTIVE、active bundle、部署与订单。

## ⚡ 当前真相（2026-08-03 — P1-DATA 已完成，必须停止）

**直接裁决：P1 pre-holdout immutable short L2 dataset 重建通过；不得自动进入 P2。**
前置 `p0_independent_acceptance=accepted`；P1.0–P1.7 的 input snapshot、schema、canonical
路径、fixture、真实 dry-run、proposal-led full build、机器审计、fail-closed loader、报告均完成。

- canonical dataset：`data/p1/p1_short_l2_preholdout_aade2a334448d644.csv`
  - SHA256 `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a`
  - 18,103 行 / 230 币 / 2026-02-01 01:00 → 2026-05-03 05:15 UTC
- manifest：`analysis/output/p1_dataset_manifest_20260803.json`
  - SHA256 `53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682`
  - `training_eligible=true` 只表示 P1 数据门通过，不是训练授权。
- 冻结 source proposal 18,379 条全部数量守恒：18,103 dataset rows + 274 无 selected
  candidate + 2 canonical outcome reject；0 holdout signal、0 post-cutoff OHLC materialized。
- full replay 只消费冻结 L1 proposal 的 exact causal windows；344 current live universe 全记账，
  112 个零 proposal 币不读 K 线，不做历史负窗 L1 mining。
- fixture/dry-run accepted；full machine verdict accepted；fail-closed loader 复读 18,103 行；
  完整 `tests/` 为 488 passed、2 skipped、0 failed、0 deselected。
- P1.0 与 full 后 `models/ACTIVE`、`data/forward_log.csv`、
  `data/executor_ledger.jsonl` SHA 均不变；`models/active_bundle.json` 不存在。
- 未训练、未调 threshold、未读 holdout OHLC、未建 active bundle、未改 ACTIVE、未部署、未下单。

交付物：

- `analysis/html/p1_preholdout_dataset_rebuild_20260803.html`
- `analysis/p1_preholdout_dataset_rebuild_20260803.md`
- `analysis/output/p1_preholdout_dataset_rebuild_20260803.json`
- `analysis/output/p1_dataset_manifest_20260803.json`
- `analysis/output/p1_dataset_hashes_20260803.sha256`
- `analysis/output/p1_test_results_20260803.json`

**停止点：P1.7。** 下一步无论是训练、threshold/selector、P2、active bundle、ACTIVE、部署或
下单，都需要 owner 新指令；当前不得继续。

## ⚡ 当前真相（2026-08-03 — P0-SAFETY 已完成，必须停在 Owner gate）

**直接裁决：P0 本地安全验收通过，但当前策略不可执行。** `models/active_bundle.json`
不存在；example bundle 只描述 v10 的 `legacy_unaligned + abnormal tie mass + paper_only +
execution_eligible=false`，所以 production 会 fail-closed。v10 是 **legacy / audit-only**，
不是 paper/live active bundle。

- H1–H7 均在完整仓库中确认并完成 P0 隔离/修复：short→buy、ACTIVE/研究配置错认、q90
  大并列、return/cost 双扣、latest fallback、decision/fill 倒置、global tip-3。
- runtime parity **REJECTED**：ACTIVE 是 28 特征、1 棵树、固定门 pass 91.13%；历史研究参考
  是 47 特征、每折 250 轮、折内十分位。研究 `+23.49bp` 不得归给 ACTIVE。
- canonical outcome 已统一；TP5/SL2/72 只做显式化，没有改经济参数。无 fill 时 actual PnL
  为空；paper 只取 decision 后第一根 future open；broker fill 只认 ledger。
- 最终 global tip age `<=2`，局部 edge/global age reject 分开计数。
- 全量安全测试：472 passed、2 skipped、1 deselected、0 failed；deselect 原因是本机没有可选
  `torchvision`。原始全量结果保留为 472 passed、2 skipped、1 dependency failure。
- P0 前后 `models/ACTIVE`、`data/forward_log.csv`、`data/executor_ledger.jsonl` SHA 均未变。
- 未训练、未碰 holdout、未 deploy、未 promote、未清账、未下任何真实或 demo 订单。

交付物：

- `analysis/html/p0_safety_protocol_repair_20260803.html`
- `analysis/html/p0_runtime_parity_audit_20260803.html`
- `analysis/output/p0_runtime_parity_audit_20260803.json`
- `analysis/output/p0_safety_baseline_20260803/`

**下一步必须由 Owner 明确授权。** 优先决策：short return convention → P1 pre-holdout
immutable dataset rebuild → P2 成本/selector gate → active bundle cutover。不得自动进入 P1、
激活 bundle、归档 forward log、实现/启用 short executor 或恢复部署。

## ⚡ 当前真相（2026-07-30 — 认知颠倒：判断层是唯一有效环节，检测层在拖后腿）

> **完整交接文档:`analysis/STATE_20260730.md`** —— 三天工作、误判记录、下一步优先级都在那里。
> 本节只放最关键的。

### 一句话
**三天前以为「检测层做好了、判断层是短板」,实际相反。** v10 检测器的候选比随机做空
还差 6bp,九种出场规则全部无效,而判断层挑单在两个不重合的候选池上都稳定给出 +17.8bp。

### v10 候选池(18,379 笔 / 232 币,`data/judgment_v10_wide.csv`)
```
候选池均值         -6.41bp   (已扣 10bp 成本)
匹配随机做空       -0.39bp
→ 检测器因果贡献   -6.02bp   ← 开火本身是负价值
顶十分位          +11.35bp   ← 唯一为正
判断层顶档提升    +17.76bp,15/15 折全为正
```

### 唯一在两个独立池上都站住的结论
```
老池(tip_v1b,25,602 笔)  +17.82bp
v10 池(18,379 笔)        +17.76bp   (回归目标下 +23.49bp)
两池 Jaccard 重合度只有 8.6%
ATR 匹配对照 -19.24bp → 顶档超对照 +42.73bp   ← 不是「挑了高波动」
```

### 已证伪的(不要再试)
- **九种出场规则**:v10 上没有一种因果超额覆盖 10bp;且排名与老池完全反转
- **分类改回归**:v10 上只值 -0.53bp(老池上的 +21.46bp 未复现)
- **+245bp 顶档提升**:孤证,复现不了,按 bug 处理
- **holdout #10**:功效算过,n=1739 只能分辨 ≥39.4bp,要证的是 4~18bp
- **Kronos 基础模型**:配对贡献 +2.42bp(t=1.13),置换 p=0.0333 未过 0.01;
  「仅 Kronos」0/15 折为正 —— 它的价格预测对这批候选的盈亏没有可用信息。
  产物留在 `data/kronos_feats_v10.csv`,换池可复用

### 下一步(优先级)
1. **剖开顶十分位** —— 唯一稳的信号却从未被解释;顶档 vs 其余 90% 的特征差异,
   带匹配对照。若差异清晰,那可能才是真正的信号定义(比 owner 手画金标更值得当目标)
2. **标 `datasets/label_live_tip_1000/`** —— 1000 张盘口图、标签全空,owner 20 分钟,
   回答「你的形态只看盘口时你自己认不认得出」
3. **滑点实测** —— ledger 缺 `avg_fill_px`,所有成本数字不含滑点,而边和摩擦已同量级

### 必须回滚的一处（已完成 2026-07-30）
`scripts/live_signal_tg.py` 的 `USE_STOP` 已改回 **True**（TP5/SL2）。
v10 上「只止盈无止损」是 -4.64bp；纸面路径与生产障碍一致。

### 仍禁止
promote / 改 ACTIVE / 清 forward_log / 动 holdout(已耗 9 次)/ 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-30 — ETH 3m short pilot v2 诊断训练完成；静态门失败）

### 一句话
**v1 因 99.74% 连续盘口恒开火而隔离；v2 改成“当前 tip 是/不是”的图像分类。
137 张数据完成一次 3060 诊断训练，但固定 0.50 门下 val 为 TP=0 / FP=0 / TN=34 / FN=8，
模型退化为全判 `no_start`，静态第一门即失败。后续语义审计又确认标签问题/来源不统一，且
锚点构造规则可 99.27% 推断类别；它不是 formal gold，禁止进入 smoke、promote 或 ACTIVE。**

### 2026-07-30 诊断训练结论
- Owner 明确授权“直接去3060跑吧”，并确认可与 PID 93656 的 v10 wide dump 并跑；原任务全程未停。
- 输入为 137 张 train/val 的 960×960 白底等比例补边副本；右端 T 完整保留；weak 150、smoke 7,089
  和 holdout 均未进训练。YOLO11n-cls、batch 4、seed 42、所有时序/颜色/裁剪增强关闭。
- RTX 3060 训练 21 epoch 早停，best=epoch 1，exit=0；远端/本地 best SHA256 均为
  `3ce89b668096e79eb00ae0ee8b4913024f91f46356626d22cbe11d3a98c30056`。
- 固定阈值 0.50：train 95 张 TP22/FP0/TN73/FN0；val 42 张 TP0/FP0/TN34/FN8。
  val top1 80.95% 恰等于多数类 34/42，balanced accuracy 50%，**FAIL** 预注册 TP≥6/8。
- 简单因果规则“当前 T 首次跌破六条 MA”在同一 val 为 TP5/FP0/TN34/FN3，明显胜过图像模型；
  因此本轮不是“多训几轮”问题，而是小样本/来源混杂/时间外泛化失败。
- 按 fail-fast 纪律，连续 smoke 与 30 事件 owner 复核未运行；阈值不下调、不扫描，不读取 holdout。

### 2026-07-30 失败根因复盘（数据结构 PASS ≠ 可学习性 PASS）
- 正例与负例不是同一个人工问题：30 个正例是 owner-yes 形态内另提橙色 T 后整批确认“来得及”；
  107 个负例是 Project 53 对原红框形态判“不是”。`label_provenance` 对 target 纯度为 100%。
- 正例 30/30 被重锚到六 MA 首次下破，负例 107/107 保留原 v10 tip；仅用构造元数据
  `anchor_time == first_below_time` 即 TP30/FP1/TN106/FN0（99.27%）。这是锚点/来源混杂，
  **不是未来泄漏，也不是可部署基线**。
- 原报告的 29 个正事件只按 box/未来标签区间归并。按模型完整暴露区间 `[T-199,T+60]`，
  137 张仅 32 个时间依赖块；30 正图仅 23 块，val 8 正图仅 5 块。跨 split 378-bar embargo
  仍然通过、无泄漏，但有效验证量远小于图片数。
- Ultralytics 用 `(top1+top5)/2` 选 best；二分类 top5 恒为 1，top1 又等于多数类基线，故 epoch 1
  被保存为“best”并不代表业务 TP/FP 最优。下一版必须逐 epoch 按固定门保存混淆矩阵。
- 详细 HTML：`analysis/output/eth3m_v2_problem_analysis_20260730/report.html`；机器审计：
  `analysis/output/eth3m_v2_problem_analysis_20260730/dataset_quality_audit.json`。

### 为什么不再画固定右缘检测框
- Project 53 的 107 张 owner-no 中，69 张历史窗口含已知 owner-yes 形态；它们是“当前 tip 不是”，
  不是“整张图没有对象”。继续写 YOLO 整图空标签会产生矛盾监督并强化右缘位置捷径。
- Owner 已明确只需要回答“是不是”；v2a 因此用 200 根 causal 图做 image-level
  `short_start / no_start`，不再把框宽当训练目标。

### 标签语义纠错（必须保留）
- v2 初稿错误地把生产扫描 `tip/tip-1/tip-2` 的**检测定位容差**解释成信号寿命，自动生成
  T/T+1/T+2 正、T+3 负，共 265 张。反方复核发现后，该版已隔离，**禁止训练**。
- 当前 v2a 只有 owner 实际确认过的时点进 train/val：固定 30 图的当前 T 正例，以及
  Label Studio Project 53 的 107 个 owner-no 当前 tip 负例。
- T-1/T+1/T+2/T+3/原 v10 共 150 条全部 target 为空，只进 `weak_or_review_manifest.csv`；
  只有逐时点复核或 owner 明确批准寿命规则后才可单变量加入。

### 数据与隔离
| 项目 | 结果 |
|---|---:|
| train/val 图片 | 137（30 是 / 107 不是） |
| 独立正事件 | 29（train 21 / val 8） |
| 完整暴露正依赖块 | 23（train 18 / val 5） |
| 全部完整暴露依赖块 | 32（train 25 / val 7） |
| train / val 图片 | 95 / 42 |
| 全局事件组 | 71 |
| 实际锚点 embargo | 378 bars（硬门 200+60=260） |
| 无标签待复核 | 150 |
| 连续 dev smoke | 7,089 bars（未标注，绝不自动转负例） |

- 30 张 timing 校准是 owner 在对话中的整批确认“看过了都来的急”，不冒充逐行 Label Studio
  金标；回执绑定固定 manifest、移动 HTML、30 张 review 图和 30 张 causal 图 SHA256。
- 30 张正图按重叠 3h 标签区间有 29 个事件，但按完整输入+标签区间只有 23 个依赖块；
  当前旧口径名称“独立正事件”不得再用于宣称统计独立性。
- 独立验证：标签白名单、图片/哈希、receipt、事件切分、因果窗、holdout 边界全通过；
  18 个相关测试通过。

### 产物
- 数据：`datasets/eth_3m_short_pilot_v2/`
- 构建器：`scripts/build_eth3m_short_pilot_dataset_v2.py`
- 独立验证：`scripts/validate_eth3m_short_pilot_dataset_v2.py`
- 验证回执：`analysis/output/eth3m_short_pilot_v2_dataset/validation.json`
- owner 回执：`datasets/eth_3m_short_pilot_v2/owner_confirmation_receipt.json`
- 审计报告：`analysis/p_eth_3m_short_pilot_v2_dataset.md`
- 可携带 HTML：`analysis/output/eth3m_short_pilot_v2_dataset/report.html`
- 训练预注册：`analysis/eth3m_short_pilot_v2_cls_prereg.json`
- 全图训练副本：`datasets/eth_3m_short_pilot_v2_cls_letterbox960/`
- 本地权重：`runs/classify/eth3m_short_pilot_v2_cls_diag_20260730/weights/best.pt`
- 远端日志：`C:/fable/logs/eth3m_short_pilot_v2_cls_diag_20260730.log`
- 本地原始远端证据：`analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/remote_train.log`、
  `remote_exit_code.txt`、`remote_best.pt`（exit=0；日志 SHA256 `b8e6487b…`；远端/本地权重一致）
- 诊断训练报告：`analysis/p_eth3m_short_pilot_v2_cls_diag_20260730.md`
- 问题分析与重建方案：`analysis/output/eth3m_v2_problem_analysis_20260730/report.html`

### 状态与下一步
- `diagnostic_pilot_only=true`；`pilot_training_eligible=false`；`formal_gold_dataset=false`；
  `promotion_eligible=false`。
- 一次诊断训练已完成并失败；未调阈值、未跑 smoke、未 promote、未改 ACTIVE。
- 推荐下一步是先做统一 current-T 二选一 D0：240 个唯一 T（旧 yes 事件 earlier/original 成对
  120、旧 no 事件 original/near-miss 成对 80、非 v10 连续 tip 40）+10% 盲重复；一致率、
  source-only 基线和完整依赖块通过 Gate A 后，才扩 600 和 2,000。不能把下调阈值或挑
  checkpoint 当修复。
- 维护计划要求的结构拆分已完成；18 个数据测试、冻结 manifest/receipt 及 287 张图逐一哈希等价。

### Holdout 事故登记
- 并行审计助手误读了 `data/kline_fetched/ETH_USDT_SWAP_1m.part.csv` 的表头及 3 行
  2026-07-15 数据，发现后立即停止；未用于统计、选样、阈值或模型结果。
- 按“看一眼就是消耗”纪律，保守登记为全局 **holdout 第 12 次误耗**。v2a 构建本身只读严格
  pre-holdout 前缀；独立验证只读冻结产物。

### 仍禁止
- 265 张语义错误版；v1 权重进入 v2 / judgment / ACTIVE；自动 promote；清 forward_log；
  未经 owner 再读 holdout；真下单；改新鲜度三门。

## ⚡ 当前真相（2026-07-29 03:30 — 检测线的前提未被证实；v10 训练中；不 promote）

### 一句话
**v9 不可用(精度 0.4%),v10 正在重训修三处数据污染;但当晚一个更根本的测量显示:
在 owner 自己的标注密度下,因果特征一个金标都定位不到。**

### 当晚的决定性测量（`scripts/diag_tip_precision_at_owner_density.py`）
负样本 = 全部 440 万根 bar(不是挑出来的,所以结论不随采样口径变化):

```
基础率 0.0384%(1685 金标 / 4,392,738 bar)
密度 0.2~1.0 条/币·月(= owner 自己的标注密度)  → 命中 0,精度 0.00%
密度 10 条/币·月                               → 精度 0.26%,6.8x 基础率
密度 48.8(v9 的开火率)                        → 精度 0.23%,召回 11.8%
```

**因果窗口里有信号但极弱。** 与另一会话的度量一致:499 个 ⭐ 里只有 2 个画在盘口,
中位可见 **97 根**未来,67.3% 在画框时 72 根持仓已全部可见。
→ `docs/learnings/zero-live-edge-labels-means-the-target-is-unverified.md`

**边界**:这测的是表格特征(28 生产 + 19 alpha),YOLO 看像素,不完全等价;
且不证明金标是前视的,只证明**这些特征定位不到**。

### v9 为什么不可用
- **精度 0.4%**:owner 逐张审 277 个非金标开火,否掉 276 个(95%CI [0.1%, 2.0%])
- **开火密度 48.8 条/币·月**,是 owner 标注密度(0.18~0.36)的 **137~274 倍**
- **「召回 84%」是在 conf 0.05 下测的,生产跑 0.30**,该门槛下真实召回 **19.5%**
- 调门槛无解:压到 owner 密度需 conf≥0.50,那时召回 0%
→ `docs/learnings/recall-without-fire-rate-rewards-a-detector-that-fires-everywhere.md`

### v10 修了什么（训练中，3060）
| 污染 | 规模 | 修法 |
|---|---|---|
| 窗口未精确还原 | **19.3%** 的金标 | 要求 `resolve_win_start` 的 MAD<0.5(该返回值此前所有调用点都丢弃) |
| 方向按「先触发」判 | **9.8%** 的「空头」48 根内涨超 2% | 改为取窗口内**较大**位移 |
| 负样本教不会边界 | 采样器 `if passes(): continue` 排除了像的 | 加入 v9 自己的误开火 612 个 |

数据集:正 1322 / 负 1983(困难负 931),train/val 泄漏检查 **0 问题**。
参数与 v9 逐项对齐(单变量纪律)。训练中期 mAP 在 0.87 与 0.00 间震荡,
属改 GT 几何后的正常现象 → `docs/learnings/yolo-e21-train-instability.md`。

### 判断层
- `models/ACTIVE`(v11)在 25,602 行池上**顶档 -32.91bp**,底档最好
- 病因不是同源:**目标选错了维度**。ATR 五分档胜率 36.2~37.7% 持平,每笔净差 5 倍
  → 边在**幅度**不在**胜率**,而生产是二分类器,AUC 0.4962 是必然
- 换回归目标:配对 +21.46bp,t=3.21,13/15 折(p=0.0074);**但置换 p=0.32,仍打不过随机**
→ `docs/learnings/the-edge-is-in-magnitude-so-a-classifier-learns-nothing.md`

### 经济性（带匹配对照，此前所有数字都没有）
```
100×6m 池       +26.91bp
匹配随机做空    +17.15bp   ← 2025-11~2026-05 是山寨下行窗
检测器的贡献     +8.97bp   (t=5.71)
往返成本          10bp     ← 边与摩擦相等
```
→ `docs/learnings/pool-internal-metrics-cannot-see-beta.md`

### Holdout 记账补充（2026-07-29）
- Owner 在对话中明确要求用 v10 跑 2026-07-18～07-27 每日绝对涨跌幅 Top20，并继续要求整理规律；该窗口全部在 `2026-05-04` 之后，补记为全局 **holdout 第 10 次消耗**（此前 N=9）。
- 十日池按完整日结果事后选币，没有预注册匹配对照，只能作检测行为与标签语义审查，**不是正式验收，不能用于调参/promote**。本次二次汇总只读同一批已生成 CSV，不另记第 11 次。
- 结论与 HTML：`analysis/output/v10_daily_movers_10d_patterns/report.html`。
- Owner 随后又明确要求用 v10 在 ETH 3m 最近三个月预打标并生成 HTML，登记为全局 **holdout 第 11 次消耗**。本次只等距审查 2,000/43,621 个 causal-tip 锚点，v10 命中 47 个；未来 3 小时只出现在人工图，且 v10(15m)→3m 属 OOD。不得据此调阈值、验收、promote 或改 live。报告：`analysis/p_eth_3m_v10_prelabels_3m.md`；HTML：`analysis/output/eth_3m_v10_prelabels_3m/index.html`。
- ETH 微周期标注口径已由 Owner 明确冻结：人工 review 固定看未来 **3 小时**（3m=60 bars）；检测层的“形态是否成立/框坐标”与判断层的“后续结果/幅度”**分开保存**。下一步先做 ETH 3m 的 240 张开发期双视图校准包并过 Gate A；判断层 TP/SL/超时/成本暂未另批。
- ETH 3m 校准包预览已生成：**240 任务 = 216 独立事件 + 24 盲重复**；独立源配额 v10/numeric/downside/random = 65/54/43/54；33 个 v10 事件显示预框。HTML `analysis/output/eth_3m_calibration240_preview/index.html`，报告 `analysis/p_eth_3m_calibration240_preview.md`。全包及未来 3h 均 `< 2026-05-04`，**未耗 holdout、未导入 Label Studio**；等待 Owner 手机确认后再导入。
- 上述混合 240 HTML 被 Owner 明确否决为“不是要看的预标图”，仅留审计，**不得导入**。按修正口径已重做 `datasets/eth_3m_v10_prebox200/`：200/200 均为 v10 conf≥0.30 真框，全部显示红框，全部 exact-tip；白底单图、右侧 future+3h，无入场/TP/SL/成交量/背景填充。HTML `v10_prebox200_mobile.html`，报告 `analysis/p_eth_3m_v10_prebox200.md`；LS 任务已准备但仍未导入，待 Owner 确认。

### 下一步（需 Owner 决策）
1. **v10 验收**:已挂自动任务,训练结束即出「conf 0.30 下的召回 + 开火密度」。
   验收门是**密度接近 0.18~0.36**,不是 mAP,不是召回单项。
2. **决定性实验**:`datasets/label_live_tip_1000/` 1000 张盘口图(右缘=tip、无后文),
   1000 个标签**全空、从未开标**。标掉它才能回答「你的形态只看盘口时还认不认得出」。
3. 若 v10 密度仍压不下来 → 检测线的前提被证伪,应停止调检测器。

### 仍禁止
- promote / 改 ACTIVE / 清 forward_log / 动 holdout(已耗 11 次)/ 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-25 00:20 — S3 shortish 过滤包 432/1000；待 Owner 目视；不 promote）

### 刚发生
- Owner 要求 1000 框包只留“看起来像空头启动”。
- 在 tip_v1b 原包上加启发式过滤：`NOT bull_stack AND ret12<=0 AND close<=ema60`。
- **保留 432/1000**（183 币）→ `analysis/output/owner_side_short_tip_v1b_detect1000_shortish/`。
- 报告：`analysis/p_short_tip_v1b_detect1000_shortish.md`。
- **未** promote / **未**动 holdout / **未**改 ACTIVE。

### 下一步（需 Owner）
1. 审 `detect1000_shortish/index.html` + 填 `review_sheet.csv`。  
2. 过严/过松再调规则阈值。  

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-24 23:53 — S3 tip_v1b 1000 框包完成；待 Owner 目视；不 promote）

### 刚发生
- Owner 批 **S3-1**：用 `owner_side_short_tip_v1b` 在真实 K 线出 ~1000 框，排除 short 金标训练集。
- **完成**：`analysis/output/owner_side_short_tip_v1b_detect1000/` — labeled **1000** / tried 1176 / symbols **224** / train collisions **0** / right p50≈**0.997**。
- 脚本：`scripts/dump_short_tip_detect_sample.py`；报告 `analysis/p_short_tip_v1b_detect1000.md`。
- 前序 S2 仍有效：100×6m 回归 = 间歇弱边（净 +0.471%，ρ=0.016）→ **停扩币**。
- **未** promote / **未**动 holdout / **未**改 ACTIVE / **未**接执行器。

### 下一步（需 Owner）
1. 目视 `index.html` + 填 `review_sheet.csv`（owner_keep/note）。  
2. 根据 keep 率决定：仅辅证 / 建新金标 / 收摊。  
3. 障碍/holdout#8/promote **另批**。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 23:19 — short 100×6m 回归+walkforward 收口；S2=间歇弱边；不 promote）

### 刚发生
- **100×6m 扫池完成**：`data/judgment_yolo_owner_side_short_100_6m.csv` n=**25602** / 100 币 / pos≈0.284；complete 100/100（17:38）。
- **回归** `p2b_yolo_short_100_6m_reg`（无 holdout）：top-decile 净 **+0.471%**（n=510）/ Spearman **0.016** / val-q90=**0.00347** / 置换 p=**0.037**。报告 `analysis/p_short_judgment_100_6m_reg.md`。
- **walkforward** 5-fold：net_mean **+0.305%** / rho_mean **−0.010** / all_folds_net_positive=**false**。报告 `analysis/p_short_judgment_100_6m_reg_walkforward.md`。
- **S2 裁决**：扩样后单切仍正，但排序塌陷、稳健级未过 → **停止继续扩币叙事**；默认转 **S3 检测金标/信号定义**（Owner 1000 目视）。**未** promote / **未**动 holdout / **未**改 TP/SL。

### 下一步（需 Owner）
1. 是否开 tip_v1b **1000 目视框**（排除训练集）——S3。  
2. 是否换命题 / 收摊 short 判断层（默认：先 S3，不烧 holdout#8）。  
3. 障碍/holdout/promote **另批**；勿再开 binary top-K。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 16:40 — short 扩 30×6m；回归主线正；binary/top-K 支线关；不 promote）

### 刚发生
- **Owner 纠正（主线）**：short = **YOLO tip_v1b → 回归 LGBM（预测空头 realized_ret）→ 分位筛单**，对齐 v11。镜像=默认输入，不当「优化旋钮」。
- **30×6m 扫完成**：`data/judgment_yolo_owner_side_short_30_6m.csv` n=**7519**；墙钟 **≈16 min**（launchd）；主路径镜像。
- **回归** `p2b_yolo_short_30_6m_reg`：top-decile 净 **+0.371%**（n=150）/ Spearman **0.149** / val-q90=**0.00362**。报告 `analysis/p_short_judgment_reg_align_v11.md`。
- **binary 支线收口**（同池；本会话交付）：镜像基线 AUC **0.518** / 净 **−0.181%** / p=**0.125**；单变量 top-K10 更差（净 −0.237%）。报告 `analysis/p_short_judgment_refactor_v2.md`。**停止 binary 特征优化**。
- CLI：`--objective` + `--features-file`。**未** promote / **未**动 holdout / **未**改 TP/SL。

### 下一步（需 Owner）
- 同构**回归**下扩样本 / walkforward。障碍/holdout/promote 另批。勿再开 binary top-K。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 16:20 — short 判断层重构 + feat_mirror 单变量；不 promote）（历史；叙事已废）

### 刚发生（历史）
- **结构性**：short 主路径统一方向特征镜像（`align_short_feature_rows`）；`train --side` 拒混边。
- 曾把 feat_mirror 当单变量优化；**Owner 已纠正** → 见上节回归主链。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 16:05 — SHORT ONLY 首表：5 币 × 6m tip_v1b；不 promote）（历史）

### 刚发生
- Owner：「后台已停；**不管 HV**；最快回测」→ **5 流动性币** BTC/ETH/SOL/DOGE/XRP × 信号窗 `[2025-11-04, 2026-05-04)`。
- 扫池 `data/judgment_yolo_owner_side_short_5_6m.csv`（n=**1240**，pos≈0.296；墙钟≈**5.7 min**）→ train tag `p2b_yolo_owner_side_short_5_6m`（**无** holdout）。
- **SHORT ONLY 首表**：val AUC **0.599**；top-decile 净 **+0.062%**（n=24）；置换 **p=0.009**。报告 `analysis/p_short_only_backtest_tip_v1b_5_6m.md`。
- **诚实**：n 小；发现级刚过线；**未** promote / **未**动 holdout。tip_v1b tip-smoke 19/27 仍为检测辅证。

### 下一步（需 Owner）
- 同窗扩币 / 或停在本表转检测金标门——见报告「下一步」。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 14:45 — tip_v1b 训完；tip-smoke 19/27；不 promote）（历史）

### 刚发生
- **`owner_side_short_tip_v1b` 训练结束**（≈57 ep early-stop；进程已死）。权重：`runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`。
- **tip-smoke**：**tip 19/27**、live **4/27**。报告 `analysis/p_owner_side_short_tip_v1b.md`。
- **未** promote / **未**动 holdout。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-24 12:52 — tip 集完成；训练已按 Owner 批准重启）（历史）

### 刚发生
- **Tip 短集已完成**：`datasets/dense_owner_side_short_tip/`（train 1037 / val 324；holdout **0**；`box_right_frac` p50≈0.997；时间切分干净）。
- **Owner 早已批准开训**；`owner_side_short_tip_v1b` 经 launchd 开训（后已训完，见上节）。
- **未** promote / **未**动 holdout / 坏集 `dense_owner_side_short` 不覆盖。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---


## ⚡ 当前真相（2026-07-24 12:40 — Owner 叫停 short v1 train）（历史）

### 刚发生
- **`owner_side_short_v1` 训练已按 Owner 指令杀掉**：原 pid **26613** + wrapper **26607**；停于 epoch≈7。**未** promote。
- **叫停原因**：① 框非 tip（`box_right_frac` 中位 0.52；旧 pretip 窗）；② 非时间切分（val 99.4% 落在 train 窗内）。
- Owner 随后选 **选项 1** → 见上节（已重建 tip 短集）。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。

---

## ⚡ 当前真相（2026-07-24 午后 — Owner 选定 short-only 全链路）（历史；v1 已叫停）

### Owner 已批准 / 当时主线
- **只做空完整链路**：① short YOLO 检测 → ② short-only 判断层 → ③ 回测/优化。作战计划：`analysis/p_short_only_pipeline.md`。
- **检测**：本机 MPS 训 `owner_side_short_v1`（**已被 Owner 叫停**）；数据 `datasets/dense_owner_side_short/`（train 1004/1036，val 313/325）；日志 `analysis/output/owner_side_short_v1_train.log`；权重若有落盘 **未**晋升。
- **判断层骨架已铺**（不依赖 best.pt）：`build_dataset --side short` → `data/judgment_dataset_v2_{mode}_short.csv`；YOLO 池路径 `yolo_candidate_source.py --side short` → `data/judgment_yolo_owner_side_short.csv`。
- **原下一步闸门**（已废）：等 short train 结束 → tip-smoke… → **改为先重建 tip 对齐短金标**。
- **仍禁止**：promote / ACTIVE 切换 / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。
- §7-2 3060 dump **并行不杀**。

---

## ⚡ 当前真相（2026-07-24 午间 — Owner 批双链路；先本机训 short YOLO）（历史）

### Owner 已批准
- **多空分模、双链路**；**先跑空**：本机训 `owner_side_short_v1`（不用 3060）。
- 数据：`datasets/dense_owner_side_short/`（short 1361 框 → train 1004 图/1036 框，val 313/325）。
- 开训：`python -m src.detection.train --data .../dense_owner_side_short/data.yaml --model models/yolo11n.pt --name owner_side_short_v1 --epochs 100 --patience 20 --device mps`（SAFE_AUG）。
- 日志：`analysis/output/owner_side_short_v1_train.log` → `runs/detect/owner_side_short_v1/`。
- **不** promote / 不 holdout#8 / 不改 ACTIVE。Long 模稍后。
- §7-2 3060 dump **并行不杀**。

---

## ⚡ 当前真相（2026-07-24 上午 — Owner 批 §7-2；3060 大样本 dump 进行中）（历史）

### Owner 已批准
- **§7-(2)**：用现有 `owner_v16_tipuni_cold.pt` 在 **3060** 扩宇宙重扫 v16 候选，复验方向墙是否小样本假象。
  **不是**双检测器训练；**不是** holdout#8；**不** promote。
- 3060 任务：WMI pid≈83452 · `logs/v16_dump_large.log` · 输出 `data/v16_candidates_large.csv`
  （`--n-symbols 999 --end 2026-05-03`）。本地评估脚本已备：`scripts/it16_large_sample_direction_wall.py`。
- dump 完成后：scp 回 Mac → 跑 IT-16 → 写 `analysis/p_it16_large_sample_direction_wall.md`。

### 仍禁止
- 像素双检测器训（IT-14 红灯）· holdout#8 · promote · 改 ACTIVE · 真下单

### 待 dump/IT-16 出结果后再决策
- 若方向墙仍在 → 回到 §7-(1) 告警-only / §7-(3) 换命题
- 若墙被打破 → 预注册卡后再谈是否申请 #8（须另批）

---

## ⚡ 当前真相（2026-07-24 通宵收口 — IT-14 红灯；tip 映射已审；未达实盘门）（历史）

### 待 Owner 醒来批准（已部分回应：选了 §7-2）

1. **§7 产品岔路**：`(1) 接受检测/告警-only` / `(2) 3060 用现有 v16 大样本重扫方向墙`
   / `(3) 换命题` / `(4) 批准「全市场密度谷 tip 扫描」单变量基线`？  
   **默认建议：1 为主；若继续判断层则先做 4；2 作最后一钉。** → **Owner 2026-07-24 选 2**
2. **不要**批「像素双检测器训」——IT-14 红灯（除非显式例外）。
3. **不要** holdout#8 / promote / 改 ACTIVE / 真下单——清单未过线。

### 通宵已完成

- **IT-14 红灯**：冻结 COCO tip 窗 embed → VIS AUC≤0.507 / top_dir_PF≤1.096；
  报告 `analysis/p_it14_visual_direction_precheck.md`。**未**上 3060 双模。
- **tip 映射审计**：`box_right_frac≈0.5` **冤枉意图**（裁图坐标）；机械上 cut 处
  dense 仅 1.55%、chg8>0 97.6%、偏谷底 ~10 bar。报告
  `analysis/p_tip_mapping_owner_intent.md`。
- **IT-15 tip remap**：Owner 子集上前移到谷底 raw PF 好看但**选择偏差不可部署**；
  报告 `analysis/p_it15_tip_remap.md`。
- **可上实盘清单**：`analysis/p_live_readiness_checklist.md` —— G0–G4/G6 仍红/黄；
  **未**到「只差 Owner 点头」。
- **learnings**：`box-right-frac-is-not-a-tip-intent-verdict` /
  `owner-subset-tip-remap-is-not-deployable-edge` /
  `frozen-visual-embed-red-means-no-dual-detector-train`。
- 活文档已更：`analysis/p_judgment_layer_lab.md` §2/§3/§7。

### 不变纪律

- **训练默认 3060**（`FABLE_3060_HOST`≈`zzc@192.168.1.3`；本机不开训）。
- **判断层 IT-00~15**：决策时刻**无可交易方向边**；判断层下一角色若继续 =
  过滤/是否交易/仓位，**不**再赌选边。
- **holdout N=7**；**未** promote / 开空 / 改 ACTIVE；detector=none；三门 30min；
  `forward_log` 0 业务行。
- **E1–E3 / 双检测器** 归档勿复活。

### 明早一键（仅当 Owner 选 §7-2；非双模）

```bash
# 连通（3060 通宵探测过：空闲、C:/fable 在）
FABLE_3060_HOST=zzc@192.168.1.3 bash scripts/sync_v16_to_windows.sh --check
# 大样本重扫规格需 Owner 点头后再写具体 scan 命令；权重在 Mac/3060:
#   models/owner_v16_tipuni_cold.pt （未晋升，仅研究用）
```

---

## ⚡ 当前真相（2026-07-24 凌晨 — 判断层实验室定论；勿烧 #8）（历史）

- **训练默认 3060**：YOLO 训练/微调/GPU 重训一律走局域网 Windows RTX 3060
  （`FABLE_3060_HOST` 默认 `zzc@192.168.1.3`，IP 会漂；WMI 开训；Mac 只建数据+sync+验收，
  **本机不开训**）。通道见 `scripts/sync_v16_to_windows.sh` + `v16_train_start.sh` /
  `train_on_3060.sh`；笔记 `docs/learnings/yolo-train-ships-over-ssh-to-3060-not-usb.md`。
- **判断层实验室 IT-00~IT-13 诚实定论**（未碰 holdout#8）：检测✓、动作真✓（oracle 2.68）、
  多空互补✓，但**决策时刻无可交易方向边**——选点 / 方向 / regime（5 角）/ 入场续势+fade
  全穷尽，落 ~1.0 或最近期塌。活文档 `analysis/p_judgment_layer_lab.md` §2/§7；
  learning `docs/learnings/dense-cluster-has-no-causally-tradeable-direction-edge.md`。
- **E1–E3 亦不解锁边**（归档，勿再申请 #8）：见下方历史节 / `p_entry_align_and_regime` /
  `p_e3_sparse_and_two_stage` / `p_chain_failure_attribution`。
- **holdout 记账 N=7**（#7 = A 空边趋势出证伪）。**未** promote / 开空 / 改 ACTIVE。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE=v11 frozen 文本指针 /
  阈值 / TP·SL **未改**；`forward_log` 仅表头（0 笔业务行）。
- **Owner 声明（须尊重）**：他框的是 **tip**，不是确认态。现有指标
  （`box_right_frac` 中位≈0.50）与之矛盾时，应**审计映射/阈值是否冤枉他**，勿否定意图。
- **IT-14 当时进行中** → 后续通宵节已收口红灯。
- **出路(需 owner 决策)**：见更新后的顶部通宵节 / `p_judgment_layer_lab.md` §7。

## ⚡ 当前真相（2026-07-23 深夜 — E1对齐抬召回边死；E2 atr门不修4月）（历史）

- **E1/E2 发现级（未碰 holdout#8）**：E1 重写入场对齐 owner short → 召回 25%→94%
  但 Jaccard 更差（0.045→0.018），因果 `no_tp` PF~**1.14**（相对 spread 1.415 倒退）；
  E2 `not_btc_up` 空转，`atr_q34` 抬至 1.607 仍救不过 2026-04（0.845）。**不申请
  holdout**。报告 `analysis/p_entry_align_and_regime.md`。
- **holdout 第 7 次（归档）**：A 因果空边趋势出证伪（PF@maker 0.997/0.969）。
  报告 `p_short_trend_holdout7.md`。**未** promote / 开空 / 改 ACTIVE。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=7**。
- **出路(历史，已被判断层定论覆盖)**：E3 稀疏化等 — **勿**再为 E1/E2/E3 烧 #8。

## ⚡ 当前真相（2026-07-23 深夜 — holdout#7:A 因果空边趋势出证伪）（历史）

- **holdout 第 7 次消耗完成（owner 批只测 A）**：**证伪**。`spread_expand` short +
  `no_tp_sl2` / `trail4` 在 ≥05-04 窗 PF@maker **0.997 / 0.969**（train 1.415 / 1.359），
  净约 0 / −0.53；扣 0.2% 更差。报告 `analysis/p_short_trend_holdout7.md`。
  **未** promote / 开空 / 改 ACTIVE。
- **A/B train 背景**（已归档）：空边趋势出曾月度过线；B oracle≫规则但事后。见
  `p_short_trend_ab.md` / `p_trend_exit_base_rate.md`。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=7**。
- **v16 tip-replay 终审已完成且证伪**（holdout#6）:1206 笔 · 胜率 29% · PF 0.78 ·
  净 −2.82；v11 判断反预测。报告 `analysis/p_v16_holdout_verdict.md`。
- **多空人工闸门（流式）已就绪**：http://127.0.0.1:8765/gallery.html ；旁路攒 tip。
- **出路(需 owner 决策)**:本挑战者收口；继续旁路攒真实 tip（v17）/ 换命题；
  勿再为同一 A 规则烧 holdout。

## ⚡ 当前真相（2026-07-23 深夜 — 空边趋势 A/B:月度过线；oracle≠规则）（历史）

- **A/B 已跑（未碰 holdout）**：空边 `spread_expand`+趋势出 **月度口径稳健过线**
  （no_tp **1.415** / trail4 **1.359** / trail3 **1.339** / ema55 **1.316**；月 top2
  净利≈51–58%）；但**季度集中 + 2026-04 翻车**。B：owner short oracle PF6–17 ≫
  规则，属事后确认态，**可部署仍认规则**。建议 holdout#7 只测 A 因果（不测 oracle）。
  报告 `analysis/p_short_trend_ab.md`。
- **Owner 已批趋势出场**（按趋势理解 / 改出场 / 目标=净收益·PF）。固定入场
  `spread_expand_chg8`+next_open；**空边** `no_tp_sl2` PF@maker **1.415**、trail3
  **1.339**、ema55 **1.316**（皆≥1.3）；多边全 <1.0。报告
  `analysis/p_trend_exit_base_rate.md`。**未**碰 holdout / ACTIVE / 三门 / 开空。
- **多空人工闸门（流式）已就绪**：打开 http://127.0.0.1:8765/gallery.html ，L/S/K 标；预览后台持续渲染（`stream_owner_side_pack.py`）。填完跑 `scripts/owner_side_feature_verdict.py`。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=6**（本轮研究**未**再耗；**maker-on-holdout 未做**，仍属需 owner
    另批的第 7 次选项）。
- **v16 tip-replay 终审已完成且证伪**（holdout#6）:1206 笔 · 胜率 29% · PF 0.78 ·
  净 −2.82；v11 判断反预测（过线 157 笔 PF 0.60；top5% PF 0.48）。未 promote。
  报告 `analysis/p_v16_holdout_verdict.md`。
- **Owner 标框手法裁决（未碰 holdout）**:oracle 选点 train PF **1.183**（相对 emergence
  0.87 有增量），但可部署因果规则 PF **0.869≈emergence 无增量**——手感来自事后确认态，
  **不是**盘口 tip 因果 alpha；勿赌 v17 tip 金标继承 1.18。
  报告 `analysis/p_owner_label_feature_verdict.md`；
  learning `owner-label-oracle-alpha-is-not-causal-tip-alpha.md`。
- **启动入场分多空（未碰 holdout）**:上一轮混边 PF 是**测量呈现 bug**（已降权）；
  分边后多边全 ≤**0.94**，空边最好 spread-short **1.245**，**皆未过 1.3**。
  主报告 `analysis/p_launch_entry_long_short.md`（混池对照已降权链自
  `p_launch_entry_base_rate.md`）；learnings
  `long-short-must-be-split-in-base-rate-tables.md` /
  `mechanical-launch-entry-lifts-pf-but-not-past-1.3.md`。
- **因果择向结论（未碰 holdout）**:**择向未救出可交易边**——排列/突破/spread 最好仍
  spread-short **1.245**；排列跳过 43% tip 也抬不过 1.3。报告
  `analysis/p_direction_select_base_rate.md`；learning
  `causal-direction-select-does-not-rescue-pf-past-1.3.md`。
- **出路(需 owner 决策)**:holdout#7 测 A 因果空边趋势出（no_tp 或 trail4）？/
  影子纸面？继续攒 tip（旁路）/ 多边另开。默认见 `p_short_trend_ab.md`。

## ⚡ 当前真相（2026-07-23 深夜 — 研究收口:oracle≠tip,启动/择向皆未过1.3）（历史）

- **多空人工闸门（流式）已就绪**：打开 http://127.0.0.1:8765/gallery.html ，L/S/K 标；预览后台持续渲染（`stream_owner_side_pack.py`）。填完跑 `scripts/owner_side_feature_verdict.py`。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=6**（本轮研究**未**再耗；**maker-on-holdout 未做**，仍属需 owner
    另批的第 7 次选项）。
- **v16 tip-replay 终审已完成且证伪**（holdout#6）:1206 笔 · 胜率 29% · PF 0.78 ·
  净 −2.82；v11 判断反预测（过线 157 笔 PF 0.60；top5% PF 0.48）。未 promote。
  报告 `analysis/p_v16_holdout_verdict.md`。
- **Owner 标框手法裁决（未碰 holdout）**:oracle 选点 train PF **1.183**（相对 emergence
  0.87 有增量），但可部署因果规则 PF **0.869≈emergence 无增量**——手感来自事后确认态，
  **不是**盘口 tip 因果 alpha；勿赌 v17 tip 金标继承 1.18。
  报告 `analysis/p_owner_label_feature_verdict.md`；
  learning `owner-label-oracle-alpha-is-not-causal-tip-alpha.md`。
- **启动入场分多空（未碰 holdout）**:上一轮混边 PF 是**测量呈现 bug**（已降权）；
  分边后多边全 ≤**0.94**，空边最好 spread-short **1.245**，**皆未过 1.3**。
  主报告 `analysis/p_launch_entry_long_short.md`（混池对照已降权链自
  `p_launch_entry_base_rate.md`）；learnings
  `long-short-must-be-split-in-base-rate-tables.md` /
  `mechanical-launch-entry-lifts-pf-but-not-past-1.3.md`。
- **因果择向结论（未碰 holdout）**:**择向未救出可交易边**——排列/突破/spread 最好仍
  spread-short **1.245**；排列跳过 43% tip 也抬不过 1.3。报告
  `analysis/p_direction_select_base_rate.md`；learning
  `causal-direction-select-does-not-rescue-pf-past-1.3.md`。默认建议收口，不值得开影子。
- **出路(需 owner 决策)**:继续攒真实 tip 分布（旁路，勿当救命主线）/
  或收摊换命题。默认建议见上述报告的「下一步」。

## 2026-07-23 夜 — v16 终审:证伪,不上线（历史）

- **holdout 第 6 次消耗完成(owner 预授权)。v16 tip-replay 终审 = 决定性负面,未 promote。**
  窗口 05-04~07-16 · 15 币 · **1206 笔 · 胜率 29% · PF 0.78 · 净 -2.82**(纯检测,亏损)。
- **判断层反预测(最关键发现)**:v16 fire 过 v11 判断层的 157 笔 PF **0.60**(更差);
  **判断分越高越亏**(top5% PF 0.48)。根因:v11 判断在"事后"候选上训练,拿到盘口
  "启动前"候选上是反向选择器。**整套 v16检测+v11判断 被证伪,不可交易。**
  报告 `analysis/p_v16_holdout_verdict.md`;learning `hindsight-trained-judgment-is-anti-predictive-at-the-tip.md`。
- **出路(需 owner 决策)**:两层都用真实盘口数据重训(v17 检测器 + tip 时刻→tip 后真实
  收益 重标定判断层),`collect_real_tips_pulse.py` 已每脉冲攒数据,owner 审 review_sheet 是闸门;
  **或**正视"实时盘口下该形态可能本就无扣成本 alpha"这一诚实可能。实盘维持空转。
- 回测搬 3060 GPU(~4h→~30min);loader 加编码容错(一个坏字节曾崩全run);前端回测页
  切 v16 tip-replay 数据源(旧 PF 6.61 折叠为"已废弃事后方法学")。

## 2026-07-23 傍晚 — 回测终审授权（历史；⑥已完成）

- **v16 判决反转(owner 目视 + 我逐图核实)**:金标"51.5% 误火"作废——那 33 张
  tip-empty-ok 是规则自动预标(非 owner 真值),v16 在 BONK/CAP/EDEN 右缘的框全在
  真实密集启动上,**是正确检出不是误火**。标签比模型错。教训:自动标签不得当裁判。
  画廊 `analysis/output/v16_empty_falsefire/`。
- **改用回测终审(owner 指令:让钱判,不让标签判)**。逐 bar 盘口 tip-replay
  回测器 `scripts/backtest_tip_replay.py`(检测器只见过去 / TP5·SL2 / maker 成本 /
  A′ 贴边门 / MIN_GAP)。小样(DOGE 单周)9 笔 PF 3.36 净 +5.6%,仅信号级不作数。
- **holdout 第 6 次**:当时预授权 + 条件闸门(pre-holdout ≥30 笔且 PF≥1.3…)后**已触发并完成**
  （终审负面，见上方「夜」节 / `p_v16_holdout_verdict.md`）。完整记账:
  ①07-08 2b ②07-15 回归 ③07-16 v8 ④07-17 v10 ⑤07-18 v11
  **⑥07-23 v16 tip-replay 终审（已完成·证伪）**。其后 **⑦07-23 A 因果空边趋势出
  证伪**（见顶部；**当前 N=7**）。注:v16 训练数据全在 05-04 前,故当时 pre-holdout 偏乐观。

## ⚡ 当前真相（2026-07-23 白天 — 实盘检测教义落地）

- **Owner 教义(纪律 12)**:实盘检测 = 最新盘口,任何"只能产出事后/延迟信号"的东西
  一律清除。已执行:①pre-v16 检测器权重**三机全删**(Mac/VPS/Windows,含现役 v12 与
  回滚备份;仅存 COCO yolo11 底座);②live 扫描删除回看窗,只扫 **tip/tip-1/tip-2**;
  ③无检测器期间 VPS 脉冲**诚实空转**(detector=none 日志,K 线照更、账本照结、TG/执行
  器静默)。看板状态条会显示权重不存在——这是事实,不是故障。
- **现役检测器:无**。**v16 已训完并验收:未过线**(应开火 3/9,空背景误火
  **17/33=51.5%** vs 要求 ≈0)——统一渲染管线没治好误火,窗末几何捷径或标注
  语义不可分仍在,见 `analysis/p_v16_tipuni_train.md`。**不 promote**,空转继续。
  **主建议已升级:训练分布必须以真实盘口 tip 窗为主体**(owner 审 48 张 +
  扩采 + `label_live_tip_1000` 盘口打标),v17 = 真实盘口分布首训,等数据。
- **v17 数据引擎已上线(2026-07-23)**:`scripts/collect_real_tips_pulse.py` 接入每轮
  VPS 脉冲(旁路,无 YOLO,120s 预算,只写 `data/real_tip_collect/`)——每脉冲采
  规则密集 tip 候选(owner 审:launch/hardneg,限 10/轮)+ 真实空背景负样本(免审,
  8/轮),MIN_GAP 去重。`scripts/build_real_tip_review_pack.py` 把 manifest 变审阅
  画廊 + review_sheet + LS 任务。**detector=none 期间照常采集**,为 v17 攒真实分布。
  Owner 动作:(a) 填 `v13_real_tip_preview/review_sheet.csv`(48 张,已有);
  (b) 数日后审 `real_tip_review/`(扩采批)。

## 当前真相（2026-07-23 凌晨）

- **数据集大清理（Owner 指令,07-23）**:旧式"非盘口分布"数据集全部隔离进
  `datasets/_deprecated_pretip/`（dense_15m_full / dense_2025h2 / dense_2026h1 /
  dense_owner_v11 / dense_owner_v12_htip / dense_swap_v1,共 11G,**任何训练禁用**;
  保留原因=golden_pool 12567 框的窗口消歧存档,见该目录 README）。错窗废品
  dense_owner_v13_pad200 已物理删除。存活:v14_pad200（v16 正样本源）、v15_tipval
  （v16 val 正样本源）、**v16_tipuni（现役）**、label_live_tip_1000（盘口打标包）、
  owner_eval_frozen（旧任务尺子,只作参考不作 tip 裁决）。
- **v16 val 修正（Owner 目检抓出）**:v14 的 val 从未 tip 对齐,v16 曾整拷 →
  1509 张中段 val 正样本已换成 v15 的 803 张 tip 对齐版;3060 已用正确 val 重启
  `owner_v16_tipuni_cold`（yolo11n 冷启动,v12 永不作底座——Owner 裁定）。

- **真实 tip 成败小样已开干（Owner 已点头）**：VPS 采集 →
  `analysis/output/v13_real_tip_preview/index.html`（tip+0 **48** 张预标：hit4 /
  miss-dense6 / noise5 / empty33）。报告 `analysis/p_real_tip_collect_started.md`。
  **下一步=Owner 目视填 `review_sheet.csv`**；审过才谈扩采/开训。**未**开训、**未** promote。
- **v15 败因定论（07-23）：正负样本两条渲染管线（风格捷径）**——训练集正样本
  100% `_pad200` 重渲、负样本 100% 旧式原图，模型学风格不学密集 → val mAP 0.72
  虚高 + 真 tip 空背景误火 58% + 真密集 0/6 全漏。**修复 = v16 一条管线渲染一切**
  （规格见 `analysis/p_v15_dataset_confound.md`，待 Owner 批）。
- **v15 已裁（07-23）：Hypothesis B 否决**——val 也 tip-align 后 tip_hit 仅 **0.017**、
  tip-smoke 仍 **0/27**，未向 v12 的 0.925 恢复。公平重验（full-MA + 真 tip 分母）
  仍否决：应开火 2/9、空背景误火 19/33，见 `analysis/p_v15_revalidate_fair.md`。
  **未 promote**，主线仍 v12。
- **tip 验收协议审计（07-23，Owner 质疑触发）**：tip_hit（val 重渲）与 tip-smoke
  （实盘同管线）测的不是同一件事；v12 的 0.925 属**过宽赦免**（slice-MA + 同分布 val），
  以后 tip 裁决以 **tip-smoke 为准**。见 `analysis/p_tip_eval_fairness.md`。
- **v14 tip 根因已写清（未过线）**：`analysis/p_v14_failure_rootcause.md`。
  主因 **C 语义≠盘口 tip**；**勿再同构 pad200**；主线仍 v12。
- **H-DET 状态**：H-DET-1 🔴（v13+v14+v15）；H-DET-7 🟢；议程
  `docs/RESEARCH_AGENDA_DETECT.md`。
- **v14 终局数字**：3060 ep26 / best=ep16；`models/owner_v14_pad200.pt`；报告
  `analysis/p_v14_pad200_train.md`。v13 错窗审计 `analysis/p_pad200_cut_audit.md`（已修仍挂）。
- **前端可视化真落地**（不抢 MPS）：前向 Tabulator + 状态条 train/fresh/tip + LWC 密集框/调试入口 —
  见 **`analysis/p_frontend_viz_opt.md`**（预览 `uvicorn …:8642`）。
- **夜间旁路（不抢 MPS）已落地**：LWC hardneg 批量 / 叠框画廊 / LS 小包 / Protections 规格 —
  见 **`analysis/p_overnight_20260722.md`** + `analysis/p_wuzao_topics_scan.md` A 档「已做」。
- **本机旁路工具集（发现级收尾）**：`.venv-tools` + `.venv-fo`；supervision 叠框 / FO 小批 /
  LS check / nvitop·mitm·marimo·profiling / ML4T+LEAN 只读对照 —
  见 **`analysis/p_side_tools_landed.md`** + `docs/LOCAL_DEBUG_TOOLS.md`（不杀 v13、不装 VPS）。
- 近期讨论过、现在不做的优化（检测 tip + 判断/执行/风控）统一记在
  **`analysis/backlog_future_optimizations.md`**——瓶颈仍在 tip；判断层多数要等 tip 通了再拧。
  判断层开源专搜（校准/熔断/一致性积木，无现成两层整机）见该文 **B4**。
- **议程与实盘**：不是「没按 `RESEARCH_AGENDA` 走」，而是旧 H9→H10→H1 发现级已结；
  07-20 起优先队列就是 H-TIP + 前向 100。实盘运维与 tip 迭代并行；H1/H3/H16 等确认级排队等 tip。
- **VPS 装机（Kuma/Grafana/exporter）**：仅清单 `docs/ops/VPS_OBSERVABILITY_PENDING.md`，**未装**。

## ⚡ 2026-07-21（A′ 贴边入账过滤上线）

**Owner 批准并已落地**：YOLO live/tip 入账只收扫描窗最后 **N=2** 根
（`bar_in_win ≥ 198`；按 bar 偏移而非像素%）。KORU 类 tip−3 / EDEN 中段框不再进账本；
脉冲日志 `tip_edge_rejected=`。**不过滤≠产生 tip**——模型 tip/tip−1 仍 0 框则
fresh 仍可为 0。见 `analysis/p_box_to_bar_lag.md` A′、`TIP_EDGE_BARS`。
三门 30min / 阈值 / TP·SL / tiered / forward_log **未改**。

## ⚡ 2026-07-21（tiered sizing 真仓上线 · 口径①）

**Owner 批准**：tiered 上 VPS 实盘；口径 **① 基础仓位减半**（不提杠杆、不充值）。

**已上线核验**（VPS live，equity≈**92.46U**，lev=3，max_concurrent=1，KILL 未置）：
| tier | size_mult | 名义 USDT | 保证金≈名义/3 | vs 权益 |
|------|-----------|-----------|---------------|--------|
| q90–q95 | 1.0 | ~138.7 | ~46.2 | 半仓 |
| q95–q99 | 1.5 | ~208.0 | ~69.3 | OK |
| q99+ | 2.0 | ~277.4 | ~92.46 | **=权益，≤可用** |

公式：`unit = (equity×lev) / 2`，`notional = unit × size_mult`（真乘仓位，`tier_headroom=True`）。
sidecar `sizing_tiers` q95≈0.02548 / q99≈0.04857；阈值仍 **0.02022**；三门 **30min**；
TP5/SL2；**未** clear forward_log。forward_log 已有 `tier`/`size_mult` 列（老行缺列=1x）。

**回滚**（止血 → 恢复 1x 满槽，去掉乘数）：
```bash
# 1) 立刻停新开仓
ssh root@206.237.14.112 'touch /opt/fable-trading/data/executor_KILL'
# 2) 回退 executor 头寸公式：把 unit_notional 段改回 notional=base*size_mult
#    或 git checkout <pre-headroom> -- src/execution/executor.py 后 rsync + restart
ssh root@206.237.14.112 'systemctl restart fable-executor'
# 3) 恢复开仓：rm data/executor_KILL
```
完整撤 tier：sidecar 删 `sizing_tiers` + forward 停打标（需另一次 owner 批准）。

**风险重申**：q99+ val 仅约 **41** 笔；2x 止损冲击 ≈ 名义×(2×atr)/权益，满档接近单笔打满保证金。
确认级仍靠前向新鲜 100 笔。

**五项其余进度**：滑点报告 ✅；tip 子集 / v12 池 / 晨报见并行会话。status-strip 新鲜度门已对齐。

## ⚡ 2026-07-20 夜（owner：检测主线 = v12）

**Owner 拍板「主线直接换 v12」**（检测层强制 promote，**未**耗 holdout）：
- `models/owner_best.pt` = H-TIP v12（tip_hit **0.925** / frozen-F1 **0.650**）
- 备份回滚：`models/owner_best_pre_v12.pt`（原 v11 chain F1 0.658）
- **判断层未改**：`ACTIVE` / `frozen_tp5_sl2_swap_yolo_v11_reg_20260718` / 池 v11  
- 报告：`analysis/p2a_v12_mainline_cutover.md` + `analysis/p_v12_htip_eval.md`
- 无 v12 历史组合回测；确认级仍靠前向 100 笔新鲜

**影子**：`FABLE_V12_SHADOW` 可关（主线已是 v12）；留作对照亦可。

## ⚡ 2026-07-20（实时 tip 路径上线）

**盘口 bar 当场入账**（commit 67d8733，已部署 VPS）：信号 bar = 最新收盘 bar 时
不再丢弃——当脉冲即写入账本（status=open，entry_time=下根开盘时刻，entry_price=
信号 bar 收盘价代理，maker_filled 留空作待回填哨兵），TG 立即通知、执行器立即可
开单；下一脉冲由 merge 回填真实下根开盘入场（detected_at 保留首见，延迟统计不失真）。
检出落账时点从信号后 31~37min 压到 **16~23min**。离线建数据集路径不变（仍要求入场 bar）。

**新鲜度三门统一 30min**（执行器 max_signal_age_min / TG 过滤 / 看板 FRESH_DETECT_MIN）：
30 = 15（bar 时长）+ 7（脉冲对齐+344 币扫描）+ 余量。**20 会结构性挡死一切**
（旧管道最快 31min 才能入账），55 会放进非 tip 迟到检出——阈值必须从管道时序推导，
见 `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`。
端到端保护：`tests/test_tip_realtime_path.py`。

**依赖**：实时 tip 依赖会在盘口开火的检测器——**现主线已是 v12**（原 v11 tip≈0.9%）。

**脉冲性能（2026-07-20 实测）**：update 76s + discover ~500s + phase2 1s ≈ 10min
< 15min 节拍，最坏落账龄 26min < 30 门。已做：14→6 窗、全帧→2000 根尾巴
（特征偏差 3e-07、渲染逐像素一致）、每币批量 predict（无增益——证明瓶颈是 YOLO
前向计算本体 ~0.24s/窗 × 2064 窗全局串行）。剩余可选杠杆（暂缓）：v12 上线后削减
回看窗 6→3-4；或每 worker 独立模型实例并行 predict（VPS ~2 核，预计 ~1.7x，
代价是内存与复杂度）。阶段耗时每轮打印（discover_wall / phase2_wall）。

## 2026-07-19 晚间（H-TIP / 事后检出）

> 注：本节「新鲜度 20min」已被 **07-20 顶部「三门 30min」** 覆盖；以顶部为准。

**定性**：打标/训练不是「全错」，是**分布错位**（框多在图中、右侧有启动后文；
实盘 tip 无后文）。对 tip 开单：检测层欠训；金标形态仍有用。见
`analysis/p_forward_hindsight_20260719.md`。

**前向（当时）**：10 行 **0 笔 lag≤20m**；EDEN `tip_fire=false`。  
**H-TIP 本机**：`dense_owner_v12_htip` → train `owner_v12_htip`。**不自动 promote**
（进度/通过线见 `analysis/week_plan_20260720.md`）。

## ⚡ 2026-07-18 主线快照（池仍 v11；细节历史）

**主线**：YOLO 检测（`owner_v11_chain`，frozen-F1 **0.658** → `models/owner_best.pt`）
→ 回归判断（`frozen_tp5_sl2_swap_yolo_v11_reg_20260718`，阈值 val-q90=**0.02022**，
池 `judgment_yolo_swap_v11.csv` · **26653** 候选 / 344 币）→ TP5/SL2 出场。
`models/ACTIVE` 与 `frozen.default_config()` 均已指向 v11 池。

**accept 回测（holdout 第 5 次消耗，owner 批准全量切流；完整记账：①07-08 2b ②07-15
回归切换 ③07-16 v8池 ④07-17 v10池 ⑤07-18 v11池）** @0.3% 成本：
**703 笔 · 净资金 +245.8% · PF 6.61 · 胜率 77.1% · maxDD 0.76%**（验收 4/4）。
对照 v8：428 笔 / +154.9% / PF 7.50。见 `analysis/p3_v11_pool_cutover.md`。

**执行层（VPS）**：`fable-executor` active · keys `environment=live`（~92U 权益）·
`fable-forward.timer` **每 15 分钟** YOLO live 脉冲 · `ENABLE_JOB_EXECUTOR=0`。
TG 通知只推 `status=open` 且 signal_age 新鲜（**现为 30min 三门**，见顶部 07-20；
本节写于 07-18 时曾用 20min）。无新鲜 open 时执行器安静空转——属正常。

**前向时钟重启（owner 2026-07-19）**：清空主线 `forward_log.csv` 重测 v11 闸门；
旧账本归档 `data/forward_log_pre_v11_retest_20260719.csv`；
`FORWARD_START=2026-07-18 16:15 UTC`（对齐最后收盘 bar，避免「start 在未收盘 bar 内」导致
candidates_seen=0）。裁决计数从 0 重计至 100。

**2026-07-19 链路优化**：tip 扫描在 start 超前数据时不再整表跳过；脉冲 `update_okx
--swap-only`；YOLO live 多线程发现 + predict 锁；时钟/设备日志。

**2026-07-19 实盘加固（overnight）**：
- forward timer 对齐 15m 收盘后 1 分钟（`:01/:16/:31/:46`）
- 脉冲结束立刻 `executor --once`（不等 30s 轮询）
- 括号 OCO 失败重试 2 次；ledger 计入 `order_partial` 防重复开仓
- 新鲜度 20min；轮询 30s；paused 不再每轮刷 ledger
- `scripts/live_health.py` + 30min timer TG 告警

**2026-07-16 快照（已被上方覆盖）**：v8 检测+判断；accept PF 7.50 / 428 笔。

**今天推翻的历史结论**（详见 `analysis/p2a_lr_bug_audit.md` + `p3_v8_pool_cutover.md`）：
- `optimizer='auto'` 的 lr=0.002 炸掉了**所有** chain 续训（epoch 3 精确崩溃，
  best.pt=epoch 1）——v7 及之前的 chain 模型等于没训过；已修（`FINETUNE_OPT` lr=1e-4）。
- "v6 0.595→v7 0.625 证明加标注有效"——撤回。干净的学习曲线（嵌套三臂，同机同val）
  给出真答案：**F1 ≈ 0.067·log2(train图数) − 0.265，未饱和**。
- "coco 血统连输两轮已弃"——补跑后反而证实（v8_coco 0.549 ≈ v6_coco 0.554）；
  但续训血统更强（0.650）。
- 旧判断池（101 币，脏检测器）→ 新池（267 币，17573 候选）：accept 窗口全指标胜，
  **holdout 第 3 次消耗，owner 明确批准**（第1次 07-08，第2次 07-15）。

**冻结尺子已物化**：`datasets/owner_eval_frozen/MANIFEST.json`（47 币/464 图）；
`is_eval` 查清单优先（两个拼写泄漏向量已封死：`_SWAP` 后缀 + `okx_` 前缀）。
**标杆基建**：`data/benchmark_exemplars.json`（176 张）；`scripts/benchmark_check.py`
体检门（训≥0.90/评≥0.60）已入 v9 流水线；**152 张标杆 ≈ 1600 张普通标注（10倍质量杠杆）**；
过采样×3 已证伪（0.636<0.650）。

**进行中**：owner 打标 round7（1000/3000，chunk3-6 已换 v8 预标）→ 标完跑
`bash scripts/train_owner_v9_from_round7.sh`（90% 闸门；曲线预测 v9_coco≈0.584 已登记）。
**训练一律走 3060**（`zzc@192.168.1.5`，7 倍速；WMI 启动防 SSH 杀进程；
`--cache false --workers 4` 防 16GB 内存爆；见 memory/training-on-3060.md）。

**最大未决疑点**：PF 7.5 属"好得反常"——检测层训练无时间切分（~2.5% 标注图落在
accept 窗口内）是结构性弱点；**前向 100 笔规则是唯一最终裁决**。v10 应登记
"检测层训练图截止 2026-05-04" 实验。

---

**写于 2026-07-08。** 读完本文件 + `CLAUDE.md` + `analysis/p2b_v2_report.md`，即可无损接手本项目。


## YOLO 主线（owner 2026-07-15 切流）
**候选源=YOLO（owner_best）+ 判断=冻结 `tp5_sl2_swap_yolo_20260715` + 出场仍 TP5/SL2。**
前向时钟从 2026-07-15 重启；规则时代 `forward_log` 已归档
`data/forward_log_rules_pre_yolo_20260715.csv`。说明见
`analysis/p2a_yolo_mainline_cutover.md`。A/B 报告：`analysis/p2a_yolo_critical_path_ab.md`。
round6 新标后只换检测权重再重扫；回滚规则：`CANDIDATE_SOURCE=rules` + 旧冻结。

## 当前状态一句话

**07-10 最新 owner 裁决（覆盖 07-09 均线决定）**：检测层、判断层及未来运行路径统一为
**SMA20/60/120 + EMA20/60/120**。新 ACTIVE 为
`models/frozen_tp5_sl2_swap_ma206_20260710.txt`，阈值 0.340933，数据 SHA256
`8df081a1...`；新前向账本从 `2026-07-10 10:30 UTC` 起独立累计。
迁移报告见 `analysis/p2b_ma206_mainline_migration.md`。全量 MA206 val AUC 0.5702/p=0.001；
0.3% 组合 PF 0.636；maker 0.06% PF 1.072，1h EMA120 过滤后 PF 1.154，
**尚未达到盈利验收线**。
看板迁移验收时发现并修复全量评分越界；**这是 MA206 配置第 1 次意外消耗 holdout**，
未经 owner 批准，结果隔离作废。当前 API/缓存只允许 `pre_holdout_only`，终审仍只认新前向。

**07-09 历史记录**：合约复制性检验通过，旧 `frozen_tp5_sl2_swap_20260709`
曾作为冻结工件；该工件和当时的 H1 PF 2.825 均属于 8-55 历史证据，现已由
`frozen_tp5_sl2_swap_ma206_20260710` 与独立 MA206 前向账本替代，不得再作为运行入口。

**2b 验收通过（holdout 已消耗）→ 阶段 3 第一轮未通过（PF 1.01@0.3%）→
owner 已委托"按推荐直接执行" → 出场结构扫描完成：TP5/SL2 为 v3 候选标签**
（val 净@0.3% +0.077%/笔 vs 基线 +0.001%，p=0.001，见 `analysis/p2b_v3_barrier_sweep.md`）。
**07-11 最新验收**：P2-11 E2.1b HSV0 自然完成，official mAP50 `0.8505`、固定
conf=0.30 一致率 `51.27%`，均未过门；固定 SAHI 全 val 使匹配 `665→625`、预测框
`1629→2753`、延迟 `11.27×`，拒绝接入。独立因果 long/short/no_trade YOLO 分类器
准确率 `34.78%`，固定 0.20% 成本后净 `-0.15236%/笔`、PF `0.7472`，同样拒绝接入。
q80 只诊断影子继续运行；截至 `19:45 UTC`，358 个 SWAP 同窗漏斗为
`67 候选 → q90 10 可执行 / q80 16 可执行`。这证明扫描和评分都压缩信号，但放宽到
q80 只增加 6 个可执行信号，不能替代前向盈利验证。
**07-10 追加（Grok）**：`codex/day1` 已合并进 `main`（`1c1344f`）并 push；owner 确认
P2-11 打标 findings + P2-12 黑名单写入 BLOCKED。  
**07-10 追加（Grok 接手）**：P2-12 数据审计完成（见 `analysis/p2_data_audit_report.md`）；
每日定时任务已含 `update_okx → forward_track → daily_digest`；正式窗口前向日志已有
**2 笔** closed 信号（冻结 TP5/SL2 SWAP）。`src/notify.py` + `scripts/daily_digest.py`
已同步进本 worktree。
**07-10 追加（多日无人值守）**：SWAP expand **完成**（399 个 15m 文件）；P2.5 Phase0–3 已合 main；
H1 shadow logger 已上线；**YOLO E2.1 正式重训已完成**：official val mAP50=**0.8503**（gate≥0.90 **FAIL**）；consistency match≈0.50；hardlist `fiftyone_hard_e21`；检测层仍非关键。
FO :5151 / Label Studio :8081 本机评审就绪；前向主线 + H1 双账本 digest。
章程：`output/offline_tasks/AUTONOMOUS_CHARTER.md`；状态：`MULTI_DAY_STATUS.md`。
**07-10 追加（P2.5）**：ops 鉴权 + 实验/议程 + **白名单 job runner**（默认 executor 关）+ **只读 data/model hub**。
公网/VPS 上 ops 前须设 `OPS_AUTH_MODE=token` + `OPS_API_TOKEN`；**禁止** VPS `ENABLE_JOB_EXECUTOR=1`。
纪律红线：holdout 与验收窗口均已消耗，v3 的确认性验证只能用前向新数据；
val 已被多次选型使用，其数字只用于排序不用于宣称绩效。
fable 拍板：主线 **SWAP** · **SMA/EMA 20/60/120** · 冻结 **TP5/SL2** · YOLO **非关键** · H1 **挑战者/影子**。

**07-20 追加（Grok，Claude 额度见底）**：主线前向诚实摘要见
`analysis/forward_mainline_status_20260720.md`——`data/forward_log.csv` 仅表头；
早期样本混 stockish；K 线约停在 07-16；**不改主线配置**。过夜规格 v2：
`docs/archive/grok_tasks/overnight_batch_v2.md`（task11–15：前向健康 / crypto-only /
H3 shadow / H16 / H1 续记）。

## 排序后的下一步（期望价值从高到低）

### ~~1. purged CV / embargo 泄漏修正~~（作废，2026-07-08 核实已实现）

原以为 train/val 边界存在标签窗口泄漏——**读代码核实后确认 purge 已在
`src/judgment/train.py` 实现**（`PURGE_WINDOW = 18.25h` = 73 根 outcome 窗口，
dev/holdout 与 train/val 两个边界均清除；与 `labeling.py` 的 entry=i+1、
HORIZON_BARS=72 精确对应）。v2 报告中的全部指标本来就是泄漏修正后的数字。
教训见 `docs/learnings/grep-before-planning-fixes.md`。

### ~~2. holdout 一次性评估~~（已完成，2026-07-08，owner 批准）

结果：AUC 0.602 / p=0.001 / top-decile 净 +0.083% —— **2b 验收通过**，明细在
`analysis/p2b_v2_report.md` 6.5 节。expanded × v2 的 holdout 已消耗，任何后续
迭代不得再评估 holdout（除非 owner 批准并注明"第 N 次消耗"）。

### 原第 2 步存档（执行方式备查）

- **为什么**：这是 2b 的正式验收。v1 已消耗过一次 holdout，v2 每个配置只许评一次。
- **怎么做**：
  `python3 -m src.judgment.train --data data/judgment_dataset_v2_expanded.csv --tag p2b_v2_expanded_final --eval-holdout`。
- **完成的样子**：holdout AUC / p / top-decile 净收益写入报告，明确判定
  "2b 验收通过/未通过"。通过 → 阶段 3；未通过 → 回 val 迭代，holdout 不许再碰。

### 3. 阶段 3：简单事件驱动回测（当前工作，2b 已验收）

- 按 `PROJECT_PLAN.md` 阶段 3 规范：自写 ~200 行事件驱动回测，taker 费 + 滑点 +
  资金费近似；检测（规则扫描）→ 判断（LightGBM 分数）→ 持仓 → 平仓全链路；
- 资金费率历史可用 CCXT 拉（唯一批准引入的新依赖，仅数据用途）；
- 验收标准在 PROJECT_PLAN 里，别改。Freqtrade 只作为回测结果的交叉验证，不做主框架。

### ~~4. 2a 全量训练与正式验收~~（2026-07-09 未达成，非关键路径暂停）

- 离线管道完成：yolo11s 官方评估 mAP50 0.8569 / mAP50-95 0.6643 /
  precision 0.8003 / recall 0.7112；
- 未达到 mAP50 ≥ 0.90，因此不写一致率脚本，不调 conf/IoU/增强凑数；
- 后续主线继续规则扫描 + 判断层 + 前向验证，YOLO 仅保留为已验证可学习的非关键路径组件。

## 停止做的三件事（含理由）

1. **停止给 strict 池单独调参**——2 898 个样本不够 LightGBM 学出超过单特征基线的
   结构（v2 实测模型 0.543 vs 基线 0.556）。扩池已验证成立，主线就是 expanded。
2. **停止在旧缓存数据上跑新实验**——新拉取的 400 天数据在时间覆盖上全面优于旧缓存
   （旧缓存仍参与 loader 合并，但不要再针对旧数据的特性做任何决策）。
3. **停止评估新框架**——2026-07-07 已做过完整评估（见会话记录/README）：
   阶段 3 自写回测，CCXT 仅拉数据，其余一概不引入。

## 未决队列（2026-07-08 深夜快照，两个后台任务当时仍在跑）

1. **YOLO 全量训练已完成**：yolo11s mAP50 0.8569，正式验收未达成，非关键路径暂停。
2. **合约数据**（okx_*_USDT_SWAP_15m_*.csv 落在 data/kline_fetched/）：
   拉完后跑冻结流水线复制性检验——expanded 池 + TP5/SL2 标签在 SWAP 序列上
   build+train（val only），合约成本：maker 0.02%/taker 0.05% + 资金费近似 0.01%/8h。
   owner 已确认实盘目标是合约。
3. **均线定义旧裁决（2026-07-09，已被 07-10 owner 推翻）**：P0-3 曾在合约数据上正面对比
   SMA/EMA 20/60/120 与现行 EMA 8/13/21/34/55+144/200。20/60/120 的 AUC 更高
   但 top-decile 净收益显著弱于 8-55；当时曾保留 8-55。当前及未来只用六线 MA206。
4. **冻结模型工件已完成**：当前生效工件为
   `models/frozen_tp5_sl2_swap_ma206_20260710.txt/.json`，阈值 val q90=0.3409333202，
   best_iteration=32，数据 SHA256=`8df081a1374c0edb1ef8a869cc4825830ecb2f07fd00209306c44dcc272040d1`。
5. 前向跟踪脚本已完成：`scripts/forward_track.py` 默认从
   `2026-07-10 10:30 UTC` 起扫描 OKX SWAP，加载 MA206 冻结模型打分，阈值以上写入
   `data/forward_log_ma206.csv`，并按 `(source, symbol, signal_time)` 幂等补记已知出场。
   **07-10 全量重建**：358 个 SWAP、19,666 个已标签候选；前向扫描见 21,086 个历史
   候选，正式窗口 `new_signals=0`、`total_rows=0`。
6. MA206 前向验证窗口从 2026-07-10 10:30 UTC 起积累；每日定时任务
   `~/.claude/scheduled-tasks/daily-okx-data-update` **已包含**
   `update_okx` + `forward_track` + `daily_digest`（2026-07-10 核实，无需再等点头）。
   ~3-4 周后用冻结 TP5/SL2+maker 配置做最终 PF 裁决。
7. 真实资金费接入已完成：`src/data/funding.py` 读取 `data/funding/*.csv` 的 OKX
   `realized_rate`，按持仓跨过的 funding settlement 累计长仓成本；`swap_replication`
   同时输出旧 maker0.06% 近似和真实资金费覆盖样本结果。当前 funding 数据只覆盖
   54 个 SWAP、约 2026-04-07→2026-07-08，val top-decile 覆盖约 73%~76%；
   TP5/SL2 在当前数据池复跑后净@maker+真实资金费（覆盖样本）约 +0.003%/笔，
   filled-only 为 -0.012%/笔，属于前向验证必须重点盯的风险信号。
8. 看板完善一批已完成：`/api/overview`、`/api/backtest`、`/api/trades`、
   `/api/symbols`、`/api/chart` 均支持 `universe=swap|spot`；分数缓存写入
   `data/scored_signals_<universe>.csv/.json`，spot 训练/打分前会过滤混入的
   `_SWAP` 行。新增 `/api/forward` 和前向验证 tab，当前 `data/forward_log_ma206.csv`
   只有表头，因此页面显示 0/100、PF/胜率为空。VPS 已同步部署。
9. H10 做空侧已完成：新增空头候选扫描、空头 barrier 标签和
   `scripts/short_replication.py`。SWAP short TP5/SL2 val AUC 0.6174、p=0.001、
   top 净@maker +0.205%；但 ma_spread 单特征 baseline 净@maker +0.343%，所以只记为
   发现级 alpha 线索，不改主线。
10. H1/H2 出场复合已完成：`scripts/exit_variants_sweep.py` 已升级为 SWAP-only
    口径，输出 `analysis/output/exit_variants_swap.json`。H1 scaled：
    AUC 0.6106、p=0.001、top 净@maker +0.326%、maker 组合 PF 2.825/maxDD 0.29%；
    H2 breakeven：p=0.1738，不显著。H1 只是发现级候选，冻结主线仍不变。
11. R4 多时间框架已完成：`scripts/mtf_sweep.py` 输出
    `analysis/output/mtf_sweep.json` 和 `analysis/p2b_mtf_report.md`。H7 5m 未带来
    机会数扩张（val 仅 0.63× 15m，filled-only 为负）；H8 30m h72 发现级通过
    （AUC 0.6297/p=0.001/净@maker +0.484%），但样本只有 0.24× 15m；1H 样本太小。
12. P2-9 冒烟测试 + CI 已完成：新增长仓 barrier 四路径、组合模拟同币种/并发不变量、
    loader 合并去重、update_okx 幂等测试；`.github/workflows/tests.yml` 在 push/PR
    运行 compileall + pytest，依赖安装限定在判断层/看板测试链路，不拉 YOLO 训练栈。
13. P2-10 非鉴权部分已完成：看板信号页新增合格未成交列表与 hover/focus tooltip
    （score、阈值差、ATR%、密集长度、标签收益、入场价）；回测页新增只读分数滑块，
    只过滤成交明细表，不重算净值/PF；移动端修复 chart grid 子项撑破 390px 视口。
    owner 2026-07-09 已拍板暂不加访问控制。
14. P2-11 Round 1 打标审计页已生成：seed=20260709，输出
    `src/webapp/static/label_audit.html`，样本清单见
    `analysis/p2a_label_audit_round1.md`。localhost:8643 真实浏览器验证桌面/390px
    手机均无横向溢出。**07-10 owner 确认** findings（PAXG 超宽、边缘残框等）；
    下一步单变量 E1 收 `x_pad_px`，改参前仍不重训。
15. P2-12 数据质量审计已完成（2026-07-10）：报告
    `analysis/p2_data_audit_report.md`；黑名单候选以股票/ETF 类薄流动性 SWAP 为主；
    **07-10 owner 确认** 22 个 base 已写入 `loader.BLOCKED_BASES`。
16. P2.5 Phase 0–3 已完成（2026-07-10）：ops Bearer/`X-Ops-Token` 鉴权、
    实验注册表、议程、**白名单 job runner**（默认 executor 关）、只读 data/model hub。
    VPS **禁止** `ENABLE_JOB_EXECUTOR=1`（`deploy_vps.sh` 强制写 0）。说明见
    `docs/P2_5_PHASE01_README.md` / `PHASE2` / `PHASE3`；设计见 `docs/P2_5_OPS_CONSOLE_DESIGN.md`。

## 明天开工的第一条消息（可直接粘贴）

> 读 CLAUDE.md、HANDOFF.md、analysis/p2b_v2_report.md。2b 已验收通过，
> 当前工作是 HANDOFF 第 3 步：阶段 3 事件驱动回测框架。按 PROJECT_PLAN 阶段 3
> 规范自写实现（taker 费 + 滑点 + 资金费近似），先给出模块划分和成本模型设计
> 让我确认，再写代码。阶段 3 的冻结 holdout 方案也需要先和我讨论
> （2b 的 holdout 窗口已消耗，回测的样本外窗口如何定义是一个待决策问题）。

## 本仓库的知识地图

| 想知道什么 | 看哪里 |
|---|---|
| 为什么做这个项目、旧项目怎么死的 | `README.md` |
| 三阶段路线图与验收标准 | `PROJECT_PLAN.md` |
| 人工标签有没有 alpha（P0） | `analysis/p0_alpha_report.md` |
| YOLO 检测层怎么训、效果如何 | `analysis/p2a_detection_report.md` |
| 判断层 v1 为什么"有信号没利润" | `analysis/p2b_judgment_report.md` |
| v2 双池实验结果与下一步选项 | `analysis/p2b_v2_report.md` |
| 踩坑记录（原子化笔记） | `docs/learnings/` |
| 工作纪律与质量标准 | `CLAUDE.md` |
