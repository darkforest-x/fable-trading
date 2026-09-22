# 1506个3R赢家、两组YOLO训练与完整后段评估：已完成，盈利筛选未达标

完成时间：2026-09-22（UTC+8）。实验：`exp-ma-profit3r-20260922-v1`。

## 结论

按Owner最新“到1500个就处理并训练”的授权，保留1506个去重train赢家，A单图1506张、B有限双视图3012张，RTX3060各完整训练40轮。6812张物理图片包含两个训练臂及共享val/test，全部审计通过。用户指出的图像扁平问题已在开训前改为可见范围价格轴，旧图与旧计划保留。

两组均未达到“后段top-decile扣20bp成本净收益为正且排序p<0.01”的研究标准。A测试top10平均净收益−28.69bp，B为−4.78bp，均仍亏损。B测试相对随机控制有+11.51bp描述性差值，但自身仍亏损、验证集差、排序p=0.145，不能称成功。研究结论登记为rejected；训练与评估任务完成，不promote、不部署、不切ACTIVE。

A在后段全部候选上都触发，包含val1130/1130、test829/829个未达到筛选条件的事件。它定位了这种形态，但没有完成赢家/非赢家区分。B的后段定位命中也很低。仅靠历史盈利筛选正样本，本轮没有得到可用的盈利筛选器；这不等于已证明“加难负例”或“换模型”必然能解决。

## 数据、标签与时间边界

- 原始1043事件按冻结收益规则仅55个合格；扩展后11772个去重候选，TP1848、SL6994、TIMEOUT2921、INVALID4、UNKNOWN5，lineage错误0。规则与人工参考校准未因凑数放宽。
- 最终train1506（LONG659/SHORT847），val1305含175正例，test989含160正例。train仅包含筛选赢家，验证/测试保留完整已解析候选，包括未达标和超时事件。超时不等于亏损。
- train核心时间2020-02-08至2025-12-28；val为2026-01-01至04-30；test为2026-05-01至09-09（UTC）。完整模型输入和12h标签窗口不得跨切点；1200根均线warmup按既定规则处理。69个参考锚点同资产±4h从后段评估隔离。
- train周期：1m709、3m498、5m243、15m54、30m1、60m1、240m0；80.15%为1m/3m。无法声称1h/4h学习充分。官方输入导入27批654币，但容量达到后仅扫描1m前17批424个可用币，不能声称全导入源均已扫描。
- 信号decision为核心末根c+5收盘，下一根开盘入场；多头止损取核心最低，空头取核心最高；先到毛3R且扣费仍正才保留，最长12h。同bar双触发SL优先，止损跳空按更差开盘，TP保守按目标。**这里是毛3R，不是净3R**；训练赢家净R中位2.8197、最低1.5224。
- 去重在看收益前，按同资产/方向4h跨源/跨周期处理。“独立事件”只指去重身份；12h结算仍重叠，不能视为统计独立交易。
- A pre9/post5；B pre7与pre11，均止于c+5。未来12h只用于标签与独立评估，不能进入训练图。当前是确认窗研究模型，不能冒充核心当时的tip信号。

## 两组配方与审计

YOLO11s同一base SHA `85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5`，两组40epochs、patience0、imgsz1280、batch8、AdamW lr1e-4/lrf.01、seed0、deterministic、rect、workers2，翻转/颜色/mosaic/mixup/cutmix/copy-paste/平移/缩放等增强全关。两份args仅data/name/save_dir不同。Mac、3060与CI核心版本合同保持torch2.8.0（3060+cu126）、ultralytics8.4.89、numpy2.0.2、pandas2.3.3。

B每轮图数翻倍，且前文变化会改变局部纵轴范围，因此比较的是完整训练配方，不是纯位置的因果效应。没有把一个事件扩成旧版的许多完成态窗口。

v4 manifest SHA `f0207e44d955fda18e27e0a258f077065db8f995f7fc5f1c0d223201d9fecd4d`。6812PNG/6812labels、13624物理文件通过Mac与3060审核；新旧membership/split/right-endpoint/x-geometry一致，旧13624文件SHA未变。6399图与4735标签因纵轴改变，18张固定样本从原始因果prefix重建与正式文件一致，并实看。专门测试覆盖未来OHLC扰动、标签盲渲染、范围/框坐标、容量与连续性；39项通过。衔接器4项测试通过。完整审计见`dataset_owner1500_v4_audit/receipt.json`。

两组各40轮，原SSH训练exit0；完成回执、best/last、CSV/args共9文件逐项远端/本地SHA与size核对。A/B共同评估各val1305/test989，低conf=.001保留预测，NMS=.70、imgsz1280、CUDA0；固定检测阈值conf=.25、IoU=.5，不按测试集调阈值。排序分数为候选已知方向最大confidence，不借只有正例才有的GT IoU排序。

## 完整后段的经济结果

每笔收益按入场名义价格计，1bp=0.01%，净值已扣0.2%往返成本；不是复利、组合净值或实盘收益。top10按整个split分数排序，平局按event_id，数量向上取整。quality_score为冻结规则单特征基线。

| 组 | 区间N/正例 | AUC | 排序p¹ | top10 N | 毛bp/笔 | 净bp/笔 | 3R目标率 | 净盈利率 |
|---|---|---|---|---|---|---|---|---|
| A | val 1305/175 | 0.5355 | 0.2230 | 131 | -3.34 | -23.34 | 14.50% | 29.77% |
| B | val 1305/175 | 0.4539 | 0.9325 | 131 | -61.92 | -81.92 | 8.40% | 25.95% |
| quality_score | val 1305/175 | 0.4700 | 0.9420 | 131 | -63.31 | -83.31 | 12.21% | 32.82% |
| A | test 989/160 | 0.5157 | 0.3820 | 99 | -8.69 | -28.69 | 16.16% | 24.24% |
| B | test 989/160 | 0.5181 | 0.1450 | 99 | 15.22 | -4.78 | 15.15% | 36.36% |
| quality_score | test 989/160 | 0.4679 | 0.7220 | 99 | -36.53 | -56.53 | 10.10% | 32.32% |

¹ 排序置换1999次、seed0，是对完整池随机排序的单侧描述性诊断。4h去重不消除12h重叠，故p不能当独立交易的正式显著性。即便不使用p作结论，两组四个主表top10净均值也全部为负。

### 同币/时间/波动匹配随机控制

先按冻结同source/资产/周期/方向、UTC ISO周、因果TR14波动桶抽最多5个随机核心，不看控制后续收益；同障碍、同20bp成本。11441个固定控制：TP1846、SL6033、TIMEOUT772、INVALID2790，保留全部结果、不补抽；其中2786个止损方向不合法、4个空头目标非正。每个目标先平均可解析控制，再对目标等权平均。

| 组/区间 | 配对数/top10 | 候选净bp（配对子集） | 随机对照净bp | 候选−对照bp |
|---|---|---|---|---|
| A/val | 131/131 | -23.34 | -14.79 | -8.55 |
| B/val | 130/131 | -69.92 | -21.81 | -48.12 |
| quality_score/val | 130/131 | -81.27 | -18.32 | -62.95 |
| A/test | 98/99 | -28.07 | -0.14 | -27.93 |
| B/test | 99/99 | -4.78 | -16.30 | 11.51 |
| quality_score/test | 98/99 | -47.52 | -7.18 | -40.34 |

完整池可解析对照覆盖val1301/1305、test985/989；缺配对不填0，top10入选后也不补人。A-test缺1、B-val缺1，因此“全部top10净均值”和“配对子集净均值”不同；差值只与同一配对子集比较。2790个INVALID及多数目标不足5个可解析控制带来complete-case覆盖偏差。2个原始控制各复用2次。对照风险距离分布与候选并不相同，R与名义bp不可混为同一衡量。

## 固定阈值检测结果

| 组/区间 | 命中/正例 | 漏检 | 额外/错误框 | 框precision | 未达标候选同方向触发率 |
|---|---|---|---|---|---|
| A/val | 175/175 | 0 | 1132 | 13.39% | 100.00% |
| A/test | 160/160 | 0 | 830 | 16.16% | 100.00% |
| B/val | 1/175 | 174 | 491 | 0.20% | 33.72% |
| B/test | 1/160 | 159 | 365 | 0.27% | 31.60% |

额外/错误框包含正例图上的多余框。event级TP与有FP的event可重叠；表格使用box precision避免混淆。B对未达标候选触发率下降，同时漏掉几乎全部正例，不能只报较少误报。排序结果、框定位和可执行收益是三个不同问题。

## 内置验证、历史指标与预处理诊断

| 训练/历史模型 | 对应mAP50-95 | 比较边界 |
|---|---:|---|
| 本轮A，CSV最高fitness轮18 | 0.01303 | 内置训练val；不能代替冻结单图经济评估 |
| 本轮B，CSV最高fitness轮2 | 0.04653 | 同上；receipt无best_epoch字段，不以此断言checkpoint内部epoch |
| 历史15m、10k正+30k负、960模型 | 0.7923 | 形态标签/人群/尺寸不同，无盈利指标，仅历史背景 |
| 历史15m、8k正+24k负、1280 HL2模型 | 0.77609 | 1043事件、post2–9变体，无盈利指标，仅配方背景 |

历史来源：`analysis/p1_15m_ma_launch_owner_yolo_neg30000_train960_20260828.md`、`analysis/p1_15m_ma_launch_owner_grade_a8000_neg24000_hl2_train1280_20260903.md`。不能用它们的高mAP宣称历史模型会赚钱，也不能跨标签/人群将本轮差异归因于样本数量。

发现内置val与单图预测的预处理不同：安装版代码val使用batch16、rect pad=.5，对1280×742图的公式目标为768×1312；单图predict实际为768×1280。EMA序列化/验证AMP路径也不同。因此训练CSV和外部检测结果不属于完全相同的评估路径。

另固定val按event_id排序的4正4未达标样本，双模型各做rect True/False，共32次CUDA诊断。rect=True的16个重放输出与冻结原预测每个框/分数逐项差为0；actual tensor为FP32、768×1280。rect=False实际1280×1280，A仍4/4定位命中、B仍0/4；部分B分数明显变化。这只证实填充敏感性和原预测可复现，**没有复现内置val的768×1312精确路径，不能据此声称已解释全部差异或修复模型**。正式结果未被诊断替换。诊断最初遇到Windows默认GBK和不存在的args.half属性，均在输出前失败，修订提交后重试；读取实际tensor dtype，未升级依赖。证据`preprocess_diagnostic_owner1500_v4/`。

## 方向与周期分层

每一层内部重新取top10，故不是全局top10的简单拆分。低样本层的正收益不能成为事后挑周期的理由。完整AUC、p、胜率与quality-score分层保留在metrics JSON；此表连同对应随机控制展示净收益和样本量。

| 组/区间 | 分层 | N | 层内top N | 候选净bp（全部top） | 配对N | 对照净bp | 配对差bp |
|---|---|---|---|---|---|---|---|
| A/val | direction=LONG | 606 | 61 | -50.95 | 61/61 | -8.22 | -42.72 |
| A/val | direction=SHORT | 699 | 70 | -54.62 | 70/70 | -15.72 | -38.90 |
| A/val | timeframe=15m | 122 | 13 | -80.01 | 13/13 | -7.82 | -72.19 |
| A/val | timeframe=1m | 380 | 38 | -20.79 | 38/38 | -16.26 | -4.52 |
| A/val | timeframe=240m | 6 | 1 | -205.81 | 1/1 | 326.76 | -532.57 |
| A/val | timeframe=30m | 86 | 9 | 12.41 | 9/9 | -6.08 | 18.49 |
| A/val | timeframe=3m | 390 | 39 | 17.17 | 39/39 | -23.96 | 41.13 |
| A/val | timeframe=5m | 258 | 26 | -31.62 | 26/26 | -36.73 | 5.11 |
| A/val | timeframe=60m | 63 | 7 | -23.58 | 7/7 | -38.80 | 15.22 |
| A/test | direction=LONG | 510 | 51 | -32.94 | 51/51 | -4.25 | -28.68 |
| A/test | direction=SHORT | 479 | 48 | -70.93 | 48/48 | -31.28 | -39.65 |
| A/test | timeframe=15m | 49 | 5 | -33.87 | 5/5 | 45.11 | -78.99 |
| A/test | timeframe=1m | 383 | 39 | -40.16 | 39/39 | -17.42 | -22.74 |
| A/test | timeframe=240m | 10 | 1 | -245.68 | 1/1 | 48.73 | -294.41 |
| A/test | timeframe=30m | 87 | 9 | -63.31 | 9/9 | -17.24 | -46.07 |
| A/test | timeframe=3m | 403 | 41 | 1.86 | 40/41 | -15.62 | 19.76 |
| A/test | timeframe=5m | 4 | 1 | -95.19 | 1/1 | -40.59 | -54.60 |
| A/test | timeframe=60m | 53 | 6 | 240.65 | 6/6 | 72.79 | 167.86 |
| B/val | direction=LONG | 606 | 61 | -71.40 | 61/61 | -12.17 | -59.23 |
| B/val | direction=SHORT | 699 | 70 | -73.35 | 69/70 | -15.19 | -35.44 |
| B/val | timeframe=15m | 122 | 13 | 46.39 | 13/13 | -44.44 | 90.83 |
| B/val | timeframe=1m | 380 | 38 | -12.19 | 38/38 | -22.66 | 10.47 |
| B/val | timeframe=240m | 6 | 1 | 222.89 | 1/1 | -6.77 | 229.66 |
| B/val | timeframe=30m | 86 | 9 | -248.63 | 8/9 | -21.21 | -53.35 |
| B/val | timeframe=3m | 390 | 39 | -103.71 | 39/39 | -10.08 | -93.62 |
| B/val | timeframe=5m | 258 | 26 | -125.48 | 25/26 | -55.39 | -61.15 |
| B/val | timeframe=60m | 63 | 7 | -155.66 | 7/7 | 12.69 | -168.35 |
| B/test | direction=LONG | 510 | 51 | -68.31 | 50/51 | 17.43 | -68.31 |
| B/test | direction=SHORT | 479 | 48 | -80.90 | 48/48 | -16.27 | -64.62 |
| B/test | timeframe=15m | 49 | 5 | 4.95 | 5/5 | -2.35 | 7.30 |
| B/test | timeframe=1m | 383 | 39 | -18.51 | 39/39 | -11.03 | -7.48 |
| B/test | timeframe=240m | 10 | 1 | -204.27 | 1/1 | 132.11 | -336.38 |
| B/test | timeframe=30m | 87 | 9 | -78.67 | 9/9 | -18.89 | -59.78 |
| B/test | timeframe=3m | 403 | 41 | -5.35 | 41/41 | -25.56 | 20.21 |
| B/test | timeframe=5m | 4 | 1 | 133.64 | 1/1 | -41.12 | 174.75 |
| B/test | timeframe=60m | 53 | 6 | 22.64 | 6/6 | 33.77 | -11.13 |

## 复现与产物

本报告的EXP为`experiments/active/exp-ma-profit3r-20260922-v1`。行情冻结、27批导入、形态扫描、去重、收益标签与第17轮停止的完整命令/失败历史见`analysis/p1_ma_profit3r_20260922.md`和EXP内原始plan、source manifests、queue receipts。以下从冻结的round017 ledger与SHA固定行情源开始；新环境按记录的同版本依赖恢复，现有输出拒绝覆盖，重放使用新的目录并保持原合同引用一致。

```bash
.venv/bin/python -m yoyo.datasets.ma_profit_dataset --plan experiments/active/exp-ma-profit3r-20260922-v1/dataset_plan_owner1500_v4.json --events experiments/active/exp-ma-profit3r-20260922-v1/selection_queue_round_017/dataset_ledger.jsonl --out datasets/ma_profit3r_owner1500_v4
.venv/bin/python -m pytest tests/test_ma_profit_dataset.py tests/test_train_ma_profit3r.py tests/evaluation/test_ma_profit_input_continuity.py tests/test_watch_ma_profit3r_owner1500_v4.py -q
```

Windows（cwd `C:/fable`），实际argv另存`training_launch_owner1500_v4.json`；版本化remote module由同SHA的` scripts/windows/train_ma_profit3r.py`复制，先不带`--train`预检，再带`--train`顺序跑A/B：

```text
C:/fable/.venv/Scripts/python.exe -m scripts.windows.train_ma_profit3r_owner1500_scalev4_20260922 --cohort-receipt C:/fable/experiments/active/exp-ma-profit3r-20260922-v1/selection_queue_round_017/selection_receipt.json --contract C:/fable/experiments/active/exp-ma-profit3r-20260922-v1/training_contract_owner1500_v2.json --dataset C:/fable/datasets/ma_profit3r_owner1500_v4 --dataset-plan C:/fable/experiments/active/exp-ma-profit3r-20260922-v1/dataset_plan_owner1500_v4.json --model C:/fable/experiments/active/exp-ma-profit3r-20260922-v1/yolo11s.pt --plan C:/fable/experiments/active/exp-ma-profit3r-20260922-v1/plan.json --run-root C:/fable/runs/ma_profit3r_owner1500_20260922_v4 --train
```

训练后的完整双臂eval/controls衔接命令为 `.venv/bin/python scripts/research/watch_ma_profit3r_owner1500_v4.py`，固定路径、冻结哈希及实际evaluator参数在已提交源代码中。它仅接受40轮completed训练，对已完成结果严格验绑定后复用，不重新训练。最终冻结证据：

```bash
.venv/bin/python -m yoyo.evaluation.ma_profit_delivery --experiment experiments/active/exp-ma-profit3r-20260922-v1 --out experiments/active/exp-ma-profit3r-20260922-v1/delivery_owner1500_v4
```

`delivery_owner1500_v4/manifest.json`重新核验46个直接证据文件，包含训练字节、评估事件/预测/指标、匹配对照输入与结果、原始计划/容量修订/数据审计、诊断。其他文件由其引用manifest展开。它不以一次哈希核对替代像素/标签/时间切分审计。

- 图片：`datasets/ma_profit3r_owner1500_v4/`；旧v3及中断v2均保留。
- Mac权重、CSV、args：`EXP/trained_owner1500_v4/arm_A/`、`arm_B/`。
- 3060权重：`C:/fable/runs/ma_profit3r_owner1500_20260922_v4/arm_A/weights/best.pt`与`arm_B/weights/best.pt`。
- 原始逐图预测/逐事件结果：`EXP/evaluation_owner1500_v4/arm_A/`、`arm_B/`。
- 随机对照结果：`EXP/control_comparison_owner1500_v4/arm_A/`、`arm_B/`。
- A best SHA `546a267dd9743b0b981a554c988088c9d033b266718f7983252e4ab2eaa239a4`；B best SHA `8a8cc335f5b0c9f0444252bb0455fadaaedae66044ee7950259036a4ed58d503`。

## 风险与诚实声明

- 本轮完成的是被授权的离线筛选、两组训练和评估，不是盈利策略开发成功；规则弱标签不是Owner逐图Gold。模型能力没有因数据合格数达到1500自动成立。
- 0.2%固定成本，未建模资金费率、冲击、挂单成交、资金约束和组合并发；不报告账户累计收益。过滤采用毛3R且净盈利，未声称净3R。
- 参考来源、现时可枚举交易对、缺失月份/断档和跨周期原15m门都有选择限制。未合格、UNKNOWN、INVALID、失败及中断记录均保留。
- 两组只跑一个seed，B训练曝光量翻倍，局部自动纵轴也变化；当前不能把A/B差值解释为位置增强因果效果。小样本padding诊断不能代替完整train/infer parity复现。
- 上一次全仓boundary/causality/parity为468通过、8个既有失败，分类在`owner1500_repository_gates_review.json`：历史archive路径、历史artifact缺source_commit、他人未提交candidates.py及旧renderer迁移账本漂移。没有放宽测试或修改他人变动；不能宣称全仓绿。本实验离线专门审计与测试分别有证据。
- 临时防睡眠仅附着任务；两组训练与自动评估均已结束。未改生产配置、ACTIVE、forward日志、真金或账户权限。

## 下一步选项（新的实验，不在本轮中继续训练）

1. 先统一训练内置val和实际推理预处理，做固定模型/同图/同尺寸的parity，再比较新配方。
2. 以本轮已保留的“形态相似但未达3R”事件构建难负例，检查其核心边界语义后做一次受控对照；不把全部超时说成亏损。
3. 保留L1形态检测与L2盈利判断的分工，用数值时序/成本特征检验是否有额外信息。均不得用本次test反复调参后仍声称独立终审。

新的训练、改障碍/成本或生产动作应作为单独实验明确口径；本轮两组结果与失败记录保持冻结。
