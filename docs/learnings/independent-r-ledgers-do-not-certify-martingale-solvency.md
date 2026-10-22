# 独立 R 账本不能证明倍投账户不爆仓

- **问题**：BTC 拐点候选的 212 笔独立 R 有 10 次重叠入场；将每笔 R 顺序倍投，无法回答 1000U 账户是否能执行和承受盘中保证金要求。
- **死胡同**：旧 SPIKE/ChartArt 资金工具拒绝重叠仓位，且只查现金或零权益。原 Nautilus 1.231.0 研究使用 frozen account 和零保证金，不能将其没有拒单误认为通过强平检查。在线新版本的 liquidation_enabled 也不是本机 1.231.0 的 API。
- **有效路径**：复用冻结的原生成交，逐根审计 O-H-L-C 成交位置，单独重放共享资金、已实现亏损状态、重叠仓位和保证金。任何已实现亏损都翻倍，小额盈利也重置；保证金不足跳过不能重置状态。完整执行和允许跳过的最高余额分别报告。
- **通用规则**：先分清独立交易收益、有限资金可执行性和交易所真实强平三种证据。成交价 OHLC 配当前保证金档位只能给模拟筛查；缺少历史标记价与历次档位时，不能承诺历史实际不爆仓。
- **牵连**：`yoyo/evaluation/range_martingale_account.py`、`tests/evaluation/test_range_martingale_account.py`、`experiments/active/exp-btc-pivot-martingale-20261022-v1/`；手续费为 Owner 明确授权的零。
