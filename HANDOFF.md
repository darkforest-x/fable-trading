# HANDOFF — 给下一个会话/模型的执行路线图

> 文档地图：`docs/DOC_MAP.md` · 本周计划：`analysis/week_plan_20260720.md` · 纪律：`CLAUDE.md`

## ⚡ 当前真相（2026-07-23 凌晨）

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
ssh root@103.214.174.58 'touch /opt/fable-trading/data/executor_KILL'
# 2) 回退 executor 头寸公式：把 unit_notional 段改回 notional=base*size_mult
#    或 git checkout <pre-headroom> -- src/execution/executor.py 后 rsync + restart
ssh root@103.214.174.58 'systemctl restart fable-executor'
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

**07-09 追加：合约复制性检验通过**（TP5/SL2 纯 SWAP 池 AUC 0.560/p=0.001，top-decile 净@maker +0.225%/笔，判定标准全过）——**主线宇宙即日起为 SWAP**；H1 分批止盈为 v3 出场头号挑战者（AUC 0.608/胜率 65%）。前向验证与后续实验一律用合约配置。YOLO11s 离线验收 mAP50 0.8569，低于正式线 0.90，检测层正式验收未达成，标记为非关键路径并暂停。P1-5 已完成：`models/frozen_tp5_sl2_swap_20260709.txt/.json` 入库，看板信号路径加载冻结模型并按 model_path/dataset_sha256 失效旧缓存。P1-8 已完成：看板新增前向验证 tab、现货/合约切换、动态总览与分宇宙 score cache，localhost 与 VPS 真浏览器验收通过。P1.5 R0~R4 已完成：H9/H10 有线索但不切主线；H1 scaled 是最强发现级候选；5m 机会扩张证伪，30m 是低频高质量新线索。P2-9 已完成：补齐冒烟测试并新增 GitHub Actions。P2-10 已完成：移动端、合格未成交 tooltip、只读分数滑块；owner 拍板暂不加访问控制。

**2b 验收通过（holdout 已消耗）→ 阶段 3 第一轮未通过（PF 1.01@0.3%）→
owner 已委托"按推荐直接执行" → 出场结构扫描完成：TP5/SL2 为 v3 候选标签**
（val 净@0.3% +0.077%/笔 vs 基线 +0.001%，p=0.001，见 `analysis/p2b_v3_barrier_sweep.md`）。
进行中：P2-11 偏 B；E1 pad 后 **E2 `MAX_DENSE_BARS=24` 长段收核** 已 relabel（PAXG 74→24 bar），
待 owner 看 `/label_audit_e2_compare.html` 后决定是否重训。
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
fable 拍板：主线 **SWAP** · **EMA 8-55** · 冻结 **TP5/SL2** · YOLO **非关键** · H1 **挑战者/影子**。

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
3. **均线定义已裁决（2026-07-09）**：P0-3 已在合约数据上正面对比
   SMA/EMA 20/60/120 与现行 EMA 8/13/21/34/55+144/200。20/60/120 的 AUC 更高
   但 top-decile 净收益显著弱于 8-55；owner 已拍板 **主线继续 8-55**。
4. **冻结模型工件已完成**：当前生效工件为
   `models/frozen_tp5_sl2_swap_20260709.txt/.json`，阈值 val q90=0.3747093215963419，
   best_iteration=18，数据 SHA256=`818304cffcdb410612780e9d42dcdf7f8488c97e0044f93c1406ed2cb4856180`。
5. 前向跟踪脚本已完成：`scripts/forward_track.py` 默认从
   `2026-07-08 00:00 UTC` 起扫描 OKX SWAP，加载冻结模型打分，阈值以上写入
   `data/forward_log.csv`，并按 `(source, symbol, signal_time)` 幂等补记已知出场。
   **07-10 冒烟**：正式窗口 `new_signals=2`、`total_rows=2`（均为 closed）。
6. 前向验证窗口从 2026-07-08 起积累；每日定时任务
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
   `_SWAP` 行。新增 `/api/forward` 和前向验证 tab，当前 `data/forward_log.csv`
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
