# uv缓存也可能包含正在运行的工具环境

- **问题**：磁盘满导致实验无法落盘；Owner批准清理5.6GB uv缓存，但全量clean发现其他uv进程占用。
- **死胡同**：把所有uv缓存统称为下载缓存，会忽略uvx的临时工具虚拟环境。实际3个fiftyone-mcp进程从archive-v0的一份820MB环境运行；强制clean或prune全体会破坏后续import。
- **有效路径**：停止本次等待中的clean，核对uv0.9.28源码：定向package clean不会删除Environments桶；先确认项目venv和在用工具env均无指向外部包缓存的软链接，再用官方package clean删除133个包的缓存。实际删除27,103文件、905.8MiB，余4.6GB工具环境保留，可用空间约1.2GiB。没有终止用户工具进程或卸载环境。
- **通用规则**：清理前区分包缓存和uvx运行环境；锁定不等于僵尸进程。只在已核实目标部分不被使用时采用定向清理，不把force当成无风险解锁。
- **牵连**：`https://docs.astral.sh/uv/concepts/cache/`及`https://github.com/astral-sh/uv/blob/0.9.28/crates/uv-cache/src/lib.rs`的remove/prune实现；2026-09-21本轮缓存清理及V6研究恢复。
