# NEXT_STEPS — 完整工程计划（2026-07-09 起，写给 Codex / 任何接手的 agent）

## 多日无人值守（2026-07-10 owner 授权）

owner 睡觉/上班/周末不在：**不要停**。读 `output/offline_tasks/AUTONOMOUS_CHARTER.md`。
价值序：前向 → 数据 → YOLO 标签/模型 → 操作台修 bug → H1 影子 → 工程卫生。
状态心跳：`output/offline_tasks/MULTI_DAY_STATUS.md`。

## 过夜进度指针（2026-07-10，main 文档包）

- H1 scaled 前向 **shadow 计划**（不替换 TP5/SL2 主线，owner 可后开）：
  [`docs/H1_SCALED_FORWARD_SHADOW_PLAN.md`](docs/H1_SCALED_FORWARD_SHADOW_PLAN.md)
- 前向样本加速选项（诚实利弊；**默认建议保持** q90 + 正式窗）：
  [`docs/FORWARD_ACCELERATION_OPTIONS.md`](docs/FORWARD_ACCELERATION_OPTIONS.md)
- MA206 全量重建：358 个 SWAP、19,666 个已标签候选；ACTIVE
  `frozen_tp5_sl2_swap_ma206_20260710`。冻结模型前向冒烟见 21,086 个历史候选，
  正式窗口 `new_signals=0` / `total_rows=0`；日志 `data/forward_log_ma206.csv`（不入 git）
- 未做：YOLO 重训、改 BLOCKED/`auto_label`、VPS 部署；MA206 holdout 曾被旧看板意外读取
  1 次，结果隔离作废，禁止再次读取

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
- ✅ yolo11s 离线管道已完成：mAP50 0.8569，低于正式验收线 0.90；
  P0-1 判定为 YOLO 正式验收未达成、非关键路径暂停。


先读 `AGENTS.md`（纪律，违反=返工），再读 `HANDOFF.md`（状态）。按优先级顺序执行；
P0 全部完成前不开 P1。每完成一项：commit + push + 在本文件划掉该项。

**环境须知（踩过的坑）：**
- YOLO 相关必须用 `.venv/bin/python`（torch 只在这）；其余用系统 `python3`；
- datetime→epoch 用 Timedelta 除法，禁 `astype(int64)//1e9`（差 1000 倍的坑，见 docs/learnings/）；
- OKX 请求带浏览器 UA（fetch_okx.py 已封装），全局 ≤8 req/s；
- 提交信息英文、汇报中文；看板改动后 `bash scripts/deploy_vps.sh` 同步 VPS。

**红线（每个 P 级都适用）：** 禁再次评估 holdout；旧配置窗口已消耗，MA206 又因看板缺陷
意外消耗 1 次且作废；禁对 2026-05-04 后窗口调参；
禁重构现有模块/升级依赖/动 .venv/动 scheduled task；坏结果如实入报告。

---

## P0 —— 明天必做（依赖今晚离线管道的产出）

### 0. ~~验收离线管道产出（5 分钟）~~ ✅ 已完成（2026-07-09）
`cat OFFLINE_RESULTS.md`；没有就 `tail -50 logs/offline_run.log` 看死在哪个阶段，
手动补跑该阶段（脚本内 5 个阶段命令均独立可执行）。

完成记录：后台脚本实际把 `OFFLINE_RESULTS.md` 写到了 main worktree
`/Users/zhangzc/fable-trading/`；Codex 只读该文件并同步副本到本 worktree。
合约复制性继续成立；YOLO11s 官方评估 mAP50 0.8569。

### 1. ~~YOLO 全量训练验收判定~~ ✅ 已完成（2026-07-09；未达正式验收线，暂停）
- mAP50 ≥ 0.90 → 写 `src/detection/consistency_check.py`：val split 每张图，
  auto_label 规则框为真值，best.pt 预测（conf=0.30）IoU≥0.5 匹配；输出一致率
  （匹配/规则框数）与误报率。一致率 ≥95% → p2a 报告追加"正式验收通过"节；
- mAP50 < 0.90（含 yolo11s）→ p2a 报告如实记录封顶值，标注"验收未达成、
  非关键路径、暂停"。禁止调 conf/IoU/增强凑数。

完成记录：yolo11s mAP50 0.8569 / mAP50-95 0.6643 / precision 0.8003 /
recall 0.7112，低于 0.90。已在 `analysis/p2a_detection_report.md` 追加正式验收未达成
结论；不写 consistency_check，不继续调参。

### 2. ~~合约复制性检验判读~~（✅ 已通过，报告已补写）
- **成立** = tp5_sl2 合约 val perm_p < 0.01 且 top-decile 净@maker0.06% > 0；
- 成立 → p2b_v3 报告追加"合约宇宙复制"节，HANDOFF 主线宇宙改为 SWAP；
- 不成立 → 停，报告如实记录，HANDOFF 标"复制失败待 owner"，禁止救数字。

### 3. ~~均线 20/60/120 全链路统一~~（✅ 2026-07-10 owner 覆盖旧裁决并完成）
历史上判断层与检测层使用不同均线；现已统一为 SMA20/60/120 + EMA20/60/120 六线。
完成内容：
1. `src/judgment/candidates.py` 统一为 SMA20/60/120 + EMA20/60/120；
   密集规则从 `src/detection/auto_label.py`（它本来就是 20/60/120 的，已被 YOLO
   验证可学）起步：fast_spread（20/60 四线）≤0.0028×1.6、full_spread（六线）
   ≤0.0055×1.6、连续 ≥5 根；volume/pre_range 等门槛从 candidates.py 原样复用；
2. 特征同构迁移：均线类特征改基于六线算，非均线类原样复用；
3. 标签 TP5/SL2 h72；宇宙用 SWAP（若第 2 步成立）；train.py 流程不动，val only；
4. 新 ACTIVE 为 `frozen_tp5_sl2_swap_ma206_20260710`，新前向账本从
   2026-07-10 10:30 UTC 独立累计；报告 `analysis/p2b_ma206_mainline_migration.md`。

**重要认知**：2a YOLO 与 2b 判断层现已使用同一六线定义，无需为另一套均线重训 YOLO。
全量 MA206 val AUC 0.5702/p=0.001；maker 组合 PF 1.072，1h EMA120 过滤后 PF 1.154。
架构统一不等于盈利通过。

### 4. 前端 bug 修复（owner 2026-07-09 截图实证，修复必须真浏览器验证）

**~~BUG-1（主）：信号页切换成交单后 K 线消失。~~** ✅ 已修复（2026-07-09）
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

完成记录：`pathSeries` 不再参与右侧 K 线价格轴 autoscale；每次切换成交单前先清空旧
entry→exit path 并恢复右轴 autoscale。localhost:8643 与 VPS
`http://103.214.174.58:8642` 均用真实 Chrome 验收通过：BNB/BTC/LTC ×
15 天/1 个月/3 个月/全部，49 次成交单切换，蜡烛像素与 entry/exit 坐标均正常。

**~~BUG-2：均线密集色带只渲染半高悬浮块~~** ✅ 已修复（2026-07-09，应为全高背景带）。
修法：给 bandSeries 设 `autoscaleInfoProvider: () => ({priceRange: {minValue: 0, maxValue: 1}})`
配合现有 scaleMargins {top:0,bottom:0}，令 value=1 的两点撑满全高。

完成记录：localhost:8643 与 VPS `http://103.214.174.58:8642` 均用真实 Chrome 验证；
BNB_USDT 1 个月窗口聚焦成交单后，色带列覆盖 430/452 像素（约 95%），y=0..451。

**~~BUG-3：出场价与止损线重叠时标签互相遮挡~~** ✅ 已修复（2026-07-09，sl 出场时两线同价）。
修法：outcome 为 sl/sl_ambiguous 时不再单独画止损障碍线（出场线已表达）；
tp 出场同理去掉止盈目标线的重复。

完成记录：localhost:8643 与 VPS `http://103.214.174.58:8642` 均用真实 Chrome 验证；
BNB_USDT 1 个月窗口 TP 与 SL 样例均只保留 3 条价格线，entry/exit 坐标在面板内。

修复过程遵守：只做外科手术式修改，禁止重构 app.js；每个 bug 单独 commit。

### 5. ~~P0 收尾~~ ✅ 已完成（2026-07-09）
更新 HANDOFF（划掉完成项）；本文件同步；全部 push。

完成记录：P0-0~P0-4 均已完成；P0 结论和后续状态已同步到
`OFFLINE_RESULTS.md`、`analysis/p2a_detection_report.md`、`HANDOFF.md`、
`PROJECT_STATUS.md` 与本文件。

---

## P1 —— 本周内（前向验证基础设施 + 数据补强，最终裁决的地基）

### 5. ~~冻结模型工件（前向验证的前提）~~ ✅ 已完成并迁移 MA206（2026-07-10）
现在每次启动都重训模型——前向验证必须用**冻结的字节**：
- 新建 `scripts/freeze_model.py`：用胜出配置（默认 tp5_sl2 SWAP；等第 2/3 步结论）
  训练一次，`booster.save_model("models/frozen_<config>_<date>.txt")` + 同名 json
  存阈值（val q90）、特征列表、数据指纹；`models/` 入 git；
- 看板与前向跟踪一律加载冻结工件，不再重训（server.py 的 build_signals 路径
  加"若存在冻结工件则加载"分支）。

当前记录：`models/frozen_tp5_sl2_swap_ma206_20260710.txt/.json`；
阈值为 val q90 = 0.3409333202，best_iteration=32，
数据指纹 `8df081a1374c0edb1ef8a869cc4825830ecb2f07fd00209306c44dcc272040d1`。
`build_signals` 在数据路径匹配冻结工件时直接加载 LightGBM txt；看板分数缓存侧边车
记录 model_path/dataset_path/dataset_sha256，不匹配即重建，避免旧缓存绕过冻结字节。
本地 8643 API 已验收 `/api/symbols`、`/api/overview`、`/api/chart/okx/BTC_USDT_SWAP`。

### 6. 前向跟踪器（paper-trading 记录仪；脚本已完成，定时任务待 owner 点头）
- ~~新建 `scripts/forward_track.py`：读最新数据 → 扫描冻结配置的新候选 →
  冻结模型打分 → 阈值以上记为信号 → 结果已知的（barrier 已触发）补记出场，
  追加写 `data/forward_log_ma206.csv`（append-only，含 maker_filled 判定）；~~
  ✅ 已完成（2026-07-09）。实现拆在 `src/judgment/forward*.py`：
  默认从 `2026-07-10 10:30 UTC` 起记录 SWAP TP5/SL2 MA206 冻结模型信号；
  新信号按 `(source, symbol, signal_time)` 加入，已打开信号只补 outcome/exit 字段，
  保留原 detected_at/model_path/dataset_sha256。
- ~~幂等：重复运行不重复记录（按 signal_time+symbol 去重）；~~
  ✅ 已验证。正式窗口当前 0 条新信号；临时回放 `--start 2026-07-01`
  写入 19 条 closed 信号，第二次运行新增 0、重复 0。
- 每日运行：**需 owner 点头**后把它加进每日定时任务（在现有 8 点任务的 prompt 里
  追加一步，或 owner 自己跑 `python3 scripts/forward_track.py`）；
- 3-4 周后累计 ≥100 笔时，用该日志算前向 PF——这是 v3 配置的最终裁决。

### 7. ~~资金费率历史（合约回测的成本精度）~~ ✅ 已完成（2026-07-09）
- ~~新建 `src/data/fetch_funding.py`：OKX `/api/v5/public/funding-rate-history`
  拉全部 SWAP 币种 400 天资金费率 → `data/funding/`（复用 UA/限速）；~~
  ✅ 已完成。脚本已由离线队列入库；当前本机 `data/funding/` 有 54 个 SWAP 文件，
  每个约 278 条，覆盖约 `2026-04-07 08:00 UTC` → `2026-07-08 16:00 UTC`。
  OKX public API 实际可返回历史短于 400 天，因此下游必须报告覆盖率。
- ~~合约回测把"资金费近似 0.02%"替换为按持仓时段的真实费率累计；~~
  ✅ 已完成。新增 `src/data/funding.py`：长仓在
  `entry_time < funding_time <= exit_time` 的 funding settlement 上累计
  `realized_rate`，正费率为成本、负费率为返还；缺历史返回缺失，不静默当 0。
- ~~重跑 swap_replication 报告差异（预期影响 <2bp/笔，但要有真数）。~~
  ✅ 已完成。`scripts/swap_replication.py` 保留旧 `maker0.06%` 近似列，并新增
  覆盖样本上的真实资金费净值列。当前数据池复跑 TP5/SL2：top funding 覆盖 76.2%，
  实测资金费均值约 +0.003%/笔，净@maker+真实资金费（覆盖样本）约 +0.003%/笔；
  资金费本身较 0.02% 近似改善约 +1.7bp，但当前数据池复跑的 top 毛利弱于 P0 旧快照，
  已在 `analysis/p2b_v3_barrier_sweep.md` 诚实记录。

### 8. ~~看板完善·第一批（依赖 5/6/7）~~ ✅ 已完成（2026-07-09）
- ~~**前向验证页（新 tab）**：读 forward_log.csv，展示前向净值曲线、累计笔数/PF/
  胜率、距"100 笔裁决线"的进度条——这是 owner 每天最想看的一页；~~
- ~~**宇宙切换**：总览/回测/信号页支持 现货/合约 数据集切换（后端加 dataset 参数，
  分数缓存按宇宙分文件）；~~
- ~~总览页数字改为全部动态读 analysis/output/*.json（现有部分硬编码）；~~
- ~~部署 VPS 并用真实浏览器验证（教训：本地过≠VPS 过）。~~

完成记录：新增 `DESIGN.md` 固化现有暗色看板设计系统；后端新增 `universe`
参数与 `data/scored_signals_<universe>.csv/.json` 分宇宙缓存，spot 侧在训练/打分前
过滤掉混入的 `_SWAP` 行；总览、回测、信号页均支持合约/现货切换；新增前向验证 tab，
读取 `data/forward_log_ma206.csv`，按 maker-filled closed 样本展示 0/100 裁决进度、PF、胜率、
净值与日志。localhost:8643 与 VPS `http://103.214.174.58:8642` 均用真实浏览器验收：
总览、回测、信号 K 线、前向空状态、现货/合约切换与 390px 移动视口均可用。

---

## P1.5 —— 研究议程执行（与 P1 并行，Codex 可做大部分）

> 总纲：`docs/RESEARCH_AGENDA.md`（假设发生器 + 两级验证制度）。按其"优先队列"执行；
> 每个假设一个独立 commit + 报告小节；负结果同样入库并更新议程状态。

### ~~R0. 工程前置：sweep 台架泛化（先做，其余依赖它）~~（已完成，2026-07-09）
- `fetch_okx.py`/`update_okx.py` 已支持 `--bar {5m,15m,30m,1H}`，文件名保留 bar；
- loader/build/train 共用 `src/data/bars.py` 的 bar 白名单与 purge 换算，
  `build_dataset.py --bar/--horizon-bars`、`train.py --bar/--horizon-bars` 已就绪；
- `barrier_sweep.py` 已注册 `fixed/trailing/scaled/breakeven/ma-exit` 插件；
  `labeling.py` 只新增 `label_candidate_ma_exit`，未改旧标签函数。

### ~~R1'. H9 复测与推广~~（已完成，2026-07-09）
旧 H9 使用 1h EMA55/144，已随 MA206 统一废弃。当前过滤器改为 1h EMA60 斜率与
EMA120 上方；MA206 val 复跑 maker PF 1.070→1.086（above EMA120），斜率过滤降至
0.934，均未过 1.3。旧 `analysis/p15_h9_report.md` 只作历史审计，不再代表当前主线。

### ~~R2. H10 做空侧（优先#2）~~（已完成，2026-07-09）
`candidates.py` 镜像规则（新函数，不改多头路径）+ `labeling.py` 空头 barrier
（TP 在下方）→ 独立池训练评估。判定：按 2b 验收标准（p<0.01 + 净@maker>0）。

完成记录：SWAP 空头 expanded + TP5/SL2 独立池发现级通过，val AUC 0.6174、
p=0.001、top-decile 净@maker +0.205%、maker filled-only +0.131%。但单特征
ma_spread baseline 净@maker +0.343%，高于 LightGBM，因此结论是“空头侧有 alpha
线索，可进入后续组合/前向候选”，不是主线替换项。详见
`analysis/p15_h10_short_report.md`。

### ~~R3. H1+H2 出场复合（同批 sweep）~~（已完成，2026-07-09）
分批止盈（半仓 2.5×ATR + 尾仓 3×ATR 拖尾）与保本推移（+1.5×ATR 后 SL=entry），
实现为 labeling 新函数进 sweep，与 TP5/SL2 基线同表对比。

完成记录：已将旧 H1/H2 原型升级为 SWAP-only 主线口径，输出
`analysis/output/exit_variants_swap.json`。H1 scaled 强通过：val AUC 0.6106、
p=0.001、top 净@maker +0.326%、maker 组合 PF 2.825、maxDD 0.29%；H2 breakeven
单独不通过（AUC 0.5172、p=0.1738）。H1 记录为最强发现级候选，但不替换冻结主线，
需前向确认。详见 `analysis/p15_h1_h2_exit_report.md`。

### ~~R4. H7/H8 多时间框架池（工程量大，R0 完成后）~~（已完成，2026-07-09）
- 主流 15 币 × 5m × 400 天拉取（量大：~11M 行，先拉 200 天试）；
- 山寨全池 × 30m/1H（量小）；
- 各池独立跑 expanded 规则 + TP5/SL2（horizon 按 RESEARCH_AGENDA 的折算网格 sweep）；
- 交付：`analysis/p2b_mtf_report.md` 跨 TF 对比表。

完成记录：新增 `scripts/mtf_sweep.py` 与 `analysis/output/mtf_sweep.json`。
H7 5m 证伪：val 样本仅 15m 基线的 0.63×，未达到机会数 ≥3×，filled-only
净@maker 为负。H8 30m 发现级通过：30m h72 AUC 0.6297、p=0.001、top 净@maker
+0.484%、filled-only +0.521%，但样本仅 0.24× 15m，属于低频高质量线索。
1H 样本 52-55，p 未达 0.01，不确认。详见 `analysis/p2b_mtf_report.md`。

## P2 —— 下周（工程加固 + 体验）

### ~~9. 冒烟测试 + CI~~ ✅ 已完成（2026-07-09）
- ~~`tests/`：labeling 障碍数学（构造 OHLC 验证 tp/sl/timeout/ambiguous 四路径）、
  组合模拟不变量（同币种不重叠、并发≤10）、loader 合并去重、update_okx 幂等；~~
- ~~GitHub Actions：push 时跑测试（无 secrets，纯 python）。~~

完成记录：新增 `tests/test_labeling_paths.py`、`tests/test_portfolio_simulation.py`、
`tests/test_loader_update_smoke.py`，覆盖长仓 barrier 四路径、组合模拟同币种/并发不变量、
loader 合并去重、update_okx 无新增 confirmed bar 幂等；新增 `.github/workflows/tests.yml`，
push 到 `codex/day1` 或 PR 时运行 compileall + pytest。CI 只安装判断层/看板测试所需轻量依赖，
不把 torch/ultralytics 放进普通 push gate。

### 10. 看板完善·第二批
- ~~移动端适配细化（手机看盘）；~~ ✅ 已完成（2026-07-09，本地 Chrome 390px 验证）
- ~~访问控制：nginx + basic auth 方案文档化（密码 owner 自设，agent 不碰凭证）；~~
  ✅ owner 2026-07-09 拍板：暂不加，继续主线
- ~~信号页：合格未成交信号的悬浮详情（分数/特征快照）；~~ ✅ 已完成
- ~~回测页：分数阈值滑块（只读展示用途，标注"验收窗口结论不随之变化"）。~~ ✅ 已完成

进度记录：访问控制已由 owner 拍板暂不加；P2-10 其余完成部分包括：
信号页右侧新增合格未成交列表，hover/focus 展示 score、阈值差、ATR%、密集长度、标签收益、
入场价；回测页新增只读展示分数滑块，仅过滤成交明细表，不改变净值/PF/验收结论；
移动端修复 Lightweight Charts 在 grid 子项内撑破 390px 视口的问题。

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

Round 1 进度（2026-07-09）：
- 已生成审计页：`PYTHONPATH=. .venv/bin/python scripts/label_audit.py --seed 20260709`
  → `src/webapp/static/label_audit.html`（看板 `/label_audit.html`）。
- 已记录样本清单：`analysis/p2a_label_audit_round1.md`。
- 已用 Playwright 验证 desktop 1280x900 / mobile 390x844：18 张图，横向溢出 0。
- **07-10 owner 确认** findings（见 `output/offline_tasks/yolo_label_audit_findings.csv`）。
- **07-10 E1 完成**：`X_PAD_PX` 12→6；`dense_15m_full` 标签重写；
  报告 `analysis/p2a_e1_xpad_report.md`。下一步：owner 过审计页 → 认可后再固定配置重训。

### 11b. YOLO 架构后续
- 检测与判断已统一 20/60/120；E2.1b 自然结束并完成正式报告后，再做固定 SAHI 评估；
- YOLO 仍是非关键路径，不阻塞 MA206 规则扫描 + LightGBM 前向验证。

### ~~12. 数据质量审计~~ ✅ 已完成（2026-07-10）
- ~~新建 `scripts/data_audit.py`：全部序列的缺口/异常值/零成交量统计 → 报告；
  发现问题币种列入 loader 黑名单候选（改动需记录理由）。~~

完成记录：`scripts/data_audit.py` + `tests/test_data_audit.py` +
`analysis/p2_data_audit_report.md` / `analysis/output/data_audit.csv`。
扫描 892 序列；OKX SWAP 15m = 206（stale 43，优先 update_okx）。
黑名单候选主要是股票/ETF 类薄流动性 SWAP（EWZ/CGNX/DKNG/… 与 AAPL/AMD 等）；
OHLC 坏样本 0。5 个 `.part.csv` 未完成拉取已列报告。
**2026-07-10 owner 确认**：报告表 SWAP 15m thin equity/ETF 候选已写入
`loader.BLOCKED_BASES`（EWZ/CGNX/…/AAPL/AMZN 等 22 个 base）。

---

## P2.5 —— 前端"操作台化"（从数据展示进化为项目控制中枢）

> owner 愿景：整个核心流程可在前端操作。分四期落地，**每期先做鉴权再做能力**
> ——公网看板一旦有"执行"按钮，没有鉴权就是把实验室交给全网。

### ~~第 0 期（硬前置）：鉴权~~ ✅ 已完成（2026-07-10）
~~nginx basic-auth 或 FastAPI 中间件 token（token 由 owner 生成放 VPS 环境变量，
agent 不接触明文）。没有这个，后面全部不许上 VPS。~~

完成记录：`src/webapp/auth.py` + `ops_flags.py`；`OPS_AUTH_MODE` /
`OPS_API_TOKEN` / `OPS_REQUIRE_AUTH`；Bearer 或 `X-Ops-Token`；
`GET /api/ops/status` 公开探测门禁；token 模式且 token 空 → 503 防空门禁。
操作说明：`docs/P2_5_PHASE01_README.md`。VPS 公网前 owner 须自设 secret。

### ~~第 1 期：实验注册表（只读，无风险先行）~~ ✅ 已完成（2026-07-10）
- ~~后端扫描 `analysis/output/*.json` 建实验索引（tag/日期/配置/关键指标）；~~
- ~~前端"实验"页：全部历史实验一张可排序对比表 + 点击看详情 JSON + 关联报告
  （markdown 渲染 `analysis/*.md`）；~~
- ~~研究议程页：渲染 `docs/RESEARCH_AGENDA.md`，状态一目了然。~~

完成记录：`experiment_registry.py` + `agenda_payloads.py`；路由
`/api/ops/experiments`、`/api/ops/experiments/{id}`、`/api/ops/agenda`；
顶栏 **实验** / **议程** tab。**job runner 已合 main**（`ENABLE_JOB_EXECUTOR` 默认关；VPS 禁止开）。

### 第 2 期：任务运行器（核心）
- ~~后端 job runner~~ ✅ Phase2 白名单+sqlite+runner；Phase3 只读 data/model hub 已合
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
| 1 | 均线主线 | ✅ 07-10 owner 定为 SMA/EMA20/60/120，覆盖旧决定 |
| 2 | 前向跟踪加入每日定时任务 | P1-6：scheduled-tasks 已含 forward_track + digest（2026-07-10 核实） |
| 3 | 看板要不要加访问控制 | P2-10：owner 2026-07-09 决定暂不加 |
| 4 | 20/60/120 YOLO 后续 | E2.1b 报告后固定 SAHI；不改增强/conf/IoU |
| 5 | 模拟盘 API key（demo 账户） | P3 |
| 6 | P2-12 黑名单候选是否写入 BLOCKED_BASES | ✅ 07-10 owner 确认已写入 |
| 7 | P2-11 Round 1 打标人工过图（漏标/误标/框形） | ✅ 07-10 owner 确认 findings；下一步 E1 x_pad |
