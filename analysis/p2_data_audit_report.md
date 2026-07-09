# P2-12 数据质量审计

**日期**：2026-07-09 19:03 UTC
**纪律**：只读扫描；不改 loader 黑名单、不碰 holdout、不调参。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/data_audit.py
python3 -m pytest tests/test_data_audit.py -q
```

## 覆盖统计

| 项 | 数值 |
|---|---:|
| 序列总数 | 1049 |
| 触发任一阈值 | 603 |
| 结构性问题（缺口/零量/尖刺/OHLC） | 299 |
| 黑名单候选（全宇宙） | 200 |
| OKX SWAP 15m 序列 | 363 |
| OKX SWAP 15m stale | 1 |
| 未完成 `.part.csv` | 2 |

分 bar：

- `15m`: 612
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
| 15m | okx | SOON_USDT_SWAP | 11 |
| 15m | okx | RAVE_USDT_SWAP | 9 |
| 15m | gate | RAVE_USDT | 8 |
| 15m | okx | LAB_USDT_SWAP | 8 |
| 15m | gate | LAB_USDT | 8 |
| 15m | okx | H_USDT_SWAP | 8 |
| 15m | gate | ESPORTS_USDT | 6 |
| 15m | okx | LIGHT_USDT_SWAP | 6 |
| 15m | gate | H_USDT | 6 |
| 5m | gate | LAB_USDT | 5 |
| 15m | gate | GUA_USDT | 5 |
| 15m | okx | MMT_USDT_SWAP | 4 |
| 15m | okx | PARTI_USDT_SWAP | 4 |
| 15m | okx | BABY_USDT_SWAP | 4 |
| 15m | gate | NFP_USDT | 4 |

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
| 15m | okx | ISRG_USDT_SWAP | 0.4634 |
| 5m | gate | AGLD_USDT | 0.4591 |
| 5m | gate | BROCCOLI_USDT | 0.4567 |
| 5m | gate | ETHW_USDT | 0.4556 |

### ohlc_bad

（无）

## 未完成拉取（`.part.csv`）

| file | approx_rows | size_bytes |
|---|---:|---:|
| `ANIME_USDT_SWAP_15m.part.csv` | 24899 | 2075977 |
| `MANA_USDT_SWAP_15m.part.csv` | 24499 | 1868552 |

这些文件**不会**被 loader 读入。重跑 `python3 -m src.data.fetch_okx` 对应币种可续传；不要手改文件名假装完成。

## 黑名单候选（SWAP 15m 结构性问题）

> 仅建议，**未写入** `loader.BLOCKED_BASES`。owner 确认后再改。

| symbol | gaps | zero_vol | spikes | ohlc_bad | reasons |
|---|---:|---:|---:|---:|---|
| ISRG_USDT_SWAP | 0 | 0.4634 | 0 | 0 | zero_vol>0.02 |
| ROK_USDT_SWAP | 0 | 0.4223 | 0 | 0 | zero_vol>0.02 |
| SONY_USDT_SWAP | 0 | 0.2748 | 0 | 0 | zero_vol>0.02 |
| TTMI_USDT_SWAP | 0 | 0.2683 | 0 | 0 | zero_vol>0.02 |
| SHLD_USDT_SWAP | 0 | 0.2477 | 0 | 0 | zero_vol>0.02 |
| XLE_USDT_SWAP | 0 | 0.2162 | 0 | 0 | zero_vol>0.02 |
| TWLO_USDT_SWAP | 0 | 0.2129 | 0 | 0 | zero_vol>0.02 |
| USO_USDT_SWAP | 0 | 0.1992 | 0 | 0 | zero_vol>0.02 |
| TSEM_USDT_SWAP | 0 | 0.1987 | 0 | 0 | zero_vol>0.02 |
| ONDS_USDT_SWAP | 0 | 0.1962 | 0 | 0 | zero_vol>0.02 |
| RIVN_USDT_SWAP | 0 | 0.1918 | 0 | 0 | zero_vol>0.02 |
| OSCR_USDT_SWAP | 0 | 0.1855 | 0 | 0 | zero_vol>0.02 |
| SOFTBANK_USDT_SWAP | 0 | 0.1828 | 0 | 0 | zero_vol>0.02 |
| TER_USDT_SWAP | 0 | 0.1765 | 0 | 0 | zero_vol>0.02 |
| RDDT_USDT_SWAP | 0 | 0.1744 | 0 | 0 | zero_vol>0.02 |
| ZHIPU_USDT_SWAP | 0 | 0.1688 | 0 | 0 | zero_vol>0.02 |
| MINIMAX_USDT_SWAP | 0 | 0.1676 | 0 | 0 | zero_vol>0.02 |
| URNM_USDT_SWAP | 0 | 0.1614 | 0 | 0 | zero_vol>0.02 |
| EWT_USDT_SWAP | 0 | 0.1501 | 0 | 0 | zero_vol>0.02 |
| WEN_USDT_SWAP | 0 | 0.1475 | 0 | 0 | zero_vol>0.02 |
| UVXY_USDT_SWAP | 0 | 0.1461 | 0 | 0 | zero_vol>0.02 |
| VRT_USDT_SWAP | 0 | 0.1421 | 0 | 0 | zero_vol>0.02 |
| HYUNDAI_USDT_SWAP | 0 | 0.133 | 0 | 0 | zero_vol>0.02 |
| IWM_USDT_SWAP | 0 | 0.1321 | 0 | 0 | zero_vol>0.02 |
| POET_USDT_SWAP | 0 | 0.1321 | 0 | 0 | zero_vol>0.02 |
| SMH_USDT_SWAP | 0 | 0.1301 | 0 | 0 | zero_vol>0.02 |
| LLY_USDT_SWAP | 0 | 0.1052 | 0 | 0 | zero_vol>0.02 |
| SMCI_USDT_SWAP | 0 | 0.1032 | 0 | 0 | zero_vol>0.02 |
| LRCX_USDT_SWAP | 0 | 0.1004 | 0 | 0 | zero_vol>0.02 |
| NFLX_USDT_SWAP | 0 | 0.099 | 0 | 0 | zero_vol>0.02 |
| INFQ_USDT_SWAP | 0 | 0.0853 | 0 | 0 | zero_vol>0.02 |
| NOW_USDT_SWAP | 0 | 0.0847 | 0 | 0 | zero_vol>0.02 |
| HPE_USDT_SWAP | 0 | 0.0839 | 0 | 0 | zero_vol>0.02 |
| LUNR_USDT_SWAP | 0 | 0.0805 | 0 | 0 | zero_vol>0.02 |
| MSFT_USDT_SWAP | 0 | 0.0751 | 0 | 0 | zero_vol>0.02 |
| ORCL_USDT_SWAP | 0 | 0.0741 | 0 | 0 | zero_vol>0.02 |
| IREN_USDT_SWAP | 0 | 0.0705 | 0 | 0 | zero_vol>0.02 |
| META_USDT_SWAP | 0 | 0.0694 | 0 | 0 | zero_vol>0.02 |
| RDW_USDT_SWAP | 0 | 0.0663 | 0 | 0 | zero_vol>0.02 |
| XPD_USDT_SWAP | 0 | 0.0653 | 0 | 0 | zero_vol>0.02 |

## 解读

- 15m 序列：okx=437，gate=175。主线宇宙是 **OKX SWAP**；gate 与 spot 仅作对照。
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
