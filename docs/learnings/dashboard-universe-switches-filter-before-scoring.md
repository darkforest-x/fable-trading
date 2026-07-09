# Dashboard Universe Switches Must Filter Before Scoring

- **问题**：P1-8 看板要支持现货/合约切换，后端最初只按 universe 切 dataset 与 score cache。
- **死胡同**：只换 CSV 路径不够。旧的 spot 侧 TP5/SL2 数据集里混有少量 `_USDT_SWAP` 行；如果直接训练/打分，现货页会显示现货标题但模型阈值和信号池仍被 SWAP 污染。
- **有效路径**：把 universe 过滤放到训练/打分之前，再按过滤后的数据集计算 val q90 阈值、全量 score、组合模拟与 symbol list；score cache metadata 也记录 universe、dataset path、dataset sha，避免旧缓存绕过过滤口径。
- **通用规则**：任何“宇宙切换”都先检查原始样本是否混池；先过滤再训练/打分/缓存，不要相信文件名天然代表纯宇宙。
- **牵连**：`src/webapp/server.py`、`data/sweep_v3/judgment_v3_tp5_sl2_h72.csv`、`data/swap_replication/swap_tp5_sl2.csv`、`data/scored_signals_<universe>.csv`。
