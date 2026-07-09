# HANDOFF — 给下一个会话/模型的执行路线图

**写于 2026-07-08。** 读完本文件 + `CLAUDE.md` + `analysis/p2b_v2_report.md`，即可无损接手本项目。

## 当前状态一句话

**07-09 追加：合约复制性检验通过**（TP5/SL2 纯 SWAP 池 AUC 0.560/p=0.001，top-decile 净@maker +0.225%/笔，判定标准全过）——**主线宇宙即日起为 SWAP**；H1 分批止盈为 v3 出场头号挑战者（AUC 0.608/胜率 65%）。前向验证与后续实验一律用合约配置。YOLO11s 离线验收 mAP50 0.8569，低于正式线 0.90，检测层正式验收未达成，标记为非关键路径并暂停。P1-5 已完成：`models/frozen_tp5_sl2_swap_20260709.txt/.json` 入库，看板信号路径加载冻结模型并按 model_path/dataset_sha256 失效旧缓存。P1-8 已完成：看板新增前向验证 tab、现货/合约切换、动态总览与分宇宙 score cache，localhost 与 VPS 真浏览器验收通过。P1.5 R0~R4 已完成：H9/H10 有线索但不切主线；H1 scaled 是最强发现级候选；5m 机会扩张证伪，30m 是低频高质量新线索。P2-9 已完成：补齐冒烟测试并新增 GitHub Actions。P2-10 已完成：移动端、合格未成交 tooltip、只读分数滑块；owner 拍板暂不加访问控制。

**2b 验收通过（holdout 已消耗）→ 阶段 3 第一轮未通过（PF 1.01@0.3%）→
owner 已委托"按推荐直接执行" → 出场结构扫描完成：TP5/SL2 为 v3 候选标签**
（val 净@0.3% +0.077%/笔 vs 基线 +0.001%，p=0.001，见 `analysis/p2b_v3_barrier_sweep.md`）。
进行中：P2-11 YOLO 迭代优化循环；Round 1 打标预审已由 owner 确认，下一步 E1（收 x_pad）。
**07-10 追加（Grok 接手）**：P2-12 数据审计完成（见 `analysis/p2_data_audit_report.md`）；
每日定时任务已含 `update_okx → forward_track → daily_digest`；正式窗口前向日志已有
**2 笔** closed 信号（冻结 TP5/SL2 SWAP）。`src/notify.py` + `scripts/daily_digest.py`
已同步进本 worktree。
纪律红线：holdout 与验收窗口均已消耗，v3 的确认性验证只能用前向新数据；
val 已被多次选型使用，其数字只用于排序不用于宣称绩效。

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
