# APFS 目录统计大小不等于删除后可释放空间

- **问题**：2026-09-10 排查本机磁盘不足，`df -h /System/Volumes/Data` 只剩约 9.9 GiB。Finder 显示 Docker.raw 494.38 GB，后续目录扫描又发现 Chrome 签名临时副本累计 72,030,552 KiB，两个数字都不能直接当成可释放空间。
- **死胡同**：本轮没有执行删除。直接按 Finder 大小判定 Docker 占满硬盘会混淆逻辑容量与分配块；改用 `du` 后仍不能把所有 Chrome 副本大小相加并承诺回收同等空间，因为 APFS 克隆可能共享块。另一个错误捷径是认为临时目录或没有打开句柄就等于可以删除：本机 `T/tmp.*` 约 10.9 GiB，但用途尚未确认，未列为安全清理项。
- **有效路径**：先用 `df` 建立卷级剩余空间基线，再用 `du` 定位目录、`stat` 对比逻辑大小与分配块。Docker.raw 的逻辑大小为 494,384,709,632 bytes，分配量为 25,758,552 × 512 bytes，约 13.19 GB。Chrome 目录中的 44 份副本顶层均只有 Google Chrome.app.bundle；`lsof` 发现其中两份仍被 Chrome 或系统辅助进程映射。将旧程序副本列为退出相关进程后可核查清理的候选，而不是宣称已经回收约 74 GB。Python/pip/npm 缓存、应用缓存、离线数据、VM 运行资源和项目历史分别判断；全程未删除任何候选文件。
- **通用规则**：先区分逻辑大小、分配量、独占可回收空间。稀疏文件和 APFS 克隆需要不同解释；只以清理前后卷级可用空间的变化验证实际回收量。缓存要核实内容与重建方式，清理采用工具支持的入口；例如 uv 使用 `uv cache prune`，不按目录名直接递归删除。
- **牵连**：Docker.raw、`/private/var/folders/fr/fq9wwmwx3xn63_cdk_v8nvt00000gn/X/com.google.Chrome.code_sign_clone/`、用户缓存与项目数据。此笔记记录只读诊断，不构成删除项目数据、退出应用或操作交易执行环境的授权。目录测量为当时状态，后续清理前需复核。

依据：

- [Docker 官方：磁盘映像最大容量与实际占用](https://docs.docker.com/desktop/troubleshoot-and-support/faqs/macfaqs/)
- [Chromium 源码：CloneApp 使用 clonefile 克隆程序包](https://chromium.googlesource.com/chromium/src.git/+/2d20934f814ddd688b6dd4bd0052019391114f8d/chrome/browser/mac/code_sign_clone_manager.mm)
- [uv 官方：缓存安全与清理方式](https://docs.astral.sh/uv/concepts/cache/)
