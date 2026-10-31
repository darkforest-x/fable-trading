# Touch-bar information pays at the close only while the price has not yet recovered

- **问题**：周一 1.618 模拟盘评分的优势来自触价那根 5m K 线本身（放量、针深、收在哪），触价前拿不到。Owner 问：既然信息在收盘后才有，收盘后进场还剩哪些盈利空间（`exp-monday-limit-close-slices-20261101-v1`）。
- **死胡同**：直觉是把收盘时最"强"的信号挑出来就行：高分、巨量、针深、同一根 25 币以上的报警 K 线。这些切片在 2020–2024 年每笔 +50～+585bp，到 2025–2026 年全部转亏。所有触价收盘进场的基线本身在 2025–2026 年也是 −15～−26bp。之前的触价前 1m 模型、秒级进场、收盘才进都在追同一个信息，都输给了价格已经反弹这件事。
- **有效路径**：把"信息"和"价格还剩多少"分开看。190 个切片里只有一组两年都过：评分 ≥0.5，且收盘时针只收回了不到 64%（仍在 1.618 外，中位 1.56%）。近侧止盈 2025 年 +136bp，2026 年 +149bp。同样高分、但收盘已收回一大半的，2025–2026 年每笔 −43～−103bp。信息有用，但只有在价格还没把它兑现时才值钱。它也不是少数崩盘日的产物：去掉前 10 天仍有 +72bp，大部分是单币触价。
- **通用规则**：评估一条事后才出现的信号时，同时量"信号出现时价格已走了多少"。先按已兑现程度（这里是收回比例）分层，再看信号强度；强信号加已兑现，等于在高位追。
- **牵连**：`yoyo/evaluation/monday_limit_close_slices.py`、`exp-monday-limit-close-reclaim-20261028-v1` 的 B30 成交、`scores_causal.csv.gz` 的 `stored`。仍是 190 选 1、每年约 120–135 笔，缺同币随机时间对照；相关：[a-score-that-only-earns-in-crash-bars-dilutes-the-alarm-leg.md](a-score-that-only-earns-in-crash-bars-dilutes-the-alarm-leg.md)。
