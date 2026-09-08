# 行情缓存冲突须独立核验并留下裁决记录

- **问题**：拼接既有15m缓存与近期OKX数据时，BTC/FIL各一根volume不同，OHLC一致，严格合并拒绝整币。
- **死胡同**：不能按latest-wins覆盖，也不能删掉这两个币缩小原54池。仅哈希正确只能证明文件身份，不能证明两个来源数值一致。
- **有效路径**：使用已提交的fetcher独立重取两币；显式verification-dir只核验冲突时点，不补行情。只允许OHLC一致、volume且完整OHLCV与某个原观测严格一致的核验裁决；manifest保留每个原始值、选择值、核验路径/哈希与未知历史首次可见性。
- **通用规则**：修数据质量必须与收益无关，保留失败快照和独立证据。不能把当前交易所确认当成过去第一次发布值的证明。
- **牵连**：yoyo/data/altcoin_history.py、独立research recent/verification_two目录；canonical缓存从未重写。
