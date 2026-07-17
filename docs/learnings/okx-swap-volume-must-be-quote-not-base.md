# OKX SWAP 成交额必须用 quote 口径，不能直接用 volCcy24h

- **问题**：多周期雷达要单独扫「主流 + 成交额前十」。用 ticker 的 `volCcy24h` 排序后，SATS/PEPE 等微单价 meme 占满榜首，BTC 反而只有 0.1M「成交额」。
- **死胡同**：把 `volCcy24h` 当 USDT 量——OKX 文档里它是 **base 币数量**；微单价币的币数天文数字，跨品种不可比。还把 MU/XAU/SOXL 等股票商品合约混进「高流动性」。
- **有效路径**：USDT 近似量 = `volCcy24h * last`；成交额榜再并入项目已有 `BLOCKED_BASES` + `STOCKISH_BASES`，主流币用固定 `CORE_MAJORS` 钉死，不跟涨跌榜抢名额。
- **通用规则**：凡「按成交额排币」，第一步先问：字段是 base 还是 quote？跨币种比较一律 quote。雷达/扫描池要显式钉主流，不要假设它们会出现在 24h 涨跌榜。
- **牵连**：`src/scout_mtf/rank.py`（`major_and_volume_pool` / `build_scan_pool`）、`src/data/loader.BLOCKED_BASES`、`src/data/universe.STOCKISH_BASES`、雷达 UI 分区 `scout_mtf_app.js`。
