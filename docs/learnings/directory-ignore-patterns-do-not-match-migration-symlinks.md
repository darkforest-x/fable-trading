# 目录型 ignore 规则不匹配迁移后留下的软链接

- **问题**：把已被 `.gitignore` 忽略的冷目录迁到外盘、原地换成软链接后，`git status` 里冒出 `?? <path>`，原本安静的工作区多了未跟踪项。
- **死胡同**：以为"原来被忽略，换成软链接也照样忽略"。实际上 `results/` 这类带斜杠的规则只匹配目录，git 把软链接当文件看，不会进去解析目标。如果去改共享 `.gitignore`，又会影响别的机器和 CI。
- **有效路径**：迁移前用 `git check-ignore -q <dir>/` 记录每个目录原来的忽略状态；迁移后对比 `git status --porcelain -- <path>`，只对"原来被忽略、现在变可见"的路径，在 `.git/info/exclude` 里补 `/完整路径`（2026-10-23 补了 5 条，改前备份为 `git-info-exclude-before`）。原本就可见的目录不加规则，保持原来的可见性。
- **通用规则**：凡是"目录换软链接"的迁移，验收都要包含 git 可见性前后对比；修补用本机 `.git/info/exclude`，共享 `.gitignore` 不动，与 task52 的 `local-excludes.json` 先例一致。
- **牵连**：`data/_storage_migrations/20261023-disk-cleanup/`；`data/_storage_migrations/20261016-task52-bulk/local-excludes.json`。
