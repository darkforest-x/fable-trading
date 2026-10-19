# 干净的 worktree 状态仍可能藏着被忽略的本地设置

- **问题**：2026-09-11 owner 明确授权删除上一轮列出的四项候选，其中包括已经合并、tracked 和 untracked 状态均为空的 `peaceful-ardinghelli-b84300` 旧 worktree。
- **死胡同**：`git status --porcelain --untracked-files=all` 中的 all 不包含 ignored 文件。只凭空状态执行目录清理，仍可能丢失用户本地设置；已合并的提交也不能证明这些设置有备份。
- **有效路径**：删除前另查 `git ls-files --others --ignored --exclude-standard`，发现 8 个忽略项：7 个 `.DS_Store` 和 1 个 `.claude/settings.local.json`。先把设置原样备份到 `/Users/zhangzc/.local/state/disk-cleanup/20260911/peaceful-ardinghelli-b84300/settings.local.json`，逐字节核对一致，限制目录/文件权限为 0700/0600，不输出内容。随后使用不带 force 的 `git worktree remove`，保留原分支引用，复核目录与注册信息已移除。另一份含独有提交和文件的 worktree 完整保留。
- **通用规则**：工作目录的可删除性需要同时覆盖 tracked、untracked、ignored 三种文件状态。对小而有用户价值的配置，先私有备份比推断其是否重复更可靠；不能为了清理成功而使用 force 绕过检查。
- **牵连**：本轮还删除了明确授权的特征缓存、失败构建图片和四个旧运行时安装残留，保留原始历史、失败清单/标签/报告、正式数据集及当前运行时。卷级可用空间从 17.866 GB 到 32.417 GB，净增加约 14.551 GB；其他应用仍在运行，这不是逐目录回收量。执行记录位于 `/tmp/codex-four-candidate-cleanup-20260911.json`。工作目录外的新发现仍属只读调查，不能把本轮删除授权推广为任意目录的清空授权。

相关：[旧 worktree 能否删除，要分别检查独有提交与未提交文件](old-worktrees-need-commit-and-file-inventories.md)。
