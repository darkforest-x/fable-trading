# Offline Watchers Need Log Markers

- **问题**：离线任务的后处理 watcher 需要在主任务完成后自动写总结；第一版按
  `screen -ls` 的 screen 名称判断 YOLO 工具是否结束，启动后误判为已结束，提前写出无效摘要。
- **死胡同**：直接把多层 Python heredoc 塞进 `screen zsh -lc` 也不稳，zsh 会提前展开
  Markdown 反引号和 Python f-string 里的内容，导致脚本在真正运行前就被破坏。
- **有效路径**：把 watcher 固化成脚本文件，并改为等待主任务日志里的稳定完成标记
  `yolo tools task finished`；最终汇总也只看各任务日志的 finished marker，不看 screen 是否存在。
- **通用规则**：后台离线流水线要用“产物或日志 marker”作为完成条件；screen/session 名称只能用来观察运行状态，
  不能作为后处理触发条件。
- **牵连**：涉及 `output/offline_tasks/post_yolo_tools_summary_fixed.sh`、
  `output/offline_tasks/final_summary_fixed.sh`；不涉及训练、阈值、成本假设或 holdout。
