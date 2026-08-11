# Owner-short compact YOLO 最近2天全市场回放（2026-08-11）

## 结论先行

- 本次按Owner在对话中的明确要求读取最近48小时数据，登记为该配置第 **1** 次消耗holdout。
- 使用刚训练完成且未promote的`owner_lsv2_short_gold_center_v1_ft`，只扫其训练分布内的215币种；实际新鲜可用币种 **214**。
- 因果扫描W12–19共 **328,704** 个窗口，原始触发 **71,204** 条；按同币、核心中点±5根合并为 **2,500** 个事件。
- 密度为 **60.845 events/1000 bar endpoints**，即 **1250.0 events/day**。这才是检测事件数，不把8种窗口的重复命中当8笔单。
- 折算到单币为 **5.84 events/币/天**；211/214个币至少触发一次。核心宽度落在Owner要求4–7根的比例为 **94.9%**。
- 已了结事件 2091 个，TP/SL/timeout/open分布：`{'tp': 453, 'sl_ambiguous': 4, 'sl': 1517, 'timeout': 117, 'running': 390, 'no_entry': 19}`；全部已了结事件净@taker均值 **-0.203%**。严格成对可比样本 1498 个：事件 **-0.231%**、匹配随机 **+0.170%**、差值 **-0.401%**。
- 已了结事件TP率 **21.7%**；匹配随机对照覆盖 1950/2500 个事件，其中双方都已了结的严格配对为 1498。
- 这仍是检测器诊断回放，不是生产晋升：无ACTIVE修改、无下单、无阈值调优，TG图均标注纸面回放。

## 回放协议

| 项目 | 冻结值 |
|---|---|
| 权重SHA-256 | `da278820f2d96a64006d9ff6358b7c98faec52249ec8a6f4fe6bf55254fc65b4` |
| 数据范围 | `2026-08-09T06:30:00+00:00`之后至`2026-08-11T06:30:00+00:00` |
| 周期/窗口 | 15m；W12–19逐bar全扫 |
| 推理门 | conf=0.25（Ultralytics诊断默认值，未调参）；NMS IoU=0.7 |
| 事件去重 | 同币预测核心中点相差≤5根；第一阈值穿越作为决策时刻 |
| 交易诊断 | 下一根开盘做空；TP5×ATR14 / SL2×ATR14 / 72根；同bar双触保守判SL |
| 成本 | swap taker往返10bp；maker往返6bp |
| holdout | Owner明确授权；该配置第1次消耗 |

## 数据统计

- 数据源：OKX公开15m接口的一次性快照；生成于`2026-08-11T06:53:13.379934+00:00`，未写canonical `data/`。
- 请求/可用币种：215 / 214；缺失：`['LRC_USDT_SWAP']`。
- bar endpoints：41,088
- window exposures：328,704
- raw detections：71,204
- deduplicated events：2,500
- 单币两天事件数 median / p90 / max：11.0 / 20.0 / 27
- 决策延迟 median / p90：3.0 / 5.0根；0–2根 1.4%，3–5根 98.1%，>5根 0.4%
- 事件最多的币种：`[('XLM_USDT_SWAP', 27), ('LAYER_USDT_SWAP', 26), ('APT_USDT_SWAP', 25), ('COMP_USDT_SWAP', 25), ('LUNA_USDT_SWAP', 24), ('ENS_USDT_SWAP', 23), ('YFI_USDT_SWAP', 23), ('DOGE_USDT_SWAP', 22), ('DOT_USDT_SWAP', 22), ('ETC_USDT_SWAP', 22)]`
- ETH events：17
- matched random controls：1950（已了结1674）
- 已了结事件TP率：21.66%
- 扫描耗时：53.0分钟

## 强信号明细（按事件最大置信度）

| 币种 | 决策时间CST | conf max | 核心宽度 | 首次延迟 | 结果 | 净@taker |
|---|---:|---:|---:|---:|---|---:|
| XPT_USDT_SWAP | 08-10 08:00 | 0.956 | 5 | 3 | sl | -0.515% |
| HUMA_USDT_SWAP | 08-11 02:45 | 0.954 | 5 | 3 | sl | -1.253% |
| STRK_USDT_SWAP | 08-11 02:30 | 0.952 | 4 | 3 | sl | -1.220% |
| ATH_USDT_SWAP | 08-11 04:45 | 0.947 | 4 | 3 | running | — |
| ENJ_USDT_SWAP | 08-10 08:15 | 0.946 | 4 | 3 | sl | -0.757% |
| ADA_USDT_SWAP | 08-10 18:45 | 0.943 | 4 | 3 | sl | -0.920% |
| DYDX_USDT_SWAP | 08-11 06:45 | 0.942 | 5 | 3 | sl | -1.011% |
| PLUME_USDT_SWAP | 08-11 08:00 | 0.940 | 4 | 3 | sl | -1.574% |
| AERO_USDT_SWAP | 08-10 23:00 | 0.938 | 4 | 3 | sl | -1.322% |
| ZIL_USDT_SWAP | 08-09 20:15 | 0.937 | 4 | 3 | tp | +2.160% |
| ENSO_USDT_SWAP | 08-11 02:30 | 0.936 | 4 | 3 | sl | -1.372% |
| BLUR_USDT_SWAP | 08-11 05:45 | 0.935 | 5 | 3 | running | — |
| RESOLV_USDT_SWAP | 08-10 15:00 | 0.933 | 4 | 3 | sl | -1.454% |
| UMA_USDT_SWAP | 08-10 08:45 | 0.931 | 5 | 3 | sl | -0.816% |
| APE_USDT_SWAP | 08-11 06:45 | 0.928 | 5 | 3 | sl | -1.122% |
| GRT_USDT_SWAP | 08-10 23:30 | 0.927 | 4 | 3 | tp | +1.801% |
| ENS_USDT_SWAP | 08-10 08:30 | 0.927 | 5 | 3 | sl | -0.777% |
| SEI_USDT_SWAP | 08-11 06:45 | 0.926 | 5 | 3 | running | — |
| WCT_USDT_SWAP | 08-11 05:45 | 0.926 | 4 | 3 | sl | -1.062% |
| AERO_USDT_SWAP | 08-11 00:30 | 0.925 | 5 | 3 | sl | -1.206% |
| BERA_USDT_SWAP | 08-10 07:30 | 0.924 | 4 | 3 | sl | -1.041% |
| PIEVERSE_USDT_SWAP | 08-11 02:30 | 0.924 | 4 | 3 | sl | -1.381% |
| TURBO_USDT_SWAP | 08-11 00:00 | 0.923 | 4 | 3 | sl | -1.351% |
| IOTA_USDT_SWAP | 08-10 07:15 | 0.923 | 4 | 3 | sl | -0.919% |
| STX_USDT_SWAP | 08-10 08:45 | 0.923 | 5 | 3 | sl | -0.981% |
| UMA_USDT_SWAP | 08-10 07:15 | 0.922 | 4 | 3 | sl | -0.623% |
| APE_USDT_SWAP | 08-11 05:15 | 0.921 | 6 | 2 | sl | -1.137% |
| PIEVERSE_USDT_SWAP | 08-10 16:15 | 0.921 | 4 | 3 | sl | -1.642% |
| SKY_USDT_SWAP | 08-10 04:15 | 0.921 | 5 | 3 | timeout | +0.608% |
| GAS_USDT_SWAP | 08-10 08:00 | 0.921 | 5 | 3 | timeout | +0.334% |

完整事件表：`analysis/output/owner_short_gold_center_recent2d_v1/events_with_outcomes.csv`；匹配对照：`analysis/output/owner_short_gold_center_recent2d_v1/matched_controls.csv`。

## Owner ETH终极参考段核对

Owner标出的核心候选时段为8月10日19:30–20:45 CST。当前模型有2个预测核心与其重叠：

| 角色 | event_id | 预测核心CST | 决策CST | 核心根数 | 延迟根数 | conf max | 结果 | 净@taker |
|---|---|---|---|---:|---:|---:|---|---:|
| 主目标匹配 | 2efeebe2eeb55f6d | 08-10 19:30–20:15 | 08-10 21:00 | 4 | 3 | 0.905 | tp | +1.020% |
| 后续重叠事件（需作为延续/重复复核） | 2ea2ed0fc0a72950 | 08-10 20:45–21:45 | 08-10 22:30 | 5 | 3 | 0.884 | tp | +1.251% |

第一条主匹配把核心落在19:30–20:15的4根K，21:00首次决策，正好是3根确认延迟；它没有把后面的整段暴跌塞入核心。第二条从20:45继续框到21:45，已进入下跌过程，不能因为同样TP就自动视为另一个高质量形态，应进入相邻延续/重复难例复核。

## 与上一版同表对照

| 配置 | 正/负训练比 | 最近2天events/1000 | events/day | holdout次数 | 裁决 |
|---|---:|---:|---:|---:|---|
| 当前1:1 easy baseline | 1:1 | 60.845 | 1250.0 | 1 | 仅诊断，等待hard-negative arm |
| 下一步1:3（1 easy+2 hard） | 1:3 | 未运行 | 未运行 | 0 | 未获本轮3060训练授权 |

## 匹配随机对照

对每个可匹配事件，在同一币、同一UTC日、同ATR波动五分桶中确定性抽取一个非事件bar，使用完全相同的入场、障碍、期限和成本。只在事件与其对应随机入场都已了结时进入差值分母，共1498对；事件净@taker均值为-0.231%，随机对照为+0.170%，逐对差值均值为-0.401%。短样本不能据此宣称统计显著或可交易。

## 必报指标状态

- val AUC：N/A，本轮只评估YOLO检测器，不训练/评分LightGBM排序层。
- 置换检验p：N/A，没有排序分数与独立大样本。
- top-decile毛/净收益：N/A，没有判断层分位数。
- 胜率：已了结事件TP率21.66%；未完结样本不进入分母。
- 单特征基线：N/A；本轮有效对照为同币×同日×同波动桶随机入场。

## 风险与诚实声明

- `conf=0.25`只是未调优诊断门，不能自动成为生产阈值；任何阈值晋升仍需Owner决策。
- 最新2天没有完整Owner逐事件金标，收益方向不能替代形态precision；本报告不能给出可信event precision/recall。
- 事件图中的紫色区域仅供人工看未来结果，模型输入严格止于青色decision线。
- 本次holdout已经消耗，不能拿同一结果反复改数据集并当独立验收。
- 模型未promote、未部署、未写forward_log、未下单。

## 复现命令

```bash
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/backtest_owner_short_gold_center_recent.py fetch \
  --out-dir analysis/output/owner_short_gold_center_recent2d_v1

# Windows 3060 PowerShell；四个i可并行启动
foreach ($i in 0..3) {
  C:/fable/.venv/Scripts/python.exe \
    C:/fable/scripts/backtest_owner_short_gold_center_recent.py scan \
    --snapshot-dir C:/fable/analysis/input/owner_short_gold_center_recent2d_v1/snapshot \
    --out-dir "C:/fable/analysis/output/owner_short_gold_center_recent2d_v1/shard$i" \
    --weights C:/fable/analysis/input/owner_short_gold_center_recent2d_v1/best.pt \
    --device 0 --shard-index $i --shard-count 4
}

# 将四个shard目录取回Mac后合并
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/backtest_owner_short_gold_center_recent.py merge \
  --scan-dirs analysis/output/owner_short_gold_center_recent2d_v1/remote_shards/shard0 \
  analysis/output/owner_short_gold_center_recent2d_v1/remote_shards/shard1 \
  analysis/output/owner_short_gold_center_recent2d_v1/remote_shards/shard2 \
  analysis/output/owner_short_gold_center_recent2d_v1/remote_shards/shard3 \
  --out-dir analysis/output/owner_short_gold_center_recent2d_v1/merged_scan

PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/backtest_owner_short_gold_center_recent.py finalize \
  --snapshot-dir analysis/output/owner_short_gold_center_recent2d_v1/kline_snapshot --scan-dir analysis/output/owner_short_gold_center_recent2d_v1/merged_scan \
  --out-dir analysis/output/owner_short_gold_center_recent2d_v1 --report analysis/p1_owner_short_gold_center_recent2d_holdout_20260811.md

python3 scripts/md_to_html.py analysis/p1_owner_short_gold_center_recent2d_holdout_20260811.md --out-dir analysis/html
```

## 下一步

继续按交接文档从原train时间块挖hard negatives，构建固定val的1:3第二训练臂。该臂构建和审计可继续；再次上3060训练前停在Owner逐次授权门。
