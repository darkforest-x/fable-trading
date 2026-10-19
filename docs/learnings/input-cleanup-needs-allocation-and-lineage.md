# 清理实验输入前，要分清实际占用、下载缓存与复现数据

- **问题**：2026-09-22 检查 `experiments/active/exp-ma-profit3r-20260922-v1/inputs`，文件逻辑大小约 102.38 GB，但 `du -k` 报告分配空间约 53.39 GB。仅凭目录名称或 Finder 文件大小，无法判断实际可回收空间和删除后果。
- **死胡同**：本轮排除两种错误捷径：把整个 `inputs` 当临时目录；把逻辑字节直接当可释放字节。`binance_1m_import` 的 CSV 带 macOS `compressed` 标记，逻辑约 66.34 GB、分配空间仅约 17.31 GB。删除这些 CSV 会移除实验行情输入，不只是清理下载缓存。
- **有效路径**：用 `du -k -d 1` 与 `stat.st_size/st_blocks` 分别核对占用和逻辑大小，再追踪生成代码与 manifest。`yoyo/data/binance_um_archives.py` 的 `_download_month` 保留月度 ZIP，`fetch_symbol` 合并为 `series` CSV。3m 目录含约 9.95 GB 下载 ZIP 和 21.99 GB 合并 CSV；清单中 654 个 complete 输出全部存在且非空，19,783 个 ZIP 与 complete 月记录集合一致，没有清单外 ZIP。ZIP 可作为优先清理候选，保留 CSV、审计记录、URL 与哈希；日后从原始 ZIP 重建需要重新下载。
- **通用规则**：先量分配空间，再区分可重新下载的中间缓存与下游直接读取的输入。存在清单和输出不等于本轮已重新验证内容哈希；将“清理建议”和“已执行删除”明确分开。空间实际回收还取决于文件系统快照等因素。
- **牵连**：本轮仅提供建议，未删除、压缩或移动数据；未重新计算全量 CSV/ZIP 哈希，也未验证远端当前可下载性。大小按十进制 GB。`inputs/high` 约 4.09 GB；`compression_probe` 约 7.58 MB；`binance_1m_staging` 为空。清理整棵输入树会破坏依赖这些路径的重新扫描、标签/图像重建流程。
