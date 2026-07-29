# ETH 3m × v10 最近三个月预打标预览

日期：2026-07-29  
性质：Owner 明确要求的视觉审查包；全局 holdout 第 11 次消耗。不是模型验收、回测或调参实验。

## 结论先行

- 已在 `2026-04-29 07:45`～`2026-07-29 04:45 UTC` 的 ETH-USDT-SWAP 3m K 线上，等距抽取 **2,000 / 43,621** 个可扫盘口锚点。
- 每个输入严格为截至锚点的 **200 根已完成 3m K 线**；v10 仅保留框右沿落在 tip/tip-1/tip-2 的结果。
- conf=0.30 时得到 **47** 个原始 tip 命中，预览开火率为 **2.35%/锚点**；因锚点间隔约 66 分钟，本包中 24 分钟去重与 1 小时聚类后仍为 47 个事件。
- HTML 为单文件自包含版，内嵌 47 张双视图 JPEG，约 5.7 MiB；上图无未来，下图额外显示 3 小时未来，仅供 Owner 人工验真。
- **v10 是 15m 模型，在 3m 图上属于 OOD。** 47 个命中里，3 小时后收跌仅 18 个（38.3%），中位收盘变化为 +0.315%；这只能说明值得目视质检，不能据此认定做空精度、改阈值或 promote。

## 数据与复现

数据：`data/kline_eth_3m_recent95/okx_ETH_USDT_SWAP_3m_45699.csv`

- 45,699 根；2026-04-25 02:51～2026-07-29 07:45 UTC
- 重复时间戳 0；最大间隔恰为 3 分钟；不存在缺口
- 为保证每张人工图都有完整 3 小时未来，实际扫描截止时间比数据末端早 3 小时

```bash
python3 -m src.data.fetch_okx \
  --symbols ETH_USDT_SWAP --days 95 --workers 1 --bar 3m \
  --out-dir data/kline_eth_3m_recent95

MPLCONFIGDIR=/tmp/fable-mpl-cache .venv/bin/python \
  scripts/scan_eth_3m_v10_prelabels_html.py \
  --input data/kline_eth_3m_recent95/okx_ETH_USDT_SWAP_3m_45699.csv \
  --max-anchors 2000 --batch-size 8 --device cpu \
  --out analysis/output/eth_3m_v10_prelabels_3m
```

## 月份分布

| 月份 | 事件 |
|---|---:|
| 2026-05 | 21 |
| 2026-06 | 16 |
| 2026-07 | 10 |

置信度范围 0.308～0.884，中位数 0.653。

## 因果与物理隔离

1. 模型图只渲染 `tip-199..tip`，不含未来数据。
2. YOLO 框先从像素坐标还原到 bar/价格坐标，再画到人工审查图。
3. 人工图的未来区域单独着色并写明 `HUMAN-ONLY FUTURE`；未来数据从未回灌模型。
4. 原始框和事件表分别保存在 `raw_detections.csv`、`events.csv`，HTML 只作展示。

## 风险与诚实声明

- 这是 2,000 锚点预览，不是 43,621 根逐 bar 穷举；不能把 47 次当作完整三个月开火总数或月密度。
- v10 的训练时框、图像尺度和时间语义来自 15m；迁移到 3m 后一次形态只代表 10 小时历史，而不是约 50 小时，分布发生实质变化。
- 3 小时未来只服务人工目视，不是预注册收益标签，也没有匹配随机对照，因此不作经济性结论。
- 本窗口全部位于 holdout 边界之后。Owner 的本次明确要求登记为全局第 11 次消耗；结果仅可用于决定是否值得建设独立 ETH 3m 标注/模型，不得用于反复试阈值、评估 v10 或改变线上配置。

## 产物

- `analysis/output/eth_3m_v10_prelabels_3m/index.html`
- `analysis/output/eth_3m_v10_prelabels_3m/events.csv`
- `analysis/output/eth_3m_v10_prelabels_3m/raw_detections.csv`
- `analysis/output/eth_3m_v10_prelabels_3m/summary.json`

