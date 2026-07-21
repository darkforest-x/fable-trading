# p_tip_subset_val — tip 可检子集 vs 全量基线（严格 val 窗）

日期：2026-07-21 · owner 授权过夜离线实验（不动 executor / forward_log / ACTIVE / frozen / 阈值 / 三门）
单变量：**样本子集是唯一变量**。同一批 eligible（frozen v11 回归 score ≥ val-q90、
entry < 2026-05-04），用 mainline v12 `models/owner_best.pt` 按 live MA 语义重渲 tip 窗，
只保留「右缘落在 tip bar」的信号，再跑与 `weight_centric_backtest` 相同的 binary 仿真。

## 结论（先行）

**实盘群体折扣系数（tip_strict 净收益 / 全量净收益，val，成本 0.3%）= 0.0465。**

全量基线 val 净 +141.0%（391 笔）里，只有约 **4.7%** 能被「tip 可检」子集兑现
（tip_strict 净 +6.56%，14 笔）。tip 命中率本身也很稀：pre-holdout eligible 的
strict tip-hit = 4.0%（117/2904），val 窗更低（14/413 = 3.4%）。

含义：主线 val 回测的漂亮数字大量来自「扫描窗中部、事后才看得见」的盒子；
live tip + 30min 新鲜度门能吃到的只是其中一小撮。把全量 PF/净收益直接当实盘预期会高估约 **20×**。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

# 1) 打分写 eligible（lightgbm 进程；已产出可跳过）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=. .venv/bin/python scripts/tip_subset_backtest.py --stage score

# 2) tip 重渲 + v12 标记（torch-only；batch=1 / workers=0；按币 checkpoint 可续跑）
#    16GB 机器建议用分片驱动，避免长寿进程被杀 / SIGSEGV 丢进度：
MAX_SYMBOLS=5 ./scripts/run_tip_subset_rerender_chunked.sh

# 3) 全量 vs tip 子集回测（可再 import lightgbm）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=. .venv/bin/python scripts/tip_subset_backtest.py --stage backtest
```

产出：

- `analysis/output/tip_subset_eligible.csv` / `tip_subset_rerender.csv` / `tip_subset_meta.json`
- `analysis/output/p_tip_subset_backtest.json`
- `analysis/output/p_tip_subset_{full_baseline,tip_strict,tip_92_loose}_trades.csv`

## 数据与协议

| 项 | 值 |
|---|---|
| 池 / 打分 | `judgment_yolo_swap_v11.csv` + `frozen_tp5_sl2_swap_yolo_v11_reg_20260718` |
| 阈值 | val-q90 = 0.02022（与 weight_centric 同源） |
| tip 权重 | `models/owner_best.pt`（v12 H-TIP），conf=0.30 |
| tip 几何 | 全序列 `add_mas` 后切 200-bar、末 bar=信号 bar（**live** 语义，非 slice-then-MA） |
| tip_hit_strict | `right_edge_to_bar` → bar ≥ 199（唯一能过 30min 新鲜度门的几何） |
| tip_hit_92 | `cx+w/2 ≥ 0.92`（灵敏度上界，非实盘口径） |
| 评估窗 | val 入场 ∈ [2026-03-12 06:45, 2026-05-04)；**holdout 零接触** |
| 资本 / 成本 / 出场 | 10 单位、0.2%/0.3% 往返、TP5/SL2 标签重放、binary w≡1 |

本机 rerender 资源（2026-07-21）：workers=0、predict batch=1、OMP/MKL/VECLIB=1；
RSS 峰值 **~548MB**（远低于 4GB 建议上限）；tmp PNG 用完即删（观测 ≤1）。

## 对照表 — val 窗（成本 0.3%；括号内 0.2%）

| 指标 | full_baseline | tip_strict | tip_92_loose |
|---|---|---|---|
| eligible（val） | 413 | 14 | 15 |
| 成交笔数 | 391 | 14 | 15 |
| 净收益（占资本） | **+141.0%**（+145.0%） | **+6.56%**（+6.70%） | +6.27%（+6.42%） |
| PF | 7.63 | 4.87 | 4.17 |
| 胜率 | 81.6% | 50.0% | 46.7% |
| maxDD | 0.83% | 0.57% | 0.64% |
| 资金利用率 | 18.9% | 0.79% | 0.80% |
| **折扣系数（净/全量净）** | 1.000 | **0.0465** | 0.0445 |

基线与已发布 `p_weight_centric_val.json` baseline_binary **逐位一致**（391 笔、净 14.1036、
PF 7.627）→ 子集差异只可能来自 tip 过滤，不是仿真器漂移。

### 按月分段（val，成本 0.3%，净单位）

| 月份 | full n / net | tip_strict n / net |
|---|---|---|
| 2026-03 | 121 / 3.33 | 1 / 0.059 |
| 2026-04 | 257 / 10.26 | 12 / 0.584 |
| 2026-05 (1–3 日) | 13 / 0.52 | 1 / 0.013 |

### tip 命中率

| 窗 | eligible | tip_strict | rate |
|---|---|---|---|
| pre-holdout 全 eligible | 2904 | 117 | 4.03% |
| val 入场 | 413 | 14 | 3.39% |
| tip_92（对照）pre-holdout | 2904 | 128 | 4.41% |

## 解读

1. **折扣主要来自供给塌缩，不是 tip 子集「更差」的单笔质量单独解释得了的。**
   全量 391 笔 → tip 14 笔（约 1/28）；净收益比约 1/21.5。单笔层面 tip_strict
   胜率从 81.6% 掉到 50%、PF 从 7.63 掉到 4.87——方向仍为正，但样本极小。
2. **tip_92 几乎不比 tip_strict 多给供给**（val 15 vs 14），说明「右缘贴 tip」和
   「norm≥0.92」在本池上高度重叠；实盘应以 strict 为准。
3. **train 窗同方向**：tip_strict 79 笔 / 净 +12.4% vs 全量 1778 / +665.7%
   （折扣 ≈ 0.019）——不是 val 偶然，但 train 折扣更狠，提示早期池 tip 几何更稀。
4. 与 v12 H-TIP 评测（`true_tip` 协议 tip_hit_rate 高）不矛盾：那里测的是
   「给定 tip 图时模型能否开火」；本实验测的是「主线 eligible 里有多少本来就长在 tip」。

## 风险与诚实声明

- tip_strict val **只有 14 笔**——折扣系数点估计可用作群体先验，**不能**当精细
  仓位公式的分母；置信区间会很宽。
- 重渲用的是当前磁盘 K 线；与当初扫池时若有缺口/复权差异，个别盒子可能错位
  （本跑 `rerender_ok` 全 True、0 skip）。
- 未动 holdout、未跑 accept；前向 100 笔新鲜裁决仍是确认级唯一标准。
- 本报告不改变生产默认（阈值 / 三门 / sizing / ACTIVE）。

## 下一步（需 owner 决策）

1. 把折扣系数 **0.0465** 写进实盘预期口径（看板 / 周报），避免用全量 val 净收益叙事。
2. 是否值得为 tip 几何单独重训 / 重扫候选池（扩大 tip 供给），还是接受低频 + 折扣。
3. 前向 log 里按 tip_hit 归因（需 live 侧已记 tip 几何）——与本离线系数交叉验证。

## 内存/续跑备注（工程）

此前停在约 60/257 且无落盘：旧实现按币批量 `predict`（单币最多 39 张图）+
仅结束时写 CSV，易 OOM/被杀丢进度。本轮改为：predict batch=1、workers=0、
每币 checkpoint、`--max-symbols` 分片子进程驱动（`run_tip_subset_rerender_chunked.sh`）。
