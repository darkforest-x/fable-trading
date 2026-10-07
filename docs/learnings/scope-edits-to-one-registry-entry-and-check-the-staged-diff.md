# 改登记表的一条记录时，替换范围要锁定在这条记录内，提交前看暂存 diff 的行数

- **问题**：`experiments/registry.yaml` 里有其他会话未提交的改动，所以我用"HEAD 版本 + 我的改动"构造暂存内容，以免把别人的行带进提交。把自己那条记录的 `status: completed` 改回 `active` 时，用的是 `sed 's/^    status: completed$/    status: active/'`，作用到了整个文件。另外 9 条历史实验的状态被改掉，并且推送了（29f1abf8a4）。
- **死胡同**：更早一次，校验脚本放在 heredoc 里，后面的提交命令另起一行，没有用 `&&` 连起来。校验报错后，提交和推送照样执行（46fbf32e3b 少了必填的 result 字段）。两次都是"有检查，但检查没拦住提交"。
- **有效路径**：校验和提交放进同一个 `if 校验; then 提交; fi`。编辑先用记录锚点（`experiment_id: ...`）截出这一条，只在这一段里替换。提交前把暂存内容和上一个干净版本做 diff，确认只有预期的那几行变化。修复时以误改之前的提交（0b28b98570）为基础重建，只保留自己的一行，单独提交恢复（259ebe99d5）。
- **通用规则**：在多人共享的大文件里，不要用全文件范围的 sed 改单条记录。用构造的 blob 提交时，`git diff --cached --stat` 的行数必须和预期一致才提交。不一致就停下来，不要先推送再修。
- **牵连**：`experiments/registry.yaml`、`yoyo/contracts/artifacts.py`（`status` 只允许 active/accepted/rejected/inconclusive/superseded；`accepted` 必须附正式报告）。
