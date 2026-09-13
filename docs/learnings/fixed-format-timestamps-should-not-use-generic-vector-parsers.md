# 固定格式时间戳不要逐行调用通用向量解析器

- **问题**：开发期全市场面板需要逐行检查时间边界，830 个 30 分钟数据源因每根 K 线调用 `pd.to_datetime(..., utc=True)` 而长期占满单核；边界协议正确，但研究吞吐被时间解析主导。
- **死胡同**：先怀疑 gzip 的逐字节读取并考虑用 `readline()` 或块读提速。这样会把 cutoff 行剩余 payload 完整物化，削弱“不解析边界后记录”的审计语义，而且没有先证明它是最大瓶颈。
- **有效路径**：先微基准各步骤，确认通用时间解析比 `pd.Timestamp` 慢两个数量级；仅将固定 ISO 标量解析替换为 `pd.Timestamp`，随后对无时区、`Z`、UTC offset 和 `+08:00` 输入统一做 UTC 本地化或转换。读取协议、cutoff 决策和 prefix digest 均保持不变。
- **通用规则**：因果前缀扫描变慢时，先分别测时间解析、解压和字段扫描；对来源契约固定的 ISO 标量使用标量解析器，并用与原解析器的参数化等价测试锁住时区语义。不要为了速度完整读取 cutoff 行。
- **牵连**：`yoyo/evaluation/spike_market_breadth_study.py` 的 `_utc`、前缀与 variant-block 读取器，以及 `tests/evaluation/test_spike_market_breadth_study.py` 的边界毒化、digest 和时区等价测试。
