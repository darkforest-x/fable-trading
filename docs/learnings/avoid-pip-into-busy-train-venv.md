# 训中调试别往训练 .venv 塞 CV 工具

- **问题**：要对 hardneg 做 supervision 叠框，但本机 `.venv` 正被 ultralytics v13 MPS 训练占用。
- **死胡同**：直接 `pip install supervision` 进训环境——依赖树可能碰 opencv/numpy 版本，增加训练进程诡异风险；装 FiftyOne 更重。
- **有效路径**：用已有 matplotlib/cv2 做等价画廊；脚本保留 `--prefer-supervision` 开关，训完或隔离 venv 再切。策展入口优先复用已接好的 Label Studio 小包，而不是再起 FO App。
- **通用规则**：与占 MPS 的训练并行时，默认 **零新依赖进训 .venv**；可视化优先「已有库 + 静态 HTML」。
- **牵连**：`scripts/overlay_hardneg_boxes.py`；`docs/LOCAL_DEBUG_TOOLS.md`；H-TOOL-2/4。
