# 验收回执必须绑定 verifier 自己的 commit，不能复用 builder 的 commit 发现逻辑

- **问题**：数据集独立验收全部通过，但首版 QA 回执里的 `verifier_commit` 实际指向 builder 的
  首次提交；哈希与检查结果是真的，声称“哪版 verifier 做了检查”的 provenance 却是假的。
- **死胡同**：复用“沿第一个 builder 输入文件找最近提交”的函数，只因 verifier 也在输入列表里
  就把返回值命名成 `verifier_commit`。列表包含某文件不等于 commit 查找绑定了该文件。
- **有效路径**：运行前仍检查 verifier 及所有合同文件已提交且工作区干净，但回执身份单独取当前
  `HEAD`（或直接对 verifier 路径取提交），并用测试断言回执值等于执行时提交。错误回执移到可恢复
  的 Trash 路径，重新执行全量验收，不能原地悄悄改字段。
- **通用规则**：任何 `*_commit` 字段都必须从字段所指对象的加载/执行边界直接产生；先写一句
  “这个字段回答哪个文件或运行身份”，再选择 git 查询。共享的 clean-tree 守门可以复用，身份解析
  不能因调用方便而复用错误语义。
- **牵连**：`scripts/verify_15m_ma_launch_t3_dataset.py`、
  `experiments/active/exp-15m-ma-launch-t3-yolo10000-v1/results/dataset_qa_receipt.json`、
  `tests/test_ma_launch_t3_training.py`；相关原则见
  [provenance-must-be-produced-by-the-loading-operation.md](provenance-must-be-produced-by-the-loading-operation.md)。
