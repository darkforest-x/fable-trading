# HANDOFF — 给下一个会话/模型的执行路线图

> 文档地图：`docs/DOC_MAP.md` · 本周计划：`analysis/week_plan_20260720.md` · 纪律：`CLAUDE.md`

## ⚡ 当前真相（2026-09-02 — 普通主板 1h/会话4h LONG 扫描已完成）

Owner 授权 Grade-A full40 native-1280 checkpoint 的 1h holdout **#11**、会话 4h
holdout **#12**，并批准 Eastmoney 在线端点不可达时只用同日冻结缓存做 parity。冻结普通
沪深主板 3,111 只；Sina 60m 日期因果 QFQ 快照中 1h 可用 3,021、4h 可用 2,903。
35,072 个 W18/W19 窗口经 373 原框 → 346 结构框 → 52 语义框 → 20 去重审计事件，按
Owner 口径仅交付 **1h 12 LONG + 4h 4 LONG**，另 4 SHORT 排除。16 张图、6 位代码及
完整来源审计见 `analysis/html/p1_ashare_grade_a_yolo_1h4h_long_sina_20260902.html`。

离线复验通过 3,021 份 K 线、5,892,102 行 QFQ 算术、346 个输入像素/语义决定及 16 张
图的 SHA 检查，网络读取和推理均为 0。该模型由 crypto 15m 训练，在 A 股 1h/4h 上仍是
OOD completed-history 研究筛选，不是收益或实时买入信号，`production_eligible=false`。
没有训练、调门、promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## ⚡ 当前真相（2026-09-02 — 最新全 A 股扫描已完成，仅作 OOD 图册）

Owner 要求用现有模型扫描“最新大 A”并给出命中图。本轮先在 main 提交预注册与 builder
`062956425c`，随后冻结 2026-09-02 11:30 CST 已完成的沪深京 15m 前复权快照。5,908 只
宇宙中 5,494 只通过最新尾根与 last-160 同源日程 parity（92.99%），414 只 fail-closed；
W18/W19 共 10,988 张输入，经 288 原框 → 281 结构框 → 47 语义框 → 31 去重事件，最终
**8 LONG / 23 SHORT**。独立 replay 的 K 线 SHA、像素、语义决定与 31 张图全部通过。

这批是 crypto checkpoint 迁移到 A 股的**分布外 completed-history 研究提案**，不是交易
信号：全部需要 post7–9，7/8 LONG 又集中在仅占池 6.1% 的北交所，明显有市场 shortcut
风险。A 股普通现货也不能把 SHORT 当直接卖空指令。完整自包含交付：
`analysis/html/p1_15m_ashare_grade_a_yolo_latest_20260902.html`；31 张原图包：
`experiments/active/exp-15m-ashare-grade-a-yolo-latest-20260902-v1/results/ashare_signal_charts_31.zip`。

这是 Grade-A full40 native-1280 checkpoint holdout 使用 **#8**。未训练、调参、promote、
部署、改 ACTIVE/frozen/forward、发 Telegram 或下单，`production_eligible=false`。后续若
要继续，正确入口是 Owner 先审 31 张形态或另建 A 股时间切分 Gold；不得拿这次已消费的
快照看结果调门重扫。

## ⚡ 当前真相（2026-09-02 — 新增因果特征有改善迹象，但未过升级门）

Owner 问 28 个 L2 特征能否继续增加并授权实际试验。本轮冻结真实 Grade-A L1 候选、
side-aligned 语义、TP5/SL2/72、0.2% 成本、dependency representatives、时间切分、模型参数
和匹配对照，只增加因果特征列。旧 rich builder 的 116 个候选先剔除 6 个语义重复列，最终从
28 列扩为 110 列（新增 82）；3,779 行复算的旧 28 列最大误差 3.553e-15，未来特征、非有限值、
时间错位和 holdout 行均为 0。

March tune 独立选择 LONG/SHORT：LONG 选 full_110，SHORT 只选 baseline_28 + 26 个 MA family
列。selection receipt 提交后才打开 April final。冻结入选组合的 exact top-decile 净收益由旧
28 列的 +89.79bp 升到 +110.78bp，q90 数量由 20 增到 31，匹配对照 8/8 为正；但固定 q90
单笔质量由 +123.11bp 降到 +98.70bp，且置换 p=0.046395 未达到预注册 p<0.01。因此实验登记
为 rejected：这是值得保留的正向迹象，不是可 promote 的升级。

完整报告：analysis/html/p3_15m_ma_launch_l2_feature_addition_20260902.html。正确下一步是增加
同一冻结 L1 目标域的独立经济事件后做一次预注册确认，不在本轮 final 上继续调组，也不提前
消耗 holdout。未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## ⚡ 当前真相（2026-09-02 — 28 特征分组消融已否决）

Owner 问为什么 L2 是 28 个特征并授权实际试验。本轮确认这 28 个是 2026-07-07 为旧
strict-rule 候选人工设计的基线，不是当前 YOLO 候选上自动选出的最优集合。实验冻结真实
Grade-A L1 候选、side-aligned 语义、TP5/SL2/72、0.2% 成本、dependency representatives、
时间切分、LightGBM 参数和 tune-q90，只在七个预注册特征子集之间变化，LONG/SHORT 分开选。

3 月 tune 的七个方案扣成本 top-decile 全部为负；仍按预注册规则冻结的“最不差”组合是
LONG 24 特征（去动量）与 SHORT 16 特征（MA 结构 + 价格趋势）。打开未参与选择的 April
final 后，两者合并 top-decile 净值 -69.05bp，冻结 q90 18 个事件净值 -76.27bp，
置换 p=0.972603，LONG/SHORT 均为负且匹配对照失败，实验登记为 rejected。

旧 28 特征基线的 1,021 个 final 分数、side percentile、阈值和 KEEP 决策全部复现，最大差
1.11e-16；其 q90 仍为 20 个、+123.11bp，但 p=0.072093、n<30、SHORT n=7，历史
拒绝不变。正确结论不是“删到 16/24 个”，而是当前 417 train +229 tune 独立事件不足以
稳定选组；后续要增加同一冻结 L1 的目标域候选，并把 early-stop 与 feature-selection
拆成两个时间窗。报告：
analysis/html/p3_15m_ma_launch_l2_feature_group_ablation_20260902.html。
未读 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## ⚡ 当前真相（2026-09-02 — 上万参考图已接入 L2，但目标域错配导致否决）

Owner 追问“不是有上万个训练图、还有很多 Gold 吗”，并授权实际接入验证。本轮没有把形态正负
冒充盈亏：旧 10,000 正图 + 10,000 配对负图逐事件联回原 K 线，在各自 `window_end_i` 只取
已收盘特征，从下一根开盘重算固定 TP5/SL2/72 收益。18,364 个训练截止前窗口中 18,069 个
成功生成经济标签；按同币完整 168 根输入 +72 根标签暴露合并后，L2 独立训练块由 417 增到
13,867。真实 L1 的 229 个 tune 与 242 个 final 独立事件完全不变，原多空模型、阈值、分数和
KEEP 决策均精确复现，`holdout_rows_opened=0`。

数量增加没有带来目标域提升：参考事件占扩充训练代表 97.18%；LONG 形态正/负 TP 率仅
25.32%/22.90%，SHORT 为 25.20%/26.65%，说明 L1 形态标签不是 L2 收益标签。扩充 28 特征
模型在固定真实 L1 final 上 AUC 0.4864、top-decile 扣 0.2% 成本后 -16.54bp、置换
`p=0.668933`；SHORT tune-q90 在 final 放过 95.29%，校准明显塌缩。单特征对照 top-decile
+90.73bp，但 `p=0.072293` 且匹配对照门仍失败。实验登记为 `rejected`，模型不得 promote。

正确下一条数据路径不是继续堆挑选出来的漂亮图片，而是用同一个冻结 L1 在更长 pre-holdout
历史、更多币种上真实扫描提案，再逐事件生成 L2 经济标签、依赖去重、按时间切分和多空分训。
Owner Gold / 正图 / hard negative 继续服务 L1 形态检测；它们不能按图片数替代 L2 目标域样本。
交付报告：`analysis/html/p3_15m_ma_launch_l2_reference_augmentation_20260902.html`；24 张实际
final 输入高清页：`analysis/html/p3_15m_ma_launch_l2_reference_augmentation_diagnostic_gallery_20260902.html`。
没有读取 holdout、promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## ⚡ 当前真相（2026-09-01 — L1.5 已从默认研究链物理旁路）

Owner 决定去掉未通过的 L1.5。新入口只执行 `冻结 L1 候选 → dependency episode 合并 →
LONG/SHORT 独立 L2`，从原始 3,779 行 L1 ledger 用明确 `usecols` 读取 27 个字段，读取的
`l15_*` 字段为 **0**，且 runner 不导入 global-shape 或 L1.5 实验模块。历史 L1.5 代码、
prereg、失败模型和报告保留为不可改写的失败证据。

独立重训已证明旁路不是文字开关：新旧 LONG / SHORT 模型 SHA-256 完全相同，两个阈值差均为
0，逐事件最大分数差均为 0，入选 ID 完全一致，仍为 34 个。经济裁决没有因此变好：整体
q90 净均值 +38.88bp，但置换 `p=0.192081`；LONG 17 个净均值 -50.94bp，SHORT 17 个
+128.69bp，所以 L2 仍 `rejected`、`production_eligible=false`。没有读取 holdout，没有
promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。交付报告：
`analysis/html/p3_15m_ma_launch_l1_l2_bypass_l15_20260901.html`；架构决定：
`docs/decisions/0003-bypass-l15-until-shape-supervision-is-valid.md`。

## ⚡ 当前真相（2026-09-01 — 因果 L1.5 + 多空 L2 全链路已跑完并否决）

Owner 授权把 L1.5 与 L2 正确路径全部落地。第一版 L1.5 虽只看检测时已经收盘的 K 线，
但弱标签与特征都包含同一段 post-core 确认进度，LONG / SHORT AUC 及单特征 AUC 都为
1.0，属于标签重建捷径，已在进入候选筛选和 L2 前停止。第二版把 L1.5 输入物理截断到
每个事件的 `core_end`：固定 128 根、0 根 post-core，3,129 个独立事件按时间切分并分别训练
LONG / SHORT。LONG 最终 AUC 0.9505、召回 82.61%、FPR 7.25% 通过；SHORT AUC 0.8906、
FPR 15.70% 超过预注册 12% 上限，因此 L1.5 总门失败。

同一冻结最终段的 242 个独立 L1 候选做四臂对照：L1 池净均值 +7.35bp；L1.5-only
筛到 146 个后反降至 -7.43bp；L2-only q90 为 34 个、+38.88bp，但置换 p=0.1921 且
LONG -50.94bp；完整 L1.5+L2 仅 18 个、+15.66bp，p=0.2304，SHORT -12.03bp。
所以正确结构已经实现并验证，但当前弱形态标签和收益 L2 不能组成可用生产门，实验登记为
`rejected`。38 张实际 128 根输入像素校验失败为 0；没有读取 `>=2026-05-04` holdout，
没有 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。交付报告：
`analysis/html/p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.html`；本地高清图库：
`analysis/html/p3_15m_ma_launch_l15_precore_l2_pipeline_gallery_20260901.html`。

## ⚡ 当前真相（2026-09-01 — 与 L1 完全同窗的 L2 多空回归已否决）

Owner 纠正上一版 168 根全局上下文 L2：判断层必须只针对 L1 实际看到的 18/19 根
1280×742 原图和当前原框，并继续使用未来实际收益回归，多空分开训练。本轮在冻结的
25,911 个 L1 原始框上按 `symbol + side` 重聚为 3,827 个 episode；3,827/3,827 原图逐像素
校验通过，3,798 个完整标签样本产生 673 个最终独立事件。模型只读图中可见 OHLC、
SMA/EMA 20/60/120、当前框和当前 confidence，没有旧 48/96/168 根原始上下文、volume、symbol、
后续 episode 最高分或 holdout。

预注册经济门 **FAIL**：总体 top-decile 扣 0.2% 成本后 **-4.7bp**，置换
`p=0.377162`，AUC `0.4519`，Spearman `-0.1201`。tune-q90 总体虽为 +4.7bp，但仅
32/61 个入选事件有完整 8/8 随机对照；覆盖组为 -49.0bp，缺配组为 +63.9bp。LONG
best iteration=1、最终仅 10 个不同分数且 q90 为 -16.9bp；SHORT q90 +70.7bp 是已经看过
final 后的探索性结果，不能事后删 LONG 再冒充独立成功。15 项 lineage/parity/safety 校验全过，
所以结论是经济预测弱，不是图、框或数据链坏掉。

实验已登记为 `rejected`；没有读取 `>=2026-05-04` holdout，没有 promote、部署、改
ACTIVE/frozen/forward、发 Telegram 或下单。交付报告：
`analysis/html/p3_15m_ma_launch_l2_short_window_side_split_20260901.html`；40 张模型实际输入：
`analysis/html/p3_15m_ma_launch_l2_short_window_side_split_gallery_20260901.html`。
下一条若做经济 L2，必须新预注册、单变量降维并用新的未见时间段；若要判断“全局形态好坏”，
那是另一个需要 Owner 全局好/坏 Gold 的 L1.5 分类任务，不能用未来收益代替形态真值。

## ⚡ 当前真相（2026-09-01 — L2 多空拆分回归有提升但预注册拒绝）

Owner 要求说明 L1.5，并把既有 15m L2 训练集按 LONG / SHORT 分开重训。实验严格复用冻结的
3,779 条数据、28 个因果特征、TP5/SL2/72、0.2% 往返成本、时间切分和 dependency blocks；
唯一变量是“混合回归器”改为“多空两个回归器”。混合模型在 242 个最终独立事件上逐分数复现，
最大差 `9.90e-17`，41 个 KEEP 决策完全一致。

多空模型的 tune-q90 在最终时间段合计保留 20 个独立事件，净均值 **+123.11bp**，高于混合模型
的 **+3.39bp**，并跑赢 8/8 组同币 × 同月 × 同 UTC 时段 × 同 ATR 桶 × 同方向随机对照；但
合并置换 `p=0.072093` 未过 0.01，合计 n=20 未达 30，SHORT n=7 未达每侧 10，因此预注册总门
**FAIL**。两个权重只保留为 rejected research artifacts，不启用、不 promote、不部署，也未读取
`>=2026-05-04` holdout。

L1.5 的正确职责是“局部 YOLO 之后、经济 L2 之前”的因果全局形态分类：输入固定 168 根已收盘
K 线，LONG / SHORT 分开建 Owner-confirmed `global_shape_good` Gold 和局部像但全局错的 hard
negatives；不得拿未来收益自动生成形态标签。报告：
`analysis/html/p3_15m_ma_launch_l2_side_split_20260901.html`。

## ⚡ 当前真相（2026-08-29 — A级 8k + 匹配负例 24k 已在 RTX3060 训练）

Owner 问“准备去训练，负样本应该用啥”。结论是**不用旧 30,000 张 OKX-only 负样本**，改用新建
`ma_launch_owner_grade_a8000_yolo_neg24000_v1`：8,000 张 A 级正图逐字节复用，每个独立正事件
同币、同源、同半年块、同 split、同核心根数与同 7–8 个位置配 2 个 dense-no-launch hard 事件和
1 个 easy 事件，最终为 16,000 hard + 8,000 easy，train/val 均精确 1:3。

全量 builder 与独立 verifier 均通过：32,000/32,000 实际 PNG 哈希唯一，8,000/8,000 正图与
标签 parity，24,000/24,000 负标签为空，负事件重叠/活动度/split/holdout 失败均为 0；随机错配
1,000 次的 exact-pairing 零假设 `p=0.000999`。第一轮发现 4 个停牌后冻结报价事件造成 27 个重复
像素副本，已保留失败 receipt，并通过源窗口最低活动度门从事件选择阶段完整重建，未事后删样本。
报告：`analysis/html/p1_15m_ma_launch_owner_grade_a8000_neg24000_20260829.html`；50 组实际输入：
`experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/results/actual_model_inputs_matched_sample50.html`。

Owner 已两次明确覆盖阶段门：先授权这一批 completed-history 数据开训，后于当前对话扩展为
**可读 holdout、可 promote、可部署**。授权回执：
`experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1/owner_authorization.json`。
这是权限放行，不是已经消耗 holdout 或已经通过生产指标；记录时 holdout / promote / deploy
均仍为 0。真金下单、撤单、改仓位不在本次授权内。

`ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960` 于 03:16:48 +08 启动，冻结配置为
YOLO11s / 960 / batch 8 / 40 epochs / patience 10，双机 64,000 个 image/label 文件的 SHA、尺寸、
类别绑定与 split 隔离全量验证通过后才启动。当前仍保持
`training_eligible=false / production_eligible=false`：它们记录客观质量状态，不用来否定 Owner 授权。
训练完成后先执行已冻结 chronological val，再预注册并执行一次 holdout 验收。若最终进行
promote / deploy，必须保持其 2–9 根 post-core K 线的 completed-history 输入契约，不冒充
tip / tip-1 / tip-2 因果即时信号。

本轮相关数据集与 registry 测试 51/51 通过。全仓为 1,769 passed、7 skipped、5 failed；失败来自
当前工作区另外存在的依赖锁改动与本机环境（FastAPI/OpenCV/PyYAML 版本不符，且缺
`ultralytics`/`torchvision`），不是本轮文件。未替 Owner 修改或提交这些外部脏改动。

## ⚡ 当前真相（2026-08-28 — ETH 30 日 41 个 episode 已完成置信度审计）

Owner 追问“按照置信度分析”。本轮只读取 holdout 消费 #5 已交付 ZIP 内的 41 个 episode / 1,057
个候选，不联网、不重推理、不读未来收益或 Owner 标签。TG 图上的**首次可见框**置信度中位 0.429，
23/41 低于 0.50，≥0.75 为 8，≥0.90 仅 4（#40、#14、#01、#37）。同 episode 后续最高分
中位却达 0.790，18/41 最终 ≥0.90；23/41 的最高分出现在首次检测之后，最晚再等 12 根 15m K。

最高分与 episode 候选数的 Spearman `rho=0.859`，10,000 次秩置换 `p=0.00010`；首次分数与
候选数只有 `rho=0.281, p=0.0761`。结论是 episode 最大 confidence 被重复窗口次数显著抬高，
不能冒充早期质量。冻结候选敏感度显示 conf 0.50/0.75/0.90 分别仍有 31/22/18 个 episode；
0.90 的中位/P90 额外延迟为 22.5/94.5 分钟。**本轮不选择、不修改阈值**，因为没有逐 episode
真值，无法证明高分更标准。完整报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_eth30d_confidence_20260828.html`。

这是同一已消费输出的描述性切片，`new_holdout_consumption=false`；训练、权重、标签、ACTIVE、
promote、forward、部署和订单变化均为 0；最终主项目回归 1755 passed、4 skipped。**下一条允许动作**：若要真正选阈值，必须在独立
pre-holdout 或新鲜因果 tip Gold 上按首次可见分数报告 precision、漏检率与延迟，不能用本月
41 张继续调门后声称未见验证。

## ⚡ 当前真相（2026-08-28 — ETHUSDT.P 近 30 日原模型扫描已逐图交付）

Owner 要求“用模型再跑一次 ETHUSDT.P 近一个月并发到 TG”。本轮按预注册范围扫描
`ETH-USDT-SWAP` 2026-07-29..08-27 的 30 个完整 UTC 日，原 Owner 10,000 正例 + 30,000
负例 YOLO、`conf=0.25`、NMS 0.7、W18–25、core4–5、confirmation4–6 全部未改。
24,480 个实际输入产生 1,318 个原始框，1,057 个通过冻结结构合同；5-bar 去重后 53 个事件，
再按跨滑窗连续重叠合为 **41 个 episode：27 LONG / 14 SHORT，覆盖 24/30 日**。

原始候选与四维框全部保留；TG 交付以用户决策单位去重，每个 episode 只画最早可见的一个原始框。
41/41 张 1920×1400 图均含 128 根整体行情与模型实际输入 inset，模型输入像素、完整重渲染、PNG
SHA、唯一性均 41/41 通过，循环错配零假设为 0/41。15:24–15:28 CST 已按 Telegram document
无压缩发送 44/44：总览、41 张逐图、无损 ZIP、HTML；完成回执合同 SHA
`7c355bf4b8d1e8037bcc5ef10a16a51a4339d84c11478ddb69273c9f9bac3460`，
`manual_owner_review_required=false`。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_eth30d_20260828.html`。最终主项目回归为
1751 passed、4 skipped；工作区外部实验 `external/Kronos` 因未安装 qlib 不在本仓依赖合同内，
按正式测试范围排除且未改动。

这是同配置经 Owner 授权的 holdout 消费 #5。41 个 episode 是模型真实提案，不是 Owner Gold；
历史 14 项样本挖掘门没有被拿来自动冒充视觉真相。没有训练、调参、改标签/权重、ACTIVE/frozen、
promote、forward、部署或订单变更，仍 `training_eligible=false / production_eligible=false`。
**下一条允许动作**：Owner 直接看 TG 图；若要改检测规则或减少信号，必须换 pre-holdout 或新鲜
tip Gold 另行预注册单变量实验，不能继续利用这 30 日调参后声称未见验证。

## ⚡ 当前真相（2026-08-28 — 08-27 检测框与实际训练图语义不一致已证实）

Owner 指出“还有很多框识别的不对，和拿去训练的图对比”。本轮冻结复用 08-27 的 43 个原始
预测和既有快照，不联网、不重推理、不改框；按 10,000 个实际训练正例当时使用的同一套 14 项
形态门与 Owner-50 距离门逐项复算。**只有 #27 TAO LONG 与 #30 LIT LONG 通过，41/43 不符合
训练正例标准。** 对 41 个失败输入穷举全部 core4/5 × confirmation4/5/6 后，替代合格核心仍为 0，
所以主要不是统一左右平移问题，而是整张输入属于训练标准外形态。

根因两层：扫描只验预测框横向几何，没有重跑训练语义门；训练标签本身也不是纯均线窄带，而是
包住核心完整影线 + 六均线再加 4% 留白。9,578/10,000 个训练正例的 K 线跨度大于均线跨度。
43/43 当前输入和 29 个不重复实际训练配对均逐像素重渲染一致；43 张 2560×946 对照图、画廊、
CSV、receipt 与独立 verifier 全通过。完整入口：
`experiments/active/exp-15m-ma-launch-owner-yolo-20260827-training-parity-audit-v1/results/comparison_gallery.html`；
报告：`analysis/html/p1_15m_ma_launch_owner_yolo_prediction_training_parity_20260828.html`。
发送回执登记后的全仓回归为 1747 passed、4 skipped。

Owner 随后明确要求“发到 tg”。14:23–14:24 CST 已按 Telegram document 无压缩发送 4/4：
分布总览、代表性并排图、含 43 张高清对照/画廊/CSV/summary 的无损 ZIP、自包含 HTML 报告；
完成回执 SHA 已登记，`manual_owner_review_required=false`。

本轮**没有 Owner 人工审核任务**：自动结论就是 2 个保留、41 个淘汰，不要求 Owner 逐图打勾、
改框或填表。这是同配置经授权的 holdout 消费 #4；禁止在 08-23..27 上据此调门。训练、标签、
权重、ACTIVE/frozen、promote、forward、部署和订单变化均为 0，仍
`training_eligible=false / production_eligible=false`。**下一条允许动作**：若继续修复，在
pre-holdout 或新鲜 tip 数据上预注册“推理后自动执行冻结形态门”的单变量方案；自动淘汰与 QA，
不把审核任务转嫁给 Owner。

## ⚡ 当前真相（2026-08-28 — 08-27 的 43 个事件已按高清全景逐张交付）

Owner 先要求详细分析，随后明确要求“先把昨天的所有信号发我看一下，高清一点，不要只给检测框，
要看整体”。`exp-15m-ma-launch-owner-yolo-20260827-fullcontext-v3` 因此冻结复用 rawbox-v2 的 43 个
08-27 事件，不重推理、不筛选：37 LONG / 6 SHORT、19 个币。每个 1920×1400 PNG 上方为冻结
快照共同覆盖的 110 根 15m 全景（08-26 22:00..08-28 01:15 UTC），右下为逐像素相同的
1280×742 W18–25 模型输入；每张只放同一个原始 YOLO 框，虚线标真实 `window_end_time`。

43/43 事件身份、模型输入、全图逐像素重渲染、原框逆投影闭环和 PNG SHA 全通过；43 张均作为
Telegram document 逐张发送并有断点回执。详细审计显示 43 个 5-bar 事件只有 34 个重叠 episode，
其中 9 个是已有 episode 的二次触发；Top20 19/20 有信号，选择性仍不足。框中位宽 3.73 根，
只占 110 根全景 3.42%，与 10k 训练标签中位宽仅 +4.2%；预测中位高度却 +43.5%。检测要在核心
后再等 4–6 根，即 60–90 分钟。全仓回归 1738 passed、4 skipped。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_20260827_fullcontext_analysis_20260828.html`。

这是同一配置 Owner 授权的 holdout 消费 #3；分析为事后探索性展示审计，禁止据此在 08-23..27
调阈值、episode 预算或框高后继续声称未见。网络、新推理、训练、ACTIVE/frozen、promote、
forward、部署和订单变化均为 0，`training_eligible=false / production_eligible=false`。
**下一条允许动作**：等 Owner 看完 43 张后给出逐样本语义判断；若要减少重复或覆盖率，另在
pre-holdout / 新鲜 tip Gold 上预注册单变量方案。

## ⚡ 当前真相（2026-08-28 — 最近五日 Top20 已按原始 YOLO 四维框重做）

Owner 指出上一版五日图一张有多个框，而且框位与训练/模型图不一致，随后明确要求“那你重新弄”。
根因已确认：v1 虽用正确的 1280×742 W18–25 输入推理，却只保留预测 `cx/w`，丢掉 `cy/h` 后
用核心 K 的 high/low 重造纵向框；又把多个滑窗事件叠到压缩的 96 根日图上。v1 因此只可继续
引用扫描数量和事件身份，**不得再作为模型原框位置证据**。

修正版 `exp-15m-ma-launch-owner-yolo-recent5d-rawbox-v2` 完全冻结原权重、conf=0.25、NMS=0.7、
W18–25、核心 4–5 和确认 4–6。4,528 个结构候选保留完整 `cx/cy/w/h`，按重叠决策区间聚成
192 个 episode；每币日复核只取最早 episode，100 张为 **97×单框 + 3×无框**。离线 verifier
重渲染 100/100 实际模型输入、重画 100/100 overlay 均逐像素一致；旧口径 239/239 事件身份
完全不变，最大置信度差 `1.11e-16`。最终全仓测试 1734 passed、4 skipped。

总览、五张每日高清图、100 输入 + 100 overlay 无损 ZIP 和 HTML 已作为 8 个 Telegram document
发完并写入回执。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_recent5d_rawbox_repair_20260828.html`；总览：
`experiments/active/exp-15m-ma-launch-owner-yolo-recent5d-rawbox-v2/results/overview_rawbox.png`。
这是该配置经 Owner 授权的 holdout 消费 #2；没有调参、训练、ACTIVE/frozen、promote、forward、
部署或订单变化，仍 `training_eligible=false / production_eligible=false`。**下一条允许动作**：停在
诚实的 97/100 过度检出结论；若要减少信号，另开 pre-holdout 或新鲜前向单变量实验，不得继续
在 2026-08-23..27 上调阈值、挑 episode 或手改框后声称未见验证。

## ⚡ 当前真相（2026-08-28 — 新 Owner YOLO 最近五个完整 UTC 日已扫描）

Owner 要求“跑一下最近 5 天的数据”。已按上一轮口径扫描 2026-08-23..27 每日事后绝对涨跌幅
Top20，共 100 个币种日、75 个唯一币、81,600 个 W18–25 因果窗。新 10k 正 + 30k 负 Owner
YOLO 在 `conf=0.25`、5-bar 去重后得到 **239 个事件：LONG 168 / SHORT 71**；分日
57 / 38 / 47 / 54 / 43。97/100 个币种日有框，平均 2.39 个；前三天完全相同榜单比旧模型
96→142（+47.9%），说明新模型在高波动完成路径上明显更积极，不能包装成更准。

RTX 3060 exit=0，远端/本机 9 个 artifact SHA 一致；独立 verifier 检查五日榜单、100/100
连续 96 根、训练支持几何、239 个坐标、去重与 6 张 PNG 均通过。跨平台回执反斜杠问题已修为
writer 统一 POSIX 路径、verifier 安全兼容旧 Windows 路径，没有重跑或改预测。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_recent5d_20260828.html`；总览：
`experiments/active/exp-15m-ma-launch-owner-yolo-recent5d-v1/results/overview.png`。本配置 holdout
消费 #1，榜单是收盘后事后信息；没有调参、训练、ACTIVE/frozen、promote、forward、部署或交易
变更，仍 `training_eligible=false / production_eligible=false`。**下一条允许动作**：停在这组真实
负面选择性结果；若要减少多框，另开 pre-holdout 或新鲜 tip Gold 实验，不能在这五天上调阈值
后再冒充未见验收。

## ⚡ 当前真相（2026-08-28 — 10,000 正 + 30,000 负的 960 YOLO 已训练完成）

Owner 明确要求“去训练”。RTX 3060 作业已经完成并正常退出：请求 40 轮，实际第 29 轮
`patience=10` 早停，交付第 19 轮 `best.pt`。冻结时间验证集回载结果为 P 0.8852、R 0.9262、
mAP50 0.9462、mAP50-95 0.7923；LONG / SHORT mAP50-95 为 0.780 / 0.805。固定
`conf=0.25` 对全部 5,445 张空标签负例推理，46 张出框，误报图率 0.845%；hard 1.148%，
easy 0.267%。权重与 3060 现场 SHA 一致。

高分追加了 split 身份与完整依赖区间审计：train/val 图像 SHA、sample ID、source sample ID
交集均为 0；191 个共用源文件的依赖区间重叠 0，间隔 76 小时。没有发现直接复制或时间交叉，
但这是 completed-history 的 Owner 批量授权弱标签静态 val，不是 tip 实盘证据；且没有同数据、
同 split、同配方的 10,000 负例训练 arm，因此不能把提升因果归给“多了 20,000 负例”。

报告：`analysis/html/p1_15m_ma_launch_owner_yolo_neg30000_train960_20260828.html`；实际预测渲染：
`experiments/active/exp-15m-ma-launch-owner-yolo-neg30000-train960-v1/results/validation_preview.png`；
权重：`analysis/output/ma_launch_owner_yolo_neg30000_v2/ma_launch_owner_yolo_neg30000_v2_y11s_ft960/weights/best.pt`。
holdout 读取 0，ACTIVE/frozen、promote、部署、forward 与交易状态变更均为 0，
`training_eligible=false / production_eligible=false`。**下一条允许动作**：停在研究结果；若要继续，
应单独预注册同数据 10k-negative 对照或 Owner 明确批准新的 tip 金标/新鲜前向验收，不能直接上线。

## ⚡ 当前真相（2026-08-27 — 负样本已从 10,000 扩为 30,000，旧数据逐字节保留）

Owner 纠正“负样本为什么只有 10,000”并明确“应该搞 3w 张”。新 v2 已在本地完成：
`datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2/` 包含 10,000 正例 + 30,000 负例，
全部 1280×742 无框 PNG；hard 19,922 / easy 10,078。每个正例有 slot 1/2/3 三个不同负窗，
旧 v1 的 10,000 正例与 10,000 负例全部保留，图片 SHA 和标签 SHA 20,000/20,000 一致；新增
20,000 负例 hard 14,923 / easy 5,077。

全量 QA 解码 40,000 张图片并核验 80,000 个文件：图片 SHA 40,000/40,000 唯一，正标签
10,000/10,000 可解析，负标签 30,000/30,000 字节为空，底图精确红框像素 0。30,000/30,000
同币、同源、同半年、同 split、同窗口几何；378/378 个源文件内负依赖区间互斥，全部 14,117
个严格正候选继续受保护。train 为正 8,161 + 负 24,483，val 为正 1,815 + 负 5,445；purge
内正 24 + 负 72 仅留 `excluded/`。holdout OHLCV 读取 0。

实际负输入抽样：
`experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results/actual_negative_inputs_seed50_added50.html`；
报告：`analysis/html/p1_15m_ma_launch_owner_yolo_neg30000_20260827.html`。78 个局部 hard 配额在
相同安全匹配块内回退 easy，未放松阈值、禁入区或时间隔离。数据仍是 completed-history 的整批
授权 weak labels，`training_eligible=false / production_eligible=false`；训练、3060、权重、
ACTIVE/frozen、forward、部署与交易变更均为 0。**下一条允许动作**：Owner 可查看抽样 HTML；
如明确要求训练，另开训练实验，不读 holdout、不自动 promote。

## ⚡ 当前真相（2026-08-27 — Owner 认可的 10,000 张已转成 10k+10k YOLO 数据集）

Owner 明确回复“刚刚的10000张不错，弄成训练数据集，同时弄好负样本”。本轮已经物化本地
`datasets/ma_launch_owner_autofill10000_yolo_v1/`：10,000 正例（LONG/SHORT 各 5,000）+
10,000 一一配对负例（hard 4,999 / easy 5,001），全部是 1280×742 无框 PNG；正例每图一个
独立 YOLO label，负例 label 字节为空。`data.yaml` 暴露 train 8,161+8,161、val 1,815+1,815；
切点 purge 内正负各 24 张只放 `excluded/`，训练看不到。

正例不是从红框图擦框：从锁定 OHLCV 重渲染干净底图，再把旧框临时叠回做 SHA 对照，
10,000/10,000 与 Owner 刚认可的审核 PNG 逐字节一致。全量复核解码 20,000/20,000 图片、
40,000/40,000 图片/标签文件；20,000 个图 SHA 全唯一，模型输入精确红框像素 0，正标签
10,000/10,000 可解析，负标签 10,000/10,000 为空。错框零假设 0/1,000 匹配。负样本保护了
同源全部 14,117 个严格候选，同币、同源、同半年、同 split、同窗口几何，不复用；train hard
4,079，val hard 908。holdout OHLCV 读取 0。

模型实际输入抽样：
`experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-v1/results/actual_model_inputs_sample100.html`；
报告：`analysis/html/p1_15m_ma_launch_owner_yolo_dataset10000_20260827.html`。预注册的手写时间标签
晚于真实墙钟，冻结字节未被事后重写；权威时间线见同目录 `provenance_time_correction.json`。

这仍是 completed-history、Owner 整批授权的 weak labels，不是逐样本 Gold，也不是 tip 检测器。
`training_eligible=false / production_eligible=false`；训练、3060、权重、ACTIVE/frozen、forward、
部署与交易变更均为 0。**下一条允许动作**：Owner 可先看实际输入 HTML；若另行要求训练，必须
单独登记训练实验，且先决定是否审查 136 个 `h_norm>0.5` 的纵向大框，不得自动 promote 或读 holdout。

## ⚡ 当前真相（2026-08-27 — 严格 15m 自动样例已扩到 10,000 张，仍非训练集）

Owner 接受 autofill v7 五十张后要求按同样标准扩到 10,000。正式 v1 已完成：5,000 LONG +
5,000 SHORT，5,153 个 4 根框 + 4,847 个 5 根框；10,000 个 1280×742 PNG 每张恰好一个
红框，事件和图片 SHA 全唯一，覆盖 229 个币。完整画廊 100 页、每页 100 张，另有 100 张等距
总览。入口：`experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/results/public/index.html`；
报告：`analysis/html/p0_15m_ma_launch_owner_autofill10000_20260827.html`。

17,186,076 根 pre-holdout 15m K 扫出 14,117 个严格唯一核心；同币同方向一小时事件 NMS 后
LONG 5,146 / SHORT 6,235，最终各取 5,000。最大距离 0.4954（冻结门 0.5），核心时间严格止于
2026-05-03 18:15Z。全量独立复核已逐张重算 SHA/尺寸/红框像素，100 个 HTML 页的 10,000 条
图片链接也全部解析；锁定 `.venv` 等距重渲染 100/100 PNG 字节一致。holdout OHLCV、label、训练、3060、ACTIVE/frozen、forward、部署和交易
变更均为 0。

这仍是 completed-history P0 shape retrieval：检索用到核心后 +1/+2/+3/+5，且 Owner 只接受了
参考族，不是逐样本确认新增 10,000 个 Gold 边界。`training_eligible=false / production_eligible=false`。
**下一条允许动作**：允许停在本地 10,000 张和 HTML；不得直接生成 labels/负样本或开训。若 Owner
以后另行要求训练，必须先走逐样本 Gold 类别/边界、负样本排除、时间 group split 和训练授权门。

## ⚡ 当前真相（2026-08-27 — 自动补齐 v7 已逐张发 TG，无 Owner 审核任务）

Owner 明确指出此前“20 张有框 + 30 张无框，再请人工复审”不是其要求。strict Review50 v5
因此改判为**任务类型错误并作废**：检索固定数量合格样例时，淘汰必须留在内部并从大池补位，
不能把无框候选和 KEEP / ADJUST / REJECT 工作转嫁给 Owner。

正式 v7 从冻结的 10,000 个 pre-holdout 候选自动补齐 50 张：25 LONG + 25 SHORT，22 个 4 根框、
28 个 5 根框，50/50 每张恰好一个红框，覆盖 47 个币、每 UTC 小时最多 1 张、每天最多 2 张。
核心/释放硬门通过 3,217，连同 Owner #42/#44 方向归一化距离门通过 106，再按时间与币种约束
求解 50 张。2026-08-27 18:42 CST，50 个 1280×742 原始 PNG 已作为 Telegram document 逐张发送，
并于 18:45 CST 发送 HTML；SHA 逐张入回执。开场消息已明确旧 v5 人工审核口径作废。

入口：`experiments/active/exp-15m-ma-launch-owner-autofill50-v7/results/public/index.html`；报告：
`analysis/html/p0_15m_ma_launch_owner_autofill50_20260827.html`。holdout OHLCV 读取 0，YOLO label、
训练、3060 作业均为 0，ACTIVE/frozen、forward、部署与交易状态未改。v6 作为“框内仍含过多已走
行情”的未发送内部失败保留，不得混用。

**下一条允许动作**：本轮允许停在“50 张示例已交付”，Owner 不需要填审核表。若以后要升级为
训练集，必须另走 P0/P1 Gold 类别与逐样本核心边界门，并取得单独训练授权；禁止直接把 v7 PNG
转成 labels、开 3060、promote 或生产切换。

## ⚡ 当前真相（2026-08-27 — 原图 imgsz=1280 重训完成，但不替换 960）

Owner 在抽查同一批 1280×742 训练 PNG 后明确回复“ok 重新去训练吧”，授权单变量重训。RTX 3060
直接读取原来的 36,812 张 PNG 和标签；manifest、train/val、框、正负样本、seed、优化器与全部
增强配方未变，唯一变量是训练 `imgsz 960 → 1280`。源 PNG 未离线缩图或重编码；训练仍保留与
基线相同的内存 `translate=0.02 / scale=0.1`。运行在第 31 轮 patience 早停，最佳第 21 轮，
exit_code=0，远端/本地文件 SHA 一致，best.pt SHA 为
`a9fae2ef64489e24f39bd51714d96b43cf680bf80d5c536537f03a4402e9f9c0`。

固定 pre-holdout 2,940 张时间 val 的同设备 2×2 网格给出：960/960 mAP50-95 0.3313，
960/1280 0.2672，1280/960 0.2187，1280/1280 0.3110。原生对原生的 P/R/mAP50/mAP50-95
分别下降 0.0414/0.0049/0.0337/0.0202；easy 负样本从 3.401 升到 4.082 框/千图，hard 维持
2.723。因此 **拒绝用 1280 替代 960**。1280 权重只保留为可复现负结果，仍是完成态弱标签、
`training_eligible=false / production_eligible=false`；holdout 消费 0，ACTIVE、frozen、forward、
部署和交易状态均未改。报告：
`analysis/html/p1_15m_ma_launch_t3_yolo10000_imgsz1280_20260827.html`。

**下一条允许动作**：回到 Owner 逐样本 Gold 类别与核心边界审核；不要继续按源图像素猜训练尺寸，
也不要 promote 两个弱标签权重。若以后再改分辨率，必须先冻结单变量并跑 train×eval 2×2 网格。

## ⚡ 当前真相（2026-08-26 — 15m t-3 审核/训练视图已纠偏，模型未变）

Owner 指出的两个视觉矛盾已修复为独立 v2 产物：首批 1,000 张审核图从原 OHLCV 重新渲染，
现在蓝线全部位于 `t-3`、橙色虚线保留原选择 `t`；与原本已经正确的后续 9,000 张合并后，
**10,000/10,000** 审核行精确满足 `review_marker_source_i = source_anchor_i - 3` 和 45 分钟差。
旧候选图不覆盖。

新的 40 页审核画廊不再把 48 根完成走势与 14--22 根模型输入当成同一缩放：左栏直接引用
canonical 训练 PNG，并用同名 YOLO `.txt` 做 CSS 叠框；右栏物理分离显示 `t-30..t+17`
未来辅助图。9,938 个正例图片/标签 SHA 逐项接回，62 个 purge 候选明确没有训练图；10,000
卡片、19,938 图片引用、9,938 框、缺失路径 0。holdout 读取 0，训练数据、旧权重、ACTIVE、
forward 和生产状态全部未改。报告：
`analysis/html/p1_15m_ma_launch_review_parity_v2_20260826.html`；完整画廊：
`experiments/active/exp-15m-ma-launch-t3-review-parity-v2/results/index.html`。

**下一条允许动作**：Owner 先看 v2 左栏“模型实际输入+框”是否表达目标。该修复不把统一 t-3
弱标签升级成 Gold，也不授权重新训练；若框语义仍不对，应逐样本修核心边界并走 P0/P1 数据门。

## ⚡ 当前真相（2026-08-24 — 做空优先；Owner-long v2 已冻结暂停）

**Owner 最新选择已覆盖执行优先级：先继续做空，做多暂停但不删除。** 做多 v2 的 1,144 个
唯一 event、SHA 与预切分继续冻结，仍全部 PENDING / `training_eligible=false`；本轮不得继续
生成 long 图片、label、negative 或训练。

当前唯一人工入口切回旧 short 模型实际训练过的 1,345 张正例：
`datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/index.html`。先审默认 A 档
385 张（`K/1` 保留、`X/2` 去掉、`?/3` 待定），不是当前 `owner_2525.html` 的 long 扩数据页。
2026-08-25 起该入口不再缩放整张 900×521 长图，而是按每张原始 Owner 绿色框坐标直接显示
局部放大 canvas；没有“完整原图”切换，不改原图、框、manifest、答案格式或本地进度 key。
1,345 行已复核为 1,345 个唯一 sample、train 1,143 / val 202、跨 split dependency 0；训练图片
与标签 2,690 个文件全部存在且 SHA 通过。未找到已落盘的 `ma_rope_prefilter_v1_answers.json`，
所以不能假装已有人工结果。

排序版审核包现已补齐 fail-closed `summarize` 接回门：必须同时锁定 public manifest、short
positive manifest、review-only truth 与 rope score ledger 的 SHA，并核对答案的 review/sample
双身份；未审行保持 PENDING，未来辅助审核图永不进入模型输入，接回后仍不会修改
`training_eligible`。下一条允许动作是 Owner 审核并导出 JSON；随后只生成回执和新版 manifest
预览，不训练、不读 holdout，等待 Owner 单独批准是否物化与开训。

Owner 当前选择继续整理数据集。做多方向没有复用 short R1 中 916 个空标签，而是回到
`analysis/output/owner_side_review/review_sheet.csv` 的 1,152 个 Owner-long 原框。8 个重复 alias
合并后得到 1,144 个稳定唯一 event；每个 alias 的 Owner 原行、原始 bar/YOLO 几何和原图均已
逐项锁 SHA。唯一 event 的审核顺序 A/B/C=339/480/325（页面去重前 long A=343）。正式 v2：
`datasets/owner_long_gold_center_candidate_v2/candidate_manifest.jsonl`，SHA256
`0b342a75e55d66a99d84e3d6a5be2be90c4e2f3e9de436aef37939cc1d31e929`。

依赖块预切分为 train 963 / val 171 / drop 10，跨 split dependency/event 都是 0；train→val 当前
只证明相隔 158 个名义 15m 时间格，`actual_ohlc_gap_bars=null`，必须在 bounded pre-holdout
物化后再验。构建器没有打开 OHLC、生成图片/label 或读取 holdout；1,144/1,144 都是 PENDING，
`training_eligible=false / production_eligible=false`。v1 保留为历史但已 superseded，禁止复用。
报告：`analysis/html/p1_owner_long_candidate_manifest_v2_20260824.html`。

**下一条允许的动作**：Owner 在 `owner_2525.html` 默认 `A + long + 未审核` 完成 343 个原始框并
导出 JSON（`K/1` 保留、`X/2` 去掉、`?/3` 待定）；先 join 成 339 个唯一 target 的结果。同一
target 的 alias 必须全部有一致答案，否则保持 PENDING 或 fail closed。没有回执前不得生成 long
训练图、YOLO label、negative 或启动训练；旧 short 1,345 refilter 也不能被新 long ledger 覆盖。

## ⚡ 当前真相（2026-08-21 — 六均线绳结预筛已落地，但只能排序、不能自动删图）

Owner 已明确纠正任务：不是审核 fixed-W10 的 2,649 行混合 Gold，而是把**旧模型实际训练过的
Owner 人工正例原图重新过滤一遍**，只留下现在仍认可的最佳形态，再构建新版本训练集。此前
`original_source_triage_v1` 混合六种来源；`canonical_ohlc_triage_v2` 又把当前 OHLC W200 与历史
长图并排，两张本来就不是同一裁剪窗口。二者均已停用为当前审核入口，只保留作谱系证据。

正确母池是 `datasets/owner_short_gold_center_v1/positive_manifest.jsonl`：Owner 当年亲自判为
`short` 的 1,361 个框，15 个重复别名合并、1 个时间切分 purge 后，形成旧训练实际使用的
**1,345 张正例（train 1,143 / val 202）**。它确实来自原来一万多张人工工作：15 份 canonical
Label Studio export 合计 12,565 张唯一图 / 12,684 个 completed annotation / 6,291 个框；随后
有效 OHLC 对齐、方向复核和 short-only 才收窄到 1,345。Owner 新参考图是向上启动，所以本轮
同时评分完整 2,525 个方向框（long 1,152 / short 1,361 / skip 12），不能只处理 short。

Luna Max 可见本机任务直接实现了因果六均线绳结分数：窄带 0.35、交叉换序 0.30、K 线实体
触碰/穿束 0.25、持续 0.05、收紧 0.05；斜率一致性权重 0。1,345/1,345 与 2,525/2,525
全部评分。旧 manifest 有 1,339 条行数后缀路径过期，均经 symbol + decision index/time 精确
闭合找回；不得按旧文件名判数据丢失。

104 个 exact ⭐ 标杆只用于固定分档：旧 1,345 为 A=385 / B=575 / C=385；完整 2,525 为
A=744 / B=1,067 / C=714。独立 390 条 Owner keep/drop 反证未通过：AUC 0.489、置换 p=0.635、
A+B precision 18.21% 等于 base rate。因此分数**只允许改变人工审核顺序**，不允许自动
keep/remove。两个正式入口：

- 旧训练 1,345：`datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/index.html`
- 完整方向池 2,525：`datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/owner_2525.html`

报告：`analysis/html/p1_ma_rope_prefilter_20260821.html`。页面每次只显示一张 Owner 原图，快捷键
`K/X/?/J/L/U/Z`，默认 A 档；不存在左右两张不同 K 线，也没有 `R`。

**下一条允许的动作**：Owner 先审 1,345 页面默认 A 档的 385 张并导出 JSON，再决定是否审 B。
不覆盖旧数据地生成新 manifest 预览后，仍需 Owner 明确批准 `training_eligible=true`；此前禁止
3060 训练。Owner 要求查看的 2026-08-16～21 BTC/ETH 已登记为设计示例 view #1，不能再冒充
未见 holdout 或最终验收。

仍需 Owner 独立裁决：两个 ATR 实现的 warmup 分歧，三选项见
`docs/consolidation/DUPLICATE_SEMANTICS.md` §4。

---

## 历史真相（2026-08-19 — 单仓收敛完成，ACTIVE 未变）

**四个卫星仓已回迁并可归档；本仓成为唯一 ACTIVE 交易研究仓。** 分支
`claude/fable-trading-consolidation-758d61`，8 个 `consolidation(c0..c7)` 提交，
验收 `accepted`：七项检查全过。**ACTIVE 指针、forward log、成本合同、部署脚本、
systemd 单元共 12 个运行安全对象与 C0 逐字节相同**；未训练、未 promote、未部署、
未下单、**holdout 本次消耗 0**。测试 701 → **1185 passing**，新增失败 **0**
（11 个先存失败全是本 worktree 缺 gitignore 掉的 `data/` 产物，集合与 C0 完全一致）。

最要紧的一条：**本仓此前跑不起来，除非 `~/yoyo-trading` 在磁盘上**——63 个文件
import `yoyo.*`，靠 editable 安装指向仓外。整包 55 个 `.py` 已字节一致迁回 `yoyo/`。
另发现 35 个脚本的跨仓 `sys.path` 桥把 yoyo-trading 插在**第一位**，即它们一直在
import 另一个仓的 `yoyo`（含 `render.py`，检测器绑死其像素）；已全部删除并加两道
AST 防回归。

**需要 owner 裁决的一件事**：两个 ATR 实现不一致（warmup 播种差异，bar 14 差 0.109，
200 根后耗尽）。ATR 定义 TP/SL 障碍距离，且差异方向取决于取数起点。已量化并钉住，
**未修**——改任一边都会移动已发布数字，障碍参数是 owner 保留项。三个选项见
`docs/consolidation/DUPLICATE_SEMANTICS.md` §4。

验收报告：[`reports/consolidation/FINAL_ACCEPTANCE.md`](reports/consolidation/FINAL_ACCEPTANCE.md)
· 交接：[`docs/consolidation/HANDOFF_AFTER_CONSOLIDATION.md`](docs/consolidation/HANDOFF_AFTER_CONSOLIDATION.md)
· 四仓历史结论：`experiments/historical/`

**收敛已闭环（2026-08-20）**：PR #1 已合并（`31d6b2a`），随后 4 个提交也已进 main；
四个来源仓的 README 顶部已加 ARCHIVED 只读声明并推送
（`e6e5164` / `be1c7bb` / `a16f495` / `06b97d5`）。**一个仓都没删。**

**下一条允许的动作：只有 P0（owner 形态定义与重复标注稳定性）→ P1（Gold Dataset）。**
在 P0/P1 通过前禁止新训练、多周期扩展、promote 与实盘替换。

旧交接曾把 Cleanlab 28 张写成可直接转成 DIRECT 错误率证据；该口径已被上面的 2026-08-20
审计纠正。28 张是模型选择队列，只能用于优先修错，不能估计无偏错误率。

---

## ⚡ 当前真相（2026-08-13 — ETH全年冻结形态门命中2次；1参考+1待Owner确认）

**最新进展（2026-08-13 17:44 CST）：Owner要求统计今年ETH有多少次8月10日参考形态，冻结门
v1已一次性扫完。** 覆盖2026-01-01至08-13 17:15 CST、21,542根连续ETH永续15m bar，0缺口；
本地规范文件止于08-05，缺失后缀用同一OKX history-candles公共接口只读内存补齐900行，未写
`data/kline_fetched`。核心门只用结束bar及以前OHLC、8根前文、ATR14和六均线；后3/5根只作
事后确认标签。扫描前冻结门、扫描后未调参，11个宽度组合→4个端点→12-bar去重为**2个事件**：
①Owner参考本身，2026-08-10 19:30–20:15 CST，4根；②唯一新候选，2026-02-22
19:00–20:15 CST，6根。Codex视觉复核第二张相似，但不得冒充Owner金标；当前口径是机器候选2、
Owner语义确认1、待确认1，且两张核心几何均未获Owner逐bar确认。该冻结门读取9,734根holdout，
登记为配置第1次且由本轮“找今年有多少”的请求明确授权；所有产物`training_eligible=false /
production_eligible=false`，不训练、不promote、不部署。报告：
`analysis/html/p2_eth_yearly_morphology_count_20260813.html`；对照图与manifest：
`analysis/output/eth_yearly_morphology_gate_v1/`；builder先由commit `afad164`入库。下一步只等Owner
对2月22日候选回复YES/NO。正确项目环境全测712 passed、2 skipped；系统Python曾因缺少yoyo/
ultralytics在收集期失败，未当代码失败或通过隐去。

**上一进展（2026-08-12 — 拿到119个早期前沿YES；主动检索机制被证伪，块效应主导）：**

**最新进展（2026-08-12 13:47 CST）：Owner已完成300张早期前沿审核，解盲诊断完成；
检索分层零区分度，时间块解释几乎全部方差；等待Owner在A–E选项中点头，不得自动开R3。**
300/300裁决与manifest精确一一对应，119 YES / 181 NO / 0 SKIP / 0改判，总YES率39.7%
（Wilson 34.3–45.3%），覆盖89个币。解盲后内部`yes_like` 61/150=40.7%、
`similar_no_boundary` 58/150=38.7%，差2.0pp、置换p=0.81；affinity AUC 0.534(p=0.33)、
最近邻距离AUC 0.509(p=0.78)、model_confidence AUC 0.548(p=0.17)——用11个Canary YES做的
5-NN检索在Owner语义上等于随机，且方向在块间翻转（B04 boundary 0.56>yes_like 0.29，
C03 反过来0.74>0.32）。唯一显著的轴是候选块：B03_20251115 4.0% → C05_20260215 73.5%，
极差69.5pp、置换p=1e-4，且各块两层样本数均衡（24–26/7–8），不是抽样混淆。几何切片
（核心6–7根44–45% vs 4–5根34%）与低波幅1–2%仅19.1% YES均未做块内控制，不能当结论。
本轮YES是**未来辅助语义裁决**，不是precision、不是基率、不是独立时间块；框坐标仍是R1提议，
只算逐样本类别确认，不算框几何金标。审核节奏中位0.87s/张（上一轮0.98s），标注噪声未量化。
300个事件仍全部`training_eligible=false`、`production_eligible=false`，未训练、未转标签、
未改conf/NMS/窗口/ACTIVE、未deploy、未读holdout。报告：
`analysis/html/p2_local_signal_v2_early_frontier_review300_owner_result_20260812.html`；
产物：同目录`owner_review_summary.json` / `owner_review_joined.jsonl`；
汇总器`scripts/summarize_local_signal_v2_early_frontier_review.py`（先入库commit e7ba4b1后运行）。
测试`pytest tests`：709 passed、2 skipped。下一步选项（需Owner决策）：
A 批准119 YES/181 NO转为**仅类别**训练标签；B 做块内随机抽样150–200张拿真实基率（推荐）；
C 用130个YES重建检索但先离线验证；D 盲重复审核10–15%估一致率；E 直接开R3（不推荐）。

**上一进展（2026-08-12 13:29 CST）：Owner批准的“早期启动前沿 YES / 相似NO”300张语义
发现包已完成，当前必须停止并等待Owner审核。** 两个冻结R1候选池合计1,484事件；精确剔除
此前四轮700个已审唯一event_id后剩784，再用上一轮Canary 11 YES / 89 NO的decision前因果
OHLC+SMA/EMA20/60/120+框几何做5-NN主动检索，内部选150 `yes_like` + 150
`similar_no_boundary`。UI已盲化抽样层/置信度/距离/来源，无推荐答案。最终300唯一事件、154币，
与旧700重叠0；300模型输入+300 auto-Y因果图+300独立未来48根图全部存在且SHA唯一，300/300
`visible_end_bar == decision_bar`、检索未来0、默认裁决0、training-eligible 0、holdout读取0，
最晚未来图仅到2026-02-16。真实浏览器已验证Y/N/S、左右键、append-only改判、刷新恢复；正式
目录未写测试裁决。全仓701 passed、2 skipped。审核入口命令：
`scripts/serve_local_signal_v2_semantic_review.py --out analysis/output/local_signal_v2_early_frontier_review300_v1 --port 8766`，
浏览器开`http://127.0.0.1:8766/`。PRE-REVIEW报告：
`analysis/html/p2_local_signal_v2_early_frontier_review300_prereview_20260812.html`。

证据等级必须保持诚实：这300张来自旧train-time块的**未审余量主动发现集**，不是新post-train
时间块或独立precision验收；最初8个D块因3060不可达、Mac MPS过慢而放弃，其96MB可重建快照
已移动到`~/.Trash/codex-local-signal-v2-cleanup-20260812/`。当前包审计的是冻结R1 W12–19候选；
293/300框为4–7根，7/300为8根，不能宣称Owner目标20–30根检测窗口已经实现。Owner审核结果
写入`analysis/output/local_signal_v2_early_frontier_review300_v1/owner_verdicts.jsonl`。审核完成后
只做ID联结、总YES率与内部150/150解盲诊断；**不自动训练R3/R4、不改positive标签/conf/NMS/
窗口/ACTIVE，不deploy、不下单、不清forward log、不读取holdout。**

**最新进展（2026-08-12 12:40 CST）：Owner批准的只读边界诊断已完成，根因进一步收敛为
“尺度分布偏移 + 启动释放语义未学稳 + 正例成熟度偏后”，禁止据此直接开R3。** 200条审核样本
逐条联结原始causal OHLC并复算：200/200 source lineage与model-input SHA通过，最大物化时间
2026-05-03 19:45 UTC，0 future图片、0 future OHLC、0 holdout。Canary模型纵轴占用/单根K高度
中位仅18.0%/29.0px，Positive为51.0%/55.2px；97/100 Canary触发6% floor，说明尺度风险明确，
但floor-off仅3条且真实跨度≥4%的8条仍0 YES，不能宣布“改auto-Y即可解决”。Canary YES相对NO
的核心差异是核心跌幅-45.4/-13.7bp、decision收盘相对六线-73.0/-9.8bp、decision六线跨度
75.4/35.0bp、20线框后斜率-4.68/-1.24bp/bar；核心横向中心57.1%/56.3%，位置不是主因。
Positive YES又明显比Canary YES成熟：框后跌幅-60.1bp vs -13.1bp，说明85% purity没有覆盖
“启动前沿”。R2 new仍0/25且结构最弱，R1 suppressed 5/25。下一步推荐先扩新的pre-holdout
早期YES/相似NO语义对照，再固定数据/split/框/配方只改renderer表示做单变量臂；当前不训练、
不调conf/NMS、不转标签。仓库主测试697 passed、2 skipped。报告：
`analysis/html/p2_local_signal_v2_semantic_boundary_diagnosis_20260812.html`。

**最新进展（2026-08-12 11:55 CST）：Owner已完成v2全部200张YES/NO审核，机器诊断明确为情况B；
禁止自动开R3/R4。** 裁决日志200行/200唯一ID，与manifest精确对应，96 YES、104 NO、0 SKIP，
0 holdout。旧Positive Pool为85 YES/15 NO（85%），当前Canary为11 YES/89 NO（11%）；内部
common retained=6/50、R2 new=0/25、R1 suppressed=5/25。R2既产生纯NO的新候选，又抑制部分
Owner认可的R1信号；R1/R2均继续blocked。Canary high-confidence也仅4/27 YES，调conf无证据
解决。真实波幅<1%的Canary为2/39 YES，2%–4%为4/15，说明模型6%最小纵轴可能损害低波幅
分辨率，但≥4%仍0/8，不能直接归因或立刻换renderer。下一步推荐先做**只读边界诊断**：比较
尺度占用、均线结构、框位置、decision延迟和R1/R2差异；不训练、不读holdout。结果报告：
`analysis/html/p2_local_signal_v2_positive_semantic_audit_owner_result_20260812.html`。85个旧Positive
YES、11个Canary YES和104个NO当前仍`training_eligible=false`，转换训练数据需Owner另行批准。

**最新进展（2026-08-12 11:36 CST）：Owner指出v1无走势对照且K线被压平，正式审核入口已
切换为v2，v1禁止继续裁决。** 根因是模型输入renderer的`MIN_REL_SPAN=0.06`：真实波幅不足6%
时蜡烛会被训练纵轴压成水平带；train/live虽一致，但低波幅形态分辨率可能因此受损，尚未验收。
v2物理分开200张原始模型输入、200张人眼auto-Y因果图和
200张独立auto-Y未来对照；左图仍为200/200止于decision，右图只作Owner参考且全部早于
holdout。Positive保持v1原100个不换；Canary保留69个并替换31个未来不足16根的事件，内部配额
仍为共同50/R2新25/R1抑制25。Positive未来48根，Canary安全未来16–46根，0 holdout读取、
0预选答案、0训练资格。正式入口`http://127.0.0.1:8766/`，裁决写入
`analysis/output/local_signal_v2_positive_semantic_review200_v2/owner_verdicts.jsonl`；报告：
`analysis/html/p2_local_signal_v2_positive_semantic_audit_prereview_v2_20260812.html`。最终YES率必须
称为“未来走势辅助的Owner语义裁决”，不得冒充纯causal precision。继续停止R3/R4与hard-negative。

**最新进展（2026-08-12 10:18 CST）：Local Signal V2 Positive语义纯度审计PRE-REVIEW已完成，
当前必须停止实验并等待Owner完成200张YES/NO/SKIP。** 审核包包含当前R2使用的1,345个SHORT
positive分层抽样100张，以及最新独立Canary分层抽样100张（内部为共同保留50、R2新生25、
R1抑制25，UI已盲化）。全部为独立PNG；`visible_end_bar == decision_bar`和`future_bars == 0`
均为200/200，0预选答案、0训练资格、0 holdout读取。界面只提供YES/NO/SKIP及Y/N/S、左右键，
裁决append-only保存，可中断继续和修改。正式入口需先运行
`scripts/serve_local_signal_v2_semantic_review.py --port 8766`，再打开`http://127.0.0.1:8766/`；
PRE-REVIEW报告：`analysis/html/p2_local_signal_v2_positive_semantic_audit_prereview_20260812.html`。
**在Owner审完前禁止训练R3/R4、继续加hard negative或提前判断模型好坏。**

**最新进展（2026-08-12 00:50 CST）：第三训练臂R2已完整训练并在新连续块终审，结论失败。**
run=`owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`在RTX 3060跑满40轮，best epoch35，
best SHA=`52cd38fd…32afe`；3060最终best复验P/R/mAP50/mAP50-95=
0.8365/0.7596/0.8780/0.7432，Mac固定val独立复验=0.8475/0.7525/0.8774/0.7413，
与R1的0.8626/0.7770/0.8980/0.7405相比没有实质val提升。新非重叠pre-holdout块为
2026-05-03 12:15–23:45 UTC，215币、10,105 endpoints、80,840个W12–19暴露窗，conf0.25、
NMS0.70、同币核心中点±5根去重全部冻结，holdout读取0。R1→R2 raw 3,964→3,538
（-10.75%），事件223→195（-12.56%），折算455.5→398.3 events/day，触发币98→93；仍远超
“少而准”。跨模型一对一配对为共同163、仅R1 60、仅R2 32，R2自身旧问题保留率83.59%。
R2不得promote、部署或调高conf美化。完整报告：
`analysis/html/p2_owner_short_gold_center_hardneg_r2_canary_20260812.html`。下一步先分层审计共同保留/
R2新生/R1被抑制事件，不自动开第四臂、不读holdout。

**最新进展（2026-08-11 23:14 CST）：Owner明确回复“允许”，第三训练臂已在RTX 3060启动。**
run=`owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`，远端`zzc@192.168.1.4`，WMI cmd
PID 42396、python PID 37808/worker 40452。上传端核对train 4,572、val 402，0 corrupt；首轮已
运行到约28%，约7 batch/s、显存2.7G。日志逐项确认Stage A SHA `c0e94f47…bf1a`、YOLO11s、
imgsz960、batch8、epochs40、patience10、seed0、AdamW lr0=1e-4、warmup0.5、rect=true，
flip/mosaic/mixup/HSV等禁用增强全0；三库版本Mac/3060一致。授权只覆盖本次训练启动，**不含
holdout、promote、部署、TG或交易操作**。启动回执：
`analysis/output/owner_short_gold_center_hardneg_r2_train_launch_20260811.json`。训练尚未完成。

**最新进展（2026-08-11 23:08 CST）：第三训练臂数据集已完成，等待Owner单独授权训练。**
新时间块200张裁决为26对、2框偏、172不对；四张train-time页累计114 target、2 rebox、584
Owner确认难负例。584个全部唯一、0 Owner框重叠、0 holdout、训练因果图0未来。为保持第二臂
W12–19分布和2,286 hard总量不变，本轮按桶内当前模型触发置信度纳入531个；W18/W19溢出的
53个暂存，未改W、复制或挤占别桶。第三臂仍为train 1,143正+1,143 easy负+2,286 hard、val
202正+200 easy负；hard组成531确认误报+852 Owner-long+903旧模型背景。base 5,376文件逐SHA
一致，0联合SHA重复、0语义区间重复、0 train/val交叉；Ultralytics数据检查通过；全测674 passed、
2 skipped。训练输入200张审计：
`analysis/html/p2_owner_short_gold_center_hardneg_r2_audit200_20260811.html`；数据报告：
`analysis/html/p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.html`。Stage A初始化、40轮、
batch8、AdamW 1e-4和增强合同均已预注册但**尚未启动训练，未promote、未部署、未读holdout**。

**最新进展（2026-08-11 22:42 CST）：第二张 train-time 难负例扩充页已由 Owner 全部裁决为
25对、0框偏、175不对，协议/源SHA/200 ID/计数/因果与时间门全部通过。** 三张 train-time
审核页累计为88正例+412难负例；412只占第二臂2,286 hard槽位18.02%，因此没有直接训练。
Owner裁决报告：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.html`。

已转向五个未使用冻结train 12小时块（2025-06/08/10/12、2026-02）：881 symbol-block、42,288
endpoints、338,304个W12–19因果窗，固定权重/conf/NMS/去重合同得到10,533 raw→589事件；剔除
22个Owner框±12 bars重叠后安全池567。C02真实只有12事件，未降阈值或复制，缺额确定性均分给
其余块；新审核页最终47/12/47/47/47，共200事件、131币，与此前500个train已审事件零重复。
选择使用累计1,308正例/666负例参考且不看未来；未来48根只在选定后渲染。600图存在、全部未来
48根完整、0 holdout、0 labels、0 training-eligible。审核入口：
`analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_20260811.html`；报告：
`analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.html`。下一步仍是Owner完成
1/2/3裁决，随后审计累计覆盖；**尚未授权训练、promote或部署。**

**最新进展（2026-08-11 21:25 CST）：正例检索100张已由Owner裁决为45对、0框偏、55不对。**
源SHA、100 ID、声明计数、0 pending、0 Owner框重叠、0前视均通过。与负例偏置页18/200=9%
target相比，正例检索页45/100=45%，同一权重下富集提高5倍，证明两种审核页必须分开；两者均为
偏置主动学习集，不能冒充总体precision。累计train-time Owner参考为63正例+237难负例；237只占
第二臂2,286 hard槽位10.37%，仍不能开第三臂训练。

已用累计1,283正例参考（1,143 train金标+77 post-val语义正例+63 train新正例）与491确认误报
参考（254 post-val+237 train）重评原池剩余617事件，生成第二张**明确标注预期多数按3**的
难负例扩充页200张：B01/B02/B03/B04=21/60/60/59，B05原59事件已全部在前两页审完而不伪造；
200事件、126币，与前300零重复，600图存在、未来48根全满、0 holdout、0 labels、0
training-eligible。入口：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_20260811.html`；
报告：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_report_20260811.html`。下一步等Owner
审核新200；随后转向新的未使用冻结train时间块扩挖，不在原五块无限循环。**尚未授权训练。**

**最新进展（2026-08-11 21:06 CST）：文档路线没有跳步。** 第一臂 1:1 easy-negative
baseline 后，第二臂已按文档完成 `1,143 positive + 1,143 easy + 2,286 hard`（负:正=3:1，
hard占负例2/3），best SHA=`029f80a5…f537`；连续行情密度仍失败，未promote。train-time
负例偏置 review200 已由 Owner 全部裁决为18对、0框偏、182不对；协议、源SHA、200 ID一一
对应、0 pending、0 Owner框重叠、0前视全部通过。182是第三臂的高质量新hard种子，但只占
2,286槽位7.96%，不能直接开训或复制凑数。

为单独回答“模型能否找到接近早上ETH的形态”，已从剩余717个未审train事件新建正例检索页，
与负例页目的相反：参考为1,143个冻结train Owner金标 + 77个post-val已审语义正例 + 18个
train新正例，对照254+182个确认误报；只用decision前OHLC/六均线/框几何排序，未直接读取
2026-08-10 ETH holdout行情。选出100个全新事件、75币、五块20/20/20/21/19，与已审200零
重复；300张图存在、未来48根全满、0 holdout、0 labels、0 training-eligible。审核入口：
`analysis/html/p2_owner_short_train_positive_retrieval100_20260811.html`；报告：
`analysis/html/p2_owner_short_train_positive_retrieval100_report_20260811.html`。下一步等Owner完成
100张1/2/3裁决，再扩hard种子并设计第三臂；**尚未授权下一次训练。**

**最新进展（2026-08-11 晚）：第三臂训练前的 train-time 难负例审核集已经就绪，下一动作是
Owner 审核 200 张，不是直接训练。** 使用当前 hard-negative R1 best 在冻结 train 末端之前的
5 个独立 12h 时间块扫描：916 symbol-block、43,968 endpoints、351,744 个 W12–19 因果窗，
20,711 条 raw 检测去重为 953 事件；剔除 36 个触碰任一 Owner 框 ±12 bars 的事件后，安全池
917。按每块 40 个固定抽出 200 事件、123 symbols。排序只用 decision 前 OHLC/六均线/预测框，
此前 Owner 已审的 254 负例 + 77 语义正例仅作形态距离参考；未来 48 根在选样完成后才单独
渲染。600/600 图片存在，200/200 未来图完整，0 holdout、0 labels、0 training-eligible。
审核页：`analysis/html/p2_owner_short_train_hardneg_review200_20260811.html`；报告：
`analysis/html/p2_owner_short_train_hardneg_review200_report_20260811.html`。Owner 完成 1/2/3 裁决
并导出 JSON 后，先统计真负例数量与覆盖；不足则继续扫未使用 train 块，足够才构建保持正例、
easy negative、冻结 val 和训练配方不变的第三臂。**尚未授权下一次训练。**

**Owner最新纠正覆盖本节后文“Codex逐图寻找启动边界并重画61个橙框”的下一步：不得在已有
Owner原始金标坐标时重新凭感觉画框。新血缘必须是`原始⭐手框 ∩ Owner亲自确认short`，外层只
重裁十几根短窗，内层橙框从原手框正中心机械截取。61张Codex橙框保留为失败对照，不再作为待
批准训练标签。**

**最新裁决覆盖下面同日所有“固定20–30根窗口 / 0–10根后文”的表述：Local Signal V2 检测
“完美平台/启动形态”语义，不是盘口信号，也不是固定裁剪模板。ETH参考中红框只落在Owner
标出的两条竖线之间，核心约4–7根，不能包入右侧快速下跌。输入从最短充分上下文开始动态变化；
首轮只试约14–22根并继续向更短收缩。核心结束后3–5根内确认，3根优先、5根硬封顶。**

- 今天 Owner 提供的 ETHUSDT 15m 图是终极形态语义参考，不替代昨晚的数据集和训练成果。
- 昨晚 3060 真正完成训练的是 `local_signal_v2_stagea_randomcrop_v1`：2,378 正例 + 2,378
  easy negatives，train 4,040 / val 716，0 holdout。模型最佳 epoch 38、53 轮 early stop，
  最终复验 P/R/mAP50/mAP50-95=0.2376/0.4330/0.2332/0.1266。
- Stage A 继续作为宽位置表征底座，不从零推倒；但它机械使用 `anchor-2..decision`，实际框宽
  只有 4/5 根。旧 `dense_owner_w20_midbox` 保存了原始 Owner 5/7 根框；按 Stage A 修复后的
  event/time split 重新联结，2,378/2,378 事件完整对应。
- 旧W20–30候选中只有 **316** 个Stage-A train事件满足框后3–5根：delay3/4/5分别94/107/115，
  旧框宽5/7根分别171/145。位置为middle/right/far-right=36/265/15，**83.86%落在right带**；
  这批只能复核语义与边界，不能直接成为新短窗训练集。
- 模型已基本打掉固定右侧 shortcut：四位置桶 recall spread 14.72pp，位置-分数
  Spearman=-0.134；但精确度不合格。conf=0.10 event precision 22.66% / recall 52.79%，
  conf=0.20 precision 30.67% / recall 13.97%，阈值无法同时修复精确度与召回。
- 最大数据缺口：这316个旧框尚未按ETH两条边界线重新裁决；hard negative=0。V2审查页抽取
  200张：delay3/4/5分别80/65/55，按钮区分“形态和框都准 / 形态像但框要改 / 不是目标”。
  不看后续收益、不看模型置信度、不读取val/holdout。Owner裁决前全部
  `semantic_status=unreviewed / geometry_status=unreviewed / training_eligible=false`。
  诚实路径说明：316张母池中45张、200张审查中30张源文件物理路径位于旧
  `dense_owner_w20_midbox/images/val/`；这是历史按币种错split留下的目录名，按修复后的Stage-A
  时间split它们全部属于train，未使用358个Stage-A val事件。
- 已新增30张**动态短窗校准集**，不是训练集：post 3/4/5各10张，pre 6–10各6张，旧核心
  5/7根各15张；W实际14–22，框中心自然分布53%–71%，30个事件/币种全不重复。三张大图证明
  “固定最右/固定正中”的几何偏差已消除，但也直接暴露旧核心框仍会把部分明显启动大K包进去。
  因而全部保持`semantic_status=unreviewed`、`geometry_status=unreviewed_legacy_core_proposal`、
  `training_eligible=false`；动态重裁剪不能替代Owner语义/边界裁决。
- Codex已按唯一确认的ETH空头参考完成保守一审：`short_keep=5`、`short_rebox=4`、
  `short_hard_negative=4`、`mirror_unconfirmed=17`。4张rebox已把明显启动侧K从核心proposal
  移到3–5根确认区；代表板用绿=保留、橙=新框（红虚线=旧框）、红叉=难负例候选、紫=多头
  镜像。该一审不是Owner金标，全部`owner_confirmed=false / training_eligible=false`。
  当前必须先由Owner确认空头一审方向，并决定多头镜像是排除、独立类别还是方向归一化同类；
  决策前17张镜像既不是正例也不是负例。
- Owner随后明确回复“确认”，冻结为**只做空**，认可绿/橙/红代表板方向，并授权把冻结train事件
  扩到200张动态短窗；多头镜像排除且不得当负例。确认回执明确：只授权扩200，不授权训练、
  holdout、生产，也不自动确认200张逐样本标签。
- 200张已按相同事件重渲染完成：post3/4/5=80/65/55、core5/7=100/100、pre6–10各40、
  W14–22、112 symbols、0重复、0 val图/标签、0 holdout行。Codex逐板一审为：
  `short_keep=40`、`short_rebox_pending=61`、`short_hard_negative=25`、`mirror_excluded=74`。
  61张橙桶已完成逐图编号、启动边界判定和重画：没有统一左移，形成14种起止位移；新核心
  4/5/6/7根=2/56/2/1，post3/4/5=25/20/16，新完整窗W14–20，框中心自然为53.3%–71.9%。
  每个新核心都早于旧核心结束，橙色新框与红色旧框已在7张对照板重渲染；最大读取时间
  2026-03-18 12:15 UTC，0 holdout。几何已完成但仍是Codex proposal，当前200张全部
  `sample_owner_confirmed=false / training_eligible=false`，未开训。**该61张手工proposal现已被
  Owner否决为标签来源**；不得再请求批量认可或写入正式数据集。
- 已恢复真正Owner金标血缘：独立Owner框2,525个中，Owner亲自确认short为1,361框/1,317图；
  再与原始`⭐标杆`坐标逐框IoU=1.000联结得到71框，69框仍有未加工原PNG、2框原图缺失而诚实
  跳过。69张新橙框全部取原红框正中心，4/5/6/7根=24/16/12/17；外层W12–17，后文3/4根=
  56/13。无Codex重画、无模型预测、几何不看未来；审核未来48根独立。当前仍
  `training_eligible=false`，Owner只需确认“原框中心裁切”合同，确认后按同法扩1,361个short框。
- Owner已回复“可以”，中心裁切合同通过并已扩全量：1,361行short标注全部定位；独立审计发现
  15组为同一市场窗+同一核心框的历史别名，已在split前去重为1,346个目标，并把全部原Owner id
  保留在canonical manifest。重叠输入仍为1,287个依赖块；按块时间切分+150 bars purge后，
  train/val/drop正例=1,143/202/1，实际间隔162 bars。配套同币、同split、同W真实空背景
  train/val=1,143/200；2个val无安全背景而诚实缺失。0联合SHA重复、0跨split、0负例碰全部
  Owner框±12根；同路径二次重建正/负manifest SHA完全一致。当前为交接规范的1:1 easy-negative
  首臂，只用于挖hard negatives；第二臂必须是1:2/1:3且hard占大头，不能直接堆三倍easy。
- Owner于2026-08-11 13:29 CST明确“直接去训练吧”。3060 run
  `owner_lsv2_short_gold_center_v1_ft`已完成：Stage A best初始化、YOLO11s、imgsz960、batch8、
  seed0、40 epochs、patience10、显式finetune AdamW lr0=1e-4；flip/mosaic/mixup/HSV全0。
  40/40耗时1,833.54秒，best epoch=30；epoch记录P/R/mAP50/mAP50-95=
  0.8619/0.9010/0.9244/0.7427，3060最终best复验=0.8508/0.9035/0.9224/0.7302，
  Mac MPS独立复验=0.8467/0.9024/0.9206/0.7294。远端/本地best SHA均为
  `da278820…fc65b4`。曲线早期曾从mAP50 0.6468跌至0.0799再恢复，且当前val只是平衡Owner正例
  与easy背景，**不得用高mAP宣布成功**。下一步冻结best扫训练时间块连续窗口，挖hard negatives并
  建1:2/1:3（hard为主）第二臂；仍`production_eligible=false / auto_promote=false`，不读holdout、
  不改ACTIVE、不部署。
- 第二训练臂的数据已按交接规范完成并审计：train正例1,143、easy negative 1,143、hard
  negative 2,286，总负例:正例=3:1，hard占负例2/3；val仍冻结为202正例+200 easy背景，没有拿
  holdout或未来收益挑负例。hard来源为916个Owner-long语义负例和1,370个仅在原train时间块由
  当前模型排序挖出的背景；200张独立审计页已生成。Owner于2026-08-11 16:12 CST明确回复
  “允许 开始吧”，逐次授权run `owner_lsv2_short_gold_center_hardneg_r1_ft`。该run已完整跑满40轮，
  总耗时3423.21秒，best SHA=`029f80a5…f537`；Stage A初始化、epochs40、patience10、batch8、
  seed0、AdamW lr0=1e-4、warmup0.5及全禁用增强均与1:1 baseline一致。Mac独立固定val
  P/R/mAP50/mAP50-95=0.8626/0.7770/0.8980/0.7405，模型更保守但不能仅凭mAP晋升。
- 新旧模型已在固定post-val、pre-holdout连续12小时canary同场扫描：215币、10,320 endpoints、
  82,560 W12–19 exposures/模型，最大物理读取时间2026-05-03 12:00 UTC，holdout读取0。原始命中
  22,037→8,268（-62.48%），去重事件732→331（-54.78%），全市场折算1,464→662 events/day，
  触发币177→140。方向有效但密度仍失败，当前权重禁止promote；先人工复核331事件，再决定是否
  建第三臂。报告：`analysis/html/p2_owner_short_gold_center_hardneg_canary_20260811.html`。
- 上述331个事件已全部做成逐张Owner审核页，不是抽样或订单：140币，首次conf median/p90=
  0.3627/0.6553，事件peak conf median/p90=0.6344/0.8699。每个事件物理隔离为原始因果输入、
  橙框审核副本、最多未来48根对照，共993张PNG；328张有完整48根、3张47根，最大读取时间
  2026-05-03 23:45 UTC，0 holdout、0 labels、0 training-eligible、0预选。审核V2已按Owner反馈
  缩成三项：`1=对 / 2=框偏 / 3=不对`，按键后自动下一张，`Z`撤销；延续重复改由相邻事件关系
  另行诊断，不再占Owner主按钮。选择只存在本机并可导出完整JSON。331只够做错误分类种子，
  不够直接承担第三臂全部hard negatives；Owner裁决后还要从多个未使用pre-holdout块补挖。审核入口：
  `analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_20260811.html`；报告：
  `analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.html`。
- Owner随后发现未来对照K线普遍视觉过平；根因不是行情或数据，而是V2复用了训练renderer的6%
  最小纵轴跨度。291/331张（87.92%）实际图幅不足6%，实际波幅p10/median/p90=
  1.50%/2.99%/6.75%。审核V3已改为每图按真实OHLC+六均线极值自动缩放，图头和卡片显示真实
  波幅；331张未来图已重渲染。V2→V3的331张原始因果图和331张带框因果图SHA逐行不变，只有
  未来审核图变化；仍0 holdout、0 labels、0 training-eligible，审核入口路径不变。
- Owner已完成V3全部331个事件裁决：66对、11框偏、254不对、0 pending；协议、源SHA、ID集合和
  声明计数全部通过一对一质量门。该12小时块精确event precision=19.94%，把框偏也算形态命中时
  semantic precision=23.26%，明确负例率76.74%。peak conf有排序但不能替代重训：≥0.8语义
  precision=50.63%且只保留40/77语义正例；≥0.9虽为81.25%，仅保留13/77。254个Owner负例覆盖
  125币，但事件时间2026-05-03 00:15–12:00 UTC严格晚于冻结val末端2026-05-02 23:45，故只能
  作错误参考尺，直接回流训练会破坏时间切分。下一步必须在冻结train末端2026-03-13 18:30之前
  挖出同类负例，再建保持总量/W桶/val/配方不变的第三臂。报告：
  `analysis/html/p2_owner_short_hardneg_canary_owner_review_20260811.html`。
- Owner随后明确要求用刚训练权重回放最近2天并发TG，登记为该配置第1次消耗holdout。一次性OKX
  快照覆盖214/215个训练分布币种，W12–19逐bar扫描328,704窗，71,204条原始命中去重为2,500
  个事件，即60.845 events/1000 bar endpoints、1,250 events/day、5.84 events/币/天；211/214
  币触发，密度明确失败。2,091个已了结事件净@taker均值-0.203%；严格同event_id成对的1,498组
  为事件-0.231%、同币×同日×ATR桶随机+0.170%、差值-0.401%。不得在本次holdout上调阈值，
  当前权重禁止promote。
- 几何/时序并非全错：94.92%核心为4–7根，98.12%首次确认延迟为3–5根；模型精确命中Owner的
  ETH参考核心（2026-08-10 19:30–20:15 CST，4根，21:00决策，延迟3根，conf_max=0.905，
  纸面TP净@taker+1.020%）。但相邻20:45–21:45又产生一个延续事件，说明“能命中终极样例”与
  “全市场precision合格”必须分开裁决，后者当前失败。
- TG交付已核对：摘要、25/25张信号图（ETH目标为第1张）、HTML和全量事件CSV全部发送成功；
  回执在`analysis/output/owner_short_gold_center_recent2d_v1/telegram_receipt.json`。详细报告：
  `analysis/html/p1_owner_short_gold_center_recent2d_holdout_20260811.html`。
- 数量口径：本机原始缓存有602个CSV（1.2GB），其中456个15m文件，237个至少覆盖365天，
  单文件最长约430天。Stage-A可追溯正事件池为2,378个（train 2,020 / val 358），不是200个；
  当前200只是从2020个train中满足旧框后3–5根条件的316个候选里抽出的语义校准包。
  “3–5”是3–5根15m K（45–75分钟），不是3–5天。正式训练数据必须在Owner确认语义后回到完整
  历史池扩正例与难负例，禁止拿200张校准包直接开训。
- Owner审核页已改为双图：左图是冻结训练短窗，右图额外显示未来48根/12小时。未来图只存在
  `review_future_only/`及独立manifest，紫线标记训练截止；61张训练图生成前后SHA逐字节不变，
  未来目录无labels、0 holdout。HTML可逐张选择“认可/还要改/剔除”，也可浏览后全部认可并复制JSON。
- 所有旧数据、权重、日志、候选 ledger 和失败对照臂均保留。固定右侧/causal blank 臂只停用，
  不删除。延迟形态检测器仍 `production_eligible=false`，不得冒充新鲜信号进入 forward/ACTIVE/
  部署；若未来用于执行，必须另批完整窗口右端时间戳和延迟预算。

V2审查页：`analysis/output/owner_eth_target_review_v2_shortdelay/index.html`。30张动态校准图：
`analysis/output/owner_eth_shortdelay_calibration30_v1/`。Owner已回到当前会话，当前无需TG；
优先直接在对话中展示PNG。Codex空头一审与代表板：
`analysis/output/owner_eth_shortdelay_codex_firstpass_v1/`。动态200与一审：
`analysis/output/owner_eth_shortdelay_dynamic_review200_v1/`、
`analysis/output/owner_eth_shortdelay_review200_codex_firstpass_v1/`。61张逐图改框与7张对照板：
`analysis/output/owner_eth_shortdelay_review200_rebox_v1/`。
Owner交互确认页：`analysis/html/p1_owner_eth_shortdelay_review61_owner_gate_20260811.html`；未来审核图：
`analysis/output/owner_eth_shortdelay_review200_rebox_v1/review_future_only/`。
上述61页只保留失败对照。当前Owner审核入口：
`analysis/html/p1_owner_gold_center_crop_owner_gate_20260811.html`；源/短窗/未来三联图：
`analysis/output/owner_gold_center_crop_review_v1/`。
全量数据：`datasets/owner_short_gold_center_v1/`；64张正/背景配对审计：
`analysis/html/p1_owner_short_gold_center_dataset_audit_20260811.html`；报告：
`analysis/html/p1_owner_short_gold_center_dataset_20260811.html`。

## ⚡ 历史阶段（2026-08-11 — Owner 授权恢复真正的 Stage A 随机裁剪）

**直接裁决：`causal_blank_w30_v3` 只改变画布 X，框相对真实 K 线内容仍贴在最后几根，
Owner 已目视否决。该数据即使九道旧 P0 为绿也不得训练。Owner 随后明确授权恢复交接文档
Stage A 离线预训练：必须改变原始连续 K 线的 `crop_start_bar`，让框在真实 K 线序列中落入
左/中/右不同位置。**

- 授权范围：历史 pre-holdout Stage A 形态表征预训练；允许窗口包含 decision 后真实 K。
- 禁止范围：不得把 Stage A 指标当实盘结论，不得直接进入 tip-smoke/forward/ACTIVE/部署。
- 最终裁决：仍只认严格因果 Stage B + 真 tip；Stage A 权重只能作为 Stage B 初始化。
- 旧 `dense_owner_w20_midbox` 虽是真随机裁剪，但按币种哈希切分、含 246 条 holdout、
  2,300 个 unmanifested 图，故只能作为失败证据，不能恢复训练。
- 新版冻结目标：W20–30、Mode C delay 1/2、box=`anchor-2..decision`、anchor X 四桶目标
  20%/35%/30%/15%，一 event 一 crop、时间切分 + 150 bars purge、完整窗口早于 holdout。

新版 `local_signal_v2_stagea_randomcrop_v1` 已完成：2,378 正例 + 2,378 easy negatives；
train 4,040、val 716。24 张独立预览按四个真实 K 线位置桶各 6 张，anchor X 为
20%–85%，每个正框右侧仍有 1–22 根真实 K；不是右边加白。正例实际位置占比
20.14% / 35.79% / 29.31% / 14.76%，最大偏差 0.79pp。时间切分 + 150 bars purge、
0 holdout、0 跨 split、4,756 image/label/manifest 守恒，十道 Stage A P0 门全绿。
同 seed 二次重建正/负 manifest SHA 分别稳定为 `ae4675a6…e89` / `0fdced5c…3f0`。

Owner 于 2026-08-11 06:33 CST 明确授权上传并开训；3060 `zzc@192.168.1.4` 上的
`owner_lsv2_stagea_randomcrop_v1_cold` 已正常完成。配置为 YOLO11s、imgsz 960、batch 8、
seed 0、epochs 60、patience 15；flip/mosaic/mixup/HSV 全为 0。early stopping 于 53 轮结束，
最佳 epoch=38，总耗时 4,207.2s；最终复验 P/R/mAP50/mAP50-95 =
0.2376 / 0.4330 / 0.2332 / 0.1266。远端与本地 `best.pt` SHA-256 均为
`c0e94f47…bf1a`，训练目录和日志已取回独立 Stage A 路径。

按推理前冻结的真实 K 线位置门，conf=0.05 四桶 recall 为
84.72% / 74.29% / 75.47% / 70.00%，最大差 14.72pp（门 20pp）；anchor X 与 IoU-matched
score 的 Spearman=-0.134（门 |rho|≤0.20），三门全绿，确认不再只认真实内容最右端。但同一
阈值 easy-negative fire=26.54%、event precision=15.23%，**安静度未解决**；conf=0.35 虽近乎
静默，recall 也只剩 0.28%，禁止靠沿用/抬高旧阈值宣布成功。

当前停止点：Stage A 仅通过“位置表征”诊断，可作为严格因果 Stage B 的初始化；仍标记
`production_eligible=false`，不得 promote/forward/ACTIVE/部署。下一步固定 Stage A 权重，
只做因果 Stage B 微调；随后才从新模型收集 hard negatives 并验证连续窗口密度。

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

**因果窗口里有信号但极弱。**（2026-08-30 撤回:此处原引用的 499/2「画在盘口」统计已删除,
该指标算的是框落在 200 根固定渲染窗内的位置,不是标注者是否使用了未来信息,结论不成立。）

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
