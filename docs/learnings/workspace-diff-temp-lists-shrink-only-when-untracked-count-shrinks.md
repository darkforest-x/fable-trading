# 工作区差异临时清单反复撑满磁盘，只有减少未跟踪文件数才能缩小它

- **问题**：2026-10-23 磁盘只剩 1.9G。`du ~` 只有 277G，真正的大头在 `/private/var/folders/.../T`，里面有 1,188 份 `tmp.*` 文件，共 99.5G。每份都是 NUL 分隔的仓库未跟踪文件清单（约 70.9 万条，单份 50–96MB），忙时约 30 秒生成一份。这已是第四次复发，前几次见 [生成者≠清理失败原因](workspace-diff-temp-producer-is-not-cleanup-failure-cause.md) 和 [挤占监控磁盘](repeated-diff-inventories-can-exhaust-the-monitor-disk.md)。
- **死胡同**：
  - 一开始没有先搜 `docs/learnings/` 的 `tmp`，而是从头排查：查仓库 pre-commit、security-guidance 和 fable-advisor 插件、Claude.app 的 app.asar、Codex 会话日志。花了大量时间，最后得到的还是旧结论。
  - 用 `ps` 轮询能看到 ChatGPT.app 在跑 `ls-files --others -z`，但同一时段并没有新文件生成，所以只是相关，算不上证据。
  - 每次只清理文件，下一轮又会长回来。
- **有效路径**：用 `lsof <新tmp文件>` 在写入瞬间抓持有者，得到确定的进程链：ChatGPT.app → `codex app-server` → workspace-diff bash 脚本 → `git ls-files --others --exclude-standard -z`，stdout 和 stderr 分别写入两个 mktemp 文件。处理时按 #52 先例，把清单做 sha256 去重、gzip 归档到外盘（99.5G 压到 0.98G），解压核对后才删本机。另外把冷的未跟踪大目录迁到外盘并留软链接后，清单从 70.9 万条降到 46.4 万条，单份从 96MB 降到 52MB。这说明清单大小由未跟踪文件数决定。
- **通用规则**：磁盘再满时，先 `ls docs/learnings | grep -i tmp`，再查 `$TMPDIR`，最后才查家目录。清理只是止血；要根治，得让 `experiments/active/*` 的海量产物被 git 忽略（改共享 `.gitignore` 由 owner 决定，`.git/info/exclude` 只影响本机），或者让 Codex 的 diff 面板别扫这个仓库。2026-10-24 owner 批准在本机 exclude 里加了 15 条规则，覆盖 streams 的 csv.gz/json、逐笔 trades/fills、inputs 的 zip 等，未跟踪文件从 387,776 降到 52,516；要提交被这些规则命中的文件，需用 `git add -f`。
- **牵连**：收据在 `data/_storage_migrations/20261023-disk-cleanup/`（`summary.json`、`temporary-git-pathlists-manifest-*.json`）；外盘路径 `MacData/zhangzc/storage-migration-20261023-disk-cleanup/`；软链接与 ignore 的交互见 [目录型 ignore 不匹配软链接](directory-ignore-patterns-do-not-match-migration-symlinks.md)。
