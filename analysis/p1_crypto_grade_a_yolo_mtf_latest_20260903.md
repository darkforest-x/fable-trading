# P1：最新加密行情四周期 Grade-A YOLO 排序图审（2026-09-03）

## 结论先行

按 Owner 本轮要求，冻结 OKX 当时全部合资格 USDT 永续合约，使用同一份 Grade-A full40
native-1280 checkpoint 扫描：

- **15m / 1h**：只扫最近一根已经完整收盘的端点；
- **4h / UTC 日线**：允许首次信号端点位于最近 15 天；
- 所有结构框继续经过原先冻结的因果 ATR/六均线语义门，再按同币 5 根本周期 K 线去重；
- 最终排序只服务于人工看图，不是收益分数或下单优先级。

结果为 **57,770 张模型输入 → 1,837 个结构合法框 → 294 个语义通过框 → 41 个去重事件**：

| 周期 | 去重事件 | LONG | SHORT | 最新端点仍成立 |
|---|---:|---:|---:|---:|
| 15m | **0** | 0 | 0 | 0 |
| 1h | **2** | 2 | 0 | 2 |
| 4h | **31** | 25 | 6 | 1 |
| 1d | **8** | 7 | 1 | 2 |
| **合计** | **41** | **34** | **7** | **5** |

当前端点仍成立的五个技术候选是：**SUI 1h LONG、SAHARA 1h LONG、MUBARAK 4h LONG、
USELESS 1d LONG、JTO 1d SHORT**。15m 当前没有语义门后候选。

全局图审排序第一、第二是 **ZEC 1d LONG + ZEC 4h LONG**，因为它是唯一出现同方向双周期覆盖的
币；但两个事件首次可见于 08-21 / 08-20，并非当前端点信号。这个“重合”只表示同币、同方向、都在
本轮允许窗口内，不是两个周期在同一时刻同时触发。

![四周期事件与语义门总览](../experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan/summary_overview.png)

完整 41 张原图、周期/方向/币种筛选和总排序：
[打开交互式排序图库](../../experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan/gallery.html)。

**诚实结论**：这 41 个只能称为“允许进入本次人工图审队列的技术候选”，不能称为允许交易的信号。
15m 权重迁移到 1h/4h/日线均为 OOD；尤其上一轮 4h 的 34 张图已经被 Owner 批次裁决为“都不太行”，
本轮 31 个新 4h 事件不会自动推翻该否决。生产仍为 `detector=none`。

## 最新端点仍成立的候选

冻结时刻为 **2026-09-03 11:35:47 CST**。表内“首次可见”是事件最早完整收盘可知的时间；
“末次可见”是本次冻结快照中最后一次仍命中的完整收盘时间。

| 总排序 | 币种 | 周期 | 方向 | conf | 本周期名次 | 首次可见（CST） | 末次可见（CST） |
|---:|---|---|---|---:|---:|---|---|
| 4 | USELESS | 1d | LONG | 0.816 | 2/8 | 09-02 08:00 | 09-03 08:00 |
| 5 | JTO | 1d | SHORT | 0.616 | 3/8 | 09-02 08:00 | 09-03 08:00 |
| 16 | MUBARAK | 4h | LONG | 0.817 | 8/31 | 09-03 04:00 | 09-03 08:00 |
| 40 | SUI | 1h | LONG | 0.891 | 1/2 | 09-03 11:00 | 09-03 11:00 |
| 41 | SAHARA | 1h | LONG | 0.678 | 2/2 | 09-03 11:00 | 09-03 11:00 |

SUI / SAHARA 虽然是最新的 1h 命中，但在全局表排第 40/41，不是因为它们的 confidence 低，而是
预注册排序先比较同方向多周期覆盖，再按 `1d > 4h > 1h > 15m` 给人工图审优先级，最后才使用
**各自周期内部**的 confidence 名次。若要建立“新鲜度优先”的另一份排序，那会是新的排序合同，
不能在看完本批结果后偷换。

分周期联系表：

- [1h 两张最新图](../../experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan/overview_1h.png)
- [4h 排序第 1 页](../../experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan/overview_4h.png)
- [日线全部 8 张](../../experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan/overview_1d.png)
- [全周期排序第 1 页](../../experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan/overview_all.png)

## 完整 41 项图审顺序

`conf` 只能在同一周期内比较；“同向周期数”优先于周期和 confidence。GRASS 同时出现 4h LONG 与
SHORT，已保留并明确标为方向冲突，没有平均或静默择边。

| 总排序 | 币种 | 周期 | 方向 | conf | 本周期名次 | 同向周期数 | 首次可见（CST） | 当前端点 |
|---:|---|---|---|---:|---:|---:|---|---|
| 1 | ZEC | 1d | LONG | 0.514 | 4/8 | 2 | 08-21 08:00 | 否 |
| 2 | ZEC | 4h | LONG | 0.849 | 6/31 | 2 | 08-20 04:00 | 否 |
| 3 | MET | 1d | LONG | 0.937 | 1/8 | 1 | 08-21 08:00 | 否 |
| 4 | USELESS | 1d | LONG | 0.816 | 2/8 | 1 | 09-02 08:00 | 是 |
| 5 | JTO | 1d | SHORT | 0.616 | 3/8 | 1 | 09-02 08:00 | 是 |
| 6 | JUP | 1d | LONG | 0.506 | 5/8 | 1 | 08-28 08:00 | 否 |
| 7 | MORPHO | 1d | LONG | 0.417 | 6/8 | 1 | 08-24 08:00 | 否 |
| 8 | GMX | 1d | LONG | 0.377 | 7/8 | 1 | 08-22 08:00 | 否 |
| 9 | CRV | 1d | LONG | 0.360 | 8/8 | 1 | 08-25 08:00 | 否 |
| 10 | OPN | 4h | LONG | 0.902 | 1/31 | 1 | 08-19 12:00 | 否 |
| 11 | FLOW | 4h | SHORT | 0.895 | 2/31 | 1 | 08-29 04:00 | 否 |
| 12 | 1INCH | 4h | LONG | 0.890 | 3/31 | 1 | 08-20 08:00 | 否 |
| 13 | MON | 4h | LONG | 0.881 | 4/31 | 1 | 08-20 12:00 | 否 |
| 14 | SOON | 4h | SHORT | 0.881 | 5/31 | 1 | 09-01 20:00 | 否 |
| 15 | PI | 4h | LONG | 0.827 | 7/31 | 1 | 08-20 20:00 | 否 |
| 16 | MUBARAK | 4h | LONG | 0.817 | 8/31 | 1 | 09-03 04:00 | 是 |
| 17 | AR | 4h | LONG | 0.804 | 9/31 | 1 | 08-21 04:00 | 否 |
| 18 | NEIRO | 4h | LONG | 0.799 | 10/31 | 1 | 08-20 20:00 | 否 |
| 19 | BIO | 4h | LONG | 0.798 | 11/31 | 1 | 08-20 04:00 | 否 |
| 20 | SUSHI | 4h | LONG | 0.780 | 12/31 | 1 | 08-20 16:00 | 否 |
| 21 | ZKP | 4h | LONG | 0.766 | 13/31 | 1 | 08-29 20:00 | 否 |
| 22 | PENGU | 4h | LONG | 0.759 | 14/31 | 1 | 08-20 20:00 | 否 |
| 23 | SATS | 4h | LONG | 0.759 | 15/31 | 1 | 08-20 20:00 | 否 |
| 24 | 0G | 4h | LONG | 0.756 | 16/31 | 1 | 08-31 16:00 | 否 |
| 25 | VIRTUAL | 4h | LONG | 0.738 | 17/31 | 1 | 08-20 20:00 | 否 |
| 26 | COMP | 4h | LONG | 0.735 | 18/31 | 1 | 08-19 12:00 | 否 |
| 27 | GRASS | 4h | SHORT | 0.718 | 19/31 | 1 | 08-20 12:00 | 否 |
| 28 | GRASS | 4h | LONG | 0.716 | 20/31 | 1 | 08-22 12:00 | 否 |
| 29 | APT | 4h | SHORT | 0.672 | 21/31 | 1 | 08-29 04:00 | 否 |
| 30 | ENSO | 4h | LONG | 0.613 | 22/31 | 1 | 08-30 00:00 | 否 |
| 31 | ZAMA | 4h | LONG | 0.607 | 23/31 | 1 | 08-22 12:00 | 否 |
| 32 | HMSTR | 4h | LONG | 0.536 | 24/31 | 1 | 08-22 00:00 | 否 |
| 33 | RIVER | 4h | LONG | 0.523 | 25/31 | 1 | 08-21 20:00 | 否 |
| 34 | AGLD | 4h | LONG | 0.466 | 26/31 | 1 | 08-22 16:00 | 否 |
| 35 | ORDI | 4h | LONG | 0.449 | 27/31 | 1 | 08-20 20:00 | 否 |
| 36 | FIL | 4h | SHORT | 0.439 | 28/31 | 1 | 08-29 04:00 | 否 |
| 37 | ETH | 4h | LONG | 0.396 | 29/31 | 1 | 08-20 08:00 | 否 |
| 38 | WET | 4h | SHORT | 0.312 | 30/31 | 1 | 08-25 08:00 | 否 |
| 39 | BASED | 4h | LONG | 0.276 | 31/31 | 1 | 08-21 08:00 | 否 |
| 40 | SUI | 1h | LONG | 0.891 | 1/2 | 1 | 09-03 11:00 | 是 |
| 41 | SAHARA | 1h | LONG | 0.678 | 2/2 | 1 | 09-03 11:00 | 是 |

## 排序合同

排序键在读行情前已经冻结：

1. 同币同方向覆盖的不同周期数，降序；
2. 同币覆盖的全部不同周期数，降序；
3. 周期人工优先级：`1d > 4h > 1h > 15m`；
4. confidence **只在本周期内**的名次；
5. 首次可见时间的新鲜度；
6. 稳定币种键打破最终并列。

24h 涨跌幅、24h 估算成交额、币价和币种身份都没有进入模型、语义门或主排序。confidence 既不是
胜率，也不是收益预期；15m 模型在不同周期的 confidence 不可校准地横向相加。

## 数据范围与漏斗

OKX 快照在 fanout 前冻结 277 个合资格 live、`instCategory=1`、USDT 永续币；沿用项目的 blocked
和 stockish 排除。每个币每周期最多取 300 根、仅保留确认 K 线，并要求最后端点准确、至少 140 根、
逐根时间连续、OHLCV 有限且 OHLC 为正。无法满足的币周期直接排除，不前向填充、不换交易所。

| 周期 | 可用币 / 冻结币 | 模型窗 | 原始框 | 结构合法框 | 语义通过框 | 语义通过率 | 去重事件 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 15m | 274 / 277 | 548 | 17 | 17 | 0 | 0.00% | 0 |
| 1h | 274 / 277 | 548 | 13 | 13 | 3 | 23.08% | 2 |
| 4h | 274 / 277 | 49,109 | 1,868 | 1,605 | 240 | 14.95% | 31 |
| 1d | 253 / 277 | 7,565 | 224 | 202 | 51 | 25.25% | 8 |
| **合计** | **1,075 份 candle 文件** | **57,770** | **2,122** | **1,837** | **294** | **16.00%** | **41** |

这不是训练集或 val 集，因此“val 样本数”按字面不适用；可检查的评估分母是 57,770 个完成态模型窗、
1,837 个结构框和 41 个事件。也不存在把语义通过框称为正标签：没有 Owner 逐样本金标时，16.00%
只是门通过率，不是正类率或 precision。

端点合同：

| 周期 | 最新模型端点 K 线开盘（UTC / CST） | 完整可知时间（CST） | 本轮端点范围 |
|---|---|---|---|
| 15m | 09-03 03:15 / 11:15 | 09-03 11:30 | 最新一根 |
| 1h | 09-03 02:00 / 10:00 | 09-03 11:00 | 最新一根 |
| 4h | 09-02 20:00 / 09-03 04:00 | 09-03 08:00 | 08-19 00:00～09-02 20:00 UTC，共 90 根端点 |
| 1d UTC | 09-02 00:00 / 08:00 | 09-03 08:00 | 08-19～09-02 UTC，共 15 根端点 |

## 模型与因果语义合同

- checkpoint：Grade-A 8k positives + 24k matched negatives，full40，native 1280；
- weight SHA-256：`862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838`；
- `conf=0.25`、`NMS=0.70`、W18/W19、core 4/5、confirmation 2～9；
- 同币事件间距：5 根当前周期 K 线；
- 环境：Python 3.9.6、torch 2.8.0、ultralytics 8.4.89、numpy 2.0.2、pandas 2.3.3、MPS；
- 没有新训练、权重或阈值变化、网格搜索、promote、ACTIVE/frozen 切换、forward 写入、部署、
  Telegram 发送或下单。

每个结构框先把数据物理截断在 `window_end_i`，再计算父实验同一份 Pine-RMA ATR14 与
SMA/EMA 20/60/120。mandatory post1/post2 及可见的 post3/post5 均不越过模型端点；事件图中右侧
后来已经发生的 K 线仅供人工看图，不参与放行或排序。

## 零假设对照与不适用项

本轮是完成态形态扫描，没有收益标签、TP/SL、成本路径或 Owner 逐样本金标。因此 val AUC、
top-decile 毛/净收益、胜率、收益置换、单特征收益基线，以及同币 × 同时间块 × 同波动桶随机入场
对照均按字面 **不适用**；本报告没有为凑模板编造这些数字。

同等严格的形态零假设是：对每个结构框固定 K 线、框、类别 ID、confidence 和端点，只在因果语义
计算内把 LONG/SHORT 方向翻转。

| 周期 | 配对结构框 | 实际方向通过 | 翻转方向通过 | 配对精确双侧 p | 解读 |
|---|---:|---:|---:|---:|---|
| 15m | 17 | 0 | 0 | 1.0 | 当前没有候选，无法证明方向性 |
| 1h | 13 | 3 | 0 | 0.25 | 样本太少，未达到项目 `p<0.01` |
| 4h | 1,605 | 240 | 0 | 1.13×10⁻⁷² | 门有方向性，但不证明 4h Gold 或收益 |
| 1d | 202 | 51 | 0 | 8.88×10⁻¹⁶ | 门有方向性，但不证明日线 Gold 或收益 |

所以 SUI/SAHARA 两个 1h 候选尤其不能包装成经过显著性确认的方向信号。4h/日线的极小 p 值只能
反对“语义门完全不看方向”的零假设，不能替代 Owner 真值或经济对照。

## ATR 故障与冻结恢复

第一次运行在读完行情并完成 15m 结构推理后，语义门 fail-closed：YOLO 的 renderer adapter 已补
六均线，但没有把父语义门所需的 `atr` 列交给复核帧。按 holdout 纪律，**#13～#16 已经消费**，
不能因为没有最终 CSV 就假装未读取。

恢复过程只做一项实现修补：在模型任务构建前复用父门的 `add_candidate_features()`，逐值测试确认
Pine-RMA ATR14 一致并穿过 `build_tasks()`。原失败目录、universe、闭合端点、1,075 份 candle
文件和三份顶层回执全部保留并哈希；恢复运行从这些 CSV 离线读取，**额外行情 API 读取为 0**。
模型、阈值、周期范围、币种全集、排序和输出语义均未改变。

相关记录：

- 初始 scanner commit：`051414d695a5f3d9bdb35164e6b9b19a75d6405f`；
- ATR 修复 commit：`24dbc36b3e964bd3648305c2458fa19f112f5b3c`；
- 恢复声明：`experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/recovery_atr_column_20260903.json`；
- 原失败现场：`experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan.failed_atr_missing_20260903T034004Z`。

这次故障沉淀为：
[渲染特征齐全不代表语义门输入齐全](../../docs/learnings/semantic-gates-need-explicit-feature-contract-tests.md)；
[Holdout 运行失败后恢复必须复用原冻结输入](../../docs/learnings/failed-holdout-recovery-must-reuse-frozen-inputs.md)。

## 独立离线验证

`verify_crypto_grade_a_yolo_mtf_latest.py` 不联网、不调用模型，直接从冻结产物重建：

| 核验 | 结果 |
|---|---:|
| candle 文件 SHA / 行数 / 连续时钟 / 最新端点 | **1,075 / 1,075 PASS** |
| 结构候选模型输入像素复放 | **1,837 / 1,837 PASS** |
| 实际方向 + 翻转方向语义特征与决策 | **1,837 / 1,837 PASS** |
| 事件去重与总排序重建 | **41 / 41 PASS** |
| 单张交付图逐像素重放 | **41 / 41 PASS** |
| 网络读取 / 模型推理 | **0 / 0** |

最终 verifier verdict：**PASS**。`summary.json` SHA-256 为
`7078b56ee8d73f8fef4208fd31a5e596dda53f29a8da54aacc1caabefc1b8c2a`；
`ranked_signals.jsonl` 为
`804cbbc703fbcbfb6810cc46c00f51ae42bd8f78e7f1fba4a2d2fd3ec33dac16`。

## Holdout 记录

Owner 在 2026-09-03 本轮对话中明确要求模型读取最新 15m、1h、4h、日线行情，并允许 4h/日线
信号位于 15 天内。本轮据此记录该 checkpoint：

| 配置 | holdout 消耗 |
|---|---:|
| 15m 最新闭合端点 | **#13** |
| 1h 最新闭合端点 | **#14** |
| 4h 最近 15 天 | **#15** |
| 1d UTC 最近 15 天 | **#16** |

ATR 实现故障及离线恢复属于同一冻结配置，没有把恢复伪装成“未消费”，也没有重新抓更新端点。

## 风险与诚实声明

- **1h/4h/日线全部 OOD。** checkpoint 只在 15m 图上训练；相同 18/19 根 K 在不同周期表示完全
  不同的实际时间尺度，confidence 不可跨周期解释。
- **4h 旧否决仍有效。** 上一轮 34 张 4h 技术门图被 Owner 判为“都不太行”；本轮 31 张并未获得
  新 Owner Gold，尤其不能因数量接近就称为修复成功。
- **没有逐样本真值。** 41 个事件无法计算 precision / recall，技术门通过不等于 Owner 认可形态。
- **没有经济验证。** 没有交易方向收益、成本、matched control、胜率或 top-decile 净收益；图中
  后续走势很容易诱发事后挑图，本轮没有据此重新排序或调门。
- **重合不是同步共振。** ZEC 的双周期排名只表示同币同向覆盖；它不是同一时刻双周期确认。
- **方向冲突保留。** GRASS 同时出现 4h LONG/SHORT，说明扫描面内部可能不稳定；未擅自择边。
- **1h 零假设未过门。** 3 对 0 的方向翻转结果 `p=0.25`，SUI/SAHARA 不具备统计确认。
- **最新不等于新鲜可交易。** 这里是完成态历史扫描；更高周期信号的执行延迟预算和生产架构均未获
  Owner 批准，不能进入 tip-smoke、forward 或 ACTIVE。
- 生产端仍诚实 `detector=none`；未训练、未 promote、未部署、未发 Telegram、未下单。

## 下一步选项

本轮已完成 Owner 要求的“跑模型、排序、给图”。下一步只有人工裁决，不应在同一批 holdout 上继续
调阈值：

1. Owner 可直接在图库筛选并逐张给 `接受 / 拒绝 / 边界不对 / 方向不对`；这属于样本审核，不会
   自动变成新训练标签。
2. 若 Owner 仍认为 4h/日线整体不符合目标，应保留为失败证据，回到 pre-holdout 建立独立周期的
   Owner Gold 协议，而不是继续让 15m 权重跨周期冒充。
3. 若要改成“最新优先”或加入收益排序，必须先明确新的排序、TP/SL、成本和 matched-control 合同；
   这些是新变量，需要 Owner 决策后另开预注册，不能用本批 41 张事后调。

## 复现命令

正式目录存在时 scanner 会拒绝覆盖。以下全量 replay 复用原冻结 candle，不产生新行情读取；约需
一小时。`/tmp` 目录名应保持为明确的新目标。

```bash
cd /Users/zhangzc/fable-trading

# 合同与回归
.venv/bin/python -m pytest -q \
  tests/test_scan_crypto_grade_a_yolo_mtf_latest.py \
  tests/test_scan_4h_ma_launch_yolo_latest.py \
  tests/test_ma_launch_yolo_semantic_gate.py \
  tests/boundaries/test_layer_imports.py

# 从原失败现场离线重放同一行情、同一模型与同一排序；不请求 OKX
PYTHONPATH=. .venv/bin/python scripts/scan_crypto_grade_a_yolo_mtf_latest.py \
  --device mps \
  --batch-size 16 \
  --workers 6 \
  --resume-frozen \
  experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan.failed_atr_missing_20260903T034004Z \
  --recovery-amendment \
  experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/recovery_atr_column_20260903.json \
  --out /tmp/fable-crypto-grade-a-yolo-mtf-latest-20260903-replay

# 不联网、不推理的独立验证；正式产物可直接复核
PYTHONPATH=. .venv/bin/python \
  scripts/verify_crypto_grade_a_yolo_mtf_latest.py \
  experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan

# 报告转 HTML
python3 scripts/md_to_html.py \
  analysis/p1_crypto_grade_a_yolo_mtf_latest_20260903.md \
  --out-dir analysis/html
```
