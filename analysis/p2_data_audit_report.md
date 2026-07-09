# P2-12 数据质量审计

**日期**：2026-07-09 16:37 UTC
**纪律**：只读扫描；不改 loader 黑名单、不碰 holdout、不调参。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/data_audit.py
python3 -m pytest tests/test_data_audit.py -q
```

## 覆盖统计

| 项 | 数值 |
|---|---:|
| 序列总数 | 892 |
| 触发任一阈值 | 594 |
| 结构性问题（缺口/零量/尖刺/OHLC） | 248 |
| 黑名单候选（全宇宙） | 172 |
| OKX SWAP 15m 序列 | 206 |
| OKX SWAP 15m stale | 43 |
| 未完成 `.part.csv` | 5 |

分 bar：

- `15m`: 455
- `1H`: 54
- `30m`: 54
- `5m`: 329

## 阈值

- 缺口数 > 5（间隔 > 1.5×bar）
- 零成交量占比 > 2% 记入 flagged；>5% 才进黑名单候选
- 单 bar \|ret\| > 25% 计 spike；≥3 才 structural，≥8 才黑名单
- OHLC 逻辑错误（high<low / 越界 / 非正价）> 0
- 末 bar 距今 > 48h → 标 stale（优先跑 `update_okx`，不是黑名单）
- 股票类 SWAP（AAPL/NVDA/…）在 zero_vol>2% 时直接进黑名单候选

## 最差缺口 Top

| bar | source | symbol | n_gaps | max_gap_h |
|---|---|---|---:|---:|
| 5m | gate | BAS_USDT | 2 | 0.17 |
| 5m | gate | BCH_USDT | 2 | 0.17 |
| 5m | gate | BSB_USDT | 2 | 0.17 |
| 5m | gate | H_USDT | 2 | 0.17 |
| 5m | gate | NEAR_USDT | 2 | 0.17 |
| 5m | gate | PI_USDT | 2 | 0.17 |
| 5m | gate | TAO_USDT | 2 | 0.17 |
| 5m | gate | UNI_USDT | 2 | 0.17 |

## 尖刺 / 零量 / OHLC 坏样本

### spikes

| bar | source | symbol | spikes |
|---|---|---|---:|
| 15m | gate | RAVE_USDT | 8 |
| 15m | gate | LAB_USDT | 8 |
| 15m | gate | ESPORTS_USDT | 6 |
| 15m | gate | H_USDT | 6 |
| 15m | gate | GUA_USDT | 5 |
| 5m | gate | LAB_USDT | 5 |
| 15m | gate | NFP_USDT | 4 |
| 30m | okx | APE_USDT_SWAP | 4 |
| 15m | okx | BABY_USDT_SWAP | 4 |
| 15m | okx | OKB_USDT | 3 |
| 5m | okx | EDGE_USDT | 3 |
| 1H | okx | APE_USDT_SWAP | 3 |
| 15m | gate | VELVET_USDT | 3 |
| 15m | okx | UNI_USDT_SWAP | 3 |
| 15m | okx | UNI_USDT | 3 |

### zero_vol

| bar | source | symbol | zero_vol_share |
|---|---|---|---:|
| 5m | gate | AVA_USDT | 0.7717 |
| 5m | gate | TOWNS_USDT | 0.7317 |
| 5m | gate | TSTBSC_USDT | 0.6289 |
| 5m | gate | OGN_USDT | 0.5833 |
| 5m | gate | BREV_USDT | 0.5292 |
| 5m | gate | ZRX_USDT | 0.5178 |
| 5m | gate | Q_USDT | 0.5167 |
| 5m | gate | NOT_USDT | 0.4989 |
| 5m | gate | THE_USDT | 0.497 |
| 15m | gate | AVA_USDT | 0.4903 |
| 5m | gate | VELODROME_USDT | 0.4811 |
| 5m | gate | AGLD_USDT | 0.4591 |
| 5m | gate | BROCCOLI_USDT | 0.4567 |
| 5m | gate | ETHW_USDT | 0.4556 |
| 5m | gate | TAIKO_USDT | 0.4448 |

### ohlc_bad

（无）

## 未完成拉取（`.part.csv`）

| file | approx_rows | size_bytes |
|---|---:|---:|
| `ANIME_USDT_SWAP_15m.part.csv` | 24899 | 2075977 |
| `GLM_USDT_SWAP_15m.part.csv` | 33099 | 2435106 |
| `GMT_USDT_SWAP_15m.part.csv` | 18099 | 1483489 |
| `GMX_USDT_SWAP_15m.part.csv` | 16999 | 1180393 |
| `GPS_USDT_SWAP_15m.part.csv` | 12899 | 1070688 |

这些文件**不会**被 loader 读入。重跑 `python3 -m src.data.fetch_okx` 对应币种可续传；不要手改文件名假装完成。

## 黑名单候选（SWAP 15m 结构性问题）

> 仅建议，**未写入** `loader.BLOCKED_BASES`。owner 确认后再改。

| symbol | gaps | zero_vol | spikes | ohlc_bad | reasons |
|---|---:|---:|---:|---:|---|
| EWZ_USDT_SWAP | 0 | 0.3694 | 0 | 0 | zero_vol>0.02 |
| CGNX_USDT_SWAP | 0 | 0.3224 | 0 | 0 | zero_vol>0.02 |
| DKNG_USDT_SWAP | 0 | 0.3015 | 0 | 0 | zero_vol>0.02 |
| BX_USDT_SWAP | 0 | 0.2874 | 0 | 0 | zero_vol>0.02 |
| CSCO_USDT_SWAP | 0 | 0.2632 | 0 | 0 | zero_vol>0.02 |
| CIEN_USDT_SWAP | 0 | 0.2337 | 0 | 0 | zero_vol>0.02 |
| GME_USDT_SWAP | 0 | 0.1737 | 0 | 0 | zero_vol>0.02 |
| CRWD_USDT_SWAP | 0 | 0.1706 | 1 | 0 | zero_vol>0.02 |
| COST_USDT_SWAP | 0 | 0.1549 | 0 | 0 | zero_vol>0.02 |
| ADBE_USDT_SWAP | 0 | 0.1503 | 0 | 0 | zero_vol>0.02 |
| GEV_USDT_SWAP | 0 | 0.1204 | 0 | 0 | zero_vol>0.02 |
| CRDO_USDT_SWAP | 0 | 0.1164 | 0 | 0 | zero_vol>0.02 |
| EWJ_USDT_SWAP | 0 | 0.0886 | 0 | 0 | zero_vol>0.02 |
| FLNC_USDT_SWAP | 0 | 0.0612 | 0 | 0 | zero_vol>0.02 |
| ALAB_USDT_SWAP | 0 | 0.0592 | 0 | 0 | zero_vol>0.02 |
| ASML_USDT_SWAP | 0 | 0.0576 | 0 | 0 | zero_vol>0.02 |
| BMNR_USDT_SWAP | 0 | 0.0516 | 0 | 0 | zero_vol>0.02 |
| APLD_USDT_SWAP | 0 | 0.0487 | 0 | 0 | zero_vol>0.02 |
| AMD_USDT_SWAP | 0 | 0.0468 | 0 | 0 | zero_vol>0.02 |
| AMAT_USDT_SWAP | 0 | 0.035 | 0 | 0 | zero_vol>0.02 |
| AAPL_USDT_SWAP | 0 | 0.034 | 0 | 0 | zero_vol>0.02 |
| AMZN_USDT_SWAP | 0 | 0.0323 | 0 | 0 | zero_vol>0.02 |

## 解读

- 15m 序列：okx=280，gate=175。主线宇宙是 **OKX SWAP**；gate 与 spot 仅作对照。
- 单 bar >25% 尖刺在山寨上偶发，可能是真实插针；列入候选时需人工 spot-check K 线。
- stale 优先跑每日 `update_okx`，不要当成永久坏币。

## 风险与诚实声明

- 缺口检测用 `diff > 1.5×bar`，节假日/停牌造成的真实空洞也会被计数。
- 旧 cache 与 kline_fetched 合并后的序列会一起审计；决策应以 OKX fetched 为准。
- 本审计不修改任何训练数据或黑名单。

## 下一步（需 owner 决策的标为决策）

1. 对上表 SWAP 15m 黑名单候选逐币 spot-check（决策）。
2. 清掉或续传 `.part.csv` 未完成币种。
3. 确认每日 `update_okx` 仍在跑，stale 应在 24h 内消失。
