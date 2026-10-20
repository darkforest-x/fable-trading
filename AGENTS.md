# AGENTS.md — fable-trading 工作规范

一句话：两层架构验证"双均线密集启动"信号——YOLO 检测层（L1）+ LightGBM 判断层（L2）。

## 回测框架复用与 token 控制（2026-10-20）

- Owner 要求复用已给过的开源框架，减少每轮临时实现和重复核对。区间翻身的独立交易回测已接入本机隔离环境 **NautilusTrader 1.231.0**，固定入口：`scripts/backtest-range`，配置：`experiments/active/exp-range-nautilus-20261020-v1/config.json`。
- 该入口真实使用框架的 HEDGING 持仓、市场单、保护止损和成交事件；同币多笔多空独立，保留原费用和时间边界。支持当前已验收的收盘启动 ATR 跟踪、可选保本、已启动后 H1 反向退出，以及可选的首次 5m 收盘达标一次分批减仓；减仓本身不改变余仓止损，R 按原整仓风险、费用按成交份额计算。固定盘中 TP 已接入原生限价，OHLC 顺序 O-H-L-C，另报止损优先的同K双触敏感性；不与分批组合。1.231.0 多限价共享报价干扰已用独立限价 FillModel 修复，原生内核仍决定触发时间。分批研究见 `experiments/active/exp-eth-5m-partial-close-20261020-v1/`，六组均未改善全期净收益。止盈止损联合搜索见 `experiments/active/exp-eth-exit-search-20261020-v1/`：3557个不同组合，最高+441.97R但随机+470.46R且87.6%净收益来自边界结算，不提升默认参数。
- 后续同类研究优先改配置、复用输入/结果缓存，先读小摘要，需要解释具体差异时再读逐笔明细。已完成且输入、参数、代码和运行版本未变的结果不重跑；不能每次另造一个回测引擎或重复全量审计。交易语义变化时才扩展适配器并跑相关回归。
- 新参数研究用新输出目录，旧结果保留。现有 PEPE 候选入口是冻结账本的候选字段投影，换币/信号需提供相应因果候选，不能只改币名。主训练环境不装框架依赖，资金账户收益与独立 R 继续区分。

## UI 设计先走 Stitch（owner，2026-10-18）

- **Pine 指标例外（owner，2026-10-20）**：Pine/TradingView 指标修改不需要 Stitch，包括勾叉、射线、配色和交易参考框；直接实现并在 TradingView 验证。此例外持续有效。

- Stitch 生成、编辑及变体调用默认显式使用 `modelId="GEMINI_3_8_FLASH"`（owner，2026-10-19）；不使用未指定模型或 Balance 默认项。官方 MCP 当前已提供该模型枚举；若后续不可用，先说明，不静默换模型。

- 后续每次修改界面，先让 Google Stitch 产出设计图和前端代码，再由 Codex 接入本仓；包括页面布局、导航、卡片、配色、字体、间距和响应式调整。不得先改实现再补设计。
- 优先通过 Google 官方 Stitch MCP（`https://stitch.googleapis.com/mcp`）调用。先验证工具可用和账户授权；配置存在不等于已连通。未连接、额度不足或生成失败时，明确说明阻塞，不能静默跳过 Stitch，除非 owner 当次明确豁免。
- 向 Stitch 提供具体需求、现有设计约束和脱敏参考；实盘余额、持仓、成交记录、凭据及其他私密账户数据不作为设计素材上传。示例数字明确为占位数据。
- 同一产品优先复用既有 Stitch 项目与设计风格，保留本次使用的项目/页面标识、设计图和代码出处。仅保留实现所需素材，不额外生成报告。
- 已核实的默认设计项目为 [Spike Trading Research Platform](https://stitch.withgoogle.com/projects/8978791379095391159)（project ID `8978791379095391159`）；已有市场、信号中心、形态巡检、账户等页面，优先读取对应页面后迭代。设计规范页面 ID 为 `f98b0c7fc0ae4f9999ae9d078e649f48`。密钥只保存在本机 Codex 配置，不写入本仓、设计提示词或产物。
- Codex 负责把设计转成现有组件和样式，接真实只读数据、保留账户与指标语义，完成桌面/手机浏览器验收。Stitch 的示例数据、按钮和生成代码不构成交易逻辑或权限授权。

## 测试浏览器收尾清理（owner，2026-10-16）

- 测试优先复用当前任务的浏览器会话；记录本任务创建的会话/标签页及其归属，避免每次验收新开独立Chrome。
- 测试结束、失败或中断后的收尾必须关闭本任务创建的临时浏览器会话与标签页，脚本用`finally`/退出清理；关闭后核对进程已退出。除非Owner明确要求保留，不为截图或交付长期挂着测试浏览器。
- 只按已核实的会话名、临时profile及进程归属精确关闭；禁止使用`killall Chrome`、宽泛`pkill`或全局关闭命令。保留Owner日常浏览器及其他仍在工作的会话，后台巡检、行情和执行服务独立保留。
- 收尾核对后台服务心跳与巡检计划仍正常。不得用定时无差别杀浏览器代替每次测试结束时清理。

## Spike Notion 交易知识库（owner 固定口径，2026-09-13）

- 交易知识中枢为 [Spike · 交易系统与研究中枢](https://app.notion.com/p/3d98856479af80d9ad43f622dd7c3efd)。
- 后续出现可执行的交易假设、可复用的市场事实、研究结论、交易复盘经验或有价值的外部资料时，
  在对应 Notion 数据库创建或更新记录；先按唯一键查重，重大语义变化新建版本并关联旧版。
- 无法确认价值的内容保留为 Inbox、观察或待验证；不得因一笔盈利、外部观点或描述性统计自动标记为
  “已验证”或“实盘”。原始观察时间、失败实验、适用边界和来源链接必须保留。
- 实盘准入、策略 promote、仓位和真金操作仍遵守本文件的 owner 人工批准门；不得在 Notion 保存密钥、
  助记词、账户密码或通知凭据。
- 数据库字段、状态门槛和记录模板见中枢子页“Spike · 记录规范与模板”。

## 子代理模型铁律（owner，2026-09-23）

- **所有子代理统一使用 `gpt-6-luna`，推理档位沿用 `max`。** 无论主模型是什么，除非 owner 在当前任务明确另行指定，不得换用其他模型或默认继承主模型。
- 使用 collaboration 子代理时，必须显式指定 `model="gpt-6-luna"`、`reasoning_effort="max"`，并使用允许模型覆盖的 `fork_turns="none"` 或有限轮数。强制锁定其他模型的自定义角色不得使用，改用可显式指定模型的通用角色。
- 本条取代旧的 Sol 可见线程约定以及 GPT-5.6 Luna/Terra 分工；技能中的旧模型表和机制要求服从本条。
- 创建后核对工具返回的实际模型与推理档位；无法确认或模型不可用时，由主代理继续并如实说明，不得静默降级或换模型。
- 简单任务直接完成；委派须有独立价值、明确文件归属和验收证据，最多两个并发，不嵌套委派。主代理负责集成和最终核验。
- 本仓共享现有 `fable-trading` 工作目录，仍只用 `main`，不建 worktree、不切分支。

**当前阶段：P0（形态定义与重复标注稳定性）→ P1（Gold Dataset）。**
执行层代码在 `yoyo/layers/l4_execution/`，但 `models/active_bundle.json` 不存在，
`require_active_bundle()` fail-closed，**生产上跑着 0 个模型**。
P0/P1 通过前禁止新训练与 promote。

- **东西放哪 / 一轮怎么走** → `docs/PROJECT_CHARTER.md`
- **当前真相、在等什么、下一条允许的动作** → `HANDOFF.md` 顶部
- **阶段与门** → `ROADMAP.md`
- 各阶段结论 → `analysis/p*_report.md`（索引在 `analysis/INDEX.md`）

## 已迁移数据的发现与复用（owner，2026-10-16）

- 后续分析须主动复用已有数据与历史结果：先查 `experiments/registry.yaml`、`artifacts/registry.yaml`、相关实验配置和 `HANDOFF.md`，不要因普通文件搜索没有结果就重新下载、重新生成或认定数据丢失。
- Notion #52 已将冷数据迁至 `/Volumes/Elements SE/MacData/zhangzc/`，项目原路径保留软链接。分析继续使用原来的 `data/...`、`datasets/...`、`experiments/...` 等路径，系统会读取外盘文件；无需 Owner 每次提供新路径。网页数据集登记在 `data/crypto/_catalog/external_locations.json`，其他归档及单文件的精确来源/目标见 `data/_storage_migrations/*/` 下迁移计划、完成收据与 `summary.json`。
- `rg --files`、默认 `os.walk` 等不会递归进入目录软链接，不能据此判定数据不存在。先定位登记的原路径，再显式读取或在选定目录用 `rg --follow --files --hidden --no-ignore <原路径>` 查找；避免无目的地遍历整块外盘。
- 读取前确认 Elements SE 已挂载、原路径软链接目标存在；网页访问使用已登记的磁盘身份与路径准入。若外盘离线、改名或权限失效，明确说明暂不可读，保留链接与索引，不能创建同名空目录、清空登记或自动重建数据。
- 已迁移数据作为历史输入复用，新分析结果写入当前实验的本机目录，不覆盖历史结果。旧脚本若拒绝仓外真实路径或跳过软链接，应按已登记位置定向适配，不能声称所有历史脚本无需修改。
- Owner 明确要求本次迁移及后续可读性检查**不做内容哈希检验**；按需检查路径、大小、时间和真实读取即可。该口径覆盖下文历史迁移验收中的哈希要求。

## YOLO 训练教训必须延续（Owner 再强调，2026-09-21）

Owner 明确要求记住此前训练的坑。后续讨论合成数据、重建数据集或训练时，先逐项核对以下清单；不能因换模型、增加图片或指标变好就略过。

- **先统一标签语义**：明确检测的是收拢核心、启动还是事后确认；类别、方向、首末 K 线逐样本核对。重裁剪、统一移框、认可一个类别协议，都不等于完成逐样本金标确认；优先复用原始标注坐标。
- **看窗口右端，不能只看长度**：训练输入只能看到约定决策时点；未来确认图与训练图分目录。均线、归一化和纵轴也要受相同时间边界约束。过去缩窗后仍含未来的错误不得重演。
- **数独立事件，不能只数图片**：同一行情的多周期图、重叠窗、重裁剪和近重复必须保留事件谱系，不能分散到训练与验证两侧。按时间切分，并单独核对样本集合与 split；像素哈希相同不代表切分可复现。
- **训练画面要对应实际输入**：检查 K 线密度、均线计算/预热、颜色、分辨率、缩放及目标所在位置；不能靠固定框位置或另一套渲染风格取得表面成绩。fliplr/flipud/mosaic/mixup/hsv 继续全关。
- **反例和边界样本必须有依据**：补充相似但不符合形态的样本，不能只堆漂亮正例；未来亏损不自动意味着形态负例。不能为凑比例侵犯金标保护区，也不能把未确认镜像强行归为正或负。
- **识别验收与交易验收分开**：自家 val/mAP 不能替代真实 tip 金标与 tip-smoke；识别到形态不等于能赚钱。交易结论另按原成本、匹配随机对照及前向验证要求验收，保留失败结果。
- **可复现与单变量纪律继续有效**：先提交 builder，再生成；记录代码、参数、数据、标签、事件与切分版本。保持跨机依赖契约，不以连续换模型或打包调参代替查因；未经证据不能断言所有失败都只因数据集。
- **本轮合成图的新增边界**：20 张程序合成图只供确认样式，红圈是审核提示，不是模型输入或 YOLO 标签。均线从合成 OHLC 计算；理想正例与技术检查通过都不能证明真实泛化。后续拟训练须另做无提示输入、确认标签、补反例，并在独立真实行情上评估；当前资格仍为 false。见 `analysis/p0_ma_launch_synthetic_preview20_20260921.md`。

本次“记住历史教训”不改变 P0/P1、训练、promote 或实盘授权门槛。具体历史证据仍以下方规则及其链接的原始报告为准。

## 铁律（违反 = 返工，没有例外）

1. **（已取消）holdout 纪律**：2026-09-19 Owner 明令「彻底删掉这个规则」。所有历史数据
   （含 2026-05-04 之后）都可直接用于研究、调试、回测和画图，不需要批准、不记消耗次数，
   也**不要再拿 holdout 当不看数据的理由**。旧报告里的「holdout 消耗 N 次」只是当时的记录。
2. **时间切分**：所有评估按时间切分，禁止随机切分，禁止跨切点的样本进入训练。
3. **无前视**：特征只能使用信号 bar 及之前的数据；只有标签允许看未来。
   新增特征必须在 docstring 写明用到的列与窗口。
4. **单变量纪律**：一次实验只改一个变量；结果无论成败都如实汇报并保留必要实验记录，不要求另写报告。
   多变量打包改动需项目所有者批准并在 PROJECT_PLAN 记录（先例：2b-v2 三项打包，2026-07-07）。
5. **YOLO 增强禁用**：fliplr/flipud/mosaic/mixup/hsv 全关——它们破坏时间方向和红绿 K 线语义
   （旧项目 180 版失败的病因之一，见 README）。
6. **数据**：`data/` 不入 git；`data/kline_cache` 是旧项目缓存的只读软链接；
   新数据用 `python3 -m src.data.fetch_okx`（可断点续传，需本机网络）。
   **版本是契约不是依赖**：`torch`/`ultralytics`/`numpy` 三处必须同版本——
   Mac venv、3060、CI（`constraints-ci.txt`）。`scripts/train_on_3060.sh` 不一致就拒绝开训，
   理由是「结果无法与历史曲线对照」。装新库前先 `pip install --dry-run --report -` 看会不会降级。

## 实盘纪律（2026-07-20 起，与铁律同级）

7. **新鲜度三门同值**：执行器 max_signal_age_min / TG 过滤 / 看板 FRESH_DETECT_MIN
   当前 30min，由管道时序推导（15 bar + 7 脉冲/扫描 + 余量）；改动必须附延迟预算表
   且三处同改（见 `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`）。
8. **脉冲预算 <15min**：禁止往 forward 脉冲加扫描窗或新任务；阶段耗时看
   discover_wall / phase2_wall 日志，>600s 要查因。
9. **VPS 是唯一写者**：K 线与 forward_log.csv 只在 VPS 写；deploy 不推 data/kline_fetched。
10. **不自动 promote**：models/ACTIVE 与 frozen 默认配置的切换需 owner 点头；
    forward_log 不清空（清账 = owner 决策）。
11. **真金操作**（下单/撤单/kill 开关/改仓位/改 API key）只有 owner 亲手做或明确逐次授权。
12. **检测任务分流**（owner 2026-08-11 口径，覆盖 Local Signal V2 的 07-23 旧口径）：
    实盘执行路径**只扫 tip / tip-1 / tip-2 因果窗**；任何使用核心形态之后 K 线的模型
    都不得冒充新鲜盘口信号，不得直接进入 tip-smoke、forward、ACTIVE 或部署。
    若未来接入实盘，输出时间必须记为**完整检测窗右端**，并由 owner 另行批准延迟预算与执行架构。
    实盘检测器只认真 tip 金标 + tip-smoke，**自家 val / mAP / 旧 frozen-F1 不得作生产裁决**；
    无验证过的实盘检测器时管道诚实空转（`detector=none`）。

    - Local Signal V2 的**研究规格**（核心根数、确认窗、镜像与确认层级）→
      `docs/protocol/local_signal_v2.md`（owner 口径逐字保留）。本条只管纪律，那里管怎么做。
    - 「pre-v16 权重已三机清除」是**错的**，只清了两机；哪些权重还在见
      `analysis/p_model_inventory_20260820.md`，为什么记录不能当事实见
      `docs/learnings/purge-records-are-claims-not-facts.md`。
13. **单仓 + 单分支纪律**（owner 2026-07-30；单仓部分 2026-08-19 收敛后生效）：
    **fable-trading 是唯一 ACTIVE 交易研究仓**——`darkforest-one` / `yolo-xx` /
    `yoyo-trading` / `yoyo-eth` 已回迁并只读归档，**不得再开新仓**（`yoyo-eth-v2` /
    `yolo-new` / `fable-next` 一律不建）；新研究进 `experiments/active/<experiment_id>/`
    并注册到 `experiments/registry.yaml`。
    **只有 `main`，不开新分支、不建 worktree。**
    直接在 main 上提交、`git push origin HEAD:main`。**每次提交前先 `git branch --show-current`
    确认自己在 main**——曾有并行会话把 HEAD 切到别的分支，后续 6 个提交落在那里，
    只因用的是显式 `HEAD:main` 才没丢。需要隔离环境（并行 agent / 跨模型实现）时，
    先问 owner；owner 点头才开，且用完当轮就删。

14. **层间契约**（owner 2026-08-03 重构）：`yoyo/layers/` 的四层**禁止互相 import**，
    只能经 `yoyo/contracts/`（protocol / outcomes / costs / schema）和 `yoyo/data/`。
    由 `tests/boundaries/test_layer_imports.py` 用 AST 强制，不是口头约定。
    病因：2026-08-03 的 side/feature-semantics 故障横跨 forward_scan + frozen + executor
    三个文件——L2 的事实（模型用什么坐标系训的）被 L1 的事实（这单做多还是做空）决定了，
    而代码里没有任何东西反对。**旧 `src/` 是转发壳，迁移期并存；新代码一律写进 `yoyo/`。**

## 弱模型在本仓库最容易犯的错（每条都真实发生过或差点发生）

### 最贵的三条 —— 每一条都让这个项目损失过数月

读不完整份清单也要记住这三条。它们的共同点是：**把不是证据的东西当成了证据。**

- **把 AUC 当成功标准** → 本项目成功标准是 top-decile 扣 0.2% 往返成本后的净收益为正
  且置换检验 p<0.01；v1 的教训就是 AUC 0.59 照样亏钱。AUC 只是参考量。
- **报池子的绝对收益，不带对照组** → 2026-07-28：100×6m 池 +16.9bp 里 +7.2bp 是做空 beta，
  检测器自己只值 +9.0bp 而往返成本 10bp。见 `docs/learnings/pool-internal-metrics-cannot-see-beta.md`。
- **把窗口缩短当成因果化** → 决定看得见多少未来的是**窗口右端落在哪根**，不是窗口有多长。
  w20_midbox 从 200 根缩到 20–30 根，95.3% 的样本窗口右端仍晚于 decision bar（中位 9 根未来 K）。
  见 `docs/learnings/window-length-does-not-control-future-visibility.md`。

### 验收口径 —— 什么才算数

- **把 val/accept PF 当实盘** → 确认级只有前向新鲜 100 笔；v11 accept PF 高仍要前向终审。
- **只汇报好消息** → 汇报须说明证据局限和失败结果，不要求另建报告或固定章节；隐瞒失败的实验记录等于污染实验日志。
- **哈希对上就宣布数据集可复现** → 数据集有多个自由度：像素内容、split 落点、样本集合。
  w20_midbox 重建时 2635/2635 图片逐字节一致，**但 405 个样本的 split 落点全错**。
  见 `docs/learnings/reproducibility-is-per-axis-not-a-boolean.md`。
- **把"币波动高"和"这根波动在扩张"当成一个变量** → 这是两条方向相反的轴：币层波动水平
  在因果排名下是倒 U（榜单前 10% 超额胜率 t=1.32≈0），而 bar 层相对自身扩张 +9.21pp（p=5e-5）。
  用 bp 判永远判不清（TP/SL 精确 ±5/2 ATR），必须换 ATR 单位或胜率；见
  `docs/learnings/volatility-level-and-volatility-expansion-are-opposite-axes.md`。
- **按"当前涨跌幅榜单"挑币回测** → 排名窗必须在交易窗之前闭合。同窗排名下"高波动更赚"是单调的，
  换成上月排名单调性当场消失；见
  `docs/learnings/symbol-ranking-window-must-end-before-the-trading-window.md`。

### 标签与金标 —— 谁有资格说这是正例

- **把动态重裁剪当成重标注** → 动态短窗只能修复位置/上下文分布，不能把旧核心proposal变成
  新语义金标。先同时审类别与核心边界，再生成训练图；见
  `docs/learnings/dynamic-recrop-does-not-repair-label-semantics.md`。
- **有原始金标还让Codex二次手画** → 二次视觉重框会引入新的主观边界和宽度锚定。先逐框联结
  原Label Studio坐标与Owner方向裁决，外层只负责重裁，内层从原框中心派生；见
  `docs/learnings/original-gold-geometry-beats-secondary-manual-reboxing.md`。
- **为了正负1:1缩小金标禁入区** → 配对比例是软目标，Owner框保护、同币同时间块和时间隔离是
  硬约束；找不到安全背景就诚实缺样，禁止跨币、复用或靠近金标凑数；见
  `docs/learnings/negative-ratio-must-not-weaken-gold-exclusion.md`。
- **把一批偏右框统一左移** → 每张图的启动首根不同，统一delta只是换一个位置shortcut。先给K线
  编号，逐图把核心右端落在启动前一根，再重渲染新旧框对照；见
  `docs/learnings/per-image-reboxing-needs-indexed-boundaries-not-global-offsets.md`。
- **把人工审核的未来K线混进训练图** → Owner审核可以看更远未来，但训练短窗必须保持逐字节
  不变。审核未来单独目录/manifest，生成前后核对训练图SHA，未来目录禁止labels；见
  `docs/learnings/human-review-future-context-must-be-physically-separated-from-training-input.md`。
- **把未确认的方向镜像强塞进二分类** → 当前只确认空头参考时，多头镜像既不能混作同类正例，
  也不能当空头负例；先隔离为`mirror_unconfirmed`。见
  `docs/learnings/unconfirmed-mirror-is-neither-positive-nor-negative.md`。
- **把协议确认冒充逐样本确认** → Owner认可类别方向不等于确认扩展后的每张图/每个框；确认层级
  必须拆开记录。见`docs/learnings/protocol-confirmation-is-not-sample-confirmation.md`。

### 数据与复现

- **重跑 build_dataset 覆盖别的池的数据集** → 输出文件名必须带池名
  （`data/ma206/judgment_dataset_strict.csv` / `..._expanded.csv`），tag 必须带池名。
- **默认拉全部币种重新 fetch** → 先检查 `data/kline_fetched/` 已有 `okx_*_15m_*.csv`，
  fetcher 会自动跳过已完成币种。
- **默认「代码没变所以结果没变」** → 先比时间戳：产物 `generated_at` 早于
  `git log --diff-filter=A` 的 builder 首次入库时间 = 跑出它的代码不在 git 里，
  一切复现声明未经验证。**先提交 builder，再跑构建。**
  见 `docs/learnings/artifacts-built-before-their-builder-landed.md`。
- **改历史报告里的路径** → 禁止。`analysis/` 与 `docs/learnings/` 记录的是
  「当时发生了什么」，路径就是当时的真实路径。重构只改活文档，旧文档靠 `docs/RESTRUCTURE_MAP.md`
  查新旧对应。2026-08-03 一次改名正则把历史报告里真实跑过的命令改了，已回退。

### 运行安全 —— 碰到就可能动到真金

- **顺手调 strict/expanded 阈值预设** → 阈值是项目所有者决策，改动需批准。
- **改一道新鲜度门忘了另两道** → 三门必须同值，见实盘纪律 7。
- **往脉冲里塞实验扫描** → 超 15min 节拍 = 结构性挡 tip；见实盘纪律 8。
- **自动 promote / 清 forward_log** → 禁止；owner 点头。

### git 与环境

- **开新分支 / 建 worktree / 开新仓** → 默认禁止，**owner 点头才可以且用完当轮删**；见铁律 13。
  提交前先确认 `git branch --show-current` 是 main。
- **用 `git status --short` 验收 .gitignore 改动** → 它把未跟踪目录折叠成一行，
  9 万个文件缩成 14 行，泄漏根本看不见。一律用 `-uall` 展开，再加
  `git add --dry-run -A <dir> | wc -l` 看真实会 stage 多少。
  另：目录级排除（`datasets/`）之下所有 `!` 否定规则都是死的，git 不会下降进去；
  忽略规则按体积/扩展名写，别按目录名写。见
  `docs/learnings/directory-level-gitignore-kills-every-negation-below-it.md`。

## 质量标准（可检查，不是形容词）

**交付方式（owner，2026-09-23）**：已彻底取消每轮或每次任务必须生成报告的要求。
默认直接在对话中给结论、关键证据和必要限制；只有 owner 明确要求报告或指定文档产物时才生成。
不自动创建 Markdown、HTML、PDF 或网站报告，也不因技能的默认交接要求调用 `build-report`。
这是一项持续有效的明确豁免，无需每轮重新询问是否需要报告。实验代码、原始结果、复现信息及必要登记
按工作本身需要保留；不为满足交付形式额外写报告或转 HTML。

以下是研究证据标准，可直接在对话和已有实验结果中体现，不是必须创建文件或固定报告章节的清单：

- [ ] 复现命令（从零跑通的完整命令序列）
- [ ] 数据统计（候选数 / 正类率 / 时间范围 / val 样本数）
- [ ] 结果表，且与上一版本同表对照
- [ ] 必报指标：val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征基线对照
- [ ] **匹配随机对照组**（同币 × 同时间块 × 同波动桶的随机入场，同障碍同成本）——
      方向性策略的每张结果表都要带。置换检验只验排序，抓不到整池踩在 beta 上
- [ ] **非方向性实验**（标签质量、渲染 parity、数据审计等没有收益可言的）：
      上面两条按字面不适用。**不许编造，也不许留空**——写一句说明为什么不适用，
      并给出**同等严格的零假设对照**（例：标签质量审计把标签打乱重跑同一方法，
      看真实标签的疑似错标率是噪声的几分之一）。
      先例：`analysis/p1_gold_label_quality_20260820.md`
- [ ] 解读（每个数字变化的归因）
- [ ] 风险与诚实声明
- [ ] 下一步选项（标注哪些需要项目所有者决策）

代码标准：python3 + pandas/lightgbm/ultralytics，模块级 docstring 说明来源与决策依据
（现有代码都是这个风格，照着写）。

**依赖有两类，别混：**
- **契约**（`torch` / `ultralytics` / `numpy` / `pandas`）——Mac、3060、CI 三处同版本，
  改动 = 改跨机契约，改完历史曲线不再可比。锁在 `constraints-ci.txt`。
- **评估用**（cleanlab、aeon 之类）——**装进独立 venv**，见 `requirements-eval.txt`。
  cleanlab 会把 numpy 降到 1.26，直接装进主 venv 就打断了上面那条契约。
  装之前先 `pip install --dry-run --report -` 看会动哪些版本。

## 不确定时的升级规则

- 涉及 **阈值预设、障碍参数（TP/SL 倍数、atr 下限）、成本假设（0.2%）** 的任何
  改动 → 停下来问项目所有者，不要"先试试"。
- 涉及 **新鲜度门、脉冲预算、ACTIVE/frozen 切换、清空 forward_log、promote owner_best、
  真下单/改仓** → 同上，见实盘纪律 7–11。
- 数据源不可用或返回结构变化 → 如实报告现象，不要静默换数据源或造数据。
- 结果好得反常（AUC 突然 >0.7、净收益突然翻倍、accept PF 夸张）→ 第一假设是泄漏或 bug，
  写最小复现验证后再汇报；确认级只认前向新鲜样本。
- 项目所有者用中文交流，汇报用中文；代码与注释用英文。

## 收敛后的入口（2026-08-19）

- **注册表是入口**：实验进 `experiments/registry.yaml`，产物进 `artifacts/registry.yaml`。
  `production_eligible` / `training_eligible` 默认 false，改动需 owner。
- **守门测试别绕**（`tests/boundaries/` + `tests/causality/` + `tests/parity/`）：
  `yoyo` 必须解析在本仓内、sys.path 不许出现兄弟仓、holdout 定义不许漂移、
  迁移资产逐个重算哈希、CI 必须从 `requirements.txt` 装且锁版本。
  **红了先看它在说什么，别先想怎么让它绿**——每一条都是有人踩过的坑。
- **两个 ATR 实现不一致**（warmup 播种，bar 14 差 0.109），已钉住未修，
  等 owner 裁决：`docs/consolidation/DUPLICATE_SEMANTICS.md` §4。
- 四仓历史结论（含全部负面结果）在 `experiments/historical/`，
  验收在 `reports/consolidation/FINAL_ACCEPTANCE.md`。

## SPIKE Pine version delivery (owner, 2026-09-20)

- Every delivered change increments the minor version (V12 → V12.1 → V12.2).
- Preserve the prior version's source file. Create a new versioned Pine file and a new private TradingView script; do not overwrite the previous script.
- Trendline display must retain historical regions. Reduce local overlap and fade historical lines; do not substitute an unrequested chart-wide three-line limit (owner correction, 2026-09-20).

## learning law

每解决一个非平凡问题（修 bug、架构决策、反直觉结论），先运行 extract-approach skill
在 `docs/learnings/` 留下笔记再继续。没有 learnings 笔记的解决方案视为未完成的工作。
