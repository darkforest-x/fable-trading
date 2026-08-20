# W10 分类 3 天 holdout 试跑

**仅 3 天 holdout 试跑，不能当验收。未 promote、未下单。**

本配置 `fixed_w10_core4_confirm1_v1_cls` **第 1 次消耗 holdout**（Owner 2026-08-13 明确点名用最近 3 天）。全局 HANDOFF 最后编号是第 12 次误耗，此后还有按配置记账的 2d/3d/ETH 年形态，没有扩成全市场 holdout。

## 怎么回测

1. 每个 15m 决策 bar 只看当时及以前的 10 根：槽 0–4 前文、5–8 核、第 9 根 confirm。没有第 10 根未来 K。
2. `render_w10 overlay=False`，白边 letterbox 到 960，无 CenterCrop、无 ImageNet normalize（与训练一致）。
3. p(SIGNAL)≥0.50 → 按冻结 SHORT 合同下一根开盘做空，TP5×ATR14 / SL2×ATR14 / 72 根，同 bar 双触判 SL。NO_SIGNAL 不做。
4. 同币信号间隔 18 根（现有 MIN_GAP）。不改障碍、不改成本。

## 用了哪份 3 天数据

canonical `~/fable-trading/data/kline_fetched` 的 USDT-SWAP 15m **mtime 停在 2026-08-05**，没有这 3 天。

实际用的是今天刚拉的 disposable 快照：

`~/fable-trading/analysis/output/yoyo_r3a_v3gold_ft_r1_holdout_losers3d_20260813/kline_snapshot/`

- 拉取时间：2026-08-13 12:26 UTC，未写回 canonical
- 26 个当时有数据的 OKX USDT-SWAP（R3A losers 清单，不是全市场）
- 决策区间 UTC：**2026-08-10 00:00 → 2026-08-13 12:00**
- 快照最晚 bar：2026-08-13 12:00 UTC
- 币：AEON, ALLO, AXTI, BEAT, BICO, BONK, COHR, DOS, GIGGLE, GRVT, HOME, KAITO, KORU, LITE, MINIMAX, MMT, MOVE, ONE, RE, ROBO, SLX, UB, UNI, ZAMA, ZEC, ZHIPU（均为 `_USDT_SWAP`）
- DOS 历史较短，只扫到 49 个窗口；其余各 337 个

权重 SHA256 `18bcb5988e6dd36bdf2fc8a1a22d3ad66ab78b777a1d02c88080c937e98d0541`（从 3060 拷回后核对）。推理在 Mac MPS，约 3.4 分钟，未 CPU 硬跑。

## 结果

扫描 8,474 个因果 W10 窗口。

| 口径 | 信号条数 | 已平仓 | 未平仓 | maker 净盈亏 | taker 净盈亏 | maker 胜率 | 出场 |
|---|---:|---:|---:|---:|---:|---:|---|
| **去重后（主口径）** | **126** | **119** | 7 | **+0.0453**（约 +3.8 bp/笔） | **−0.0023**（约 −0.2 bp/笔） | **31.9%** | TP 24 / SL 76 / timeout 19 |
| 未去重 | 938 | 877 | 61 | +4.70 | +4.35 | 40.4% | 同一密集段会重复进场，不当成交 |

净盈亏是每笔单位名义收益之和（1.0 = 100%），maker 成本 6 bp、taker 10 bp 往返。快照在 08-13 12:00 截断，靠近末端的 7 笔未走完 72 根，不计入已平仓。

## 结论

去重后 119 笔已平仓：maker 勉强略正、taker 打平偏负，胜率 32%，SL 远多于 TP。样本是 3 天 × 26 个 losers 币，不是全市场，也不是 train 之后的独立验收窗。

同一快照上旧 R3A YOLO（W12–19）去重事件 191（间隔 5 根），本分类器 126（间隔 18 根）。进场语义不同，那边这次扫描也没有同一套 SHORT 净盈亏，**不能直接比划算不划算**。

**不能当验收，不能 promote。** 下一步若要可比：同一快照、同一 18 根间隔、同一 TP5/SL2/72 再跑旧 YOLO；或 Owner 另批全市场 3 天。
