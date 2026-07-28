# 附录 C:经验笔记

`docs/learnings/` 共 124 篇。每篇提取标题、问题、通用规则。

### 3060 局域网 IP 会从 .5 漂走；ping 不通先扫同网段

`3060-lan-ip-can-drift-from-dot5.md`


### ATR 等比障碍 + 固定成本 = 假的"高波动 alpha";判据是毛 PF,不是净收益

`atr-scaled-barriers-vs-fixed-cost-fake-an-edge.md`


### 训中调试别往训练 .venv 塞 CV 工具

`avoid-pip-into-busy-train-venv.md`


### 后台标签页会暂停 ResizeObserver 和 rAF——图表初始化要能自愈

`background-tab-rendering-suspension.md`


### 框右缘映射的是启动 bar，不是 tip——几何对、语义错

`box-right-edge-maps-launch-bar-not-tip.md`


### 框右缘分数不能当「是不是 tip」的判决

`box-right-frac-is-not-a-tip-intent-verdict.md`


### tip 上因果择向（排列/突破/散开）救不出 ≥1.3 的边

`causal-direction-select-does-not-rescue-pf-past-1.3.md`


### 链路失败主因是 regime + 入场错位，不是出场或打分

`chain-failure-is-regime-plus-entry-mismatch.md`


### 全图缩放下 canvas 密集框要有最小可视尺寸

`chart-overlay-boxes-need-min-size-at-full-zoom.md`


### 图表标注层不要驱动主 K 线缩放

`chart-overlays-must-not-drive-candle-autoscale.md`


### CI gates should stay on lightweight contracts

`ci-gates-should-stay-on-lightweight-contracts.md`


### 切后文必须左补满 200，禁止短窗拉伸

`crop-after-box-must-left-pad-to-200.md`


### 跨宇宙复测要先路由成本口径

`cross-universe-retests-need-cost-routing.md`


### Cursor agent shell 会杀后台 uvicorn

`cursor-agent-shell-kills-background-servers.md`


### Dashboard Universe Switches Must Filter Before Scoring

`dashboard-universe-switches-filter-before-scoring.md`


### 看板可视化：加深 LWC，不要换主图库

`dashboard-viz-deepen-lwc-not-replace.md`


### 数据审计：偶发 tip 尖刺 ≠ 黑名单

`data-audit-spikes-are-not-blacklist.md`


### 双均线密集启动:动作真、方向不可交易 —— 三个结构性杠杆系统性穷尽

`dense-cluster-has-no-causally-tradeable-direction-edge.md`


### 检测器切流后的"事后行"多是补账，不能用 detected_at 评判新检测器

`detector-cutover-verdicts-need-signal-time-not-detected-time.md`


### Detector lag was model-side; a conf number without box position proves nothing

`detector-lag-is-model-side-check-box-position-not-just-conf.md`


### 诊断实盘延迟要走实盘那条代码路径,离线统计会给出反向结论

`diagnose-live-lag-on-the-live-code-path.md`

- **问题**:前向日志 lag 中位 542 分钟 / 门 30 分钟 → 100% 拒单。 我先后给出**两个错误病因**,都被后续测量推翻: 1. "管道太慢" → 实测全宇宙 344 币扫描 **2.8 分钟**,算力从来不是瓶颈; 2. "v11 检测器把框画在 200 根窗口中部(中位 33 根)" → 这个统计取自 `mode="full"` 的离线扫描,而 live 走的是 `mode="live"`,**tip-edge 门 本来就把框卡在盘口**。把 v11 放进 live 路径重测:框距盘口 p50=**1 根**, 和 v9 一模一样。
- **规则**:任何"实盘表现"归因,必须在实盘那条 code path 上复现 (同 mode、同门控、同调度),并且把观测量拆成它混合的各个时间基准。 一个跨越数天的中位数,先看它在时间上是否稳定——尾部自愈的指标说明 病因已消失,继续修它是在修历史。

### torch+lightgbm 同进程加载两份 libomp，OMP 多线程时启动即段错误

`duplicate-libomp-segfault-needs-omp-threads-1.md`


### 单笔 edge 与成本同数量级时，回测结论是成本假设的函数

`edge-vs-cost-tolerance.md`


### 入场 close vs next_open 几乎同 PF

`entry-close-vs-next-open-almost-same-pf.md`


### 执行器每条腿都是市价单 → maker 成本口径不可达;裁决成本必须按真实路由

`executor-places-market-on-every-leg-so-maker-cost-is-unreachable.md`


### 回测/前向出场等价性靠调用方显式传参维持，不靠共享常量

`exit-parity-holds-only-via-explicit-call-args.md`


### Exit variants must match current universe costs

`exit-variants-must-match-current-universe-costs.md`


### 同池上 binary 扩币/top-K 死、回归仍可出边

`expanding-liquid-universe-can-erase-few-coin-short-edge.md`


### 外源 chart-YOLO 印证的是右缘锚定协议，不是公开权重

`external-chart-yolo-validates-tip-anchor-not-weights.md`


### tip 公平验收要分母拆开 + full-MA，不能靠 slice tip_hit 翻案

`fair-tip-eval-needs-split-denominator-and-full-ma.md`


### 固定障碍在空边砍趋势；无 TP / 跟踪才过 1.3

`fixed-tp-cuts-short-trend-edge.md`


### Forward logs need stable signal keys

`forward-logs-need-stable-signal-keys.md`


### 新鲜度阈值不是拍出来的,是从管道时序算出来的

`freshness-gates-must-be-derived-from-pipeline-arithmetic.md`

- **问题**:实盘要"实时检出",但三处新鲜度门(执行器/TG/裁决)被先后设成 55 → 20 分钟,两个值都错——55 放进了非 tip 的迟到检出,20 则结构性挡死一切。
- **规则**:任何"年龄/新鲜度/超时"阈值,先列出它守门的事件链每一环的 最坏耗时,阈值必须 > 链路最坏和,否则就是隐形 kill switch。改阈值的 PR 必须附这张预算表。

### 前端采用 Hummingbot Dashboard 壳 + 策略页节奏

`frontend-hummingbot-shell-adopted.md`


### Frozen artifacts must own cache identity

`frozen-artifacts-must-own-cache-identity.md`


### 冻结视觉 embed 预检红灯则不要开双检测器训

`frozen-visual-embed-red-means-no-dual-detector-train.md`


### full 模式的因果性靠检测器"学会贴右边",不是靠代码门 —— 换个检测器就会静默漏未来

`full-mode-causality-is-behavioral-not-structural.md`


### Funding Cost Reruns Need Input Snapshots

`funding-cost-reruns-need-input-snapshot.md`


### 过滤队列 + 标完出队时，「上一张」必须靠 visit trail

`gallery-prev-needs-visit-trail-under-unlabeled-filter.md`


### Generated audit grids need fluid minmax

`generated-audit-grids-need-fluid-minmax.md`


### GitHub chart-YOLO 搜不到 tip 几何解药

`github-chart-yolo-rarely-solves-tip-geometry.md`


### 金标准测的是"你给的判据"，不是你以为的概念

`golden-set-measures-what-you-instruct.md`


### 规划"修复"之前先读代码——你要修的东西可能已经存在

`grep-before-planning-fixes.md`


### 高 AUC 不能替代交易经济性验收

`high-auc-can-still-lose-economics.md`


### 事后训练的判断层，在盘口是反预测的（分越高越亏）

`hindsight-trained-judgment-is-anti-predictive-at-the-tip.md`


### Holdout PF 塌到 ~1.0 不等于测量 bug

`holdout-pf-collapse-is-not-automatically-a-measurement-bug.md`


### isotonic 校准做仓位映射会把排序分压成台阶，阈值附近的交易被静默弃单

`isotonic-sizing-collapses-rank-scores-to-steps.md`


### 打标包去重不能只靠 window stride

`label-packs-need-base-coin-caps-not-only-window-stride.md`


### 标签 pad 收紧 ≠ 解决「长密集段」

`label-pad-is-not-segment-length.md`


### 扩 short 宇宙可抬厚 top-n 净，却稀释排序（ρ 塌）

`larger-short-universe-can-dilute-rank-while-keeping-thin-net.md`


### macOS venv 里先 import lightgbm 再跑 ultralytics predict 会段错误（exit 139）

`lightgbm-import-before-ultralytics-predict-segfaults.md`


### Lightweight Charts in CSS grid needs min-width zero

`lightweight-charts-grid-children-need-min-width-zero.md`


### 长密集段要用收核，不是加 pad

`long-dense-runs-need-core-trim.md`


### 多空混池 PF 不是「方向规则错了」——是测量没分边

`long-short-must-be-split-in-base-rate-tables.md`


### MAD-on 复验仍过不了 tip-smoke

`mad-on-pad200-still-fails-tip-smoke.md`


### 我从 smoke 计数推出的"平移害了 tip 对齐",一测就崩;而且那两个计数本来就不该比

`measure-placement-directly-instead-of-inferring-it-from-smoke-counts.md`


### 机械「启动」入场抬 PF，但抬不过可交易线

`mechanical-launch-entry-lifts-pf-but-not-past-1.3.md`


### 中段金标右对齐 ≠ 可标的 tip 密集框

`mid-gold-right-align-is-not-labelable-tip.md`


### 参数扫到边界还在变好,就说明扫的不是那个参数

`monotone-sweep-to-the-edge-needs-a-control-arm.md`

- **问题**:给 TP5×ATR/SL2×ATR 的止损加"最小距离下限",净收益随下限单调上升—— 0.4% 时 +0.0314%,一路到 4.0% 时 +0.1336%,**扫到扫描区间尽头都没有拐点**。 第一版脚本的判读模板还写着"TP率没塌 = 没砍长尾",直接把它报成了改进。
- **规则**:任何单调到扫描边界的 sweep,**先加对照组再解释**,不要加宽区间。 对照组 = 把该参数推到 0 或 ∞ 的退化情形。如果对照组和"最优参数"结果接近, 那么这个参数从来不是在被优化,只是在被移除。 推论:判读文案不许用固定模板断言"没砍长尾"这类事实—— 必须由数据算出来(TP 率相对基线的比值),否则模板会替你撒谎。

### 多日无人值守要预留磁盘余量

`multi-day-disk-headroom.md`


### `nice -n 19` 不隔离 GPU —— 保护训练只能靠挂起或杀

`nice-does-not-isolate-gpu-contention.md`


### Offline Watchers Need Log Markers

`offline-watchers-need-log-markers.md`


### OKX 公共 API：403 是 WAF 挑 UA，慢是延迟不是限速

`okx-fetch-waf-and-latency.md`


### OKX SWAP 成交额必须用 quote 口径，不能直接用 volCcy24h

`okx-swap-volume-must-be-quote-not-base.md`


### Owner 对齐抬召回不抬 Jaccard，因果边仍死

`owner-align-raises-recall-not-jaccard-edge-stays-dead.md`


### owner 眼中的"密集"与机械定义**反相关**——三个代理全部失败

`owner-eye-is-anticorrelated-with-the-mechanical-dense-definition.md`


### Owner 标框的 oracle 增量不是盘口 tip 的因果 alpha

`owner-label-oracle-alpha-is-not-causal-tip-alpha.md`


### 按 owner 自己的定义把检测器做对了,回测仍然是硬币 —— 边不在形态里

`owner-own-pattern-detected-correctly-still-has-no-edge.md`


### 分边标框抬高的是 oracle，不是可部署因果规则

`owner-side-split-does-not-unlock-deployable-rule.md`


### Owner 子集上的 tip 前移不是可部署边

`owner-subset-tip-remap-is-not-deployable-edge.md`


### pad200 空标背景 ≠ 中段硬负样本

`pad200-empty-bg-is-not-mid-hardneg.md`


### pad200 MAD bulk 在 16GB 上要 resume+watchdog

`pad200-mad-bulk-needs-resume-watchdog-on-16gb.md`


### pad200 bulk 关 MAD 会把 okx_ start 窗切成 end_incl

`pad200-mad-gate-off-corrupts-okx-start-stems.md`


### pad200「修过又复发」：preview 修好 ≠ bulk 默认安全

`pad200-preview-fix-bulk-mad-off-regressed.md`


### pad200 训图能开火≠盘口 tip

`pad200-train-fire-not-live-tip.md`


### pandas datetime64 的时间单位不可移植——epoch 换算别用 astype(int64)

`pandas-datetime64-unit-portability.md`


### pandas rolling.skew 在「未来突变」测试下会数值漂移

`pandas-rolling-skew-not-bitwise-causal.md`


### 正负样本必须出自同一条渲染管线,否则模型学风格不学内容

`pos-neg-must-share-one-render-pipeline.md`

- **问题**:v14/v15(pad200 系)自家 val mAP 0.72,但真实盘口 tip 上密集全漏 (0/6)、空背景乱开火(58%)——训练看似成功,部署行为完全错乱。
- **规则**:构造检测/分类数据集时,正负样本必须经过**逐字节相同的生成 管线**,唯一允许的差异是标签本身;改造正样本(重渲/裁剪/对齐)时,负样本 必须做同样的改造。验收集要能捕获这种捷径:用与部署同管线的真实样本, 同时测"应开火命中"和"应沉默误火"两个方向。

### 预 holdout 短窗 + 少币 chunk resume 才能分钟级出 SHORT 首表

`pre-holdout-short-window-few-symbols-beats-full-universe-scan.md`


### 旧 pretip 窗上的框不能当 live tip 监督

`pretip-window-boxes-are-not-live-tip-supervision.md`


### Python 默认参数在 def 时绑定——猴子补丁模块常量对已定义函数无效

`python-default-args-bind-at-def.md`


### 真实 tip 金标要 Owner 审，不能靠 pad200

`real-tip-gold-needs-owner-review-not-pad200.md`


### 「实时 YOLO」优先治 tip 出生率，不是换 TensorRT/DeepStream

`realtime-yolo-needs-tip-birth-not-tensorrt.md`


### 回归 realized_ret 比二分类 label 更贴 top-decile 净收益

`regression-target-beats-binary-label-for-rank-pnl.md`


### 视觉模型的标签语义必须在渲染层保持不变量

`render-semantics-must-be-invariant.md`


### 扩 OHLCV 因子救不出分边可部署因果规则

`rich-ohlcv-features-do-not-rescue-side-causal-pf.md`


### SAHI Needs A Direct Baseline

`sahi-needs-direct-baseline.md`


### 样本量决定"模型是否比单特征强"——池子小就别怪模型

`sample-size-gates-model-value.md`


### 规则入池后，与门槛共线的因子 IC 会塌缩

`selection-conditioned-ic-collapse.md`


### Shadow exit logs should share mainline entries, not a half-frozen booster

`shadow-exits-reuse-mainline-entries.md`


### short 判断层必须回归同构，不能 binary 小样本拧镜像

`short-judgment-must-match-v11-regression-not-binary-mirror.md`


### short 判断主路径必须做方向特征对齐，不能只换标签

`short-judgment-needs-directional-feature-align-on-main-path.md`


### Short mirrors need directional feature semantics

`short-mirrors-need-directional-feature-semantics.md`


### 只做空链路：池文件名必须带 side，规则池与 YOLO 池分流

`short-only-pipeline-keeps-pools-side-tagged.md`


### 短检测器 18% 精度的根因:训练目标既"晚了 10 根"又"用了过严的 full_spread"

`short-tip-dataset-target-was-late-and-too-strict.md`


### 空边趋势出场：月度过线仍可能季度集中

`short-trend-monthly-pass-can-still-be-quarter-concentrated.md`


### short YOLO 6m 扫用 resume+chunk，别绑 Cursor 会话

`short-yolo-6m-resume-beats-cursor-kill.md`


### 稀疏化能抬 4 月 train PF，靠的是脆弱搬家而不是边增量

`sparsity-fixes-apr-by-relocating-fragility-not-edge.md`


### 看板顶栏 15/100 与 forward 页 0/100 不一致：status-strip 不做新鲜度过滤

`status-strip-decision-counter-skips-freshness-gate.md`


### dense_owner stem 数字是窗末 bar，不是窗起点

`stem-index-is-window-end-not-start.md`


### 结构出场（收盘破 EMA21）可在 val 上显著短持仓并抬 top 净

`structure-exit-can-beat-fixed-barriers.md`


### 仓位乘数上实盘时：减半槽预算，而不是提杠杆

`tier-live-deploy-halves-slot-budget-not-leverage.md`


### 仓位乘数上线前必须先算 max_mult×基础仓位的保证金 ≤ 权益，否则高档位=必拒单丢单

`tier-multiplier-needs-margin-headroom-in-base-notional.md`


### 多时间框架台架要先统一 bar 时钟

`timeframe-generalization-needs-single-bar-clock.md`


### Timeframe sweeps must separate frequency from quality

`timeframe-sweeps-must-separate-frequency-from-quality.md`


### tip 对齐短金标过 tip-smoke；pad200 长链未过

`tip-aligned-short-beats-pad200-on-tip-smoke.md`


### tip 调度/阈值证伪后，下一刀必须是训练分布

`tip-birth-needs-train-distribution-not-schedule.md`


### tip 窗像素（冻结 COCO embed）也不携带可交易方向边

`tip-chart-pixels-do-not-carry-direction-edge.md`


### tip 崩盘先查几何审计再怪标签坏

`tip-collapse-audit-labels-before-retrain.md`


### tip-only 调度救不了 tip 出生率——先采真实 tip 成败图

`tip-only-scan-does-not-raise-tip-birth-rate.md`


### tip 重裁 + 时间切分才是 short 金标的最低可训条件

`tip-recrop-plus-time-split-for-owner-short-gold.md`


### 16GB 上 YOLO tip 重渲必须 batch=1 + 分片进程 + 每币落盘

`tip-rerender-needs-batch1-chunked-checkpoint.md`


### 盘口信号先入账后回填,而不是等字段齐了才入账

`tip-rows-record-first-backfill-entry-later.md`

- **问题**:前向账本的行 schema 要求入场价/maker 判定,而这些字段来自信号 bar 的**下一根** bar——盘口信号天然缺字段,旧代码的选择是直接丢弃(`entry_i >= len` → None),每笔实盘信号白白损失 15~22 分钟先机。
- **规则**:流式系统里"字段还没发生"的行,先用已知值+带语义的代理+一个 天然未知字段作哨兵落盘,由幂等 merge 负责收敛到真值;不要为"暂缺"发明新 状态机。回填规则必须写明哪些字段永不覆盖(如 detected_at)。

### Train 月度过线 ≠ holdout 可交易边

`train-monthly-pf-does-not-imply-holdout-edge.md`


### ultralytics 的 optimizer='auto' 会在 epoch 3 炸掉续训模型

`ultralytics-auto-lr-destroys-finetune.md`


### 打通标签到交易必须先冻结因果触发协议

`unlock-chain-needs-causal-trigger-protocol-first.md`


### 「能用」过滤器按整仓，不是 tip 唯一

`useful-filter-is-whole-repo-not-tip-only.md`


### "有信号没利润"的病根在标签经济学，不在模型

`v1-death-was-label-economics.md`


### v13 val mAP 崩 ≠ tip 裁决；tip-smoke 才是

`v13-val-map-is-not-tip-verdict.md`


### Windows 3060 是精简 GPU 箱，开训用 train_dense.py 不是 src.detection.train

`windows-3060-is-train-dense-not-full-repo.md`


### YOLO E2.1 训练中期 mAP 振荡

`yolo-e21-train-instability.md`


### YOLO 重训走局域网 SSH 到 3060，不是 U 盘拷

`yolo-train-ships-over-ssh-to-3060-not-usb.md`


### YOLO 训练加速：动 I/O 与 early-stop，不动 imgsz/增强

`yolo-train-speed-without-accuracy-hit.md`


### YOLO epoch-end val 可在 ap_per_class 把 16GB 打爆

`yolo-val-ap-per-class-oom-on-16gb.md`


