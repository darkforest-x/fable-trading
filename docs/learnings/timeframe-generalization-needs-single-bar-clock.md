# 多时间框架台架要先统一 bar 时钟

- **问题**：R0 要让 fetch/update/loader/build/train 支持 5m/15m/30m/1H，同时让 sweep 能挂不同出场函数。危险点不是参数本身，而是各模块各自写死 15m 后，purge、文件发现、输出命名会悄悄分叉。
- **死胡同**：只给 CLI 加 `--bar` 不够；`fetch_okx.py` 之前已有参数但内部依赖全局 `BAR`，`update_okx.py` 仍只扫 `okx_*_15m_*.csv`，`train.py` 的 purge 也直接按 15 分钟算。这样看起来支持多周期，实际训练切分仍可能用错时钟。
- **有效路径**：先把 bar 白名单和 `horizon_bars + 1` 的 purge 换算集中到 `src/data/bars.py`，再让 fetch/update/loader/build/train 复用同一时钟；sweep 侧只注册 exit plugin，不把新实验配置混入当前结果。
- **通用规则**：遇到跨时间框架改造，第一步先找所有“时间单位写死”的地方，建立单一换算入口，再加 CLI 参数。
- **牵连**：`src/data/bars.py`、`src/data/fetch_okx.py`、`src/data/update_okx.py`、`src/data/loader.py`、`src/judgment/build_dataset.py`、`src/judgment/train.py`、`src/judgment/barrier_sweep.py`、`src/judgment/labeling.py`；默认仍为 15m/h72，未触碰 holdout、TP/SL、成本假设。
