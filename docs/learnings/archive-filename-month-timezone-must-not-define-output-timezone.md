# 月包文件名的月份时区不能顺手当成输出 K 线时区

- **问题**：OKX 文件名为 `2024-01` 的官方月度 K 线包，第一根实际是 `2023-12-31 16:00Z`，即按 UTC+8 月份打包；若拿 UTC 月边界校验，会把正确文件误判越界，更危险的宽松实现则可能静默错月。
- **死胡同**：根据文件名直接假定 `[2024-01-01 00:00Z, 2024-02-01 00:00Z)`，再把这个假定同时用于来源完整性和 15m 聚合。文件名只说明供应商的归档日历，不能证明输出坐标系。
- **有效路径**：先检查真实压缩包的首尾原始 timestamp，冻结来源窗口为 UTC+8 月份（UTC 中减 8 小时）；来源窗口校验通过后，再单独把每根 1m candle 按 Unix epoch floor 到 UTC 15m。holdout 门比较最终 UTC timestamp，并要求整个月包的 UTC 右端早于安全边界。
- **通用规则**：接入任何按日/月分片的市场数据时，第一步抽查首尾 timestamp；把「归档分片日历」「K 线对齐时区」「实验/holdout 时区」写成三个独立契约，禁止由文件名互相推断。
- **牵连**：`src/data/fetch_okx.py`、`tests/test_fetch_okx_archives.py`、`experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json`；外部约束为 OKX 官方月包按 UTC+8 分片、仓库 holdout 为 `2026-05-04T00:00:00Z`。
