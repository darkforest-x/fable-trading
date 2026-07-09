# NEXT_STEPS — 完整工程计划（2026-07-09 起，写给 Codex / 任何接手的 agent）

## 工作方式（Codex 必读）

- **工作目录**：`~/fable-trading-codex`（独立 worktree，分支 `codex/day1`）。
  **禁止**在 `~/fable-trading`（main 所在目录）做任何修改；
- 数据/模型/虚拟环境是软链共享的（data、datasets、runs、.venv、logs、models）——
  可读可跑，别删；`.venv/bin/python` 用于 YOLO，其余用系统 python3；
- 每完成一项：`git commit` + `git push -u origin codex/day1`；owner 在 GitHub 上看 diff 后合并；
- **别杀后台进程**（yolo11s 训练、offline_pipeline）；别占 8642 端口（起看板用 `--port 8643`）。

## 已由前一会话完成、不要重做的事（07-09 凌晨）

- ✅ P0-2 合约复制检验：**通过**（TP5/SL2 纯 SWAP 池净@maker +0.225%/笔，p=0.001），
  主线宇宙已切 SWAP（HANDOFF 已更新），p2b_v3 报告已补"合约宇宙复制"节；
- ✅ H1/H2 出场变体已跑（H1 分批止盈：AUC 0.608/胜率 65%/净 +0.135%，头号挑战者）、
  H9 趋势过滤已验证（+0.05%/笔）；1H/30m/5m 多时间框架数据、54 币资金费率已到位；
- ⏳ yolo11s 训练中，跑完管道自动评估并写 OFFLINE_RESULTS.md——P0-1 依据它判定。


先读 `AGENTS.md`（纪律，违反=返工），再读 `HANDOFF.md`（状态）。按优先级顺序执行；
P0 全部完成前不开 P1。每完成一项：commit + push + 在本文件划掉该项。

**环境须知（踩过的坑）：**
- YOLO 相关必须用 `.venv/bin/python`（torch 只在这）；其余用系统 `python3`；
- datetime→epoch 用 Timedelta 除法，禁 `astype(int64)//1e9`（差 1000 倍的坑，见 docs/learnings/）；
- OKX 请求带浏览器 UA（fetch_okx.py 已封装），全局 ≤8 req/s；
- 提交信息英文、汇报中文；看板改动后 `bash scripts/deploy_vps.sh` 同步 VPS。

**红线（每个 P 级都适用）：** 禁评估 holdout；禁对 2026-05-04 后窗口调参（已消耗两次）；
禁重构现有模块/升级依赖/动 .venv/动 scheduled task；坏结果如实入报告。

---

## P0 —— 明天必做（依赖今晚离线管道的产出）

### 0. 验收离线管道产出（5 分钟）
`cat OFFLINE_RESULTS.md`；没有就 `tail -50 logs/offline_run.log` 看死在哪个阶段，
手动补跑该阶段（脚本内 5 个阶段命令均独立可执行）。

### 1. YOLO 全量训练验收判定
- mAP50 ≥ 0.90 → 写 `src/detection/consistency_check.py`：val split 每张图，
  auto_label 规则框为真值，best.pt 预测（conf=0.30）IoU≥0.5 匹配；输出一致率
  （匹配/规则框数）与误报率。一致率 ≥95% → p2a 报告追加"正式验收通过"节；
- mAP50 < 0.90（含 yolo11s）→ p2a 报告如实记录封顶值，标注"验收未达成、
  非关键路径、暂停"。禁止调 conf/IoU/增强凑数。

### 2. ~~合约复制性检验判读~~（✅ 已通过，报告已补写）
- **成立** = tp5_sl2 合约 val perm_p < 0.01 且 top-decile 净@maker0.06% > 0；
- 成立 → p2b_v3 报告追加"合约宇宙复制"节，HANDOFF 主线宇宙改为 SWAP；
- 不成立 → 停，报告如实记录，HANDOFF 标"复制失败待 owner"，禁止救数字。

### 3. ~~均线 20/60/120 对比实验~~（✅ 已完成；owner 2026-07-09 决策：主线继续 8-55）
现行判断层用 EMA 8/13/21/34/55+144/200；owner 心中策略是 SMA/EMA 20/60/120 六线。
**只做加法**：
1. 新建 `src/judgment/candidates_v206.py`：SMA20/60/120 + EMA20/60/120；
   密集规则从 `src/detection/auto_label.py`（它本来就是 20/60/120 的，已被 YOLO
   验证可学）起步：fast_spread（20/60 四线）≤0.0028×1.6、full_spread（六线）
   ≤0.0055×1.6、连续 ≥5 根；volume/pre_range 等门槛从 candidates.py 原样复用；
2. 特征同构迁移：均线类特征改基于六线算，非均线类原样复用；
3. 标签 TP5/SL2 h72；宇宙用 SWAP（若第 2 步成立）；train.py 流程不动，val only；
4. 交付 `analysis/p2b_ma206_comparison.md`：8-55 版 vs 20/60/120 版同表对比
   （候选数/AUC/p/毛利/净收益），结论给 owner 裁决，**禁止自行替换主线**。

**重要认知（写给 owner 也写给执行者）**：2a 的 YOLO 本来就是在 SMA/EMA 20/60/120
渲染图上训练的——如果 20/60/120 判断层胜出，检测层和判断层将第一次真正对齐，
**无需重训 YOLO**；如果 8-55 保持主线，才存在"要不要为 8-55 训练一个新 YOLO"
的问题（列在 P2）。

### 4. 前端 bug 修复（owner 2026-07-09 截图实证，修复必须真浏览器验证）

**BUG-1（主）：信号页切换成交单后 K 线消失。**
复现：信号浏览 → BNB_USDT → "1 个月" → 连续点击右侧成交列表里不同的单
（尤其入场价差异大的，如 07-01 @552 和 06-21 @588 交替点）→ K 线区域变空白，
价格轴范围与可见时间窗的真实价格不匹配（实录：轴显示 548-564，而该时段真实
价格 583-603，蜡烛全部被推到可视区外；成交量副图正常）。
排查线索（按嫌疑排序）：
1. 右侧价格轴的 autoScale 被关闭或被残留元素钉住——检查连续 focus 后
   `priceScale('right')` 的 autoScale 状态；试 `applyOptions({autoScale:true})` 恢复；
2. `pathSeries`（在 right scale 上）持有上一笔的两个点，若其时间在当前可视窗外，
   LWC 自动缩放的取值集合可能异常——试改 pathSeries 到独立 overlay scale 或
   每次 focus 先 `setData([])` 再设新值；
3. `subscribeSizeChange` 重放 `lastFocusRange` 与快速连点的竞态。
验收标准：任意顺序连点 20 次成交单 + 切换 4 档 K 线范围 + 切换 3 个币种，
蜡烛始终可见、价格轴始终匹配可视区间；**localhost 与 VPS（真实 Chrome）双端验证**。

**BUG-2：均线密集色带只渲染半高悬浮块**（应为全高背景带）。
修法：给 bandSeries 设 `autoscaleInfoProvider: () => ({priceRange: {minValue: 0, maxValue: 1}})`
配合现有 scaleMargins {top:0,bottom:0}，令 value=1 的两点撑满全高。

**BUG-3：出场价与止损线重叠时标签互相遮挡**（sl 出场时两线同价）。
修法：outcome 为 sl/sl_ambiguous 时不再单独画止损障碍线（出场线已表达）；
tp 出场同理去掉止盈目标线的重复。

修复过程遵守：只做外科手术式修改，禁止重构 app.js；每个 bug 单独 commit。

### 5. P0 收尾
更新 HANDOFF（划掉完成项）；本文件同步；全部 push。

---

## P1 —— 本周内（前向验证基础设施 + 数据补强，最终裁决的地基）

### 5. 冻结模型工件（前向验证的前提）
现在每次启动都重训模型——前向验证必须用**冻结的字节**：
- 新建 `scripts/freeze_model.py`：用胜出配置（默认 tp5_sl2 SWAP；等第 2/3 步结论）
  训练一次，`booster.save_model("models/frozen_<config>_<date>.txt")` + 同名 json
  存阈值（val q90）、特征列表、数据指纹；`models/` 入 git；
- 看板与前向跟踪一律加载冻结工件，不再重训（server.py 的 build_signals 路径
  加"若存在冻结工件则加载"分支）。

### 6. 前向跟踪器（paper-trading 记录仪）
- 新建 `scripts/forward_track.py`：读最新数据 → 扫描冻结配置的新候选 →
  冻结模型打分 → 阈值以上记为信号 → 结果已知的（barrier 已触发）补记出场，
  追加写 `data/forward_log.csv`（append-only，含 maker_filled 判定）；
- 幂等：重复运行不重复记录（按 signal_time+symbol 去重）；
- 每日运行：**需 owner 点头**后把它加进每日定时任务（在现有 8 点任务的 prompt 里
  追加一步，或 owner 自己跑 `python3 scripts/forward_track.py`）；
- 3-4 周后累计 ≥100 笔时，用该日志算前向 PF——这是 v3 配置的最终裁决。

### 7. 资金费率历史（合约回测的成本精度）
- 新建 `src/data/fetch_funding.py`：OKX `/api/v5/public/funding-rate-history`
  拉全部 SWAP 币种 400 天资金费率 → `data/funding/`（复用 UA/限速）；
- 合约回测把"资金费近似 0.02%"替换为按持仓时段的真实费率累计；
- 重跑 swap_replication 报告差异（预期影响 <2bp/笔，但要有真数）。

### 8. 看板完善·第一批（依赖 5/6/7）
- **前向验证页（新 tab）**：读 forward_log.csv，展示前向净值曲线、累计笔数/PF/
  胜率、距"100 笔裁决线"的进度条——这是 owner 每天最想看的一页；
- **宇宙切换**：总览/回测/信号页支持 现货/合约 数据集切换（后端加 dataset 参数，
  分数缓存按宇宙分文件）；
- 总览页数字改为全部动态读 analysis/output/*.json（现有部分硬编码）；
- 部署 VPS 并用真实浏览器验证（教训：本地过≠VPS 过）。

---

## P1.5 —— 研究议程执行（与 P1 并行，Codex 可做大部分）

> 总纲：`docs/RESEARCH_AGENDA.md`（假设发生器 + 两级验证制度）。按其"优先队列"执行；
> 每个假设一个独立 commit + 报告小节；负结果同样入库并更新议程状态。

### R0. 工程前置：sweep 台架泛化（先做，其余依赖它）
- `fetch_okx.py`/`update_okx.py`：`--bar {5m,15m,30m,1H}` 参数化（API 原生支持），
  文件名带 bar（loader 正则已兼容 5m/15m，补 30m/1H）；
- `barrier_sweep.py`：出场函数插件化（dict 注册：fixed/trailing/scaled/breakeven/ma-exit），
  labeling.py 只加新函数不改旧的；
- `build`/`train` 路径支持 bar 参数（purge 随 horizon×bar 自动换算）。

### R1'. H9 复测与推广（发现级已通过，见 scripts/h9_trend_filter.py 与议程记录）
Claude 已验证："1h EMA144 上方"过滤使净@maker +0.152%→+0.203%（1h 从 15m 聚合，
无前视处理见脚本 docstring——复用它，别重写）。Codex 接力三件事：
1. 把该过滤器接入 maker_val_sim 组合模拟，看 PF 变化（信号减半后并发占用更低）；
2. 合约宇宙就绪后在 SWAP 池复测；
3. 作为特征（而非硬过滤）重训一版对比——若模型能自己学会用它，特征版更优雅。

### R2. H10 做空侧（优先#2）
`candidates.py` 镜像规则（新函数，不改多头路径）+ `labeling.py` 空头 barrier
（TP 在下方）→ 独立池训练评估。判定：按 2b 验收标准（p<0.01 + 净@maker>0）。

### R3. H1+H2 出场复合（同批 sweep）
分批止盈（半仓 2.5×ATR + 尾仓 3×ATR 拖尾）与保本推移（+1.5×ATR 后 SL=entry），
实现为 labeling 新函数进 sweep，与 TP5/SL2 基线同表对比。

### R4. H7/H8 多时间框架池（工程量大，R0 完成后）
- 主流 15 币 × 5m × 400 天拉取（量大：~11M 行，先拉 200 天试）；
- 山寨全池 × 30m/1H（量小）；
- 各池独立跑 expanded 规则 + TP5/SL2（horizon 按 RESEARCH_AGENDA 的折算网格 sweep）；
- 交付：`analysis/p2b_mtf_report.md` 跨 TF 对比表。

## P2 —— 下周（工程加固 + 体验）

### 9. 冒烟测试 + CI
- `tests/`：labeling 障碍数学（构造 OHLC 验证 tp/sl/timeout/ambiguous 四路径）、
  组合模拟不变量（同币种不重叠、并发≤10）、loader 合并去重、update_okx 幂等；
- GitHub Actions：push 时跑测试（无 secrets，纯 python）。

### 10. 看板完善·第二批
- 移动端适配细化（手机看盘）；
- 访问控制：nginx + basic auth 方案文档化（密码 owner 自设，agent 不碰凭证）；
- 信号页：合格未成交信号的悬浮详情（分数/特征快照）；
- 回测页：分数阈值滑块（只读展示用途，标注"验收窗口结论不随之变化"）。

### 11. YOLO 迭代优化循环（owner 定调 07-09："识别准确性最关键，需要迭代优化"）

每一轮迭代固定四步，不许跳（打标质量是模型上限，先审标签再谈模型）：

1. **打标审计**：`PYTHONPATH=. .venv/bin/python scripts/label_audit.py --seed <新数>`
   生成抽样页（看板 /label_audit.html），**owner 人工过目**并记录问题图名；
   错误分类三类：漏标（有密集没框）/ 误标（框了不密集）/ 框形不贴（过宽过窄）；
2. **规则修正**：按错误分类改 auto_label.py 的对应参数（阈值改动列 owner 审批项），
   重建数据集，重跑审计确认修复、不引入新错误；
3. **训练 + 官方评估**：固定配置重训，mAP50/P/R 与上一轮同表对比（单变量纪律）；
4. **一致率回归门**：consistency_check.py（P0-1 定义）作为每轮的回归测试，
   一致率下降即回退。

提升弹药库（按顺序尝试，每轮只用一发）：hard-negative 挖掘（把上一轮的高置信
误报图加入训练集）；边界样本复审（spread 恰好卡阈值的图单独抽审）；分辨率
imgsz 1280；yolo11s/m。**禁止**：动增强开关、为指标好看放宽 IoU/conf 定义。

### 11b. YOLO 架构后续（视第 1/3 步结论）
- 若 20/60/120 判断层胜出：检测+判断已对齐，做"YOLO 预测框 → 判断层候选"的
  端到端串联 demo（阶段 4 实盘检测入口的雏形）；
- 若 8-55 保持主线：评估是否需要为 8-55 渲染训练新 YOLO（工作量：改 render.py
  的均线组 + 重建数据集 + 训练；价值：仅在坚持视觉检测路线时存在——先问 owner）。

### 12. 数据质量审计
- 新建 `scripts/data_audit.py`：全部序列的缺口/异常值/零成交量统计 → 报告；
  发现问题币种列入 loader 黑名单候选（改动需记录理由）。

---

## P2.5 —— 前端"操作台化"（从数据展示进化为项目控制中枢）

> owner 愿景：整个核心流程可在前端操作。分四期落地，**每期先做鉴权再做能力**
> ——公网看板一旦有"执行"按钮，没有鉴权就是把实验室交给全网。

### 第 0 期（硬前置）：鉴权
nginx basic-auth 或 FastAPI 中间件 token（token 由 owner 生成放 VPS 环境变量，
agent 不接触明文）。没有这个，后面全部不许上 VPS。

### 第 1 期：实验注册表（只读，无风险先行）
- 后端扫描 `analysis/output/*.json` 建实验索引（tag/日期/配置/关键指标）；
- 前端"实验"页：全部历史实验一张可排序对比表 + 点击看详情 JSON + 关联报告
  （markdown 渲染 `analysis/*.md`）；
- 研究议程页：渲染 `docs/RESEARCH_AGENDA.md`，状态一目了然。

### 第 2 期：任务运行器（核心）
- 后端 job runner：sqlite 任务表 + subprocess 执行白名单命令
  （**白名单硬编码**：build_dataset / barrier_sweep / swap_replication / update_okx /
  forward_track / deploy 自身；绝不接受自由命令字符串）；
- 前端"任务"页：从白名单发起任务（参数用表单约束）、实时日志（SSE 或轮询）、
  任务历史与产物链接；
- 跑在本机看板实例即可（训练资源在 Mac）；VPS 版默认禁用执行器。

### 第 3 期：数据与模型中枢
- 数据页：各宇宙/TF 覆盖热力图、缺口审计结果、一键增量更新（走任务运行器）；
- 模型页：`models/` 冻结工件列表、当前生效配置、指纹校验、（owner 点击）晋升/回滚；
- 配置页：阈值预设/成本假设的**只读**展示 + 修改申请流（生成 diff 供 owner 在
  git 里确认——配置变更永远走代码评审，不走网页表单直改）。

### 第 4 期：监控与告警
- 前向页加异常标注（数据断更/连续止损/成交率骤降）；
- 告警通道（owner 选定后接入）。

## P3 —— 阶段 4 准备（实盘前置，多数需 owner 参与）

- **OKX 模拟盘（demo trading）接入调研**：post-only 下单、撤单、仓位查询 API；
  需要 owner 创建**模拟盘** API key（真实资金 key 永远不要给 agent）；
- **执行细节**：maker 挂单的排队/改价策略设计文档（当前回测假设"挂开盘价、
  跌破成交"，实盘需定义挂单超时与追价规则——先写文档，owner 批准再实现）；
- **风控规则**：单日最大亏损熔断、最大并发、单币种敞口、异常波动黑名单——
  写成配置文件 + 模拟盘强制执行；
- **告警**：前向跟踪异常（数据断更/连续止损超阈值）时的通知渠道（owner 选：
  邮件/TG/webhook）。

---

## 需要 owner 拍板的事项清单（集中列出，避免散落）

| # | 事项 | 依赖 |
|---|---|---|
| 1 | 均线主线：8-55 还是 20/60/120（看第 3 步对比表） | P0-3 |
| 2 | 前向跟踪加入每日定时任务 | P1-6 |
| 3 | 看板要不要加访问控制 | P2-10 |
| 4 | 8-55 专用 YOLO 训不训（仅当 8-55 保持主线） | P2-11 |
| 5 | 模拟盘 API key（demo 账户） | P3 |
