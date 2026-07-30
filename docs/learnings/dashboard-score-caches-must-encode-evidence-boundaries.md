# 看板评分缓存必须编码证据边界

- **问题**：新冻结模型虽未使用 `--eval-holdout`，但旧看板会评分整份数据集，并把 holdout 回测展示为当前动态指标。
- **死胡同**：只把缓存身份绑定模型路径和数据 SHA；这能防模型错配，却不能区分“全量评分”和“holdout 前评分”。
- **有效路径**：评分读取在时间边界前停止，缓存元数据增加 `pre_holdout_only` 与截止时间；运行面把验证集标为发现级，并关闭仍依赖旧特征的入口。
- **通用规则**：任何模型迁移先审计所有读模型和读数据的运行面；实验开关安全不代表看板、缓存和导出安全。
- **牵连**：`src/judgment/frozen.py`、`src/backtest/run.py`、`src/webapp/dashboard_cache.py`、`src/webapp/dashboard_payloads.py`、holdout 纪律。
