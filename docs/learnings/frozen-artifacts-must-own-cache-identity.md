# Frozen artifacts must own cache identity

- **问题**：Forward validation needs a byte-frozen model, but the dashboard already had `data/scored_signals.csv` and could short-circuit before reaching the frozen loader.
- **死胡同**：Only changing `build_signals()` to load `models/frozen_*.txt` is not enough. A valid-looking old score cache can still be returned, making the dashboard appear frozen while actually serving scores from a previous training run.
- **有效路径**：Treat the frozen artifact as the cache identity. The score-cache sidecar must store the frozen model path, dataset path, and dataset SHA, and the dashboard must rebuild whenever those fields do not match the latest artifact.
- **通用规则**：When freezing a model, update every persistent cache boundary to carry the artifact fingerprint before trusting downstream reads.
- **牵连**：`src/judgment/frozen.py`, `src/backtest/run.py`, `src/webapp/server.py`, `data/scored_signals_meta.json`, `models/frozen_<config>_<date>.json`.
