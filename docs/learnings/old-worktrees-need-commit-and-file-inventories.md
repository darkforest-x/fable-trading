# 旧 worktree 能否删除，要分别检查独有提交与未提交文件

- **问题**：2026-09-11 owner 询问还能删除哪些数据。两个 `.claude/worktrees` 目录合计约 11.19 GB，外观看起来都是旧副本，清理风险却不同。
- **死胡同**：只依据旧日期、分支名称或当前无进程 CWD，就把整个目录视为重复文件。未合并的提交与未提交的文件是两类独立资产，检查其中一类不能替代另一类；本轮仅核查，没有删除这些目录。
- **有效路径**：先确认 `.git` 指针和主仓的 worktree 注册关系，再分别检查与 `main` 的提交差异、tracked 修改、完整 untracked 列表。`peaceful-ardinghelli-b84300` 约 5.775 GB，其 HEAD 已在 main，状态为 0 修改、0 untracked，可列为按 Git 正常移除的候选，保留分支引用。`fable-trading-consolidation-758d61` 约 5.417 GB，仍有两个独有提交 `273039a`、`ae3c269`，以及 1 个 tracked 修改和 96 个 untracked 文件，必须保留。主任务独立复核了两分支相对 main 的独有提交数，分别为 0 和 2；没有打开样本、标注或市场数据。
- **通用规则**：删除资格需要同时满足提交已保留、工作区无独有改动、未跟踪内容已处理；真正删除前再次确认状态，使用正常 `git worktree remove`，不要用 `rm -rf` 或 `--force` 绕过检查。清理旧注册元数据也不授权删除它曾指向的目录及模型。目录大小只是候选体积，APFS 共享块下不等于可回收量。
- **牵连**：两个目录位于 `/Users/zhangzc/fable-trading/.claude/worktrees/`，与主仓共用 `.git`；候选清单保存在 `/tmp/codex-data-cleanup-candidates-20260911.json`。这是一轮磁盘元数据核查，不是训练/评估实验，没有新增 holdout 消耗，也未新建或切换分支/worktree。
