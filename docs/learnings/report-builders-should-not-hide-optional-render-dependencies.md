# 分析报告生成器不应隐式依赖可选渲染包

- **问题**：全市场数值产物全部落盘后，报告在 `DataFrame.to_markdown()` 才因缺少可选 `tabulate` 失败，使长任务表现为整体失败。
- **死胡同**：为一个小表格临时安装 `tabulate` 会改变运行环境，并让从零复现仍依赖未声明包；重跑数值计算也不能解决排版依赖。
- **有效路径**：对报告中的小型二维表使用内置 Markdown 渲染，显式处理浮点精度、缺失值、竖线和换行；数值阶段与报告阶段继续使用已经写出的、有 SHA 凭据的产物。
- **通用规则**：长分析在启动前应单测最终报告路径；报告所需依赖必须锁定，或用标准库实现。数值产物应先原子落盘并附哈希，让排版失败可以单独恢复。
- **牵连**：`yoyo/evaluation/spike_market_breadth_study.py` 的 `_markdown_table`、`tests/evaluation/test_spike_market_breadth_study.py`，以及 `scripts/md_to_html.py` 的最终 HTML 转换。
