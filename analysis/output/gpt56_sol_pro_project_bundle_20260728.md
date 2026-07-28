# GPT 5.6 Sol Pro Attachment Bundle

The attached bundle contains local files for review. Local paths are provenance labels only.

## Manifest
- `.` (6829 bytes)
- `.` (4305 bytes)
- `.` (11983 bytes)
- `.` (9054 bytes)
- `.` (2855 bytes)
- `.` (2449 bytes)
- `.` (3265 bytes)
- `.` (5595 bytes)
- `.` (3712 bytes)
- `.` (2602 bytes)
- `.` (3784 bytes)
- `.` (20369 bytes)
- `.` (10934 bytes)
- `.` (19052 bytes)
- `.` (5391 bytes)
- `.` (14417 bytes)
- `.` (15789 bytes)
- `.` (16117 bytes)
- `.` (6161 bytes)
- `.` (1947 bytes)
- `.` (7170 bytes)
- `.` (5189 bytes)

## Files

### `.`

````markdown
# AGENTS.md — fable-trading 工作规范

一句话：两层架构验证"双均线密集启动"信号——YOLO 检测层（2a）+ LightGBM 判断层（2b），
2026-07 起进入 **VPS 实盘阶段**（执行层 + 前向 100 笔新鲜裁决）。
当前进度与下一步看 `HANDOFF.md` 顶部"当前真相"；各阶段结论看 `analysis/p*_report.md`；
本周执行计划看 `analysis/week_plan_20260720.md`；路线图（历史）看 `PROJECT_PLAN.md`。

## 铁律（违反 = 返工，没有例外）

1. **holdout 纪律**：holdout（≥2026-05-04）只在最终验收时评估，每次动用必须先获得项目
   所有者在对话中的明确批准，并在报告里记录"这是该配置第 N 次消耗 holdout"。
   训练/调参/特征选择的任何环节不得读取 holdout；`train.py` 不加 `--eval-holdout` 即安全。
2. **时间切分**：所有评估按时间切分，禁止随机切分，禁止跨切点的样本进入训练。
3. **无前视**：特征只能使用信号 bar 及之前的数据；只有标签允许看未来。
   新增特征必须在 docstring 写明用到的列与窗口。
4. **单变量纪律**：一次实验只改一个变量；结果无论成败都写入报告。
   多变量打包改动需项目所有者批准并在 PROJECT_PLAN 记录（先例：2b-v2 三项打包，2026-07-07）。
5. **YOLO 增强禁用**：fliplr/flipud/mosaic/mixup/hsv 全关——它们破坏时间方向和红绿 K 线语义
   （旧项目 180 版失败的病因之一，见 README）。
6. **数据**：`data/` 不入 git；`data/kline_cache` 是旧项目缓存的只读软链接；
   新数据用 `python3 -m src.data.fetch_okx`（可断点续传，需本机网络）。

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
12. **检测只认盘口**（owner 2026-07-23）：live 扫描只扫 tip/tip-1/tip-2 窗；凡"只能产出
    事后信号"的路径（回看窗、事后模型、非盘口分布数据集）一律不得存在。pre-v16 检测器
    权重已三机清除（仅存 COCO yolo11 底座）；检测器晋升唯一门 = 真 tip 金标 + tip-smoke，
    自家 val/mAP/旧 frozen-F1 永不作裁决。无验证过的检测器时管道诚实空转（detector=none）。

## 弱模型在本仓库最容易犯的错（每条都真实发生过或差点发生）

- **把 AUC 当成功标准** → 本项目成功标准是 top-decile 扣 0.2% 往返成本后的净收益为正
  且置换检验 p<0.01；v1 的教训就是 AUC 0.59 照样亏钱。AUC 只是参考量。
- **在 holdout 上"看一眼"** → 看一眼就是消耗一次，见铁律 1。
- **重跑 build_dataset 覆盖别的池的数据集** → 输出文件名必须带池名
  （`judgment_dataset_v2_strict.csv` / `..._expanded.csv`），tag 必须带池名。
- **顺手调 strict/expanded 阈值预设** → 阈值是项目所有者决策，改动需批准。
- **只汇报好消息** → 报告必须含"风险与诚实声明"节；隐瞒失败的实验记录等于污染实验日志。
- **默认拉全部币种重新 fetch** → 先检查 `data/kline_fetched/` 已有 `okx_*_15m_*.csv`，
  fetcher 会自动跳过已完成币种。
- **把 val/accept PF 当实盘** → 确认级只有前向新鲜 100 笔；v11 accept PF 高仍要前向终审。
- **报池子的绝对收益，不带对照组** → 2026-07-28：100×6m 池 +16.9bp 里 +7.2bp 是做空 beta，
  检测器自己只值 +9.0bp 而往返成本 10bp。见 `docs/learnings/pool-internal-metrics-cannot-see-beta.md`。
- **拿人工标注当天然可学习的目标** → 先量「标注时可见多少未来」：499 个 ⭐标杆里
  只有 2 个画在盘口，中位可见 97 根。见 `docs/learnings/zero-live-edge-labels-means-the-target-is-unverified.md`。
- **改一道新鲜度门忘了另两道** → 三门必须同值，见实盘纪律 7。
- **往脉冲里塞实验扫描** → 超 15min 节拍 = 结构性挡 tip；见实盘纪律 8。
- **自动 promote / 清 forward_log** → 禁止；owner 点头。

## 质量标准（可检查，不是形容词）

每轮实验的交付物是 `analysis/pXX_report.md`，必须包含：

- [ ] 复现命令（从零跑通的完整命令序列）
- [ ] 数据统计（候选数 / 正类率 / 时间范围 / val 样本数）
- [ ] 结果表，且与上一版本同表对照
- [ ] 必报指标：val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征基线对照
- [ ] **匹配随机对照组**（同币 × 同时间块 × 同波动桶的随机入场，同障碍同成本）——
      方向性策略的每张结果表都要带。置换检验只验排序，抓不到整池踩在 beta 上
- [ ] 解读（每个数字变化的归因）
- [ ] 风险与诚实声明
- [ ] 下一步选项（标注哪些需要项目所有者决策）

代码标准：python3 + pandas/lightgbm/ultralytics，无新增重型依赖；模块级 docstring
说明来源与决策依据（现有代码都是这个风格，照着写）。

## 不确定时的升级规则

- 涉及 **holdout、阈值预设、障碍参数（TP/SL 倍数、atr 下限）、成本假设（0.2%）** 的任何
  改动 → 停下来问项目所有者，不要"先试试"。
- 涉及 **新鲜度门、脉冲预算、ACTIVE/frozen 切换、清空 forward_log、promote owner_best、
  真下单/改仓** → 同上，见实盘纪律 7–11。
- 数据源不可用或返回结构变化 → 如实报告现象，不要静默换数据源或造数据。
- 结果好得反常（AUC 突然 >0.7、净收益突然翻倍、accept PF 夸张）→ 第一假设是泄漏或 bug，
  写最小复现验证后再汇报；确认级只认前向新鲜样本。
- 项目所有者用中文交流，汇报用中文；代码与注释用英文。

## learning law

每解决一个非平凡问题（修 bug、架构决策、反直觉结论），先运行 extract-approach skill
在 `docs/learnings/` 留下笔记再继续。没有 learnings 笔记的解决方案视为未完成的工作。
````

### `.`

````markdown
# darkforest-trading

验证一个交易假设:**K 线多均线"密集后启动"形态,在启动初期可被视觉模型识别,且其中
一小部分在扣除成本后可交易**。两层架构——YOLO 检测"长得像的",LightGBM 回归排序
"值得进的"——外加一套防自欺的实验纪律。

> **实时状态只看一处:[`HANDOFF.md`](HANDOFF.md) 顶部。**  
> 文档索引:[`docs/DOC_MAP.md`](docs/DOC_MAP.md)。  
> 本 README 讲不随进度变化的东西:动机、架构、纪律、怎么跑。

## 架构

```
OKX 合约 15m K线(400+ 币种,VPS 每 15 分钟增量,是 K 线唯一写者)
   │  src/data/fetch_okx.py(断点续传) / update_okx.py(脉冲内增量)
   ▼
渲染 200-bar 窗口(K线 + SMA/EMA 20/60/120)          src/detection/render.py
   ▼
[2a 检测层] YOLO11 —— 在项目所有者手工标注(~9500张)上训练
   │         权重: models/owner_best.pt(晋升制,泄漏审计,标杆体检门)
   │         live 扫描:tip+近端 6 窗,盘口 bar 当场入账(实时 tip 路径)
   ▼
[2b 判断层] LightGBM 回归 predicted_realized_ret      src/judgment/
   │         冻结工件 + val-q90 阈值,事前锁定          (frozen.py 是唯一咽喉)
   ▼
TP5/SL2 三重障碍出场 → 前向验证(100 笔新鲜裁决,事后检出剔除)
   ▼
[执行层] src/execution/ —— OKX 实盘(VPS systemd):市价入场 + OCO 括号
         + 72-bar 超时平仓;新鲜度三门 30min 一致(执行器/TG/裁决)
   ▼
看板 :8642 / TG 信号
```

- **看板**: http://103.214.174.58:8642(部署 `bash scripts/deploy_vps.sh`)
- **打标**: Label Studio :8081,轮次制;round8 起生成器保证窗口零重叠、排除冻结评估币种

## 纪律(为什么这个项目还没自欺)

细则在 [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md),不可协商的几条:

1. **holdout(≥2026-05-04)每次动用需项目所有者批准并记账**(消耗账本见 HANDOFF 顶部,当前 5 次)
2. **成功标准是 top-decile 扣成本净收益 + 置换检验 p<0.01**,AUC 只是参考量
3. **冻结评估尺子是清单不是规则**:`datasets/owner_eval_frozen/MANIFEST.json`
   (47 币种从未参训);训过尺子币种的模型会被晋升门自动拒绝
4. **每轮实验交付 `analysis/pXX_report.md`**,必含复现命令与"风险与诚实声明"
5. **每个非平凡问题解决后写 `docs/learnings/` 笔记**(40+ 篇,含
   "optimizer=auto 炸掉所有续训"、"新鲜度门必须从管道时序推导"、"tip 分布错位"等)

## 快速上手

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 数据(断点续传,~1h)
PYTHONPATH=. .venv/bin/python -m src.data.fetch_okx

# 判断层训练/评估(不带 --eval-holdout 即安全;当前主线池 v11)
PYTHONPATH=. .venv/bin/python -m src.judgment.train --data data/judgment_yolo_swap_v11.csv

# 阶段3回测(accept 窗口是 holdout,跑之前先读 CLAUDE.md 铁律 1)
PYTHONPATH=. .venv/bin/python -m src.backtest.run --frozen-config default \
    --data data/judgment_yolo_swap_v11.csv

# 检测层评估(冻结尺子) + 标杆体检
PYTHONPATH=. .venv/bin/python scripts/promote_owner_best.py
PYTHONPATH=. .venv/bin/python scripts/benchmark_check.py

# 本地看板
.venv/bin/uvicorn src.webapp.server:app --port 8642
```

## 仓库地图

| 路径 | 内容 |
|---|---|
| `src/detection/` | 渲染、YOLO 训练配方(含续训 lr 修复)、评估尺子(唯一实现) |
| `src/judgment/` | 候选→特征→三重障碍标签→LightGBM→冻结工件→前向 |
| `src/backtest/` | 阶段3 事件驱动模拟(成本扫描、并发上限、`--frozen-config`) |
| `src/execution/` | OKX 实盘执行器(市价+OCO+超时平仓;secrets 在 data/ 不入 git) |
| `src/short_tf/` | 1m/5m 规则 tip 支线(独立日志,不接主线 executor) |
| `src/costs.py` | 成本路由表(owner 管控,唯一来源) |
| `src/webapp/` | FastAPI 看板(总览/回测/前向/探索/ops) |
| `scripts/` | 流水线与实验脚本;**跑过的实验脚本冻结不改**(保复现) |
| `analysis/` | 每轮实验报告(p0 → p3),结论以此为准 |
| `docs/learnings/` | 事故与反直觉结论笔记 |
| `docs/archive/` | 已被取代的历史文档(只增不删) |
| `models/` | 冻结工件、ACTIVE 指针、owner_best 检测权重、yolo11* 冷启动基座 |
````

### `.`

````markdown
# 对照组终判：检测器的边 ≈ 成本，而金标本身没有一个盘口样本 — 2026-07-28

> 触发：owner「从头到尾分析一下这个项目，到目前为止还没弄好我想要的模型和交易系统」。
> 本轮做的是**全程审计 + 四个新测量**。全部离线、只读，**未动 holdout、未 promote、未改 ACTIVE、未下单**。
> 新测量的共同点：项目此前所有评估都是"池 vs 池"或"档 vs 全池"，
> **从未在主线池上做过匹配对照组**。这一条解释了 21 天里反复出现的"样本内有效、样本外失效"。

---

## 0. 一句话

**检测器是真的有选择力（+8.97bp，t=5.71），但一笔往返要花 10bp——边和成本几乎完全相等。**
而 `judgment_yolo_owner_side_short_100_6m` 那个 +16.9bp 的净收益里，**2/3 是做空 beta，不是策略**。

---

## 1. 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/diag_matched_base_rate.py   # §3 §4
PYTHONPATH=. .venv/bin/python scripts/diag_gold_hindsight.py      # §5 §6
```

产物：`analysis/output/base_rate_random_short_atr.csv`（39,692 行随机对照）、
`analysis/output/gold_hindsight.csv`（499 个金标 tip 的障碍结果 + 可见未来根数）。

## 2. 数据统计

| 项 | 值 |
|---|---|
| 主线池 | `data/judgment_yolo_owner_side_short_100_6m.csv` |
| 候选数 / 币种 | 25,602 / 100 |
| 信号窗 | 2025-11-04 → 2026-05-03（**全部在 train 窗，未碰 holdout**） |
| 障碍 | TP 5×ATR14 / SL 2×ATR14 / 72 根超时 / 入场=下一根开盘 |
| 成本假设 | 10bp 往返（taker 0.05%/边，owner 管控，本轮未改） |
| 随机对照 | 同 100 币、同窗、同障碍、每币随机 400 根 → 39,692 笔 |
| 金标 | 499 个 ⭐标杆 tip（234 币，2025-06 → 2026-06） |

---

## 3. 【新】主线池的 +26.9bp 毛收益，2/3 是 beta

之前所有报告读的都是池子的绝对收益。加一个对照组——**同币、同月、同 ATR 桶的随机做空**——
唯一的差别就只剩"这一根是不是检测器挑的"：

| | n | 毛 | 扣 10bp 净 | 胜率 |
|---|---:|---:|---:|---:|
| v9/tip_v1b 候选 | 25,602 | **+26.91bp** | +16.91bp | 36.55% |
| 随机做空（同币同窗） | 29,756 | **+17.15bp** | +7.15bp | 34.27% |
| **检测器的净贡献** | | **+9.76bp** | | |

**这个窗口里，闭着眼睛做空这 100 个币，本身就赚 +17.15bp。**
2025-11 → 2026-05 是山寨币下行窗，做空侧带正 drift。

### 逐月：beta 和选择力是两回事

| 月 | 检测器 bp | 随机 bp | 选择力 bp |
|---|---:|---:|---:|
| 2025-11 | -8.65 | +20.46 | **-29.11** |
| 2025-12 | +36.38 | +11.38 | +25.00 |
| 2026-01 | +36.35 | +19.15 | +17.20 |
| 2026-02 | +57.00 | +45.07 | +11.93 |
| 2026-03 | +26.80 | +12.83 | +13.98 |
| 2026-04 | +15.63 | -0.89 | +16.52 |
| 2026-05 | -17.04 | -20.15 | +3.10 |

## 4. 【新】ATR 匹配后：选择力 = +8.97bp，成本 = 10bp

上表的对照只匹配了币和月。检测器偏好高波动 bar，而高波动 bar 的障碍更宽、绝对收益更大，
所以还要**匹配 ATR 桶**才干净：

```
=== ATR 匹配 + 月匹配 ===
n = 25,602
excess = +8.97 bp   se=1.57   t=+5.71   扣 10bp 成本后 = -1.03 bp
```

| atr 五分位 | n | 检测器 bp | 匹配随机 bp | 选择力 bp | t | 扣成本 |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 5121 | 16.85 | 13.20 | +3.65 | 2.38 | -6.35 |
| 1 | 5120 | 18.75 | 12.84 | +5.91 | 2.72 | -4.09 |
| 2 | 5120 | 24.49 | 16.43 | +8.05 | 2.97 | -1.95 |
| 3 | 5120 | 29.88 | 11.18 | **+18.70** | 5.48 | **+8.70** |
| 4 | 5121 | 44.56 | 36.05 | +8.51 | 1.43 | -1.49 |

**读数**：

1. **信号是真的**。t=5.71，n=25,602，这不是噪声，也不是 beta——匹配对照组已经把 beta 差掉了。
2. **但它值 9bp，而下单要花 10bp。** 到三位有效数字，这套系统是恰好打平，算上滑点是亏的。
3. 未匹配时最好的是 q4（+19.86bp），匹配后最好的变成 q3（+18.70bp）——
   **桶一换排名就换，这是过拟合的签名**，不要拿 q3 去建仓位规则。

### 这解释了 21 天里所有的"样本外失效"

判断层要把系统救活，必须从这 9bp 里筛出一个 **≥30bp 的子集**（覆盖成本还要有富余）。
也就是要求筛选层做到平均值的 3 倍以上集中度。
五类攻法全部失败，不是因为方法不对，是因为**被要求做一件超出这个池信息量的事**。

---

## 5. 【新，最重要】金标本身，99.6% 是带着未来画的

`PROJECT_FULL_REPORT` 的卡点三写着「检测精度与赚钱的关系未被证明」。
这个问题不需要检测器就能答：把 owner 自己画的 ⭐标杆 tip 直接按生产障碍做空。

### 先说被污染的那版（不要用）

只取 `star_side()` 判为空头的 319 个：**+266.37bp，胜率 79.3%**。
**这个数字是假的**——`star_side()` 从 tip 往后扫 24 根，
要求"收盘跌破均线束下轨 且 8 根跌幅 ≥1 ATR"才算空头。
**它是"已经知道跌了"的筛选**，从这些点做空当然赚。同一份数据里，
被它判为多头的 177 个做空是 **-123.39bp，胜率 5.1%**——一个筛选切出了 ±2 倍的分离，这就是前视的形状。

### 去掉前视筛选后

全部 499 个 tip 一律做空，不看后来：

| | n | 毛 | 胜率 | t |
|---|---:|---:|---:|---:|
| 全部 ⭐标杆 tip | 499 | **+126.69bp** | 52.7% | +8.61 |
| 对照：同月同 ATR 桶随机 | | | | |
| **超额** | 266 | **+106.62bp** | | **+4.82** |

**owner 的眼睛，超额 +107bp。v9 检测器，超额 +9bp。差 12 倍。**

### 但是 —— 这 499 个标注是怎么画出来的

| 项 | 值 |
|---|---|
| 框右缘之后**还能看到的未来根数** | p10=39 · **p50=97** · p90=159 · max=192 |
| 框右缘在窗口中的位置 | p50 = **0.513** |
| 画在盘口（未来 ≤2 根）的标注 | **2 个 / 499 = 0.4%** |
| 画的时候 72 根交易周期**已经全部可见** | **336 个 = 67.3%** |

**中位数的那个标注，owner 在画的时候右边还摊着 97 根（24 小时）的后续走势。**
67% 的标注，整个持仓周期在画框时就已经在屏幕上了。

按可见未来分组：

| 可见未来 | n | 毛 bp | 超额 bp | t |
|---|---:|---:|---:|---:|
| 盘口 0–2 根 | **2** | — | — | — |
| 3–24 根 | 22 | +58.30 | +123.57 | +1.80 |
| 25–72 根 | 147 | +96.90 | +84.95 | +1.95 |
| >72 根 | 328 | +146.39 | +119.57 | +4.39 |

corr(可见未来根数, 收益) = **+0.040**。

**诚实读法**：相关系数只有 0.04，超额在各组之间是平的（124 / 85 / 120），
所以**这份数据并不能证明 +107bp 是前视造成的**。
但它同样**不能证明不是**——因为盘口组只有 2 个样本。
**这个项目没有一个可以用来判定的盘口标注。**

---

## 6. 把三件事接起来

这三条一直被当成独立问题，其实是同一件事：

1. **owner 看 30 张 live 图说"这不是我的形态"**（今天）
2. **v9 的开火密度是 owner 标注密度的 422 倍**（昨天 `diag_v9_precision_vs_recall`）
3. **v9 的因果选择力只有 9bp ≈ 成本**（本轮）

检测器被要求复现一个**可能部分建立在"后来跌了"之上**的目标。
盘口那一刻的图里如果本来就没有这个信息，模型学不到它，
于是退化成"看到密集就开火"——密度爆炸和 9bp 是同一个事实的两面。

**v9 修的是框画在哪（右缘偏移 0 根），没有修 owner 当初为什么在那里画框。**

### 决定性实验早就准备好了，但一个都没标

```
datasets/label_live_tip_1000/   1000 张盘口窗（右缘=tip，无后文）
                                manifest: "PNGs have no boxes; labels empty"
                                实测：1000 个 label 文件，非空 0 个，总字节 0
```

**这 1000 张图是为了回答"owner 看不到未来时还认不认得出来"而准备的，从未开标。**

---

## 7. 结果表：与上一版本对照

| 指标 | 之前报告的 | 本轮匹配对照后 |
|---|---|---|
| 100×6m 池单笔净 | +16.91bp（"池子有真实的边"） | +16.91bp，其中 **beta +7.15 / 选择力 +9.76** |
| 检测器价值 | v9 召回 84%（conf 0.05；生产 conf 0.30 下 19.5%） | 因果超额 **+8.97bp**，t=5.71 |
| 扣成本后 | 正 | **-1.03bp** |
| owner 金标价值 | AUC 0.667（对交易结果） | 超额 +107bp，但 **99.6% 带未来画的，不可判定** |
| 判断层任务难度 | "顶档要比全池高 40bp 才能被证实" | 要从 9bp 里筛出 ≥30bp = **3 倍集中度** |

## 8. 解读

- **不是"没有 alpha"**。t=5.71、n=25,602、beta 已差掉——这是真信号，量级 9bp。
- **是"alpha 的量级正好等于摩擦"**。这类系统只有两条出路：把成本压到 alpha 之下，
  或者去找单笔幅度大得多的机会（固定 10bp 在 30bp 的边上是灾难，在 300bp 的边上无所谓）。
- **判断层不是做错了，是被指派了一个不可能的任务**。9bp 的池要筛出 30bp 的档。
- **21 天里没有一次评估带对照组**。成功标准写的是"top-decile 扣成本净收益为正 + 置换 p<0.01"，
  这两个都是**池内**判据——beta 在池内是常数，永远不会被置换检验抓到。
  这是纪律里唯一的漏洞，也是最贵的一个。

## 9. 风险与诚实声明

1. **全部是 train 窗样本内（2025-11-04 ~ 2026-05-03），未动 holdout。** holdout 已消耗 9 次。
2. **随机对照的 72 根持仓期彼此可能重叠**（每币 400 根抽自约 17,280 根），
   这会低估对照组均值的标准误，因此 §3/§4 的 t 值**偏乐观**。结论方向不受影响（差值 9bp vs 成本 10bp
   不依赖精确的 t），但不要引用这些 t 值做功效计算。
3. **金标对照只匹配了月 + ATR 桶，没匹配币种**——金标来自 234 币，对照池只有 100 币，
   499 个 tip 里只有 266 个落在对照覆盖的格子里。
4. **§5 不能判定金标是否前视**。盘口样本 n=2。这是一个"未知"，不是一个"已证伪"。
   任何往前视方向的强断言（包括我上面第 6 节的因果串联）目前都只是**最省事的解释**，不是证据。
5. **10bp 成本不含滑点**。executor ledger 至今 **0 笔完整往返**
   （10 行：1 笔部分成交、6 次保证金不足失败、3 次 kill 开关暂停），所以滑点仍是 0 个观测。
6. 本轮**未**改任何阈值、障碍、成本假设、ACTIVE、新鲜度门，**未**下单。

## 10. 下一步选项（全部需 owner 决策）

| # | 选项 | 成本 | 能回答什么 |
|---|---|---|---|
| **A** | **标 100 张盘口图**（`datasets/label_live_tip_1000` 已就绪，抽 100 张即可） | owner 约 1–2 小时 | **唯一能判定金标是否前视的实验。** 若盘口标注仍有超额 → 命题活，检测器要重训；若没有 → 21 天的检测线索到此为止，省下后面全部工作 |
| B | 成本工程：止盈腿改 maker（+1.09bp，零漏单）+ 入场限价（+2.4bp，15% 漏单） | 已测完，改执行器 | 把 10bp 压到 ~6.5bp → 9bp 的边变成 +2.5bp 净。**唯一不需要新 alpha 的改进**，但结果仍是极薄 |
| C | 换命题：找单笔幅度 ≥100bp 的机会（更长周期 / 更大目标） | 重做 | 固定摩擦下唯一的结构性出路 |
| D | 收摊检测线，只保留数据管道 + 纪律 | 0 | 承认 9bp ≈ 10bp 这个算术 |

**我的建议：A 优先，且在 A 出结果之前不要再动检测器、判断层、holdout。**
A 是全项目唯一一个「几小时的人工 + 零算力」就能改变结论方向的实验，
而 B/C/D 都建立在「金标到底是不是可因果学习的」这个未知之上。

**明确不建议**：消耗 holdout #10（`diag_holdout_power` 已算清，当前效应量在 holdout 分辨能力之下）；
继续扩候选池（扩样解决的是分辨率，不解决 9bp<10bp）；再训一版检测器（目标本身待验证）。
````

### `.`

````markdown
# 系统架构（2026-07-20 刷新）

> **实时状态只看 [`HANDOFF.md`](../HANDOFF.md) 顶部。**  
> 下文描述**当前运行架构**；文末保留 07-09 / 07-16 历史图供对照。

一句话：**YOLO 检测候选 → LightGBM 回归排序 → 冻结阈值进前向 → VPS 执行器下单**；  
确认级只认 **100 笔新鲜前向**（事后/迟到检出剔除），不认 val / accept 再扫。

## 总览图（现行）

```
┌─────────────────────────── 数据层 ───────────────────────────┐
│ OKX 公共 API                                                 │
│  fetch_okx.py（全量/断点）  update_okx.py（脉冲内增量）         │
│  **VPS 是 K 线唯一写者**（本机不写 kline_fetched 上生产）        │
│  loader.py 合并去重 + BLOCKED / stockish 过滤                  │
└───────────────────────────┬─────────────────────────────────┘
                            │ 15m USDT-SWAP OHLCV（~344+ 币）
              ┌─────────────┴──────────────┐
              ▼                            ▼
┌── 2a 检测层（主线候选源）────┐  ┌── 规则扫描（回滚旁路）────┐
│ render 200-bar 窗            │  │ candidates.py EMA 8-55   │
│ SMA/EMA 20/60/120 画图       │  │ strict/expanded 预设     │
│ YOLO11 owner_best.pt         │  │ CANDIDATE_SOURCE=rules   │
│ live: tip + 近端 6 窗        │  │ 仅回滚/对照用             │
│ tip 盘口 bar 当场入账        │  └──────────────────────────┘
└──────────────┬───────────────┘
               ▼
┌── 2b 判断层 ───────────────────────────────────────────────┐
│ features 无前视 → LightGBM **回归** predicted_realized_ret   │
│ 冻结: frozen_tp5_sl2_swap_yolo_v11_reg_20260718              │
│ 阈值 val-q90（当前 ≈0.02022）；池 judgment_yolo_swap_v11     │
│ frozen.py::default_config() = 唯一咽喉                       │
└──────────────┬─────────────────────────────────────────────┘
               ▼
┌── 出场 / 前向 ─────────────────────────────────────────────┐
│ 主线出场 TP5/SL2 · horizon 72 · 72-bar 超时                 │
│ forward_track 脉冲（15m 收盘后对齐）→ data/forward_log.csv  │
│ 新鲜度三门 30min 同值（执行器 / TG / 看板 FRESH_DETECT_MIN） │
│ 裁决：100 笔 maker-filled closed · 事后检出不计入            │
└──────────────┬─────────────────────────────────────────────┘
               ▼
┌── 执行层（VPS）────────────┐    ┌── 观测 ──────────────────┐
│ src/execution/             │    │ webapp :8642 看板         │
│ fable-executor（市价+OCO）  │    │ TG 信号（仅 open+新鲜）   │
│ fable-forward.timer 15min  │    │ live_health 30min 告警    │
│ ENABLE_JOB_EXECUTOR=0      │    │ analysis/p*_report.md     │
└────────────────────────────┘    └──────────────────────────┘

旁路（不接主 executor）:
  · H1 scaled shadow 日志
  · H-TIP v12 训练中（owner_v12_htip）— 不自动 promote
  · src/short_tf/ 1m/5m 规则 tip
```

## 模块地图

| 路径 | 职责 | 关键约束 |
|---|---|---|
| `src/data/fetch_okx.py` | 全量历史 | 浏览器 UA；≤8 req/s；断点续传 |
| `src/data/update_okx.py` | 脉冲/日增量 | 幂等；VPS 主写 |
| `src/data/loader.py` | 合并去重 | BLOCKED；断链软链跳过 |
| `src/detection/*` | 渲染 / YOLO / owner 评估 | 增强全关；FINETUNE_OPT lr=1e-4；尺子 MANIFEST |
| `src/judgment/candidates.py` | 规则候选（旁路） | 阈值预设 owner 资产 |
| `src/judgment/labeling.py` | 障碍标签 | entry=次根开盘；无前视 |
| `src/judgment/features.py` | 特征 | 信号 bar 及之前 |
| `src/judgment/train.py` | 训练 | purge；holdout 仅 `--eval-holdout` |
| `src/judgment/frozen.py` | 冻结默认配置 | **唯一主线咽喉** |
| `src/judgment/forward.py` | 前向扫描/合并 | tip 实时路径；幂等键；shadow 隔离 |
| `src/execution/*` | 实盘下单 | 新鲜度门；ledger 防重；secrets 不入 git |
| `src/backtest/*` | accept/组合回测 | holdout 窗口消耗记账 |
| `src/costs.py` | 成本路由表 | owner 管控唯一来源 |
| `src/webapp/*` | 看板 | 只读产物；ops executor 默认关 |
| `src/short_tf/*` | 短周期支线 | 独立日志，不接主 executor |
| `scripts/deploy_vps.sh` | 部署 | 不推 data/kline；executor 强制 0 |
| `scripts/build_htip_dataset.py` | H-TIP tip 重渲克隆 | train-only；不自动 promote |
| `scripts/promote_owner_best.py` | 检测权重晋升 | 泄漏门 + 标杆门 |

## 均线定义（现行裁决）

| 层 | 均线 | 说明 |
|---|---|---|
| **检测渲染 / 视觉** | SMA/EMA **20/60/120** | 与 owner 打标图一致；live YOLO 主线候选源 |
| **规则扫描旁路** | EMA **8/13/21/34/55** +144/200 | 规则时代主线；现仅回滚 |
| **判断特征** | 特征表含 spread/order 等（由候选 bar 导出） | 不在候选源上再套一套「密级闸门」 |

历史「两层均线不一致」在 **YOLO 已是候选主源** 后变为：检测看 20/60/120 图，规则 8-55 只作旁路。  
P0-3 曾在合约上对比过 8-55 vs 20/60/120 的**判断经济性**；切 YOLO 主线后以 **owner 视觉一致性** 优先。

## 数据资产与产物

| 路径 | 入 git? | 内容 |
|---|---|---|
| `data/kline_fetched/` | 否 | 15m 序列；VPS 写 |
| `data/forward_log.csv` | 否 | 主线前向裁决账本 |
| `data/forward_log_*.csv` | 否 | shadow / 归档（禁混入 0/100） |
| `data/judgment_yolo_swap_v11.csv` 等 | 否 | 判断池 |
| `models/ACTIVE` + `frozen_*` + `owner_best.pt` | 部分 | 冻结与晋升指针 |
| `datasets/owner_eval_frozen/` | 部分 | 检测冻结尺子 MANIFEST |
| `analysis/p*_report.md` + `output/` | 是 | 实验结论 |
| `docs/learnings/` | 是 | 事故/反直觉 |
| `runs/` `datasets/dense_*` | 否 | YOLO 训练 |

## 部署拓扑（2026-07-20）

```
MacBook                              VPS (Debian)
├─ 开发 / 部分 YOLO 训练(v12)         ├─ /opt/fable-trading
├─ Label Studio / 打标                ├─ fable-dashboard :8642
├─ golden_pool / promote / 评测       ├─ fable-forward.timer（15m 脉冲）
└─ git push → GitHub                  ├─ fable-executor（live keys）
                                      ├─ K 线唯一写者 + forward_log 写者
                                      └─ ENABLE_JOB_EXECUTOR=0
可选: 局域网 3060 训 YOLO（见 memory/training-on-3060）
```

## 全局不变量

1. 时间切分 + purge；特征无前视  
2. holdout / accept 消耗次数记账（见 HANDOFF）  
3. 成功指标 = 扣成本净收益 + 显著性；确认级 = 前向 100 笔新鲜  
4. 实验加法优先；跑过的实验脚本冻结不改  
5. 结论进 report / learnings；没写 = 没做  
6. 实盘：新鲜度三门同值、脉冲 <15min、不自动 promote（见 `CLAUDE.md` 实盘纪律）

## 图解

- LightGBM 流水线：![](diagrams/lightgbm_pipeline.svg)  
- triple-barrier：![](diagrams/triple_barrier.svg)

---

## 历史附录 A — 2026-07-09 架构图（规则主线时代）

当时一句话是「规则扫描 → ML 排序 → 回测 → 前向」，YOLO 为旁路。  
总览 ASCII 与「检测 20/60/120 vs 判断 8-55」讨论以 git 历史为准；**已不代表现行主线**。

## 历史附录 B — 2026-07-16 现状补记（v8/v9 池时代）

```
K线 → YOLO owner_best → LGBM 回归 v8 池 → TP5/SL2 → forward / 看板
```

治理设施（仍有效）：

| 设施 | 位置 |
|---|---|
| 冻结尺子清单 | `datasets/owner_eval_frozen/MANIFEST.json` |
| eval 唯一实现 | `src/detection/owner_eval.py` |
| 续训 lr | `FINETUNE_OPT`（禁 optimizer=auto） |
| 标杆门 | `scripts/benchmark_check.py` |
| 成本表 | `src/costs.py` |
| promote 泄漏门 | `scripts/promote_owner_best.py` |

v11 切流（07-18）与实时 tip 路径（07-20）见 HANDOFF 顶部。
````

### `.`

````markdown
# 文档地图（2026-07-22）

**唯一实时状态**：仓库根目录 [`HANDOFF.md`](../HANDOFF.md) 顶部。  
**本周执行**：[`analysis/week_plan_20260720.md`](../analysis/week_plan_20260720.md)。  
**夜间旁路纪要**：[`analysis/p_overnight_20260722.md`](../analysis/p_overnight_20260722.md)。

## 活文档（会随阶段改）

| 文件 | 角色 |
|---|---|
| `HANDOFF.md` | 当前真相、holdout 账本、进行中 |
| `CLAUDE.md` / `AGENTS.md` | 铁律 + 实盘纪律（两文件保持同步,由 pre-commit 钩子强制;新克隆需 `git config core.hooksPath scripts/hooks`） |
| `README.md` | 动机/架构/怎么跑（不堆日报） |
| `docs/ARCHITECTURE.md` | 现行系统图与模块地图 |
| `docs/RESEARCH_AGENDA.md` | 假设状态表 + 优先队列（含 H-FE / H-TOOL） |
| `docs/RESEARCH_AGENDA_DETECT.md` | 检测层 H-DET 子簇（tip/pad200/渲染）；汇总见 `analysis/p_yolo_dense_hypotheses.md` |
| `docs/DENSE_CLUSTER_DEFINITION.md` | 形态视觉定义（标杆） |
| `docs/LOCAL_DEBUG_TOOLS.md` | 本机 nvitop/netron/LWC·叠框命令（不抢 MPS） |
| `docs/EXEC_PROTECTIONS_SPEC.md` | Freqtrade Protections→executor 规格（不引 GPL） |
| `docs/ops/VPS_OBSERVABILITY_PENDING.md` | Kuma/Grafana 等 **待 Owner 批** |
| `analysis/week_plan_*.md` | 当周执行计划 |
| `analysis/INDEX.md` | **全部 analysis 报告的一行索引（动手前先搜这里）**；自动生成,重跑 `scripts/gen_analysis_index.py` |
| `analysis/p*_report.md` | 单次实验记录（只增不改结论） |
| `analysis/p_wuzao_topics_scan.md` | wuzao 全站可迁移清单（A/B/C/D） |
| `analysis/backlog_future_optimizations.md` | tip 通后再拧的积木 |
| `docs/learnings/*` | 事故/反直觉（只增） |

## 历史 / 已合入 / 只读

| 文件 | 说明 |
|---|---|
| `PROJECT_PLAN.md` | 07-07 三阶段路线图；顶注已标「阶段完成→实盘」 |
| `docs/archive/*` | NEXT_STEPS / PROJECT_STATUS 等已并入 HANDOFF |
| `docs/archive/FORWARD_ACCELERATION_OPTIONS.md` | 07-10 加速 N 备忘（已移档） |
| `docs/archive/H1_SCALED_FORWARD_SHADOW_PLAN.md` | H1 shadow 设计；已实现、非主线（已移档） |
| `scripts/_archive_pretip/` | pre-v16 训练/评测/打包脚本（铁律 12 清除件，仅复现追溯） |
| `docs/OWNER_LABELING_PLAYBOOK.md` | 打标流程；当前阻塞是 H-TIP 非堆轮次 |
| `docs/P2_5_*` | Ops 台 Phase0–3 说明；已合主线 |
| `docs/LABEL_REVIEW_TOOLS.md` | FO/LS 审查工具 |
| `output/offline_tasks/*` | 多日无人值守快照；数字会旧 |
| `analysis/p*.md`（非当周） | 实验报告；**勿改历史结论** |

## 不要做的文档维护

- 不要平行维护第二份「当前状态」  
- 不要改旧 `p*_report` 的结论数字去「对齐现状」  
- 改纪律时 **CLAUDE.md 与 AGENTS.md 必须同改**
````

### `.`

````markdown
# 池内判据看不见 beta：置换检验永远抓不到"这个窗口做空本来就赚"

- **问题**：`judgment_yolo_owner_side_short_100_6m`（25,602 笔）单笔净 +16.91bp、
  t=10.77，被读成"检测器的候选带真实的边"。同一个池上判断层怎么调都提纯不动，
  五类攻法全部"样本内有效、样本外失效"，21 天没解开。
- **死胡同**：全部往下游找原因——判断层反选、同源分布、障碍参数、样本量 MDE、
  扩池到 3 万行。每一条都测得很干净，每一条都没改变结局。
  因为**它们全都在池子内部比较**：top-decile vs 全池、这一档 vs 那一档。
  项目的成功标准写的是"top-decile 扣成本净收益为正 + 置换检验 p<0.01"——
  **两个都是池内判据**。beta 在池内是一个常数，打乱标签重训一万次也不会动它。
- **有效路径**：加匹配对照组——**同币、同月、同 ATR 桶的随机做空**，同一套障碍、
  同一套成本。唯一的差别只剩"这一根是不是检测器挑的"。
  一跑就分开了：池子 +26.91bp 里，**随机做空自己就有 +17.15bp**（2025-11~2026-05
  是山寨下行窗），检测器的因果贡献只有 **+8.97bp**（ATR 匹配后，t=5.71）。
  往返成本 10bp。**边和摩擦到三位有效数字相等。**
- **通用规则**：**方向性策略的每一张结果表都必须带一行匹配随机对照。**
  匹配维度至少是 币 × 时间块 × 波动桶——只匹配前两个会把"检测器偏好高波动 bar、
  高波动 bar 障碍更宽"算进 alpha（本例中未匹配 ATR 时最好的是 q4，匹配后变成 q3，
  桶一换排名就换）。
  推论：**置换检验不能替代对照组**。置换打乱的是"哪一行是哪一行"，
  验证的是"排序有没有信息"；它对"整个池子踩在一个方向性 drift 上"完全无感。
  这两个检验回答不同的问题，必须都做。
- **牵连**：`scripts/diag_matched_base_rate.py`（对照组生成 + ATR/月匹配），
  `analysis/p_20260728_matched_control_verdict.md`，
  CLAUDE.md「质量标准 · 必报指标」已加对照组一条。
  相关：[单调扫到边界要加对照组](monotone-sweep-to-the-edge-needs-a-control-arm.md)
  （同一个病：没有对照组时，判读模板会替你把结论说反）、
  [长短必须在 base rate 表里分开](long-short-must-be-split-in-base-rate-tables.md)。
````

### `.`

````markdown
# 499 个金标里只有 2 个画在盘口，所以"目标能不能被因果学习"至今是未知

- **问题**：13 轮 YOLO 训练都在追一个目标——复现 owner 手画的 ⭐标杆框。
  v9 把召回做到 84%（conf 0.05；生产 conf 0.30 下是 19.5%），
  然后 owner 看 live 图说"这不是我的形态"，且开火密度是自己标注密度的 422 倍。
- **死胡同**：把它当检测器质量问题。六个版本全在调**框画在哪**——
  框宽比、右缘锚点、IoU、密集阈值。v9 把右缘偏移修到 0 根、宽度比修到 1.00，
  指标全绿，owner 一看还是不对。**修的是框的几何，不是 owner 当初为什么在那里画框。**
- **有效路径**：不测检测器，测标注本身。对每个 ⭐标杆算一个数：
  **框右缘之后，标注图里还剩多少根未来**。
  ```
  可见未来根数  p10=39  p50=97  p90=159  max=192
  框右缘位置    p50 = 0.513（窗口中点）
  画在盘口(≤2 根未来)的：2 / 499 = 0.4%
  画框时 72 根持仓周期已全部可见的：336 = 67.3%
  ```
  **中位数的标注，画的时候右边摊着 24 小时的后续走势。**
  把这些 tip 直接按生产障碍做空：超额 +107bp（对照：同月同 ATR 桶随机），
  而 v9 的因果超额只有 +9bp——**12 倍差距**。
- **关键判断**：**不能因此断言金标是前视的。** 按可见未来分组，
  超额是平的（124 / 85 / 120 bp），corr(可见未来, 收益) 只有 +0.040。
  但也**不能断言不是**——盘口组 n=2。
  正确的结论是"**不可判定**"，而不可判定本身就是最重要的发现：
  **整条检测线 21 天建立在一个从未验证过可因果学习的目标上。**
  且决定性实验早就备好了：`datasets/label_live_tip_1000/`
  （1000 张右缘=tip、无后文的盘口窗），manifest 写着 "labels empty" ——
  实测 1000 个 label 文件非空 0 个、总字节 0，**从未开标**。
- **通用规则**：**任何以人工标注为训练目标的项目，第一件事是量"标注时可见多少未来"，
  不是量模型学得多像。** 这个数只要不是 0，"模型学不会"和"信息不在因果窗里"
  就无法区分，而这两个的处置完全相反（前者继续训，后者立刻停）。
  推论：金标集必须留一个**盘口子集**（画框时右缘=最后一根）作为判定用的对照，
  哪怕只有 100 张。没有这个子集，检测器的召回率是不可解释的数字。
- **牵连**：`scripts/diag_gold_hindsight.py`，
  `analysis/p_20260728_matched_control_verdict.md` §5，
  `datasets/label_live_tip_1000/`（已就绪未标），
  `scripts/build_star_tip_dataset_v9.py::star_side`（另一层前视：往后扫 24 根定方向，
  被它判空的 319 个做空 +266bp、判多的 177 个做空 -123bp——±2 倍分离就是前视的形状）。
  相关：[owner 标框的 oracle 增量不是盘口 tip 的因果 alpha](owner-label-oracle-alpha-is-not-causal-tip-alpha.md)
  （同一现象的早期定性版，本条是量化版）、
  [中段金标右缘对齐不是可标注的 tip](mid-gold-right-align-is-not-labelable-tip.md)、
  [池内判据看不见 beta](pool-internal-metrics-cannot-see-beta.md)。
````

### `.`

````python
"""Was the owner looking at the future when they drew these boxes?

Each star label was drawn on a 200-bar rendered window. If the box's right edge
sits at bar 150 of 200, the owner could see 50 bars of what happened next while
deciding whether to mark it. That would make the +126bp of gold_unfiltered.py
hindsight rather than eye, and would make the target unlearnable by any detector
that only sees up to the tip.

Splits the same 499 tips by how much future was visible in the labelling image.
"""
from __future__ import annotations

import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
sys.path.insert(0, "/Users/zhangzc/fable-trading/scripts")

import cv2  # noqa: E402

from diag_v9_precision_vs_recall import (  # noqa: E402
    WINDOW,
    add_mas,
    archive_index,
    boxes_cut_and_spans,
    load_star_boxes,
    make_chart_transform,
    resolve_series,
    resolve_win_start,
    symbol_of,
)
from src.data.loader import list_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import HORIZON_BARS, label_short_candidate  # noqa: E402

OUTDIR = "analysis/output/"

known = {s for (_x, s) in list_series(bar="15m")}
arch = archive_index()
stars = load_star_boxes()

rows = []
for stem, boxes in stars.items():
    sym = symbol_of(stem, known)
    if sym is None:
        continue
    base = resolve_series(sym)
    if base is None:
        continue
    framed = add_mas(base)
    m = re.search(r"_(\d+)$", stem)
    if not m:
        continue
    stored = cv2.imread(str(arch[stem])) if stem in arch else None
    r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed, stored_img=stored)
    if r is None:
        continue
    _mo, ws, _mad = r
    sub_old = framed.iloc[ws : ws + WINDOW].reset_index(drop=True)
    if len(sub_old) != WINDOW:
        continue
    _c, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub_old))
    if not spans:
        continue
    _b0, b1, _ph, _pl = spans[0]
    tip = ws + b1
    if tip < WINDOW or tip + 1 + HORIZON_BARS >= len(framed):
        continue
    enr = add_indicators(framed)
    o = label_short_candidate(enr, int(tip), tp_mult=5.0, sl_mult=2.0)
    if o is None:
        continue
    ts = pd.Timestamp(enr["open_time"].iloc[int(tip)])
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    rows.append(
        {
            "symbol": sym,
            "t": ts,
            "short_ret": o.realized_ret,
            "atr_pct": float(enr["atr_pct"].iloc[int(tip)]),
            "b1": int(b1),
            "right_frac": b1 / (WINDOW - 1),
            "future_bars_visible": (WINDOW - 1) - int(b1),
        }
    )

g = pd.DataFrame(rows)
g.to_csv(OUTDIR + "gold_hindsight.csv", index=False)
cost = 0.001

print(f"n = {len(g)}\n")
print("=== how much future was visible in the labelling image ===")
print(f"future bars right of the box: p10={np.percentile(g.future_bars_visible,10):.0f}  "
      f"p50={np.percentile(g.future_bars_visible,50):.0f}  "
      f"p90={np.percentile(g.future_bars_visible,90):.0f}  max={g.future_bars_visible.max():.0f}")
print(f"box right edge as fraction of window: p50={g.right_frac.median():.3f}")
print(f"tips drawn at the live edge (<=2 future bars): {(g.future_bars_visible<=2).sum()} "
      f"of {len(g)} = {(g.future_bars_visible<=2).mean()*100:.1f}%")
print(f"the 72-bar barrier horizon was fully visible for: "
      f"{(g.future_bars_visible>=72).sum()} = {(g.future_bars_visible>=72).mean()*100:.1f}%\n")


def show(x: pd.Series, name: str) -> None:
    if len(x) < 8:
        print(f"{name:46s} n={len(x):5d}  (too few)")
        return
    se = x.std() / np.sqrt(len(x))
    print(f"{name:46s} n={len(x):5d}  gross={x.mean()*10000:+8.2f} bp  "
          f"net={(x.mean()-cost)*10000:+8.2f} bp  win={(x>0).mean()*100:5.1f}%  t={x.mean()/se:+6.2f}")


print("=== short return, split by how much future the owner could see ===")
bins = [(-1, 2), (2, 24), (24, 72), (72, 999)]
names = ["live edge      (0-2 future bars)", "some future    (3-24)",
         "much future    (25-72)", "whole horizon  (>72)"]
for (lo, hi), nm in zip(bins, names):
    show(g.short_ret[(g.future_bars_visible > lo) & (g.future_bars_visible <= hi)], nm)

print()
print("correlation(future bars visible, short return) = "
      f"{np.corrcoef(g.future_bars_visible, g.short_ret)[0,1]:+.3f}")

# Matched control, live-edge subset only.
base = pd.read_csv(OUTDIR + "base_rate_random_short_atr.csv")
base["t"] = pd.to_datetime(base["t"], utc=True)
pool = pd.read_csv("/Users/zhangzc/fable-trading/data/judgment_yolo_owner_side_short_100_6m.csv")
edges = np.quantile(pool.atr_pct, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
edges[0], edges[-1] = -np.inf, np.inf
for d in (g, base):
    d["aq"] = pd.cut(d.atr_pct, edges, labels=False, include_lowest=True)
    d["cell"] = d["t"].dt.strftime("%Y-%m") + "|q" + d["aq"].astype(str)
b = base.groupby("cell")["realized_ret"].agg(rnd_n="count", rnd_m="mean")
mg = g.merge(b, left_on="cell", right_index=True, how="inner")
mg = mg[mg.rnd_n >= 20]
print("\n=== excess over matched random, by visibility ===")
for (lo, hi), nm in zip(bins, names):
    s = mg[(mg.future_bars_visible > lo) & (mg.future_bars_visible <= hi)]
    if len(s) < 8:
        print(f"{nm:46s} n={len(s):5d}  (too few)")
        continue
    ex = s.short_ret - s.rnd_m
    se = ex.std() / np.sqrt(len(ex))
    print(f"{nm:46s} n={len(s):5d}  excess={ex.mean()*10000:+8.2f} bp  "
          f"t={ex.mean()/se:+6.2f}  net={ex.mean()*10000-10:+8.2f} bp")
````

### `.`

````python
"""ATR-matched control: is the top-ATR quintile's excess detector skill, or just
"high-vol bars drift down more"? Compares detector fires against random bars in
the SAME month AND the SAME atr_pct bucket, so volatility cannot leak in."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
from src.data.loader import list_series, load_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import HORIZON_BARS, label_short_candidate  # noqa: E402

OUTDIR = "analysis/output/"
LO = pd.Timestamp("2025-11-04", tz="UTC")
HI = pd.Timestamp("2026-05-04", tz="UTC")
RNG = np.random.default_rng(7)

pool = pd.read_csv("/Users/zhangzc/fable-trading/data/judgment_yolo_owner_side_short_100_6m.csv")
pool["t"] = pd.to_datetime(pool["signal_time"], utc=True)
symbols = set(pool["symbol"].unique())

# Rebuild the random baseline, this time recording atr_pct so it can be matched.
series = {}
for (src, sym), paths in list_series().items():
    if sym in symbols:
        series.setdefault(sym, paths)

rows = []
for k, sym in enumerate(sorted(symbols), 1):
    if sym not in series:
        continue
    enr = add_indicators(load_series(series[sym]))
    ts = pd.to_datetime(enr["open_time"], utc=True)
    ok = np.where((ts >= LO) & (ts < HI))[0]
    ok = ok[ok + 1 + HORIZON_BARS < len(enr)]
    if len(ok) == 0:
        continue
    pick = RNG.choice(ok, size=min(400, len(ok)), replace=False)
    ap = enr["atr_pct"].to_numpy()
    for i in pick:
        o = label_short_candidate(enr, int(i), tp_mult=5.0, sl_mult=2.0)
        if o is None:
            continue
        rows.append({"symbol": sym, "t": ts.iloc[int(i)], "realized_ret": o.realized_ret,
                     "atr_pct": ap[int(i)]})
    if k % 25 == 0:
        print(f"  [{k}/{len(symbols)}] {len(rows)}", flush=True)

base = pd.DataFrame(rows)
base.to_csv(OUTDIR + "base_rate_random_short_atr.csv", index=False)
print(f"baseline rows={len(base)}\n")

# Shared ATR bucket edges, taken from the detector pool so buckets are comparable.
edges = np.quantile(pool.atr_pct, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
edges[0], edges[-1] = -np.inf, np.inf
for d in (pool, base):
    d["m"] = d["t"].dt.strftime("%Y-%m")
    d["aq"] = pd.cut(d.atr_pct, edges, labels=False, include_lowest=True)
    d["cell"] = d["m"] + "|q" + d["aq"].astype(str)

b = base.groupby("cell")["realized_ret"].agg(rnd_n="count", rnd_m="mean")
p = pool.merge(b, left_on="cell", right_index=True, how="inner")
p = p[p.rnd_n >= 30]
p["excess"] = p.realized_ret - p.rnd_m

print("=== ATR-matched, month-matched detector excess ===")
print(f"n = {len(p)}")
m, se = p.excess.mean(), p.excess.std() / np.sqrt(len(p))
print(f"excess = {m*10000:+.2f} bp   se={se*10000:.2f}   t={m/se:+.2f}   "
      f"net after 10bp cost = {m*10000-10:+.2f} bp\n")

g = p.groupby("aq").apply(
    lambda x: pd.Series({
        "n": len(x),
        "det_bp": x.realized_ret.mean() * 10000,
        "rand_bp": x.rnd_m.mean() * 10000,
        "excess_bp": x.excess.mean() * 10000,
        "t": x.excess.mean() / (x.excess.std() / np.sqrt(len(x))),
        "net_bp": x.excess.mean() * 10000 - 10,
    }),
    include_groups=False,
)
print("--- by atr_pct quintile (random bars matched on the same quintile) ---")
print(g.round(2).to_string())

print("\n--- same, by month, top quintile only ---")
top = p[p.aq == 4]
gm = top.groupby("m").apply(
    lambda x: pd.Series({
        "n": len(x),
        "excess_bp": x.excess.mean() * 10000,
        "net_bp": x.excess.mean() * 10000 - 10,
    }),
    include_groups=False,
)
print(gm.round(2).to_string())
````

### `.`

````bash
#!/usr/bin/env bash
# Periodic forward-clock tick for mainline gate (data/forward_log.csv).
#
# Prefer YOLO candidates (mainline). If ultralytics/torch missing, fall back to
# rules candidates so the clock still moves on lean VPS hosts.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG=logs/forward_pulse.log
exec >>"$LOG" 2>&1
echo "=== forward_pulse $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH=.

if ! "$PY" -c "import ultralytics" 2>/dev/null; then
  echo "ultralytics missing → FABLE_CANDIDATE_SOURCE=rules"
  export FABLE_CANDIDATE_SOURCE=rules
else
  export FABLE_CANDIDATE_SOURCE="${FABLE_CANDIDATE_SOURCE:-yolo}"
  echo "candidate_source=$FABLE_CANDIDATE_SOURCE"
fi

# Optional tip-only mainline (default unchanged = live 6-window).
#   FABLE_YOLO_MODE=tip          # pure tip window only
#   TIP_CONF=0.22                # tip-window conf floor (other live windows stay 0.30)
#   FABLE_YOLO_RIGHT_BIAS=1      # within min_gap prefer rightmost box
# Rollback: unset the three vars (or set FABLE_YOLO_MODE=live).
echo "yolo_mode=${FABLE_YOLO_MODE:-live} tip_conf=${TIP_CONF:-off} right_bias=${FABLE_YOLO_RIGHT_BIAS:-0}"

# Optional light kline refresh (skip if offline). SWAP-only: mainline universe;
# full-universe update is a separate daily job.
if [ "${SKIP_UPDATE_OKX:-0}" != "1" ]; then
  if [ -f scripts/../src/data/update_okx.py ] || [ -f src/data/update_okx.py ]; then
    echo "update_okx --swap-only --bar 15m"
    "$PY" -m src.data.update_okx --bar 15m --swap-only 2>&1 | tail -25 || echo "update_okx skipped/failed"
  fi
fi

echo "forward_track start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$PY" scripts/forward_track.py
echo "forward_log lines=$(wc -l < data/forward_log.csv 2>/dev/null || echo 0)"

# (v12 shadow removed 2026-07-23 — pre-v16 detectors are deleted per iron
# rule 12; no shadow may run a banned model.)

# Real-tip data engine (v17 training distribution). Light side-step: no YOLO,
# own budget, writes only data/real_tip_collect/. Never blocks the pulse.
if [ "${FABLE_COLLECT_REAL_TIPS:-1}" = "1" ]; then
  echo "real_tip_collect start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$PY" scripts/collect_real_tips_pulse.py 2>&1 | tail -3 || echo "real_tip_collect skipped/failed"
fi

# Immediately try to trade any fresh open rows — do not wait up to 30s for the
# executor loop. Failures here must never fail the pulse unit.
echo "executor --once (post-pulse)"
"$PY" -m src.execution --once 2>&1 | tail -5 || echo "executor once failed/skipped"

echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
````

### `.`

````python
"""Executor knobs (no secrets). Trading environment is in the keys file."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT / "data" / "executor_config.json"
# Relative paths so example JSON is portable across machines.
DEFAULT_KILL_PATH = "data/executor_KILL"
DEFAULT_LEDGER = "data/executor_ledger.jsonl"
DEFAULT_FORWARD_LOG = "data/forward_log.csv"

# Mainline barriers (freeze name tp5_sl2; HANDOFF TP5/SL2).
TP_ATR_MULT = 5.0
SL_ATR_MULT = 2.0


@dataclass
class ExecutorConfig:
    """Paper/live executor knobs (secrets stay in okx keys file).

    Sizing (owner 2026-07-17):
      sizing_mode=equity_times_leverage → target gross notional = equity * leverage
      e.g. 100U equity, leverage 3 → ~300U total notional budget (cross).
      Concurrent slots share the remaining budget (not 3x each).
      sizing_mode=fixed → always use notional_usdt per entry (legacy).
    """

    max_concurrent: int = 1
    notional_usdt: float = 20.0  # fixed mode, or floor/fallback when equity missing
    leverage: int = 3
    # equity_times_leverage | fixed
    sizing_mode: str = "equity_times_leverage"
    # min notional per entry (USDT); skip if remaining budget below this
    min_notional_usdt: float = 5.0
    max_consecutive_losses: int = 5
    # validated strategy exits at 72 bars (18h); live must too
    timeout_hours: float = 18.0
    # A forward row stays "open" for up to the 18h barrier horizon, but the edge
    # is the launch moment: refuse to open positions on signals older than this.
    # Arithmetic (2026-07-20 tip path): age counts from the signal bar OPEN, so
    # a tip detection is already 16 min old at the first possible pulse (:01/
    # :16/:31/:46) and the 344-symbol scan adds up to ~7 min before the log is
    # written. 30 = 15 (bar) + 7 (pulse+scan) + headroom; 20 would drop real
    # tip signals scanned late in the pulse, and the pre-tip pipeline could not
    # record ANYTHING younger than 31 min. Align with TG + dashboard verdict.
    max_signal_age_min: int = 30
    poll_seconds: int = 30
    # Retry OCO bracket this many times after market entry (0 = no retry).
    bracket_retries: int = 2
    bracket_retry_sleep_sec: float = 1.5
    td_mode: str = "cross"  # full cross margin
    kill_switch_file: str = DEFAULT_KILL_PATH
    forward_log: str = DEFAULT_FORWARD_LOG
    ledger: str = DEFAULT_LEDGER
    # Only take signals with score >= row threshold (already filtered in log)
    # and status in these sets:
    open_statuses: tuple[str, ...] = ("open", "pending")
    require_score_ge_threshold: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> "ExecutorConfig":
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text(encoding="utf-8"))
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in raw.items() if k in known}
        if "open_statuses" in kwargs and isinstance(kwargs["open_statuses"], list):
            kwargs["open_statuses"] = tuple(kwargs["open_statuses"])
        return cls(**kwargs)

    def save_example(self, path: Path | None = None) -> Path:
        p = Path(path) if path else DEFAULT_CONFIG_PATH.with_suffix(".example.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return p


def kill_switch_active(cfg: ExecutorConfig) -> bool:
    p = Path(cfg.kill_switch_file)
    if not p.is_absolute():
        p = PROJECT / p
    return p.exists()
````

### `.`

````python
"""Poll forward_log → place market + TP/SL bracket.

Hard rules:
- OkxDemoClient reads environment from keys file (demo|live).
- Kill switch file blocks new entries.
- Circuit breaker: consecutive closed losses pause new entries.
- Invalid TP/SL refuses entry (never leave a naked position).
"""
from __future__ import annotations

import math
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.execution.config import (
    SL_ATR_MULT,
    TP_ATR_MULT,
    ExecutorConfig,
    kill_switch_active,
)
from src.execution import ledger as led
from src.execution.okx_client import OkxDemoClient, OkxDemoError
from src.execution.symbols import round_price, size_for_notional, to_okx_inst_id


def signal_key(row: pd.Series) -> str:
    return f"{row.get('source','okx')}|{row.get('symbol')}|{row.get('signal_time')}|{row.get('score')}"


_NOTIFY_EVENTS = {
    "order_placed": "🟢 <b>实盘开仓</b>",
    "order_partial": "🟡 <b>开仓成功·括号失败</b>(需人工补止损!)",
    "order_failed": "🔴 <b>下单失败</b>",
    "skipped_invalid_barriers": "⚠️ <b>拒单</b>(止盈止损价不可用)",
    "timeout_close": "⏱ <b>超时平仓</b>(72bar 到期,按验证策略出场)",
    "timeout_close_failed": "🔴 <b>超时平仓失败</b>(需人工处理!)",
}


def _notify_event(ev: dict) -> None:
    """Push trade events to Telegram. Fire-and-forget: the trading loop must
    never stall or die because a notification did."""
    label = _NOTIFY_EVENTS.get(str(ev.get("event")))
    if label is None:
        return
    try:
        from src.notify import send

        parts = [label, f"品种: <b>{ev.get('inst_id') or ev.get('symbol')}</b>"]
        if ev.get("mark_px"):
            parts.append(f"价格: {ev['mark_px']}")
        if ev.get("tp_px") and ev.get("sl_px"):
            parts.append(f"止盈 {ev['tp_px']} / 止损 {ev['sl_px']}")
        if ev.get("sz"):
            parts.append(f"数量: {ev['sz']}  名义: {ev.get('notional_usdt', '?')}U")
        if ev.get("error"):
            parts.append(f"错误: {str(ev['error'])[:160]}")
        if ev.get("note"):
            parts.append(str(ev["note"])[:160])
        send("\n".join(parts))
    except Exception as exc:  # noqa: BLE001
        print(f"executor notify failed: {exc}")


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return Path(__file__).resolve().parents[2] / p


def load_actionable_signals(cfg: ExecutorConfig) -> pd.DataFrame:
    path = _resolve(cfg.forward_log)
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "status" in df.columns:
        df = df[df["status"].astype(str).isin(cfg.open_statuses)]
    if cfg.require_score_ge_threshold and "score" in df.columns and "threshold" in df.columns:
        df = df[pd.to_numeric(df["score"], errors="coerce") >= pd.to_numeric(df["threshold"], errors="coerce")]
    # Freshness gate: a signal stays status=open until its barrier resolves (up
    # to 18h), but the EDGE is the launch moment -- entering hours late is a
    # different, untested trade. Only rows younger than max_signal_age_min may
    # open positions (the backtest enters at the very next bar).
    if "signal_time" in df.columns:
        age_cap = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=cfg.max_signal_age_min)
        ts = pd.to_datetime(df["signal_time"], errors="coerce", utc=True)
        df = df[ts >= age_cap]
        df = df.sort_values("signal_time")
    return df.reset_index(drop=True)


# Owner-approved tier cap (2026-07-20, analysis/p_weight_centric_val.md):
# q90-q95 / q95-q99 / q99+ → 1x / 1.5x / 2x. The cap guards against a corrupt
# forward_log ever inflating risk past the approved maximum.
TIER_SIZE_MULT_CAP = 2.0


def signal_size_mult(row: pd.Series) -> float:
    """Tiered sizing multiplier from the forward-log row.

    Legacy rows (pre-tier log, missing column or NaN) trade the historic 1x.
    A stamped 0.0 (below-threshold, should never be logged) yields notional 0,
    which the min_notional gate then skips — corrupt data can only shrink
    exposure, never inflate it beyond TIER_SIZE_MULT_CAP.
    """
    raw = row.get("size_mult")
    try:
        mult = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(mult):
        return 1.0
    return min(max(mult, 0.0), TIER_SIZE_MULT_CAP)


def barriers(entry: float, atr_pct: float) -> tuple[float, float]:
    try:
        atr = abs(entry * float(atr_pct))
    except (TypeError, ValueError):
        atr = float("nan")
    # `atr <= 0` misses NaN (all NaN comparisons are False): a forward row with
    # atr_pct=None sailed through here on 2026-07-16, produced tp=sl=NaN -> 0.0
    # after tick rounding, OKX rejected the bracket (51250) and a REAL DOGE long
    # sat naked. `not (atr > 0)` is True for NaN, zero, and negatives alike.
    if not (atr > 0) or not math.isfinite(atr):
        atr = entry * 0.01  # 1% proxy so the position is never unprotected
    tp = entry + TP_ATR_MULT * atr
    sl = entry - SL_ATR_MULT * atr
    return tp, sl


def compute_entry_notional(
    client: OkxDemoClient | None,
    cfg: ExecutorConfig,
    *,
    open_n: int,
    open_notional: float = 0.0,
) -> dict[str, Any]:
    """How much USDT notional to open for the next slot.

    equity_times_leverage: remaining_budget / slots_left
      remaining = equity * leverage - open_notional
    fixed: cfg.notional_usdt
    """
    mode = (cfg.sizing_mode or "fixed").strip().lower()
    out: dict[str, Any] = {
        "sizing_mode": mode,
        "leverage": cfg.leverage,
        "open_n": open_n,
        "open_notional": open_notional,
    }
    if mode in {"equity_times_leverage", "equity_x_leverage", "equity_leverage"}:
        if client is None:
            out["notional_usdt"] = float(cfg.notional_usdt)
            out["note"] = "no client — fell back to fixed notional_usdt"
            return out
        equity = client.usdt_equity()
        target = max(0.0, float(equity) * float(cfg.leverage))
        remaining = max(0.0, target - max(0.0, float(open_notional)))
        slots_left = max(1, int(cfg.max_concurrent) - int(open_n))
        notional = remaining / slots_left
        out.update({
            "equity_usdt": equity,
            "target_gross_usdt": target,
            "remaining_budget_usdt": remaining,
            "slots_left": slots_left,
            "notional_usdt": notional,
        })
        return out
    out["notional_usdt"] = float(cfg.notional_usdt)
    return out


def open_one(
    client: OkxDemoClient | None,
    cfg: ExecutorConfig,
    row: pd.Series,
    *,
    dry_run: bool,
    notional_usdt: float | None = None,
    sizing_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Place one long paper trade (+ OCO). Returns ledger event dict."""
    sk = signal_key(row)
    symbol = str(row["symbol"])
    inst_id = to_okx_inst_id(symbol)
    atr_pct = float(row["atr_pct"]) if pd.notna(row.get("atr_pct")) else 0.01
    notional = float(notional_usdt if notional_usdt is not None else cfg.notional_usdt)
    event: dict[str, Any] = {
        "event": "dry_run" if dry_run else "order_placed",
        "signal_key": sk,
        "symbol": symbol,
        "inst_id": inst_id,
        "score": row.get("score"),
        "threshold": row.get("threshold"),
        "side": "buy",
        "tp_atr_mult": TP_ATR_MULT,
        "sl_atr_mult": SL_ATR_MULT,
        "td_mode": cfg.td_mode,
    }
    if sizing_meta:
        event["sizing"] = sizing_meta

    if dry_run or client is None:
        # Estimate without keys when dry-run and no client
        mark = float(row["entry_price"]) if pd.notna(row.get("entry_price")) else None
        event.update({
            "mark_px": mark,
            "notional_usdt": notional,
            "note": "dry-run: no order sent",
        })
        if mark:
            tp, sl = barriers(mark, atr_pct)
            event["tp_px"], event["sl_px"] = tp, sl
        return event

    if notional < float(cfg.min_notional_usdt):
        event["event"] = "skipped"
        event["note"] = (
            f"notional {notional:.4f} < min_notional_usdt {cfg.min_notional_usdt}"
        )
        return event

    inst = client.instrument(inst_id)
    mark = client.mark_px(inst_id)
    sz = size_for_notional(notional, mark, inst)
    tick = inst.get("tickSz") or "0.01"
    tp_raw, sl_raw = barriers(mark, atr_pct)
    tp_px = round_price(tp_raw, tick)
    sl_px = round_price(sl_raw, tick)
    event.update({
        "mark_px": mark,
        "sz": sz,
        "tp_px": tp_px,
        "sl_px": sl_px,
        "notional_usdt": notional,
        "leverage": cfg.leverage,
    })

    # The bracket IS the risk control: if these numbers are unusable, there is
    # nothing safe to place afterwards, so refuse the ENTRY -- do not discover
    # the problem with a live position already open (2026-07-16 DOGE incident).
    if not (math.isfinite(tp_px) and math.isfinite(sl_px) and 0 < sl_px < mark < tp_px):
        event["event"] = "skipped_invalid_barriers"
        event["note"] = f"tp/sl unusable: tp={tp_px} sl={sl_px} mark={mark}"
        return event

    try:
        client.set_leverage(inst_id, str(cfg.leverage), mgn_mode=cfg.td_mode)
    except OkxDemoError as exc:
        # leverage may already be set; log and continue
        event["leverage_warn"] = str(exc)

    # Account may be net_mode or long_short_mode (hedge).
    mode = client.pos_mode()
    pos_side = "long" if mode == "long_short_mode" else "net"
    event["pos_mode"] = mode
    event["pos_side"] = pos_side

    cl_id = f"f{abs(hash(sk)) % 10**10}"
    order = client.place_market(
        inst_id, "buy", sz, td_mode=cfg.td_mode, cl_ord_id=cl_id, pos_side=pos_side
    )
    event["order_resp"] = order.get("data")
    # closing side for long = sell; same posSide in hedge mode
    # Retry bracket: a transient OKX 5xx after fill must not leave us naked.
    retries = max(0, int(getattr(cfg, "bracket_retries", 2)))
    sleep_s = float(getattr(cfg, "bracket_retry_sleep_sec", 1.5))
    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            algo = client.place_bracket(
                inst_id, "sell", sz, tp_px, sl_px, td_mode=cfg.td_mode, pos_side=pos_side
            )
            event["algo_resp"] = algo.get("data")
            event["bracket_attempts"] = attempt + 1
            last_err = None
            break
        except OkxDemoError as exc:
            last_err = str(exc)
            event["algo_error"] = last_err
            if attempt < retries:
                time.sleep(max(0.2, sleep_s))
    if last_err is not None:
        event["event"] = "order_partial"  # entry ok, bracket failed — owner must watch
        event["bracket_attempts"] = retries + 1
    return event


def enforce_timeout_exits(client, cfg: ExecutorConfig, ledger_path: Path) -> int:
    """Close positions older than the validated 72-bar horizon (18h).

    The strategy every backtest and the forward gate validated has exactly three
    exits: TP, SL, or timeout at 72 bars. Live had only the OCO bracket, so a
    position that touched neither barrier would linger indefinitely -- an
    untested trade. Closing is reduce-only, and the bracket algo is cancelled
    FIRST: a leftover OCO on a flat position would otherwise fire later and
    open a naked short.
    """
    import src.execution.ledger as led

    timeout = pd.Timedelta(hours=float(getattr(cfg, "timeout_hours", 18.0)))
    now = pd.Timestamp.now(tz="UTC")
    rows = led.load_all(ledger_path)
    # last entry event + algo id per instrument, minus anything already closed
    entries: dict[str, dict] = {}
    for r in rows:
        inst = r.get("inst_id")
        if not inst:
            continue
        ev = r.get("event")
        if ev in {"order_placed", "order_partial"}:
            algo_id = None
            for a in r.get("algo_resp") or []:
                algo_id = a.get("algoId") or algo_id
            entries[inst] = {"ts": r.get("ts"), "algo_id": algo_id}
        elif ev == "timeout_close":
            entries.pop(inst, None)
    closed = 0
    try:
        positions = client.positions()
    except Exception as exc:  # noqa: BLE001 -- position read failing must not kill the loop
        print(f"timeout check: positions read failed: {exc}")
        return 0
    for p in positions:
        try:
            pos_sz = abs(float(p.get("pos") or 0))
            if pos_sz <= 0:
                continue
            inst = p.get("instId")
            meta = entries.get(inst) or {}
            # Entry time: ledger first, OKX cTime fallback, explicit NaT checks.
            # pd.Timestamp(None) returns NaT WITHOUT raising, and `now - NaT <
            # timeout` is False -- the unit test caught this closing every
            # position that lacked a ledger row. Unknown age => never close.
            entry_ts = pd.NaT
            if meta.get("ts"):
                entry_ts = pd.Timestamp(meta["ts"])
                entry_ts = (entry_ts.tz_localize("UTC") if entry_ts.tzinfo is None
                            else entry_ts.tz_convert("UTC"))
            if pd.isna(entry_ts):
                ctime = int(p.get("cTime") or 0)
                if ctime <= 0:
                    continue
                entry_ts = pd.Timestamp(ctime, unit="ms", tz="UTC")
            if pd.isna(entry_ts) or now - entry_ts < timeout:
                continue
            if meta.get("algo_id"):
                try:
                    client.cancel_algo(inst, meta["algo_id"])
                except Exception as exc:  # noqa: BLE001 -- may be gone already
                    print(f"timeout close {inst}: cancel_algo: {exc}")
            side = "sell" if str(p.get("posSide", "net")) != "short" else "buy"
            resp = client.place_market(
                inst, side, str(pos_sz), td_mode=cfg.td_mode,
                pos_side=(p.get("posSide") if p.get("posSide") in {"long", "short"} else None),
                reduce_only=True,
            )
            ev = {
                "event": "timeout_close", "inst_id": inst, "sz": str(pos_sz),
                "held_hours": round((now - entry_ts).total_seconds() / 3600, 1),
                "order_resp": resp.get("data"),
            }
            led.append(ledger_path, ev)
            _notify_event(ev)
            closed += 1
        except Exception as exc:  # noqa: BLE001 -- one bad position must not skip the rest
            ev = {"event": "timeout_close_failed", "inst_id": p.get("instId"), "error": str(exc)}
            led.append(ledger_path, ev)
            _notify_event(ev)
    return closed


def run_once(cfg: ExecutorConfig, *, dry_run: bool = False) -> dict[str, Any]:
    """Single poll cycle. Returns summary counters."""
    ledger_path = _resolve(cfg.ledger)
    summary: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "opened": 0,
        "skipped": 0,
        "errors": 0,
        "paused": None,
    }

    if kill_switch_active(cfg):
        summary["paused"] = f"kill switch: {cfg.kill_switch_file}"
        # Do not append paused every 30–60s — it bloated the ledger to 300+ noise rows.
        return summary

    # Enforce the validated 72-bar exit BEFORE any early return: a position ages
    # past its horizon precisely on quiet cycles, when no-signal returns would
    # otherwise skip the check. Runs under circuit-breaker pause too (it reduces
    # exposure); only the explicit kill switch above silences everything.
    if not dry_run:
        try:
            n_to = enforce_timeout_exits(OkxDemoClient(), cfg, ledger_path)
            if n_to:
                summary["timeout_closed"] = n_to
        except Exception as exc:  # noqa: BLE001
            print(f"timeout enforcement failed: {exc}")

    losses = led.consecutive_losses(ledger_path)
    if losses >= cfg.max_consecutive_losses:
        summary["paused"] = f"circuit breaker: {losses} consecutive losses"
        return summary

    taken = led.signal_keys_already_taken(ledger_path)
    signals = load_actionable_signals(cfg)
    if signals.empty:
        summary["note"] = "no actionable rows in forward_log"
        return summary

    client: OkxDemoClient | None = None
    open_n = 0
    open_notional = 0.0
    if not dry_run:
        client = OkxDemoClient()
        try:
            positions = client.positions("SWAP")
            open_n = sum(1 for p in positions if abs(float(p.get("pos") or 0)) > 0)
            open_notional = client.open_swap_notional_usd()
        except OkxDemoError as exc:
            summary["errors"] += 1
            summary["error"] = str(exc)
            led.append(ledger_path, {"event": "error", "where": "positions", "error": str(exc)})
            return summary
    else:
        # dry-run: count opens from ledger order_placed without closed
        placed = {r["signal_key"] for r in led.load_all(ledger_path) if r.get("event") == "order_placed"}
        closed = {r["signal_key"] for r in led.load_all(ledger_path) if r.get("event") == "closed"}
        open_n = len(placed - closed)
        open_notional = float(cfg.notional_usdt) * open_n

    slots = max(0, cfg.max_concurrent - open_n)
    summary["open_n"] = open_n
    summary["open_notional_usd"] = open_notional
    summary["max_concurrent"] = cfg.max_concurrent
    if slots <= 0:
        summary["note"] = f"at max_concurrent={cfg.max_concurrent} (open={open_n})"
        return summary

    for _, row in signals.iterrows():
        if slots <= 0:
            break
        sk = signal_key(row)
        if sk in taken:
            continue
        try:
            sizing = compute_entry_notional(
                client, cfg, open_n=open_n, open_notional=open_notional
            )
            base_notional = float(sizing.get("notional_usdt") or cfg.notional_usdt)
            # Tiered sizing (owner 2026-07-20): per-signal multiplier stamped
            # by the forward pulse; legacy rows without the column trade 1x.
            # Headroom (owner deploy option ①, 2026-07-21): unit = full-slot
            # budget / TIER_SIZE_MULT_CAP so q99+ (2x) fills equity*leverage
            # and never trips OKX 51008. 1x trades at half budget.
            size_mult = signal_size_mult(row)
            unit_notional = base_notional / TIER_SIZE_MULT_CAP
            notional = unit_notional * size_mult
            sizing["tier"] = row.get("tier")
            sizing["size_mult"] = size_mult
            sizing["base_notional_usdt"] = base_notional
            sizing["unit_notional_usdt"] = unit_notional
            sizing["notional_usdt"] = notional
            sizing["tier_headroom"] = True
            summary["last_sizing"] = sizing
            ev = open_one(
                client, cfg, row, dry_run=dry_run,
                notional_usdt=notional, sizing_meta=sizing,
            )
            led.append(ledger_path, ev)
            _notify_event(ev)
            if ev.get("event") in {"order_placed", "order_partial", "dry_run"}:
                summary["opened"] += 1
                slots -= 1
                open_n += 1
                open_notional += notional
                taken.add(sk)
            else:
                summary["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the loop
            summary["errors"] += 1
            fail_ev = {
                "event": "order_failed",
                "signal_key": sk,
                "symbol": row.get("symbol"),
                "error": str(exc),
                "trace": traceback.format_exc(limit=4),
            }
            led.append(ledger_path, fail_ev)
            _notify_event(fail_ev)
    return summary


def run_loop(cfg: ExecutorConfig, *, dry_run: bool = False, once: bool = False) -> None:
    while True:
        summary = run_once(cfg, dry_run=dry_run)
        print(json_dumps(summary), flush=True)
        if once:
            return
        time.sleep(max(5, int(cfg.poll_seconds)))


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)
````

### `.`

````python
"""Forward tracking entrypoint for the frozen tp5_sl2 SWAP model.

Also provides H1 scaled *shadow* tracking: same mainline freeze for entry
scoring/threshold, but exit outcomes from scaled barrier math, written only to
`data/forward_log_h1_scaled.csv` (never mainline `forward_log.csv`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from src.judgment.forward_records import (
    merge_forward_log,
    read_forward_log,
    write_forward_log,
)
from src.judgment.forward_scan import (
    ExitResolver,
    forward_candidate_indices,
    resolve_forward_exit,
    resolve_forward_exit_scaled,
    scan_forward_records,
)
from src.judgment.forward_types import (
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_PATH,
    FORWARD_START,
    ForwardRecord,
    ForwardRunSummary,
    ForwardScanInput,
    ForwardSummaryJson,
)
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, latest_artifact

__all__ = (
    "FORWARD_LOG_H1_SCALED_PATH",
    "FORWARD_LOG_PATH",
    "FORWARD_START",
    "ForwardRecord",
    "ForwardRunSummary",
    "ForwardSummaryJson",
    "forward_candidate_indices",
    "merge_forward_log",
    "normalize_start_time",
    "resolve_forward_exit",
    "resolve_forward_exit_scaled",
    "run_forward_tracking",
    "run_forward_tracking_h1_shadow",
    "summary_to_json",
)


def run_forward_tracking(
    output_path: Path = FORWARD_LOG_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    """Mainline forward pulse. Scan mode from env FABLE_YOLO_MODE (default live)."""
    from src.judgment.yolo_candidates import resolve_yolo_mode

    return _run_forward_tracking(
        output_path=output_path,
        start_time=start_time,
        exit_resolver=resolve_forward_exit,
        yolo_mode=resolve_yolo_mode("live"),
    )


def run_forward_tracking_h1_shadow(
    output_path: Path = FORWARD_LOG_H1_SCALED_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    """Shadow paper book for H1 scaled exits.

    Entry signals: mainline frozen TP5/SL2 SWAP model + val-q90 threshold
    (identical candidate universe and score filter). Outcomes: scaled 2.5 bank
    + 3 trail via `resolve_forward_exit_scaled`.

    Refuses to write into the mainline log path. Legacy
    `models/frozen_scaled_25_t3_*` is a stub and is intentionally not loaded;
    a proper scaled-label freeze is a future owner step (see plan doc).
    """
    resolved = Path(output_path).resolve()
    if resolved == Path(FORWARD_LOG_PATH).resolve():
        raise ValueError(
            "H1 shadow must not write to mainline data/forward_log.csv; "
            f"use {FORWARD_LOG_H1_SCALED_PATH} (or another non-mainline path)"
        )
    return _run_forward_tracking(
        output_path=output_path,
        start_time=start_time,
        exit_resolver=resolve_forward_exit_scaled,
    )


def _run_forward_tracking(
    *,
    output_path: Path,
    start_time: pd.Timestamp,
    exit_resolver: ExitResolver,
    yolo_weights: Path | None = None,
    yolo_mode: str = "live",
) -> ForwardRunSummary:
    import os

    # OpenMP/thread clash between torch and lightgbm can hang multi-series YOLO scans.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

    normalized_start = normalize_start_time(start_time)
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is None:
        raise FileNotFoundError("missing frozen artifact; run scripts/freeze_model.py first")
    existing = read_forward_log(output_path)
    # Load YOLO *before* LightGBM booster when YOLO is the candidate source —
    # reverse order has hung on Apple Silicon mid-scan (0% CPU sleep).
    from src.judgment.forward_types import CANDIDATE_SOURCE
    from src.judgment.yolo_candidates import load_yolo_model

    if CANDIDATE_SOURCE == "yolo":
        try:
            load_yolo_model(yolo_weights) if yolo_weights is not None else load_yolo_model()
        except FileNotFoundError:
            # detector=none idle mode (iron rule 12): scan_forward_records
            # logs it and skips discovery; the pulse itself must not crash.
            pass
    scan = scan_forward_records(
        ForwardScanInput(
            artifact=artifact,
            booster=lgb.Booster(model_file=str(artifact.model_path)),
            detected_at=datetime.now(timezone.utc).isoformat(),
            start_time=normalized_start,
            existing_log=existing,
        ),
        exit_resolver=exit_resolver,
        yolo_weights=yolo_weights,
        yolo_mode=yolo_mode,
    )
    # Same-symbol minimum gap (2026-07-19): the validated pool spaces signals
    # >= MIN_GAP_BARS (18 bars = 4.5h) per symbol, but each live pulse scans
    # independently, so one move emitted KAITO signals at 03:00/03:15/03:30/
    # 04:00 -- four counts of the same trade. Enforce the pool's spacing here:
    # a NEW signal is dropped if the log (or this batch) already has one for
    # the same symbol within the gap. Exit updates of existing keys pass through.
    from src.judgment.candidates import MIN_GAP_BARS
    from src.judgment.forward_records import row_key as _row_key

    gap = pd.Timedelta(minutes=15 * MIN_GAP_BARS)
    known_keys = {_row_key(r) for r in existing.to_dict("records")} if not existing.empty else set()
    sym_times: dict[str, list[pd.Timestamp]] = {}
    if not existing.empty:
        for _, r in existing.iterrows():
            sym_times.setdefault(str(r["symbol"]), []).append(
                pd.Timestamp(r["signal_time"]).tz_localize("UTC")
                if pd.Timestamp(r["signal_time"]).tzinfo is None
                else pd.Timestamp(r["signal_time"]).tz_convert("UTC"))
    gapped_records = []
    for rec in scan.records:
        key = _row_key(rec) if isinstance(rec, dict) else None
        ts = pd.Timestamp(rec["signal_time"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        sym = str(rec["symbol"])
        if key not in known_keys and any(abs(ts - t) < gap for t in sym_times.get(sym, [])):
            continue
        gapped_records.append(rec)
        sym_times.setdefault(sym, []).append(ts)
    merged = merge_forward_log(existing, gapped_records)
    write_forward_log(output_path, merged.frame)
    # Pulse lag digest: how many of this batch's NEW rows would be tip-fresh.
    try:
        now_utc = pd.Timestamp.now(tz="UTC")
        fresh_n = stale_n = 0
        lags: list[float] = []
        for rec in gapped_records:
            if str(rec.get("status", "")).lower() != "open":
                continue
            key = _row_key(rec) if isinstance(rec, dict) else None
            if key in known_keys:
                continue
            sig_ts = pd.Timestamp(rec["signal_time"])
            if sig_ts.tzinfo is None:
                sig_ts = sig_ts.tz_localize("UTC")
            lag_m = (now_utc - sig_ts).total_seconds() / 60.0
            lags.append(lag_m)
            if lag_m <= 30:
                fresh_n += 1
            else:
                stale_n += 1
        if lags:
            med = sorted(lags)[len(lags) // 2]
            print(
                f"forward_freshness: new_open={len(lags)} tip_fresh≤30m={fresh_n} "
                f"hindsight={stale_n} lag_med={med:.0f}m",
                flush=True,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"forward_freshness: skip ({exc})", flush=True)
    # Telegram: only mainline path, only brand-new signal keys (not exit updates).
    if Path(output_path).resolve() == Path(FORWARD_LOG_PATH).resolve() and merged.new_signals:
        try:
            from src.judgment.forward_records import forward_key, row_key
            from src.notify_signal import notify_new_forward_signals

            existing_keys = {row_key(r) for r in existing.to_dict("records")} if not existing.empty else set()
            now_utc = pd.Timestamp.now(tz="UTC")
            brand_new: list[dict] = []
            for rec in scan.records:
                key = forward_key(rec["source"], rec["symbol"], pd.Timestamp(rec["signal_time"]))
                if key in existing_keys:
                    continue
                # Alert only on ACTIONABLE signals. A catch-up scan backfills
                # history whose outcome is already sealed (status=closed, or a
                # signal bar hours old) -- on 2026-07-18 the first yolo-source
                # pulse pushed dozens of those to the channel and the owner
                # reasonably asked why OKX had not traded them. Match the
                # executor freshness gate (max_signal_age_min=30: 15 bar + 7
                # pulse/scan + headroom) so TG never pages about trades nobody
                # can take.
                if str(rec.get("status", "")).lower() != "open":
                    continue
                sig_ts = pd.Timestamp(rec["signal_time"])
                if sig_ts.tzinfo is None:
                    sig_ts = sig_ts.tz_localize("UTC")
                lag_m = (now_utc - sig_ts).total_seconds() / 60.0
                if lag_m > 30:
                    continue
                row = dict(rec)
                row["lag_min"] = round(lag_m, 1)
                # absolute ATR for chart TP/SL (atr_pct ≈ atr14/close)
                if row.get("atr14") is None and row.get("atr_pct") and row.get("entry_price"):
                    row["atr14"] = float(row["entry_price"]) * float(row["atr_pct"])
                brand_new.append(row)
            n_sent = notify_new_forward_signals(brand_new)
            print(f"tg_signal: new={len(brand_new)} sent_ok={n_sent}")
        except Exception as exc:  # noqa: BLE001 -- never block forward tracking
            print(f"tg_signal: skipped ({exc})")
    open_rows = int((merged.frame["status"] != "closed").sum()) if not merged.frame.empty else 0
    closed_rows = int((merged.frame["status"] == "closed").sum()) if not merged.frame.empty else 0
    return ForwardRunSummary(
        artifact=artifact,
        start_time=normalized_start,
        scanned_series=scan.scanned_series,
        candidates_seen=scan.candidates_seen,
        threshold_signals_seen=scan.threshold_signals_seen,
        new_signals=merged.new_signals,
        closed_updates=merged.closed_updates,
        total_rows=int(len(merged.frame)),
        open_rows=open_rows,
        closed_rows=closed_rows,
        output=output_path,
    )


def normalize_start_time(value: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def summary_to_json(summary: ForwardRunSummary) -> str:
    return json.dumps(summary.to_json(), ensure_ascii=False, indent=2)
````

### `.`

````python
"""SWAP candidate scanning and partial barrier outcome resolution.

Mainline (2026-07-15+): YOLO detector proposes candidates; LightGBM freeze
scores them; exits stay fixed TP5/SL2 (`resolve_forward_exit`). H1 shadow
reuses the same candidate/score path with scaled exits.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.data.universe import is_stockish
from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS, add_indicators, strict_mask
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.forward_records import forward_key, open_keys
from src.judgment.forward_types import (
    BAR,
    CANDIDATE_SOURCE,
    SCALED_SL_MULT,
    SCALED_TP1_MULT,
    SCALED_TRAIL_MULT,
    SL_MULT,
    TP_MULT,
    ForwardExit,
    ForwardRecord,
    ForwardScanInput,
    ForwardScanResult,
)
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS
from src.judgment.yolo_candidates import (
    get_tip_edge_rejected,
    load_yolo_model,
    reset_tip_edge_rejected,
    resolve_tip_conf,
    resolve_yolo_mode,
    scan_series_with_yolo,
)

ExitResolver = Callable[[pd.DataFrame, int], Optional[ForwardExit]]

# Recent-tail length for live scans (see jobs assembly below).
LIVE_TAIL_BARS = 2000


def _forward_workers() -> int:
    """Series-level parallelism for live YOLO. Override with FABLE_FORWARD_WORKERS."""
    raw = os.environ.get("FABLE_FORWARD_WORKERS", "").strip()
    if raw:
        try:
            return max(1, min(8, int(raw)))
        except ValueError:
            pass
    # Default 3: render can overlap; predict is locked inside yolo_candidates.
    return 3


def scan_forward_records(
    scan: ForwardScanInput,
    *,
    exit_resolver: Optional[ExitResolver] = None,
    yolo_weights: str | Path | None = None,
    yolo_mode: str | None = None,
) -> ForwardScanResult:
    """Scan SWAP series for threshold signals and resolve exits.

    `exit_resolver` defaults to mainline TP5/SL2. Pass
    `resolve_forward_exit_scaled` for the H1 shadow paper book.

    `yolo_weights` / `yolo_mode` override the mainline detector for shadow
    books (e.g. v12 tip-only). Mainline callers leave defaults; unset
    `yolo_mode` resolves from env ``FABLE_YOLO_MODE`` (default live).
    """
    resolve = exit_resolver or resolve_forward_exit
    if yolo_mode is None:
        yolo_mode = resolve_yolo_mode("live")
    tip_conf = resolve_tip_conf()
    records: list[ForwardRecord] = []
    scanned_series = 0
    candidates_seen = 0
    threshold_signals_seen = 0
    tracked_keys = open_keys(scan.existing_log)
    yolo_model = None
    if CANDIDATE_SOURCE == "yolo":
        try:
            yolo_model = load_yolo_model(yolo_weights) if yolo_weights is not None else load_yolo_model()
        except FileNotFoundError as exc:
            # Owner doctrine 2026-07-23: pre-v16 detectors are deleted (they
            # could only ever produce hindsight rows). Until a validated tip
            # detector lands, the pulse idles honestly: klines stay fresh and
            # open rows keep resolving, but NO candidate discovery happens.
            print(f"forward_scan: detector=none ({exc}) — awaiting validated v16; "
                  "no candidate discovery this pulse", flush=True)
            yolo_model = None

    jobs: list[tuple[str, str, pd.DataFrame]] = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP"):
            continue
        if is_stockish(symbol):
            continue
        # Live scans only need a recent tail, not 400 days: indicators/MAs were
        # recomputed over the FULL history for every series every pulse, and
        # that pandas cost grows with the archive. 2000 bars (~3 weeks) keeps
        # every lookback numerically converged at the bars we score (max
        # rolling=168, WARMUP=288; the EWMs -- EMA120/ATR14 -- differ only at
        # the 1e-11 level after this much warm-up) and caps how far back a
        # pulse can "discover" old signals, which the freshness gates would
        # reject anyway.
        jobs.append((source, symbol, frame.tail(LIVE_TAIL_BARS).reset_index(drop=True)))
    scanned_series = len(jobs)
    workers = _forward_workers() if CANDIDATE_SOURCE == "yolo" else 1
    wlabel = str(yolo_weights) if yolo_weights is not None else "owner_best"
    tip_conf_s = f"{tip_conf:.2f}" if tip_conf is not None else "off"
    print(
        f"forward_scan: series={scanned_series} workers={workers} source={CANDIDATE_SOURCE} "
        f"yolo_mode={yolo_mode} tip_conf={tip_conf_s} weights={wlabel}",
        flush=True,
    )
    reset_tip_edge_rejected()

    def _discover(job: tuple[str, str, pd.DataFrame]) -> tuple[str, str, pd.DataFrame, pd.DataFrame, list[int]]:
        """Phase 1 (parallel-safe): indicators + YOLO/rules indices only."""
        source, symbol, frame = job
        enriched = add_indicators(frame)
        if CANDIDATE_SOURCE == "yolo" and yolo_model is None:
            # detector=none idle mode: no discovery, tracked rows still resolve
            signal_indices: set[int] = set()
        else:
            signal_indices = set(
                forward_candidate_indices(
                    enriched,
                    frame=frame,
                    yolo_model=yolo_model,
                    start_time=scan.start_time,
                    yolo_mode=yolo_mode,
                )
            )
        tracked_times = {key[2] for key in tracked_keys if key[0] == source and key[1] == symbol}
        if tracked_times:
            signal_times = enriched["open_time"].astype(str)
            signal_indices.update(
                int(idx) for idx in signal_times[signal_times.isin(tracked_times)].index
            )
        return source, symbol, frame, enriched, sorted(signal_indices)

    t_discover = time.monotonic()
    discovered: list[tuple[str, str, pd.DataFrame, pd.DataFrame, list[int]]] = []
    if workers <= 1:
        discovered = [_discover(job) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_discover, job) for job in jobs]
            for fut in as_completed(futs):
                discovered.append(fut.result())
    t_phase2 = time.monotonic()
    tip_edge_n = get_tip_edge_rejected()
    print(
        f"forward_scan: discover_wall={t_phase2 - t_discover:.0f}s "
        f"(indicators+render+predict, {workers} workers) "
        f"tip_edge_rejected={tip_edge_n}",
        flush=True,
    )

    # Phase 2 (sequential): LightGBM predict + barrier resolve (not thread-safe).
    for source, symbol, frame, enriched, ordered_indices in discovered:
        if not ordered_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, ordered_indices)
        scores = scan.booster.predict(
            feature_rows[FEATURE_COLUMNS], num_iteration=scan.artifact.best_iteration
        )
        candidates_seen += len(ordered_indices)
        for row_pos, signal_i in enumerate(ordered_indices):
            signal_time = pd.Timestamp(enriched["open_time"].iloc[signal_i])
            key = forward_key(source, symbol, signal_time)
            tracked_open = key in tracked_keys
            if not tracked_open and signal_time < scan.start_time:
                continue
            score = float(scores[row_pos])
            if not tracked_open and score < scan.artifact.threshold:
                continue
            exit_state = resolve(enriched, signal_i)
            if exit_state is None:
                continue
            threshold_signals_seen += 1
            entry_i = signal_i + 1
            feature_row = feature_rows.iloc[row_pos]
            # Tip signal: entry bar hasn't printed. entry_time is known (next
            # bar open = signal bar close time); entry_price uses the signal
            # bar close as a PROXY so TG/executor have a sane number, and
            # maker_filled stays empty as the "entry pending backfill" sentinel
            # -- merge_forward_log overwrites all three with the true next-bar
            # values on the following pulse.
            tip_pending = entry_i >= len(enriched)
            if tip_pending:
                entry_time = str(signal_time + pd.Timedelta(minutes=15))
                entry_price = float(enriched["close"].iloc[signal_i])
                maker_filled = None
            else:
                entry_time = str(pd.Timestamp(enriched["open_time"].iloc[entry_i]))
                entry_price = float(enriched["open"].iloc[entry_i])
                maker_filled = bool(
                    float(enriched["low"].iloc[entry_i]) < float(enriched["open"].iloc[entry_i])
                )
            # Tiered sizing (owner 2026-07-20): tier is stamped at detection
            # time from the artifact sidecar; artifacts without sizing_tiers
            # (shadow books, stubs) log the legacy 1x.
            tiers = getattr(scan.artifact, "sizing_tiers", None)
            if tiers is not None:
                tier, size_mult = tiers.tier_for_score(score, scan.artifact.threshold)
            else:
                tier, size_mult = "", 1.0
            records.append(
                {
                    "source": source,
                    "symbol": symbol,
                    "is_stockish": is_stockish(symbol),
                    "signal_time": str(signal_time),
                    "detected_at": scan.detected_at,
                    "status": exit_state.status,
                    "score": score,
                    "threshold": scan.artifact.threshold,
                    "model_path": scan.artifact.relative_model_path,
                    "dataset_sha256": scan.artifact.dataset_sha256,
                    "signal_i": int(signal_i),
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "maker_filled": maker_filled,
                    "outcome": exit_state.outcome,
                    "label": exit_state.label,
                    "exit_offset": exit_state.exit_offset,
                    "exit_time": exit_state.exit_time,
                    "realized_ret": exit_state.realized_ret,
                    "atr_pct": float(feature_row["atr_pct"]),
                    "dense_run_len": int(feature_row["dense_run_len"]),
                    "tier": tier,
                    "size_mult": size_mult,
                }
            )
    print(
        f"forward_scan: phase2_wall={time.monotonic() - t_phase2:.0f}s "
        f"(features+score+resolve, {sum(1 for d in discovered if d[4])} series with candidates)",
        flush=True,
    )
    return ForwardScanResult(records, scanned_series, candidates_seen, threshold_signals_seen)


def forward_candidate_indices(
    enriched: pd.DataFrame,
    *,
    frame: pd.DataFrame | None = None,
    yolo_model=None,
    start_time: pd.Timestamp | None = None,
    yolo_mode: str = "live",
) -> list[int]:
    """Mainline candidate bars: YOLO by default, rules if CANDIDATE_SOURCE=rules."""
    if CANDIDATE_SOURCE == "rules":
        return _rule_candidate_indices(enriched)
    # YOLO path
    raw = frame if frame is not None else enriched
    start_from_i = None
    if start_time is not None and "open_time" in raw.columns:
        times = pd.to_datetime(raw["open_time"], utc=True)
        st = pd.Timestamp(start_time)
        if st.tzinfo is None:
            st = st.tz_localize("UTC")
        else:
            st = st.tz_convert("UTC")
        hits = np.flatnonzero(times >= st)
        if len(hits) == 0:
            # FORWARD_START often sits *inside* the still-open 15m bar (e.g. start
            # 16:30 while last *closed* open_time is 16:15). Returning [] here
            # blanked the whole live gate after the 2026-07-19 retest clock reset
            # (candidates_seen=0 on 344 series). Still scan the tip; the score
            # stage already drops signal_time < start_time for new rows.
            start_from_i = max(0, len(raw) - 10)
        else:
            start_from_i = max(0, int(hits[0]) - 5)
    mode = yolo_mode if yolo_mode in ("live", "tip", "full") else "live"
    return scan_series_with_yolo(
        raw,
        yolo_model,
        start_from_i=start_from_i,
        mode=mode,
        tip_conf=resolve_tip_conf(),
    )


def _rule_candidate_indices(enriched: pd.DataFrame) -> list[int]:
    if len(enriched) < WARMUP_BARS + 2:
        return []
    mask = strict_mask(enriched, mode="expanded").fillna(False)
    idx = np.flatnonzero(mask.to_numpy())
    # live fallback path: the tip bar is a valid signal (entry backfills next pulse)
    idx = idx[(idx >= WARMUP_BARS) & (idx < len(enriched))]
    if len(idx) == 0:
        return []
    scores = enriched["shape_score"].to_numpy()
    selected: list[int] = []
    for signal_i in sorted(idx, key=lambda item: scores[item], reverse=True):
        if all(abs(signal_i - previous) >= MIN_GAP_BARS for previous in selected):
            selected.append(int(signal_i))
    return sorted(selected)


def resolve_forward_exit(enriched: pd.DataFrame, signal_i: int) -> ForwardExit | None:
    entry_i = signal_i + 1
    atr = float(enriched["atr14"].iloc[signal_i])
    atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    if entry_i >= len(enriched):
        # Tip signal (2026-07-20 real-time path): the signal bar IS the newest
        # closed bar, so the entry bar has not printed yet. Record it as open
        # with pending entry fields (backfilled next pulse) instead of dropping
        # it -- dropping cost 15-22 min of edge on every live signal.
        return ForwardExit("open", "", -1, 0, "", float("nan"))
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
        return None
    last_i = entry_i + HORIZON_BARS - 1
    available_last_i = min(last_i, len(enriched) - 1)
    highs = enriched["high"].to_numpy()[entry_i : available_last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : available_last_i + 1]
    upper = entry + TP_MULT * atr
    lower = entry - SL_MULT * atr
    hit_up = highs >= upper
    hit_dn = lows <= lower
    up_first = int(np.argmax(hit_up)) if hit_up.any() else len(highs)
    dn_first = int(np.argmax(hit_dn)) if hit_dn.any() else len(highs)
    entry_time = pd.Timestamp(enriched["open_time"].iloc[entry_i])
    if up_first < dn_first:
        exit_offset = up_first + 1
        return ForwardExit("closed", "tp", 1, exit_offset, _exit_time(entry_time, exit_offset), upper / entry - 1)
    if dn_first < up_first:
        exit_offset = dn_first + 1
        return ForwardExit("closed", "sl", 0, exit_offset, _exit_time(entry_time, exit_offset), lower / entry - 1)
    if up_first == dn_first < len(highs):
        exit_offset = dn_first + 1
        return ForwardExit(
            "closed", "sl_ambiguous", 0, exit_offset, _exit_time(entry_time, exit_offset), lower / entry - 1
        )
    if available_last_i >= last_i:
        realized_ret = float(enriched["close"].iloc[last_i]) / entry - 1
        return ForwardExit(
            "closed", "timeout", 0, HORIZON_BARS, _exit_time(entry_time, HORIZON_BARS), realized_ret
        )
    return ForwardExit("open", "", -1, 0, "", float("nan"))


def resolve_forward_exit_scaled(
    enriched: pd.DataFrame,
    signal_i: int,
    *,
    tp1_mult: float = SCALED_TP1_MULT,
    trail_mult: float = SCALED_TRAIL_MULT,
    sl_mult: float = SCALED_SL_MULT,
    horizon: int = HORIZON_BARS,
) -> ForwardExit | None:
    """Partial-horizon port of `label_candidate_scaled` for forward shadow logs.

    Math matches labeling.py: hard SL until TP1 (half bank), then trail under
    running high; stop checked before target within a bar; trail uses prior-bar
    run_max. Incomplete horizon without a terminal barrier → status=open.
    """
    entry_i = signal_i + 1
    atr = float(enriched["atr14"].iloc[signal_i])
    atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    if entry_i >= len(enriched):
        # tip signal: entry bar not printed yet (see resolve_forward_exit)
        return ForwardExit("open", "", -1, 0, "", float("nan"))
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
        return None

    last_i = entry_i + horizon - 1
    available_last_i = min(last_i, len(enriched) - 1)
    n_bars = available_last_i - entry_i + 1
    if n_bars <= 0:
        return None

    highs = enriched["high"].to_numpy()[entry_i : available_last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : available_last_i + 1]
    opens = enriched["open"].to_numpy()[entry_i : available_last_i + 1]
    entry_time = pd.Timestamp(enriched["open_time"].iloc[entry_i])

    hard_stop = entry - sl_mult * atr
    tp1 = entry + tp1_mult * atr
    ret1: float | None = None
    run_max = tp1

    for j in range(n_bars):
        if ret1 is None:
            if lows[j] <= hard_stop:  # stop first: conservative
                exit_price = min(hard_stop, float(opens[j]))
                ret = exit_price / entry - 1
                exit_offset = j + 1
                return ForwardExit("closed", "sl", 0, exit_offset, _exit_time(entry_time, exit_offset), ret)
            if highs[j] >= tp1:
                ret1 = tp1 / entry - 1
            continue  # phase-2 trailing starts on the NEXT bar
        stop = max(run_max - trail_mult * atr, hard_stop)
        if lows[j] <= stop:
            exit_price = min(stop, float(opens[j]))
            ret = 0.5 * ret1 + 0.5 * (exit_price / entry - 1)
            exit_offset = j + 1
            return ForwardExit(
                "closed", "scaled", int(ret > 0), exit_offset, _exit_time(entry_time, exit_offset), ret
            )
        run_max = max(run_max, float(highs[j]))

    if available_last_i >= last_i:
        timeout_close = float(enriched["close"].iloc[last_i])
        if ret1 is None:
            ret = timeout_close / entry - 1
            return ForwardExit(
                "closed", "timeout", int(ret > 0), horizon, _exit_time(entry_time, horizon), ret
            )
        ret = 0.5 * ret1 + 0.5 * (timeout_close / entry - 1)
        return ForwardExit(
            "closed", "scaled_timeout", int(ret > 0), horizon, _exit_time(entry_time, horizon), ret
        )
    return ForwardExit("open", "", -1, 0, "", float("nan"))


def _exit_time(entry_time: pd.Timestamp, exit_offset: int) -> str:
    return str(entry_time + exit_offset * BAR)
````

### `.`

````python
"""Typed forward-log records and run summaries."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

import lightgbm as lgb
import pandas as pd

from src.judgment.frozen import FrozenArtifact

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
FORWARD_LOG_PATH: Final = PROJECT_DIR / "data" / "forward_log.csv"
# H1 scaled shadow paper book — never mixed into mainline 100-trade gate.
FORWARD_LOG_H1_SCALED_PATH: Final = PROJECT_DIR / "data" / "forward_log_h1_scaled.csv"
# YOLO mainline cutover (owner 2026-07-15): new candidate source → new forward clock.
# Pre-cutover rule-scan log archived as data/forward_log_rules_pre_yolo_20260715.csv
# Owner 2026-07-18/19: clear pre-v11 mixed book and restart gate for clean retest.
# Archived: data/forward_log_pre_v11_retest_20260719.csv (VPS + local).
# Use last *closed* bar open (not wall-clock "now") so live YOLO is not skipped
# while the current 15m candle is still forming.
FORWARD_START: Final = pd.Timestamp("2026-07-18 16:15:00", tz="UTC")
# "yolo" = detector proposes bars; "rules" = expanded dense-MA scan (legacy).
# Override with env FABLE_CANDIDATE_SOURCE=rules when VPS has no ultralytics/torch.
import os as _os
CANDIDATE_SOURCE: Final = _os.environ.get("FABLE_CANDIDATE_SOURCE", "yolo").strip().lower() or "yolo"
BAR: Final = pd.Timedelta(minutes=15)
TP_MULT: Final = 5.0
SL_MULT: Final = 2.0
# H1 scaled exit params (single-variable vs mainline TP5/SL2).
SCALED_TP1_MULT: Final = 2.5
SCALED_TRAIL_MULT: Final = 3.0
SCALED_SL_MULT: Final = 2.0
FORWARD_COLUMNS: Final = (
    "source",
    "symbol",
    "signal_time",
    "detected_at",
    "status",
    "score",
    "threshold",
    "model_path",
    "dataset_sha256",
    "signal_i",
    "entry_time",
    "entry_price",
    "maker_filled",
    "outcome",
    "label",
    "exit_offset",
    "exit_time",
    "realized_ret",
    "atr_pct",
    "dense_run_len",
    # Tiered sizing (owner 2026-07-20). Appended LAST so pre-tier readers of
    # positional CSVs are unaffected; legacy rows read back as NaN → 1x.
    "tier",
    "size_mult",
)
OUTCOME_COLUMNS: Final = ("status", "outcome", "label", "exit_offset", "exit_time", "realized_ret")


class ForwardRecord(TypedDict):
    source: str
    symbol: str
    signal_time: str
    detected_at: str
    status: str
    score: float
    threshold: float
    model_path: str
    dataset_sha256: str
    signal_i: int
    entry_time: str
    entry_price: float
    # None while a tip-recorded row awaits its entry-bar backfill
    maker_filled: bool | None
    outcome: str
    label: int
    exit_offset: int
    exit_time: str
    realized_ret: float
    atr_pct: float
    dense_run_len: int
    # score→size tier of the frozen val distribution (q90_q95/q95_q99/q99_plus)
    tier: str
    size_mult: float


class ForwardSummaryJson(TypedDict):
    model_path: str
    threshold: float
    start_time: str
    scanned_series: int
    candidates_seen: int
    threshold_signals_seen: int
    new_signals: int
    closed_updates: int
    total_rows: int
    open_rows: int
    closed_rows: int
    output: str


@dataclass(frozen=True)
class ForwardExit:
    __slots__ = ("status", "outcome", "label", "exit_offset", "exit_time", "realized_ret")

    status: str
    outcome: str
    label: int
    exit_offset: int
    exit_time: str
    realized_ret: float


@dataclass(frozen=True)
class ForwardScanInput:
    __slots__ = ("artifact", "booster", "detected_at", "start_time", "existing_log")

    artifact: FrozenArtifact
    booster: lgb.Booster
    detected_at: str
    start_time: pd.Timestamp
    existing_log: pd.DataFrame


@dataclass(frozen=True)
class ForwardScanResult:
    __slots__ = ("records", "scanned_series", "candidates_seen", "threshold_signals_seen")

    records: list[ForwardRecord]
    scanned_series: int
    candidates_seen: int
    threshold_signals_seen: int


@dataclass(frozen=True)
class MergeResult:
    __slots__ = ("frame", "new_signals", "closed_updates")

    frame: pd.DataFrame
    new_signals: int
    closed_updates: int


@dataclass(frozen=True)
class ForwardRunSummary:
    __slots__ = (
        "artifact",
        "start_time",
        "scanned_series",
        "candidates_seen",
        "threshold_signals_seen",
        "new_signals",
        "closed_updates",
        "total_rows",
        "open_rows",
        "closed_rows",
        "output",
    )

    artifact: FrozenArtifact
    start_time: pd.Timestamp
    scanned_series: int
    candidates_seen: int
    threshold_signals_seen: int
    new_signals: int
    closed_updates: int
    total_rows: int
    open_rows: int
    closed_rows: int
    output: Path

    def to_json(self) -> ForwardSummaryJson:
        return {
            "model_path": self.artifact.relative_model_path,
            "threshold": self.artifact.threshold,
            "start_time": str(self.start_time),
            "scanned_series": self.scanned_series,
            "candidates_seen": self.candidates_seen,
            "threshold_signals_seen": self.threshold_signals_seen,
            "new_signals": self.new_signals,
            "closed_updates": self.closed_updates,
            "total_rows": self.total_rows,
            "open_rows": self.open_rows,
            "closed_rows": self.closed_rows,
            "output": str(self.output),
        }
````

### `.`

````python
"""Frozen LightGBM artifacts for forward validation.

The project selected tp5_sl2 on the SWAP universe as the current mainline.
This module centralizes artifact discovery, metadata fingerprints, and
frozen-model scoring so dashboards and forward tracking do not retrain.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Mapping, TypedDict

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import DEFAULT_HORIZON_BARS, load_splits, train_model

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
BAR: Final = pd.Timedelta(minutes=15)
DEFAULT_SCORE_QUANTILE: Final = 0.90
# Tiered sizing (owner 2026-07-20, analysis/p_weight_centric_val.md):
# val-score quantile bands [q90,q95) / [q95,q99) / q99+ -> notional multiplier.
# Band edges live in the artifact sidecar ("sizing_tiers"); multipliers are a
# fixed owner decision, not a tunable.
TIER_MULTIPLIERS: Final = {"q90_q95": 1.0, "q95_q99": 1.5, "q99_plus": 2.0}
# Mainline 2026-07-18+: v11_chain pool (owner-authorized full cutover).
# Accept-window compare vs v8 = holdout consumption #4.
DEFAULT_CONFIG_NAME: Final = "tp5_sl2_swap_yolo_v11_reg"
V11_POOL_CONFIG_NAME: Final = DEFAULT_CONFIG_NAME
# v12 pool artifact name only — DEFAULT stays v11 until owner promotes.
V12_POOL_CONFIG_NAME: Final = "tp5_sl2_swap_yolo_v12_reg"
V8_POOL_CONFIG_NAME: Final = "tp5_sl2_swap_yolo_v8_reg"
# 2026-07-15 mainline (old pool, pre-lr-fix detector); rollback only.
OLD_POOL_CONFIG_NAME: Final = "tp5_sl2_swap_yolo_reg"
# Previous YOLO binary freeze (shadow / rollback / dashboard compare).
BINARY_YOLO_CONFIG_NAME: Final = "tp5_sl2_swap_yolo"
# Legacy rule-scan freeze (pre-cutover); kept for rollback / comparisons.
LEGACY_RULES_CONFIG_NAME: Final = "tp5_sl2_swap"


class ScoreCacheMetadata(TypedDict, total=False):
    threshold: float
    model_path: str
    dataset_path: str
    dataset_sha256: str


@dataclass(frozen=True)
class FrozenConfig:
    __slots__ = (
        "name", "project_dir", "dataset_path", "models_dir",
        "score_quantile", "horizon_bars", "objective",
    )

    name: str
    project_dir: Path
    dataset_path: Path
    models_dir: Path
    score_quantile: float
    horizon_bars: int
    objective: str  # binary | regression


@dataclass(frozen=True)
class SizingTiers:
    """Val-score quantile band edges for tiered notional sizing.

    Owner-approved 2026-07-20 (analysis/p_weight_centric_val.md): bands
    [q90,q95) / [q95,q99) / q99+ map to 1x / 1.5x / 2x. q90 is the existing
    entry threshold (threshold_val_q90); q95/q99 come from the same frozen
    val-score distribution and live in the artifact sidecar.
    """

    __slots__ = ("q95", "q99")

    q95: float
    q99: float

    def tier_for_score(self, score: float, threshold: float) -> tuple[str, float]:
        """Map a frozen score to (tier name, notional multiplier).

        Below-threshold scores never trade (multiplier 0.0) — same semantics
        as the experiment's weight function, so a bad caller can only shrink
        exposure, never inflate it.
        """
        if not (score >= threshold):  # catches NaN too
            return "below_q90", 0.0
        if score >= self.q99:
            return "q99_plus", TIER_MULTIPLIERS["q99_plus"]
        if score >= self.q95:
            return "q95_q99", TIER_MULTIPLIERS["q95_q99"]
        return "q90_q95", TIER_MULTIPLIERS["q90_q95"]


@dataclass(frozen=True)
class FrozenArtifact:
    __slots__ = (
        "config",
        "model_path",
        "metadata_path",
        "dataset_path",
        "relative_model_path",
        "relative_dataset_path",
        "threshold",
        "feature_columns",
        "dataset_sha256",
        "dataset_size_bytes",
        "best_iteration",
        "sizing_tiers",
    )

    config: FrozenConfig
    model_path: Path
    metadata_path: Path
    dataset_path: Path
    relative_model_path: str
    relative_dataset_path: str
    threshold: float
    feature_columns: tuple[str, ...]
    dataset_sha256: str
    dataset_size_bytes: int
    best_iteration: int
    # None when the sidecar predates tiered sizing → everything trades 1x.
    sizing_tiers: SizingTiers | None


class FrozenArtifactError(RuntimeError):
    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def default_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """Mainline: regression on the v11_chain candidate pool (2026-07-18 cutover)."""
    return FrozenConfig(
        name=DEFAULT_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "judgment_yolo_swap_v11.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
        objective="regression",
    )


yolo_v11_pool_config = default_config


def yolo_v12_pool_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """v12 H-TIP candidate pool (2026-07-20). Artifact ready; promote needs owner."""
    return FrozenConfig(
        name=V12_POOL_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "judgment_yolo_swap_v12.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
        objective="regression",
    )


def yolo_v8_pool_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """2026-07-16 mainline (v8_chain pool). Rollback / SHADOW compare only."""
    return FrozenConfig(
        name=V8_POOL_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "judgment_yolo_swap_v8.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
        objective="regression",
    )


def yolo_old_pool_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """2026-07-15 mainline (old pool, pre-lr-fix detector). Rollback only."""
    return FrozenConfig(
        name=OLD_POOL_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "judgment_yolo_swap.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
        objective="regression",
    )


def binary_yolo_shadow_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """Previous YOLO binary freeze — shadow compare / emergency rollback."""
    return FrozenConfig(
        name=BINARY_YOLO_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "judgment_yolo_swap.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
        objective="binary",
    )


def rules_legacy_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """Pre-cutover rule-scan freeze (rollback only)."""
    return FrozenConfig(
        name=LEGACY_RULES_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "swap_replication" / "swap_tp5_sl2.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
        objective="binary",
    )


DEFAULT_FROZEN_CONFIG: Final = default_config()


def latest_artifact(config: FrozenConfig = DEFAULT_FROZEN_CONFIG) -> FrozenArtifact | None:
    # date-suffix only: frozen_{name}_YYYYMMDD.json -- a greedy * here once
    # matched a different config (…_ma206_…) and crashed the dashboard
    pattern = re.compile(rf"^frozen_{re.escape(config.name)}_\d{{8}}\.json$")
    metadata_paths = sorted(
        p for p in config.models_dir.glob(f"frozen_{config.name}_*.json")
        if pattern.match(p.name))
    for path in reversed(metadata_paths):  # newest valid wins; corrupt ones skip
        try:
            return load_artifact(config, path)
        except FrozenArtifactError as exc:
            print(f"frozen: skipping {path.name}: {exc}")
    return None


def load_artifact(config: FrozenConfig, metadata_path: Path) -> FrozenArtifact:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    feature_columns = tuple(metadata["feature_columns"])
    if feature_columns != tuple(FEATURE_COLUMNS):
        raise FrozenArtifactError(metadata_path, "feature list does not match current FEATURE_COLUMNS")
    model_path = _project_path(config, metadata["model_path"])
    if not model_path.exists():
        raise FrozenArtifactError(metadata_path, "model file is missing")
    dataset_path = _project_path(config, metadata["dataset_path"])
    sizing_tiers = _load_sizing_tiers(metadata_path, metadata)
    return FrozenArtifact(
        config=config,
        model_path=model_path,
        metadata_path=metadata_path,
        dataset_path=dataset_path,
        relative_model_path=str(metadata["model_path"]),
        relative_dataset_path=str(metadata["dataset_path"]),
        threshold=float(metadata["threshold_val_q90"]),
        feature_columns=feature_columns,
        dataset_sha256=str(metadata["dataset_sha256"]),
        dataset_size_bytes=int(metadata["dataset_size_bytes"]),
        best_iteration=int(metadata["best_iteration"]),
        sizing_tiers=sizing_tiers,
    )


def _load_sizing_tiers(metadata_path: Path, metadata: Mapping) -> SizingTiers | None:
    """Optional "sizing_tiers" sidecar block. Missing → None (legacy 1x).

    A malformed block raises: silently trading 1x when the owner enabled
    tiered sizing would misreport live risk, so fail loudly instead.
    """
    raw = metadata.get("sizing_tiers")
    if raw is None:
        return None
    try:
        q95 = float(raw["q95"])
        q99 = float(raw["q99"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FrozenArtifactError(metadata_path, f"bad sizing_tiers block: {exc}")
    threshold = float(metadata["threshold_val_q90"])
    if not (threshold < q95 < q99):
        raise FrozenArtifactError(
            metadata_path, f"sizing_tiers not ordered: q90={threshold} q95={q95} q99={q99}"
        )
    return SizingTiers(q95=q95, q99=q99)


def train_frozen_artifact(config: FrozenConfig, artifact_date: str) -> FrozenArtifact:
    config.models_dir.mkdir(parents=True, exist_ok=True)
    train, val, _ = load_splits(config.dataset_path, horizon_bars=config.horizon_bars)
    model = train_model(train, val, objective=config.objective)
    best_iteration = int(model.best_iteration or model.current_iteration())
    val_scores = model.predict(val[FEATURE_COLUMNS], num_iteration=best_iteration)
    threshold = float(np.quantile(val_scores, config.score_quantile))

    stem = f"frozen_{config.name}_{artifact_date}"
    model_path = config.models_dir / f"{stem}.txt"
    metadata_path = config.models_dir / f"{stem}.json"
    model.save_model(str(model_path), num_iteration=best_iteration)
    metadata = {
        "artifact_version": 1,
        "config": config.name,
        "objective": config.objective,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_path": _relative_path(config, model_path),
        "dataset_path": _relative_path(config, config.dataset_path),
        "dataset_sha256": file_sha256(config.dataset_path),
        "dataset_size_bytes": config.dataset_path.stat().st_size,
        "threshold_val_q90": threshold,
        "score_quantile": config.score_quantile,
        "feature_columns": list(FEATURE_COLUMNS),
        "best_iteration": best_iteration,
        "splits": {
            "train": _split_summary(train),
            "val": _split_summary(val),
        },
        "holdout_policy": "holdout excluded from training and threshold selection; not evaluated",
        "score_semantics": (
            "predicted_realized_ret" if config.objective == "regression" else "class_probability"
        ),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_artifact(config, metadata_path)


def score_with_artifact(artifact: FrozenArtifact) -> tuple[pd.DataFrame, float]:
    model = lgb.Booster(model_file=str(artifact.model_path))
    full = pd.read_csv(artifact.dataset_path, parse_dates=["signal_time"])
    full["score"] = model.predict(full[list(artifact.feature_columns)], num_iteration=artifact.best_iteration)
    full["entry_time"] = full["signal_time"] + BAR
    full["exit_time"] = full["entry_time"] + full["exit_offset"] * BAR
    return full.sort_values(["entry_time", "score"], ascending=[True, False]), artifact.threshold


def cache_metadata(threshold: float, artifact: FrozenArtifact | None) -> ScoreCacheMetadata:
    metadata: ScoreCacheMetadata = {"threshold": threshold}
    if artifact is not None:
        metadata["model_path"] = artifact.relative_model_path
        metadata["dataset_path"] = artifact.relative_dataset_path
        metadata["dataset_sha256"] = artifact.dataset_sha256
    return metadata


def cache_matches_artifact(
    metadata: Mapping[str, str | float],
    artifact: FrozenArtifact | None,
) -> bool:
    if artifact is None:
        return "model_path" not in metadata
    return (
        metadata.get("model_path") == artifact.relative_model_path
        and metadata.get("dataset_path") == artifact.relative_dataset_path
        and metadata.get("dataset_sha256") == artifact.dataset_sha256
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_path(config: FrozenConfig, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return config.project_dir / path


def _relative_path(config: FrozenConfig, path: Path) -> str:
    return path.relative_to(config.project_dir).as_posix()


def _split_summary(frame: pd.DataFrame) -> dict[str, int | list[str]]:
    return {
        "n": int(len(frame)),
        "range": [str(frame["signal_time"].min()), str(frame["signal_time"].max())],
    }
````

### `.`

````python
"""Train and evaluate the judgment-layer LightGBM model.

Usage:
  python3 -m src.judgment.train --data PATH --tag TAG [--side long|short|auto]
  python3 -m src.judgment.train --data PATH --tag TAG --objective regression
  # Never pass --eval-holdout unless the owner explicitly authorizes a holdout burn.

Split discipline (strict time-based, no shuffling):
- HOLDOUT_START is frozen: samples with signal_time >= 2026-05-04 00:00 UTC
  are never touched by training or tuning; they are evaluated only when
  --eval-holdout is passed (once, results reported as-is).
- The remaining samples are split by time into train (first 80%) and
  val (last 20%).

Objective (ACTIVE ≈ v11 mainline):
- binary: classify label (TP vs not) — historical default / CLI default
- regression: predict realized_ret; rank by score; entry gate = val score q90
  (same philosophy as frozen_tp5_sl2_swap_yolo_v11_reg)

Side discipline (2026-07-24 short-only):
- Datasets with a `side` column must be homogeneous (no long/short mix).
- `--side short` (or auto-detect from the column / tag containing "short")
  asserts every row is short and refuses mixed pools.
- Short tags should include `short` so outputs stay pool-tagged
  (e.g. p2b_yolo_short_30_6m_reg).

Outputs metrics JSON to analysis/output/{tag}_metrics.json and feature
importance to analysis/output/{tag}_feature_importance.csv.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from src.data.bars import BAR_CHOICES, purge_window
from src.judgment.features import FEATURE_COLUMNS

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_DIR / "data" / "judgment_dataset.csv"
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"

HOLDOUT_START = pd.Timestamp("2026-05-04 00:00:00", tz="UTC")  # frozen, do not tune on >= this
TRAIN_FRACTION = 0.8
THRESHOLDS = (0.4, 0.5, 0.6, 0.7)
# Align with frozen.DEFAULT_SCORE_QUANTILE (v11 ACTIVE entry gate).
SCORE_QUANTILE = 0.9
from src.costs import LEGACY_P0_ROUND_TRIP as ROUND_TRIP_COST  # reporting-only, see src/costs.py
SEED = 42

LGB_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 30,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": SEED,
    "verbosity": -1,
}


DEFAULT_HORIZON_BARS = 72
DEFAULT_BAR = "15m"
PURGE_WINDOW = purge_window(DEFAULT_HORIZON_BARS, DEFAULT_BAR)


def resolve_side(data: pd.DataFrame, *, side_arg: str, tag: str) -> str:
    """Resolve training side and refuse mixed long/short pools.

    side_arg:
      - long|short: assert column (if present) matches; fail on mix
      - auto: prefer unique `side` column; else infer from tag containing 'short'
    """
    if side_arg not in ("long", "short", "auto"):
        raise ValueError(f"side must be long|short|auto, got {side_arg!r}")
    col_side: str | None = None
    if "side" in data.columns:
        sides = sorted({str(s).lower() for s in data["side"].dropna().unique()})
        if not sides:
            col_side = None
        elif len(sides) > 1:
            raise SystemExit(
                f"mixed side values in dataset: {sides}; "
                "judgment main tables must be long-only or short-only"
            )
        else:
            col_side = sides[0]
            if col_side not in ("long", "short"):
                raise SystemExit(f"unknown side value {col_side!r}; expected long|short")

    tag_implies_short = "short" in tag.lower()
    if side_arg == "auto":
        if col_side is not None:
            resolved = col_side
        elif tag_implies_short:
            resolved = "short"
        else:
            resolved = "long"
    else:
        resolved = side_arg
        if col_side is not None and col_side != resolved:
            raise SystemExit(
                f"--side {resolved} but dataset side column is {col_side!r}"
            )

    if resolved == "short" and not tag_implies_short:
        raise SystemExit(
            "short-only training requires --tag containing 'short' "
            f"(got {tag!r}) so outputs stay pool-tagged"
        )
    if resolved == "long" and tag_implies_short and col_side == "long":
        raise SystemExit(
            f"tag {tag!r} implies short but dataset/side is long; refuse ambiguous run"
        )
    return resolved


def load_splits(
    dataset_path: Path = DATASET_PATH, *, horizon_bars: int = DEFAULT_HORIZON_BARS, bar: str = DEFAULT_BAR
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    purge = purge_window(horizon_bars, bar)
    data = pd.read_csv(dataset_path, parse_dates=["signal_time"])
    data = data.sort_values("signal_time").reset_index(drop=True)
    dev = data[data["signal_time"] < HOLDOUT_START - purge].reset_index(drop=True)
    holdout = data[data["signal_time"] >= HOLDOUT_START].reset_index(drop=True)
    split_i = int(len(dev) * TRAIN_FRACTION)
    train, val = dev.iloc[:split_i], dev.iloc[split_i:]
    # purge train samples whose triple-barrier window overlaps the val period
    val_start = val["signal_time"].min()
    train = train[train["signal_time"] < val_start - purge]
    return train, val, holdout


def evaluate(
    y_true: np.ndarray,
    y_score: np.ndarray,
    returns: np.ndarray,
    *,
    objective: str = "binary",
) -> dict:
    """Rank/threshold metrics. Primary economic gate = top-decile net (0.2% RT).

    For regression, y_score is predicted realized_ret; AUC/PR are secondary
    rank diagnostics against the binary label, not the success criterion.
    """
    out = {
        "n": int(len(y_true)),
        "positive_rate": round(float(np.mean(y_true)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, y_score)), 4),
        "pr_auc": round(float(average_precision_score(y_true, y_score)), 4),
        "thresholds": {},
    }
    for threshold in THRESHOLDS:
        pred = (y_score >= threshold).astype(int)
        out["thresholds"][str(threshold)] = {
            "n_signals": int(pred.sum()),
            "precision": round(float(precision_score(y_true, pred, zero_division=0)), 4),
            "recall": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        }
    # top-decile triple-barrier expected return net of round-trip cost
    k = max(1, len(y_score) // 10)
    top_idx = np.argsort(y_score)[-k:]
    out["top_decile"] = {
        "n": int(k),
        "mean_realized_ret": round(float(returns[top_idx].mean()), 5),
        "mean_net_ret": round(float(returns[top_idx].mean() - ROUND_TRIP_COST), 5),
        "win_rate": round(float(y_true[top_idx].mean()), 4),
    }
    out["all_mean_net_ret"] = round(float(returns.mean() - ROUND_TRIP_COST), 5)
    if objective == "regression":
        rho = spearmanr(y_score, returns).statistic
        out["spearman_score_vs_ret"] = None if rho is None or np.isnan(rho) else round(float(rho), 4)
        thr = float(np.quantile(y_score, SCORE_QUANTILE))
        q_mask = y_score >= thr
        out["threshold_val_q90"] = round(thr, 8)
        out["score_quantile"] = SCORE_QUANTILE
        out["above_q90"] = {
            "n": int(q_mask.sum()),
            "mean_realized_ret": round(float(returns[q_mask].mean()), 5) if q_mask.any() else None,
            "mean_net_ret": (
                round(float(returns[q_mask].mean() - ROUND_TRIP_COST), 5) if q_mask.any() else None
            ),
            "win_rate": round(float(y_true[q_mask].mean()), 4) if q_mask.any() else None,
        }
    return out


def permutation_pvalue(y_true: np.ndarray, y_prob: np.ndarray, *, n_perm: int = 1000) -> float:
    """P(label-permuted AUC >= observed AUC); tests AUC > 0.5 significance."""
    rng = np.random.default_rng(SEED)
    observed = roc_auc_score(y_true, y_prob)
    hits = 0
    for _ in range(n_perm):
        if roc_auc_score(rng.permutation(y_true), y_prob) >= observed:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def train_model(
    train: pd.DataFrame,
    val: pd.DataFrame,
    *,
    feature_columns: Sequence[str] = FEATURE_COLUMNS,
    objective: str = "binary",
) -> lgb.Booster:
    """Train judgment model.

    objective:
      - binary: classify label (TP vs not) — historical default
      - regression: rank by predicted realized_ret (economic target; 2026-07-15+)
    """
    cols = list(feature_columns)
    params = dict(LGB_PARAMS)
    if objective == "regression":
        params["objective"] = "regression"
        y_train, y_val = train["realized_ret"], val["realized_ret"]
    elif objective == "binary":
        params["objective"] = "binary"
        y_train, y_val = train["label"], val["label"]
    else:
        raise ValueError(f"unknown objective {objective!r}; expected binary|regression")
    dtrain = lgb.Dataset(train[cols], label=y_train)
    dval = lgb.Dataset(val[cols], label=y_val, reference=dtrain)
    return lgb.train(
        params,
        dtrain,
        num_boost_round=600,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )


def train_baseline(train: pd.DataFrame) -> tuple[StandardScaler, LogisticRegression]:
    """Naive baseline: logistic regression on ma_spread_pct alone."""
    scaler = StandardScaler()
    x = scaler.fit_transform(train[["ma_spread_pct"]].fillna(0))
    model = LogisticRegression()
    model.fit(x, train["label"])
    return scaler, model


def baseline_prob(scaler: StandardScaler, model: LogisticRegression, frame: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(scaler.transform(frame[["ma_spread_pct"]].fillna(0)))[:, 1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-holdout", action="store_true", help="Evaluate the frozen holdout (run once).")
    parser.add_argument("--data", type=Path, default=DATASET_PATH, help="Dataset CSV from build_dataset.")
    parser.add_argument(
        "--tag",
        default="p2b",
        help="Output file prefix; short-only runs must include 'short' (e.g. p2b_v2_strict_short).",
    )
    parser.add_argument(
        "--side",
        choices=("long", "short", "auto"),
        default="auto",
        help="Force side assertion. auto = unique side column, else tag containing 'short'.",
    )
    parser.add_argument(
        "--objective",
        choices=("binary", "regression"),
        default="binary",
        help="binary=label classifier (legacy CLI default); "
        "regression=predict realized_ret (v11 ACTIVE philosophy).",
    )
    parser.add_argument("--bar", choices=BAR_CHOICES, default=DEFAULT_BAR)
    parser.add_argument("--horizon-bars", type=int, default=DEFAULT_HORIZON_BARS)
    parser.add_argument(
        "--features-file",
        type=Path,
        default=None,
        help="Optional text file of feature names (one per line; # comments ok). "
        "Must be a non-empty subset of FEATURE_COLUMNS. Single-variable ablations only.",
    )
    args = parser.parse_args()

    feature_columns = list(FEATURE_COLUMNS)
    if args.features_file is not None:
        if not args.features_file.exists():
            raise SystemExit(f"--features-file missing: {args.features_file}")
        wanted: list[str] = []
        for ln in args.features_file.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            wanted.append(s)
        unknown = [c for c in wanted if c not in FEATURE_COLUMNS]
        if unknown:
            raise SystemExit(f"--features-file has unknown columns: {unknown}")
        if not wanted:
            raise SystemExit("--features-file is empty")
        # Preserve file order (importance rank) but de-dupe.
        seen: set[str] = set()
        feature_columns = []
        for c in wanted:
            if c not in seen:
                seen.add(c)
                feature_columns.append(c)

    raw = pd.read_csv(args.data, parse_dates=["signal_time"])
    side = resolve_side(raw, side_arg=args.side, tag=args.tag)

    train, val, holdout = load_splits(args.data, horizon_bars=args.horizon_bars, bar=args.bar)
    model = train_model(
        train, val, feature_columns=feature_columns, objective=args.objective
    )
    scaler, base = train_baseline(train)

    val_score = model.predict(val[feature_columns], num_iteration=model.best_iteration)
    results = {
        "dataset": str(args.data),
        "side": side,
        "objective": args.objective,
        "score_semantics": (
            "predicted_realized_ret" if args.objective == "regression" else "class_probability"
        ),
        "feature_columns": feature_columns,
        "n_features": len(feature_columns),
        "bar": args.bar,
        "horizon_bars": args.horizon_bars,
        "purge_window": str(purge_window(args.horizon_bars, args.bar)),
        "holdout_start": str(HOLDOUT_START),
        "holdout_policy": "holdout excluded from training and threshold selection; not evaluated"
        if not args.eval_holdout
        else "holdout evaluated once (owner-authorized)",
        "splits": {
            "train": {"n": len(train), "range": [str(train["signal_time"].min()), str(train["signal_time"].max())]},
            "val": {"n": len(val), "range": [str(val["signal_time"].min()), str(val["signal_time"].max())]},
            "holdout": {"n": len(holdout), "range": [str(holdout["signal_time"].min()), str(holdout["signal_time"].max())]},
        },
        "best_iteration": model.best_iteration,
        "val": evaluate(
            val["label"].to_numpy(),
            val_score,
            val["realized_ret"].to_numpy(),
            objective=args.objective,
        ),
        "val_permutation_p": permutation_pvalue(val["label"].to_numpy(), val_score),
        "val_baseline_ma_spread_logreg": evaluate(
            val["label"].to_numpy(),
            baseline_prob(scaler, base, val),
            val["realized_ret"].to_numpy(),
            objective="binary",
        ),
    }

    importance = pd.DataFrame({
        "feature": feature_columns,
        "gain": model.feature_importance(importance_type="gain"),
        "split": model.feature_importance(importance_type="split"),
    }).sort_values("gain", ascending=False).reset_index(drop=True)
    results["feature_importance_top10"] = importance.head(10)[["feature", "gain"]].to_dict("records")

    if args.eval_holdout:
        hold_score = model.predict(holdout[feature_columns], num_iteration=model.best_iteration)
        results["holdout"] = evaluate(
            holdout["label"].to_numpy(),
            hold_score,
            holdout["realized_ret"].to_numpy(),
            objective=args.objective,
        )
        results["holdout_permutation_p"] = permutation_pvalue(
            holdout["label"].to_numpy(), hold_score
        )
        results["holdout_baseline_ma_spread_logreg"] = evaluate(
            holdout["label"].to_numpy(),
            baseline_prob(scaler, base, holdout),
            holdout["realized_ret"].to_numpy(),
            objective="binary",
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    importance.to_csv(OUTPUT_DIR / f"{args.tag}_feature_importance.csv", index=False)
    (OUTPUT_DIR / f"{args.tag}_metrics.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
````

### `.`

````python
"""YOLO detector as judgment-layer candidate source (mainline after 2026-07-15).

Replaces rule `scan_candidates` / `forward_candidate_indices` for the critical
path. Downstream labeling, features, LightGBM freeze, and TP5/SL2 exits are
unchanged — only *which bars* are proposed as signals differs.

Requires ultralytics/torch (use `.venv/bin/python` for any path that calls
`scan_series_with_yolo`).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.detection.data import add_mas
from src.detection.render import render_chart
from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS
from src.judgment.labeling import HORIZON_BARS

PROJECT_DIR = Path(__file__).resolve().parents[2]
WINDOW = 200
STRIDE = 50
DEFAULT_CONF = 0.30
DEFAULT_WEIGHTS = PROJECT_DIR / "models" / "owner_best.pt"
# A′ tip-edge gate (owner-approved 2026-07-21): only accept boxes whose
# mapped signal bar sits in the last N bars of the scan window.
# Source: analysis/p_box_to_bar_lag.md — KORU right_norm≈97.5% still mapped
# 3 bars back of tip, so the gate is bar-offset based, not pixel %.
# N=2 → bar_in_win >= window-2 (tip or tip-1; offset 0..1). Does NOT invent
# tip fires when the model draws 0 boxes on tip/tip-1.
TIP_EDGE_BARS = 2
# Optional tip-window conf floor (env TIP_CONF). When set, the tip window
# (rightmost start) may use a lower floor than other live windows; predict
# runs at min(tip_conf, conf) then non-tip windows post-filter to `conf`.
# Unset → every window uses the same `conf` (default 0.30).
# Optional right-bias (env FABLE_YOLO_RIGHT_BIAS=1): within min_gap, keep the
# rightmost signal instead of the leftmost (live multi-window only).
# Base temp dir; each predict call uses a unique filename (thread-safe live scan).
_TMP_DIR = PROJECT_DIR / "data"


def resolve_yolo_mode(default: str = "live") -> str:
    """Mainline scan mode from FABLE_YOLO_MODE (tip|live|full). Default live."""
    raw = os.environ.get("FABLE_YOLO_MODE", "").strip().lower()
    if raw in ("tip", "live", "full"):
        return raw
    return default if default in ("tip", "live", "full") else "live"


def resolve_tip_conf(fallback: float | None = None) -> float | None:
    """Optional tip-window conf from TIP_CONF. None = use the shared conf."""
    raw = os.environ.get("TIP_CONF", "").strip()
    if not raw:
        return fallback
    try:
        v = float(raw)
    except ValueError:
        return fallback
    if not (0.0 < v < 1.0):
        return fallback
    return v


def resolve_right_bias(default: bool = False) -> bool:
    """Prefer rightmost signal within min_gap when FABLE_YOLO_RIGHT_BIAS=1."""
    raw = os.environ.get("FABLE_YOLO_RIGHT_BIAS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default

_model_cache: dict[str, Any] = {}
_predict_lock = threading.Lock()
_predict_device: str | None = None
_tip_edge_lock = threading.Lock()
_tip_edge_rejected_total = 0


def reset_tip_edge_rejected() -> None:
    """Zero the process-wide tip_edge_rejected counter (call before a pulse)."""
    global _tip_edge_rejected_total
    with _tip_edge_lock:
        _tip_edge_rejected_total = 0


def get_tip_edge_rejected() -> int:
    """Boxes dropped by the A′ tip-edge gate since last reset."""
    with _tip_edge_lock:
        return _tip_edge_rejected_total


def _bump_tip_edge_rejected(n: int = 1) -> None:
    global _tip_edge_rejected_total
    if n <= 0:
        return
    with _tip_edge_lock:
        _tip_edge_rejected_total += n


def _resolve_predict_device() -> str:
    """Prefer CUDA on VPS; fall back to CPU (MPS has hung multi-series scans)."""
    global _predict_device
    if _predict_device is not None:
        return _predict_device
    forced = os.environ.get("FABLE_YOLO_DEVICE", "").strip()
    if forced:
        _predict_device = forced
        return _predict_device
    try:
        import torch

        if torch.cuda.is_available():
            _predict_device = "0"
            return _predict_device
    except Exception:  # noqa: BLE001
        pass
    _predict_device = "cpu"
    return _predict_device


def right_edge_to_bar(cx: float, w: float, tf, *, n_bars: int) -> int:
    """Normalized box right edge -> bar index within the window."""
    right_px = (cx + w / 2) * tf.width
    if tf.plot_w <= 0:
        return n_bars - 1
    idx = round((right_px - tf.left) / tf.plot_w * (tf.n_bars - 1))
    return int(min(max(idx, 0), tf.n_bars - 1))


def load_yolo_model(weights: str | Path | None = None):
    """Lazy-load and cache YOLO weights (heavy import kept local)."""
    path = str(Path(weights) if weights is not None else DEFAULT_WEIGHTS)
    if path not in _model_cache:
        from ultralytics import YOLO

        if not Path(path).exists():
            raise FileNotFoundError(f"YOLO weights missing: {path}")
        _model_cache[path] = YOLO(path)
    return _model_cache[path]


def scan_series_with_yolo(
    frame: pd.DataFrame,
    model=None,
    *,
    conf: float = DEFAULT_CONF,
    window: int = WINDOW,
    stride: int = STRIDE,
    min_gap: int = MIN_GAP_BARS,
    tmp_png: Path | None = None,
    start_from_i: int | None = None,
    mode: str = "full",
    tip_edge_bars: int = TIP_EDGE_BARS,
    tip_conf: float | None = None,
    right_bias: bool | None = None,
    signal_time_lo: pd.Timestamp | None = None,
    signal_time_hi: pd.Timestamp | None = None,
) -> list[int]:
    """Return sorted signal bar indices for one OHLCV frame (causal at each bar).

    mode:
      - "full": offline dataset build (stride over history); no tip-edge gate
      - "live": forward/mainline — only windows near the right edge (and
        covering start_from_i..end). Avoids multi-hour full-history scans.
      - "tip": single rightmost window only (H-TIP / FABLE_YOLO_MODE=tip;
        ~1 predict/series vs live's ≤6). Allows tip-bar pending entry like live.

    tip_edge_bars (live/tip only): keep boxes with
    ``bar_in_win >= window - tip_edge_bars`` (default TIP_EDGE_BARS=2).
    See analysis/p_box_to_bar_lag.md option A′. Rejected boxes increment
    ``tip_edge_rejected`` (get_tip_edge_rejected). Set 0 to disable.

    tip_conf: optional lower floor for the tip window only (else env TIP_CONF).
    right_bias: within min_gap keep rightmost signal (else env FABLE_YOLO_RIGHT_BIAS).

    signal_time_lo / signal_time_hi (optional, hi exclusive): only emit signal
    bars whose open_time is in ``[lo, hi)``. Warmup / rendering still use bars
    before lo (full frame kept); only the slid-window schedule is clipped so
    windows that cannot produce an in-range signal are skipped.
    """
    if model is None:
        model = load_yolo_model()
    if len(frame) < WARMUP_BARS + window + 2:
        return []
    if tip_conf is None:
        tip_conf = resolve_tip_conf()
    if right_bias is None:
        right_bias = resolve_right_bias(False)
    # Optional signal-time gate → bar index bounds (inclusive lo, inclusive hi).
    i_lo: int | None = None
    i_hi: int | None = None
    if signal_time_lo is not None or signal_time_hi is not None:
        if "open_time" not in frame.columns:
            return []
        times = pd.to_datetime(frame["open_time"], utc=True)
        mask = pd.Series(True, index=frame.index)
        if signal_time_lo is not None:
            lo = pd.Timestamp(signal_time_lo)
            if lo.tzinfo is None:
                lo = lo.tz_localize("UTC")
            else:
                lo = lo.tz_convert("UTC")
            mask &= times >= lo
        if signal_time_hi is not None:
            hi = pd.Timestamp(signal_time_hi)
            if hi.tzinfo is None:
                hi = hi.tz_localize("UTC")
            else:
                hi = hi.tz_convert("UTC")
            mask &= times < hi
        idxs = np.flatnonzero(mask.to_numpy())
        if len(idxs) == 0:
            return []
        i_lo = int(idxs[0])
        i_hi = int(idxs[-1])
    enriched_ma = add_mas(frame)
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Unique path per call (thread id + pid) so parallel live scans never clobber.
    if tmp_png is None:
        tmp_png = _TMP_DIR / f"_yolo_cand_tmp_{os.getpid()}_{threading.get_ident()}.png"
    last_start = len(frame) - window
    tip_last_start = last_start  # true series tip (for tip-window conf / tip mode)
    first_start = WARMUP_BARS
    if start_from_i is not None:
        first_start = max(first_start, int(start_from_i) - window + 1)
    if i_lo is not None and i_hi is not None:
        # Windows that can map a box onto [i_lo, i_hi]; pre-lo bars still render.
        first_start = max(first_start, i_lo - window + 1)
        last_start = min(last_start, i_hi)
        if last_start < first_start:
            return []
    if mode == "tip":
        # Tip-only: single rightmost window (right edge = last closed bar).
        starts = [tip_last_start] if tip_last_start >= first_start else []
    elif mode == "live":
        # Live schedule (2026-07-20): pin the tip and two bars back, then
        # coarse stride for context — at most 6 windows. The 14-window
        # "tip-dense" schedule (backs 0..21 + half-stride walk) rested on a
        # false premise: a box's right edge maps to ANY bar inside the window
        # (right_edge_to_bar), so recent-but-not-tip bars are already
        # discoverable from the tip window itself (EDEN 2026-07-19: the tip
        # window's mid-window box mapped 35 bars back). Its real effect was
        # 14/6 x predict cost: pulses went 6->25 min wall, the 15-min cadence
        # degraded to 25 min, and rows landed older than the 30-min freshness
        # gate — the dense schedule destroyed the very tip-latency it chased.
        # Owner doctrine 2026-07-23: LIVE detection means the newest bars ONLY.
        # The old stride walk-back windows (tip-50/-100/-150) could discover
        # nothing but 12h+ old bars — hindsight rows by construction, every one
        # of them freshness-rejected downstream. A path that can only produce
        # after-the-fact signals must not exist. tip / tip-1 / tip-2 cover
        # pulse-miss overlap; everything older is not a signal, it's history.
        starts_set: set[int] = set()
        for back in (0, 1, 2):
            s = tip_last_start - back
            if s >= first_start:
                starts_set.add(s)
        starts = sorted(starts_set, reverse=True)
    else:
        starts = list(range(first_start, last_start + 1, stride))

    chosen: list[int] = []
    device = _resolve_predict_device()
    n_fail = 0
    last_err: str | None = None
    # Chunked render→predict→unlink: live/tip stay ≤6 windows (one chunk);
    # full offline can be hundreds of windows — holding all PNGs + one giant
    # batch predict OOM-killed Mac scans (2026-07-24 short tip_v1b pool build).
    # Chunk size keeps CPU batching benefit without multi-GB RSS per series.
    predict_conf = conf
    if tip_conf is not None and tip_conf < conf:
        predict_conf = tip_conf
    tip_edge_rejected = 0
    apply_tip_edge = mode in ("live", "tip") and tip_edge_bars > 0
    min_bar_in_win = window - tip_edge_bars if apply_tip_edge else 0
    allow_pending_entry = mode in ("live", "tip")
    # Live/tip: one chunk. Full offline: small chunks — 16 still Jetsam'd
    # 16GB Macs mid-series (2026-07-24 short tip_v1b pool; residual 16 PNGs/pid).
    # Override with FABLE_YOLO_FULL_CHUNK (int >=1) if needed.
    if mode in ("live", "tip"):
        chunk_size = len(starts)
    else:
        raw_chunk = os.environ.get("FABLE_YOLO_FULL_CHUNK", "4").strip()
        try:
            chunk_size = int(raw_chunk)
        except ValueError:
            chunk_size = 4
    chunk_size = max(1, int(chunk_size))
    for chunk_i in range(0, len(starts), chunk_size):
        chunk_starts = starts[chunk_i : chunk_i + chunk_size]
        rendered: list[tuple[int, object, Path]] = []
        for k, start in enumerate(chunk_starts):
            sub = enriched_ma.iloc[start : start + window]
            win_png = tmp_png.with_name(f"{tmp_png.stem}_{chunk_i + k}.png")
            try:
                _, tf = render_chart(sub, out_path=win_png)
            except Exception as exc:  # noqa: BLE001 — keep series alive; count failures
                n_fail += 1
                last_err = f"{type(exc).__name__}: {exc}"
                continue
            rendered.append((start, tf, win_png))
        results = []
        if rendered:
            try:
                # Serialize predict: ultralytics is not reliably thread-safe.
                with _predict_lock:
                    results = model.predict(
                        [str(p) for _, _, p in rendered],
                        conf=predict_conf,
                        verbose=False,
                        device=device,
                    )
            except Exception as exc:  # noqa: BLE001
                n_fail += len(rendered)
                last_err = f"{type(exc).__name__}: {exc}"
                results = []
        for (start, tf, win_png), res in zip(rendered, results):
            try:
                boxes = res.boxes
                if boxes is None:
                    continue
                is_tip_window = start == tip_last_start
                floor = tip_conf if (is_tip_window and tip_conf is not None) else conf
                xywhn = boxes.xywhn.cpu().numpy()
                confs = boxes.conf.cpu().numpy() if boxes.conf is not None else None
                for bi, b in enumerate(xywhn):
                    if confs is not None and float(confs[bi]) < floor:
                        continue
                    cx, _, w, _ = map(float, b[:4])
                    bar_in_win = right_edge_to_bar(cx, w, tf, n_bars=window)
                    if apply_tip_edge and bar_in_win < min_bar_in_win:
                        tip_edge_rejected += 1
                        continue
                    signal_i = start + bar_in_win
                    if signal_i < WARMUP_BARS or signal_i >= len(frame):
                        continue
                    # Offline full builds need the entry bar for labels; live/tip must
                    # NOT wait — tip bar is the real-time path (entry backfills next pulse).
                    if not allow_pending_entry and signal_i + 1 >= len(frame):
                        continue
                    if start_from_i is not None and signal_i < start_from_i:
                        continue
                    if i_lo is not None and signal_i < i_lo:
                        continue
                    if i_hi is not None and signal_i > i_hi:
                        continue
                    chosen.append(int(signal_i))
            finally:
                try:
                    win_png.unlink(missing_ok=True)
                except OSError:
                    pass
    if tip_edge_rejected:
        _bump_tip_edge_rejected(tip_edge_rejected)
    if n_fail and n_fail >= len(starts):
        # Only noisy when the whole series failed (data/render/device issue).
        print(f"yolo_live: all {n_fail} windows failed last={last_err}", flush=True)
    if not chosen:
        return []
    if right_bias:
        # Prefer rightmost within min_gap (position bias for multi-window live).
        chosen_desc = sorted(set(chosen), reverse=True)
        deduped: list[int] = []
        for si in chosen_desc:
            if not deduped or deduped[-1] - si >= min_gap:
                deduped.append(si)
        return sorted(deduped)
    chosen = sorted(set(chosen))
    deduped = []
    for si in chosen:
        if not deduped or si - deduped[-1] >= min_gap:
            deduped.append(si)
    return deduped


def dedupe_indices(indices: list[int], min_gap: int = MIN_GAP_BARS) -> list[int]:
    out: list[int] = []
    for si in sorted(indices):
        if not out or si - out[-1] >= min_gap:
            out.append(int(si))
    return out
````

### `.`

````python
from __future__ import annotations

import math

import pandas as pd

from src.judgment.forward import ForwardRecord, merge_forward_log, resolve_forward_exit


def _record(status: str, detected_at: str = "2026-07-09T00:00:00+00:00") -> ForwardRecord:
    return {
        "source": "okx",
        "symbol": "BTC_USDT_SWAP",
        "signal_time": "2026-07-08 00:00:00+00:00",
        "detected_at": detected_at,
        "status": status,
        "score": 0.5,
        "threshold": 0.4,
        "model_path": "models/frozen.txt",
        "dataset_sha256": "abc",
        "signal_i": 1,
        "entry_time": "2026-07-08 00:15:00+00:00",
        "entry_price": 100.0,
        "maker_filled": True,
        "outcome": "tp" if status == "closed" else "",
        "label": 1 if status == "closed" else -1,
        "exit_offset": 1 if status == "closed" else 0,
        "exit_time": "2026-07-08 00:30:00+00:00" if status == "closed" else "",
        "realized_ret": 0.05 if status == "closed" else math.nan,
        "atr_pct": 0.01,
        "dense_run_len": 8,
    }


def test_merge_forward_log_updates_open_row_without_duplicate() -> None:
    existing = pd.DataFrame([_record("open", "first-seen")])

    result = merge_forward_log(existing, [_record("closed", "later-seen")])

    assert result.new_signals == 0
    assert result.closed_updates == 1
    assert len(result.frame) == 1
    row = result.frame.iloc[0]
    assert row["detected_at"] == "first-seen"
    assert row["status"] == "closed"
    assert row["outcome"] == "tp"


def test_merge_forward_log_is_idempotent_for_closed_rows() -> None:
    existing = pd.DataFrame([_record("closed", "first-seen")])

    result = merge_forward_log(existing, [_record("closed", "later-seen")])

    assert result.new_signals == 0
    assert result.closed_updates == 0
    assert len(result.frame) == 1
    assert result.frame.iloc[0]["detected_at"] == "first-seen"


def test_resolve_forward_exit_marks_open_before_horizon_without_barrier() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=4, freq="15min", tz="UTC"),
            "open": [99.0, 100.0, 100.0, 100.0],
            "high": [100.0, 101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01],
        }
    )

    outcome = resolve_forward_exit(frame, 1)

    assert outcome is not None
    assert outcome.status == "open"
    assert outcome.label == -1


def test_resolve_forward_exit_closes_on_partial_tp_hit() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=4, freq="15min", tz="UTC"),
            "open": [99.0, 100.0, 100.0, 100.0],
            "high": [100.0, 101.0, 106.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01],
        }
    )

    outcome = resolve_forward_exit(frame, 1)

    assert outcome is not None
    assert outcome.status == "closed"
    assert outcome.outcome == "tp"
    assert outcome.exit_offset == 1


def test_resolve_forward_exit_tip_signal_is_pending_open() -> None:
    """Signal bar == newest closed bar: record as open, never drop (tip path)."""
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=4, freq="15min", tz="UTC"),
            "open": [99.0, 100.0, 100.0, 100.0],
            "high": [100.0, 101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01],
        }
    )

    outcome = resolve_forward_exit(frame, 3)  # tip bar

    assert outcome is not None
    assert outcome.status == "open"
    assert outcome.label == -1


def test_resolve_forward_exit_tip_signal_still_gated_on_atr() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=2, freq="15min", tz="UTC"),
            "open": [99.0, 100.0],
            "high": [100.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "atr14": [1.0, 1.0],
            "atr_pct": [0.01, 0.0001],  # below ATR_PCT_MIN at tip
        }
    )

    assert resolve_forward_exit(frame, 1) is None


def _tip_record() -> ForwardRecord:
    rec = _record("open", "tip-seen")
    rec["entry_price"] = 100.5  # proxy: signal bar close
    rec["maker_filled"] = None  # entry-pending sentinel
    return rec


def test_merge_backfills_tip_entry_fields_once_entry_bar_prints() -> None:
    existing = pd.DataFrame([_tip_record()])

    update = _record("open", "later-seen")  # real entry now known
    result = merge_forward_log(existing, [update])

    assert result.new_signals == 0
    row = result.frame.iloc[0]
    assert row["detected_at"] == "tip-seen"  # lag accounting keeps first-seen
    assert row["entry_price"] == 100.0
    assert row["maker_filled"] == True  # noqa: E712 -- object column
    assert row["status"] == "open"


def test_merge_backfills_entry_even_when_close_arrives_same_pulse() -> None:
    existing = pd.DataFrame([_tip_record()])

    result = merge_forward_log(existing, [_record("closed", "later-seen")])

    assert result.closed_updates == 1
    row = result.frame.iloc[0]
    assert row["detected_at"] == "tip-seen"
    assert row["entry_price"] == 100.0
    assert row["maker_filled"] == True  # noqa: E712
    assert row["status"] == "closed"
    assert row["outcome"] == "tp"


def test_merge_does_not_reopen_or_touch_confirmed_entry() -> None:
    confirmed = _record("open", "first-seen")
    existing = pd.DataFrame([confirmed])

    shifted = _record("open", "later-seen")
    shifted["entry_price"] = 42.0  # a re-scan must not rewrite confirmed entries
    result = merge_forward_log(existing, [shifted])

    row = result.frame.iloc[0]
    assert row["entry_price"] == 100.0
    assert row["detected_at"] == "first-seen"
````

### `.`

````python
from __future__ import annotations

import pandas as pd

from src.backtest.run import MAX_CONCURRENT, simulate


def _signal(
    *,
    symbol: str,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    score: float = 0.9,
) -> dict[str, object]:
    return {
        "source": "okx",
        "symbol": symbol,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "score": score,
        "outcome": "tp",
        "realized_ret": 0.01,
    }


def test_simulate_skips_overlapping_positions_for_same_symbol() -> None:
    start = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    signals = pd.DataFrame(
        [
            _signal(symbol="BTC_USDT_SWAP", entry_time=start, exit_time=start + pd.Timedelta(hours=1)),
            _signal(
                symbol="BTC_USDT_SWAP",
                entry_time=start + pd.Timedelta(minutes=15),
                exit_time=start + pd.Timedelta(hours=2),
                score=0.95,
            ),
            _signal(
                symbol="BTC_USDT_SWAP",
                entry_time=start + pd.Timedelta(hours=1),
                exit_time=start + pd.Timedelta(hours=3),
            ),
        ]
    )

    trades = simulate(signals, threshold=0.5)

    assert len(trades) == 2
    assert trades["entry_time"].tolist() == [start, start + pd.Timedelta(hours=1)]


def test_simulate_respects_global_concurrency_cap() -> None:
    start = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    signals = pd.DataFrame(
        [
            _signal(
                symbol=f"SYM{i}_USDT_SWAP",
                entry_time=start,
                exit_time=start + pd.Timedelta(hours=1),
                score=1.0 - i * 0.01,
            )
            for i in range(MAX_CONCURRENT + 3)
        ]
    )

    trades = simulate(signals, threshold=0.5)

    assert len(trades) == MAX_CONCURRENT
    assert trades["symbol"].tolist() == [f"SYM{i}_USDT_SWAP" for i in range(MAX_CONCURRENT)]
````

### `.`

````python
"""A′ tip-edge gate: only last N bars of the scan window enter the ledger.

Source: analysis/p_box_to_bar_lag.md (KORU right_norm≈97.5% → bar offset 3).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from src.detection.render import ChartTransform
from src.judgment.yolo_candidates import (
    TIP_EDGE_BARS,
    WINDOW,
    right_edge_to_bar,
    scan_series_with_yolo,
)


def _tf(n_bars: int = WINDOW, width: int = 1280, height: int = 742) -> ChartTransform:
    left = top = 12
    return ChartTransform(
        n_bars=n_bars,
        width=width,
        height=height,
        left=left,
        top=top,
        plot_w=width - 2 * left,
        plot_h=height - 2 * top,
        price_min=100.0,
        price_max=110.0,
        candle_half_w=3,
    )


def _xywhn_for_bar(tf: ChartTransform, bar: int, w_norm: float = 0.02) -> list[float]:
    """Build xywhn whose right edge maps to `bar` via right_edge_to_bar."""
    right_px = float(tf.x_at(bar))
    right_norm = right_px / tf.width
    return [right_norm - w_norm / 2.0, 0.5, w_norm, 0.1]


def _frame(n: int = 500) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [1.0] * n,
            "high": [1.1] * n,
            "low": [0.9] * n,
            "close": [1.0] * n,
            "volume": [100.0] * n,
        }
    )


def _predict_with_bars(tf: ChartTransform, bars: list[int]) -> MagicMock:
    model = MagicMock()
    res = MagicMock()
    xywhn = np.array([_xywhn_for_bar(tf, b) for b in bars], dtype=np.float64)
    boxes = MagicMock()
    boxes.xywhn = MagicMock()
    boxes.xywhn.cpu.return_value = MagicMock()
    boxes.xywhn.cpu.return_value.numpy.return_value = xywhn
    res.boxes = boxes
    model.predict.return_value = [res]
    return model


def test_tip_edge_bars_default_is_two() -> None:
    assert TIP_EDGE_BARS == 2


def test_right_edge_to_bar_roundtrip() -> None:
    """Regression: x_at ↔ right_edge_to_bar is exact for all bars (incl. non-square)."""
    tf = _tf(n_bars=WINDOW)
    for bar in range(WINDOW):
        cx, _, w, _ = _xywhn_for_bar(tf, bar)
        assert right_edge_to_bar(cx, w, tf, n_bars=WINDOW) == bar


def test_tip_edge_accepts_tip_and_tip_minus_one_live() -> None:
    """bar_in_win 199 (tip) and 198 (tip-1) pass; N=2 (min_gap=1 so both survive)."""
    tf = _tf()
    frame = _frame()
    tip = len(frame) - 1
    model = _predict_with_bars(tf, [WINDOW - 1, WINDOW - 2])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(
            frame, model=model, mode="live", window=WINDOW, tip_edge_bars=2, min_gap=1
        )
    assert tip in out
    assert tip - 1 in out


def test_tip_edge_rejects_koru_offset_three() -> None:
    """KORU-class: bar_in_win=196 (offset 3) rejected on tip and live tip-window."""
    tf = _tf()
    frame = _frame()
    koru_bar = WINDOW - 4  # 196
    cx, _, w, _ = _xywhn_for_bar(tf, koru_bar)
    assert right_edge_to_bar(cx, w, tf, n_bars=WINDOW) == koru_bar
    model = _predict_with_bars(tf, [koru_bar])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        tip_out = scan_series_with_yolo(
            frame, model=model, mode="tip", window=WINDOW, tip_edge_bars=2
        )
    assert tip_out == []
    # live: same xywhn on every window is an unrealistic mock; assert the gate
    # drops bar_in_win=196 on the tip window via tip_edge_rejected bump.
    from src.judgment.yolo_candidates import get_tip_edge_rejected, reset_tip_edge_rejected

    reset_tip_edge_rejected()
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        scan_series_with_yolo(frame, model=model, mode="live", window=WINDOW, tip_edge_bars=2)
    assert get_tip_edge_rejected() >= 1


def test_tip_mode_accepts_tip_bar() -> None:
    """tip mode aligns with live realtime path: tip bar itself may enter."""
    tf = _tf()
    frame = _frame()
    tip = len(frame) - 1
    model = _predict_with_bars(tf, [WINDOW - 1])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(frame, model=model, mode="tip", window=WINDOW, tip_edge_bars=2)
    assert out == [tip]


def test_tip_mode_accepts_tip_minus_one() -> None:
    """tip-1 (198) still passes tip-edge N=2."""
    tf = _tf()
    frame = _frame()
    tip = len(frame) - 1
    model = _predict_with_bars(tf, [WINDOW - 2])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(frame, model=model, mode="tip", window=WINDOW, tip_edge_bars=2)
    assert out == [tip - 1]


def test_right_bias_keeps_rightmost_within_gap() -> None:
    """With right_bias, min_gap keeps the later (rightmost) signal."""
    tf = _tf()
    frame = _frame()
    tip = len(frame) - 1
    # Two tip-edge boxes on tip window → tip and tip-1; gap default would keep tip-1 first
    # under left-prefer; right_bias keeps tip.
    model = _predict_with_bars(tf, [WINDOW - 1, WINDOW - 2])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(
            frame,
            model=model,
            mode="live",
            window=WINDOW,
            tip_edge_bars=2,
            min_gap=18,
            right_bias=True,
        )
    assert out == [tip]


def test_resolve_yolo_mode_env(monkeypatch) -> None:
    from src.judgment.yolo_candidates import resolve_tip_conf, resolve_yolo_mode

    monkeypatch.delenv("FABLE_YOLO_MODE", raising=False)
    assert resolve_yolo_mode("live") == "live"
    monkeypatch.setenv("FABLE_YOLO_MODE", "tip")
    assert resolve_yolo_mode("live") == "tip"
    monkeypatch.setenv("TIP_CONF", "0.22")
    assert resolve_tip_conf() == 0.22
    monkeypatch.setenv("TIP_CONF", "nope")
    assert resolve_tip_conf() is None


def test_full_mode_keeps_mid_window_boxes() -> None:
    """Offline full builds must not apply the A′ gate."""
    tf = _tf()
    frame = _frame(n=500)
    koru_bar = WINDOW - 4
    model = _predict_with_bars(tf, [koru_bar])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(frame, model=model, mode="full", window=WINDOW, stride=WINDOW)
    # first full start is WARMUP; mid-window box must still enter offline ledger
    from src.judgment.candidates import WARMUP_BARS

    assert WARMUP_BARS + koru_bar in out
````

### `.`

````python
"""End-to-end guard for the 2026-07-20 real-time tip path.

A signal on the NEWEST closed bar must produce a forward-log row on the same
pulse (status=open, proxy entry, maker_filled empty), and the next pulse must
backfill the true entry fields without touching detected_at. Before this path
existed the scan dropped tip signals entirely, costing 15-22 min of edge on
every live trade (and 20-min freshness gates made trading structurally
impossible).
"""
from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

import src.judgment.forward_scan as fs
from src.judgment.forward_records import merge_forward_log, read_forward_log
from src.judgment.forward_types import ForwardScanInput


def _synthetic_frame(n_bars: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    open_time = pd.date_range("2026-07-01", periods=n_bars, freq="15min", tz="UTC")
    base = 100 + np.cumsum(rng.normal(0, 0.35, n_bars))
    spread = np.abs(rng.normal(0.4, 0.1, n_bars)) + 0.2
    opens = base
    closes = base + rng.normal(0, 0.25, n_bars)
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    return pd.DataFrame(
        {
            "ts": (open_time.view("int64") // 10**6),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.abs(rng.normal(1000, 100, n_bars)),
            "open_time": open_time.astype(str),
        }
    )


class _StubBooster:
    def predict(self, rows, num_iteration=None):  # noqa: ANN001, ARG002
        return np.full(len(rows), 0.9)


def _stub_artifact() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        threshold=0.5,
        relative_model_path="models/stub.txt",
        dataset_sha256="stub",
        model_path="models/stub.txt",
        best_iteration=1,
    )


def _run_pulse(frame: pd.DataFrame, existing: pd.DataFrame, monkeypatch: pytest.MonkeyPatch, detected_at: str):
    tip_i = len(frame) - 1
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
    monkeypatch.setattr(
        fs, "iter_series", lambda **kw: iter([("okx", "TESTCOIN_USDT_SWAP", frame)])
    )
    monkeypatch.setattr(fs, "forward_candidate_indices", lambda enriched, **kw: [tip_i])
    scan = fs.scan_forward_records(
        ForwardScanInput(
            artifact=_stub_artifact(),
            booster=_StubBooster(),
            detected_at=detected_at,
            start_time=pd.Timestamp("2026-07-01", tz="UTC"),
            existing_log=existing,
        )
    )
    return scan


def test_tip_signal_recorded_same_pulse_and_backfilled_next(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    frame_t0 = _synthetic_frame(650)
    empty = read_forward_log(tmp_path / "missing.csv")

    # Pulse A: signal bar IS the tip -- must be recorded, not dropped.
    scan_a = _run_pulse(frame_t0, empty, monkeypatch, "pulse-A")
    assert len(scan_a.records) == 1
    rec = scan_a.records[0]
    assert rec["status"] == "open"
    assert rec["maker_filled"] is None  # entry pending sentinel
    sig_time = pd.Timestamp(rec["signal_time"])
    assert pd.Timestamp(rec["entry_time"]) == sig_time + pd.Timedelta(minutes=15)
    # proxy entry = signal bar close
    enriched_tip = float(frame_t0["close"].iloc[-1])
    assert rec["entry_price"] == pytest.approx(enriched_tip)

    merged_a = merge_forward_log(empty, scan_a.records)
    assert merged_a.new_signals == 1

    # CSV round-trip must preserve the pending sentinel (NaN, not False).
    log_path = tmp_path / "forward_log.csv"
    merged_a.frame.to_csv(log_path, index=False)
    persisted = read_forward_log(log_path)
    assert pd.isna(persisted.iloc[0]["maker_filled"])

    # Pulse B: one more bar printed; tracked key re-resolves with real entry.
    frame_t1 = _synthetic_frame(651)  # same seed -> same first 650 bars + 1
    scan_b = _run_pulse_tracked(frame_t1, persisted, monkeypatch, "pulse-B")
    assert len(scan_b.records) == 1
    rec_b = scan_b.records[0]
    assert rec_b["maker_filled"] is not None

    merged_b = merge_forward_log(persisted, scan_b.records)
    assert merged_b.new_signals == 0
    row = merged_b.frame.iloc[0]
    assert row["detected_at"] == "pulse-A"  # first-seen wins (lag accounting)
    assert row["entry_price"] == pytest.approx(float(frame_t1["open"].iloc[650]))
    assert not pd.isna(row["maker_filled"])


def _run_pulse_tracked(frame, existing, monkeypatch, detected_at):
    """Pulse where the candidate comes from the tracked-open key injection."""
    signal_i = len(frame) - 2  # yesterday's tip, now one bar back
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
    monkeypatch.setattr(
        fs, "iter_series", lambda **kw: iter([("okx", "TESTCOIN_USDT_SWAP", frame)])
    )
    monkeypatch.setattr(fs, "forward_candidate_indices", lambda enriched, **kw: [signal_i])
    return fs.scan_forward_records(
        ForwardScanInput(
            artifact=_stub_artifact(),
            booster=_StubBooster(),
            detected_at=detected_at,
            start_time=pd.Timestamp("2026-07-01", tz="UTC"),
            existing_log=existing,
        )
    )
````
