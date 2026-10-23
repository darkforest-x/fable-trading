# 两个Owner空头YOLO最近15天核心10币描述性扫描（2026-08-21）

## 结论先行

- 固定同一份Core-10快照、W12–19、conf=0.25、IoU=0.70与5根事件去重后，baseline产生 **1,005** 个事件，hard-negative R1产生 **437** 个事件。
- R1相对baseline事件变化 **-568**（-56.52%），原始框变化 **-17212**（-62.54%）。
- 两模型匹配事件 **428**，baseline-only **577**，R1-only **9**。
- 本轮只描述检测密度：没有读取未来收益、没有跑TP/SL、没有调阈值、没有promote、没有下单。

## 这两个模型到底是什么

| 模型 | 训练正例 | 训练负例 | 方向 | 状态 |
|---|---:|---:|---|---|
| `owner_lsv2_short_gold_center_v1_ft` | 1,143 | 1,143 easy | 只识别Owner空头形态 | baseline；未通过生产密度门 |
| `owner_lsv2_short_gold_center_hardneg_r1_ft` | 同一批1,143 | 1,143 easy + 2,286 hard | 仍只识别空头；916张Owner多头被当作“不要报空头”的负例 | R1；更克制但也未获生产资格 |

- 两者都不是当前生产模型，ACTIVE没有启用任何一个。
- 严格说它们也不是该支线最新权重：后面还有 `owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`（SHA `52cd38fda253f052c3c8eb712d93557c0125dceb336fb4cd58136236dca32afe`）。本轮只比较Owner点名的这两个，不偷偷替换成R2。
- 它们是W12–19紧凑因果图上的L1检测器，不是收益模型；“报框”只表示看起来像空头形态。
- 旧冻结val仅作历史参考：baseline P/R/mAP50=`0.847/0.902/0.921`，R1=`0.863/0.777/0.898`。R1的主要变化是少报而非全面变好；这些val数字不能裁决生产资格。

## 冻结合同

| 项目 | 值 |
|---|---|
| 时间范围UTC | `2026-08-06T06:30:00+00:00`之后至`2026-08-21T06:30:00+00:00` |
| 时间范围CST | `2026-08-06 14:30:00+08:00`之后至`2026-08-21 14:30:00+08:00` |
| 币种 | `BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, DOT` |
| 周期/窗口 | 15m；W12–19逐bar全扫 |
| conf / NMS IoU | 0.25 / 0.7 |
| NMS执行 / 实现 / 超时预算 | cpu / torchvision / 60.0秒每图；禁止批内截断 |
| 去重 | 同币预测核心中点相差≤5根，保留首次跨门决策 |
| bar endpoints / exposures | 14,400 / 115,200 |
| baseline权重 | `da278820f2d96a64006d9ff6358b7c98faec52249ec8a6f4fe6bf55254fc65b4` |
| R1权重 | `029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537` |
| holdout登记 | baseline第2次；R1第1次 |

## 总体结果

| 模型 | raw | raw/1000 exposures | 去重events | events/1000 endpoints | events/day | 触发币数 |
|---|---:|---:|---:|---:|---:|---:|
| 1:1 baseline | 27,522 | 238.906 | 1,005 | 69.792 | 67.00 | 10/10 |
| 1:3 hardneg R1 | 10,310 | 89.497 | 437 | 30.347 | 29.13 | 10/10 |

## 逐币结果

| 币 | baseline raw | R1 raw | baseline events | R1 events | event差 | 共同事件 |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 2,625 | 1,117 | 110 | 46 | -64 | 46 |
| ETH | 2,501 | 991 | 103 | 46 | -57 | 45 |
| SOL | 2,820 | 1,030 | 94 | 37 | -57 | 36 |
| BNB | 3,452 | 1,063 | 129 | 53 | -76 | 53 |
| XRP | 3,059 | 1,190 | 115 | 55 | -60 | 54 |
| DOGE | 2,957 | 1,120 | 111 | 50 | -61 | 48 |
| ADA | 3,382 | 1,156 | 109 | 46 | -63 | 44 |
| AVAX | 2,494 | 932 | 84 | 38 | -46 | 38 |
| LINK | 1,662 | 717 | 66 | 30 | -36 | 29 |
| DOT | 2,570 | 994 | 84 | 36 | -48 | 35 |

## 两模型各自最近10个事件

| 模型 | 币 | decision time UTC | core midpoint UTC | peak conf | 核心根数 | 延迟根数 |
|---|---|---|---|---:|---:|---:|
| baseline | AVAX | 2026-08-19T16:00:00+00:00 | 2026-08-19T14:30:00+00:00 | 0.519 | 7 | 3 |
| baseline | XRP | 2026-08-19T15:30:00+00:00 | 2026-08-19T13:30:00+00:00 | 0.453 | 7 | 5 |
| baseline | DOGE | 2026-08-19T15:30:00+00:00 | 2026-08-19T13:45:00+00:00 | 0.334 | 7 | 4 |
| baseline | SOL | 2026-08-19T15:00:00+00:00 | 2026-08-19T13:00:00+00:00 | 0.262 | 7 | 5 |
| baseline | ETH | 2026-08-19T15:00:00+00:00 | 2026-08-19T13:00:00+00:00 | 0.560 | 7 | 5 |
| baseline | BTC | 2026-08-19T14:45:00+00:00 | 2026-08-19T12:45:00+00:00 | 0.339 | 7 | 5 |
| baseline | BNB | 2026-08-19T14:45:00+00:00 | 2026-08-19T13:00:00+00:00 | 0.538 | 7 | 4 |
| baseline | ADA | 2026-08-19T14:30:00+00:00 | 2026-08-19T13:30:00+00:00 | 0.651 | 4 | 3 |
| baseline | AVAX | 2026-08-19T14:00:00+00:00 | 2026-08-19T13:00:00+00:00 | 0.742 | 4 | 3 |
| baseline | DOGE | 2026-08-19T13:15:00+00:00 | 2026-08-19T11:30:00+00:00 | 0.283 | 7 | 4 |
| hardneg R1 | AVAX | 2026-08-19T14:15:00+00:00 | 2026-08-19T12:45:00+00:00 | 0.670 | 7 | 3 |
| hardneg R1 | AVAX | 2026-08-19T11:30:00+00:00 | 2026-08-19T10:00:00+00:00 | 0.580 | 7 | 3 |
| hardneg R1 | BTC | 2026-08-19T10:30:00+00:00 | 2026-08-19T09:00:00+00:00 | 0.306 | 7 | 3 |
| hardneg R1 | BNB | 2026-08-19T09:30:00+00:00 | 2026-08-19T07:45:00+00:00 | 0.278 | 9 | 3 |
| hardneg R1 | DOGE | 2026-08-19T08:30:00+00:00 | 2026-08-19T07:00:00+00:00 | 0.299 | 7 | 3 |
| hardneg R1 | BNB | 2026-08-19T05:45:00+00:00 | 2026-08-19T04:15:00+00:00 | 0.600 | 7 | 3 |
| hardneg R1 | DOGE | 2026-08-19T05:30:00+00:00 | 2026-08-19T03:30:00+00:00 | 0.481 | 8 | 4 |
| hardneg R1 | XRP | 2026-08-19T04:30:00+00:00 | 2026-08-19T02:30:00+00:00 | 0.264 | 8 | 4 |
| hardneg R1 | BNB | 2026-08-19T04:15:00+00:00 | 2026-08-19T02:45:00+00:00 | 0.460 | 7 | 3 |
| hardneg R1 | BTC | 2026-08-19T04:00:00+00:00 | 2026-08-19T02:30:00+00:00 | 0.510 | 7 | 3 |

完整事件逐条保存在各扫描目录的`events.jsonl`，这里不按事后涨跌筛选，只按decision time倒序展示。

## 零假设与必报指标

- 自比较零对照：baseline事件集与自身比较，1005/1005全部匹配，baseline-only=0、R1-only=0；证明比较器不会凭空制造差异。
- AUC、置换p、top-decile毛/净收益、胜率、匹配随机交易对照：**N/A**。本轮没有L2排序或未来结果，禁止编造交易指标。
- BTC虽然属于仓库固定Core-10，但不在本模型Owner-short正例母池中；其结果属于跨币泛化描述，不能与有训练正例的币等价解释。

## 运行完整性

- 首次15天native-MPS尝试出现`NMS time limit 3.600s exceeded`；Ultralytics会在超时后跳过该批剩余图片，因此该次运行被中止，未生成最终结果。
- 第二次CPU纯Torch NMS不会漏图，但密集候选下过慢，也在最终artifact写出前中止；没有把两次半成品拼进正式结果。
- 修复只把NMS后处理移到CPU、使用编译版torchvision NMS并把每图预算设为60秒；模型前向、FP32、图片、conf与IoU均不变。
- 在冻结ETH 48小时、1,536个窗口上，native与CPU-NMS均为411个raw / 18个events；`raw_detections.jsonl`与`events.jsonl`逐字节一致。SHA分别为`1342b84fdefab98e50e07ea04a1ec3eb9cd6b5072f7271cd388df38822dcb11c`、`22f605c282501f9f3b9e6c8802849838818dc95e3551cfe05bd0e9b5aa89b153`。

## 风险与诚实声明

- 最近15天全部晚于2026-05-04 holdout边界；本轮由Owner在对话中明确要求运行，登记为baseline第2次、R1第1次holdout描述性读取。
- 这不是独立canary，也不能在看完结果后调conf、删币、回灌难负例，再把同一时间块称为新验收。
- 两个模型都是空头形态检测器；事件数不等于正确空头信号数，更不等于订单或收益。
- `production_eligible=false`、`training_eligible=false`；ACTIVE未改，订单为0。

## 产物

- baseline完整事件：`analysis/output/owner_short_gold_center_recent15d_core10_20260821/baseline_fp32/events.jsonl`
- hard-negative R1完整事件：`analysis/output/owner_short_gold_center_recent15d_core10_20260821/hardneg_fp32/events.jsonl`
- 机器对比：`analysis/output/owner_short_gold_center_recent15d_core10_20260821/comparison.json`
- 本报告记录上述scan summary、raw、events与`_SUCCESS`的逐文件SHA；任一字节变化都要重新汇总。

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/backtest_owner_short_gold_center_recent.py scan \
  --snapshot-dir analysis/output/owner_short_gold_center_recent15d_core10_20260821/kline_snapshot \
  --out-dir /path/to/baseline \
  --weights analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_v1_ft/weights/best.pt \
  --hours 360 --window-min 12 --window-max 19 --conf 0.25 --iou 0.70 \
  --imgsz 960 --device mps --batch 32 --nms-device cpu \
  --nms-implementation torchvision --nms-max-time-img 60 \
  --evaluation-scope holdout --holdout-use-number 2

# R1保持完全相同参数，只替换权重、输出目录，并使用--holdout-use-number 1。

PYTHONPATH=. .venv/bin/python scripts/summarize_owner_short_recent_core10.py \
  --baseline-dir /path/to/baseline --hardneg-dir /path/to/hardneg \
  --snapshot-summary analysis/output/owner_short_gold_center_recent15d_core10_20260821/fetch_summary.json \
  --out-json analysis/output/owner_short_gold_center_recent15d_core10_20260821/comparison.json \
  --report analysis/p2_owner_short_recent15d_core10_comparison_20260821.md

python3 scripts/md_to_html.py analysis/p2_owner_short_recent15d_core10_comparison_20260821.md --out-dir analysis/html
```
