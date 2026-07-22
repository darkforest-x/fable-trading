# 训中调试别往训练 .venv 塞 CV 工具

- **问题**：要对 hardneg 做 supervision 叠框，但本机 `.venv` 正被 ultralytics v13 MPS 训练占用。
- **死胡同**：直接 `pip install supervision` 进训环境——依赖树可能碰 opencv/numpy 版本，增加训练进程诡异风险；装 FiftyOne 更重。把 FiftyOne 与 mitmproxy/ydata-profiling 塞进**同一个** side venv 也会掐依赖钉。
- **有效路径**：
  1. 零依赖：matplotlib/cv2 静态画廊（昨夜路径仍可用）。
  2. 旁路：`.venv-tools` 装 supervision / nvitop / mitm / marimo / ydata；**另开** `.venv-fo` 只装 FiftyOne。
  3. 策展入口可继续用 Label Studio 小包，FO App 按需 `--launch`。
- **通用规则**：与占 MPS 的训练并行时，默认 **零新依赖进训 .venv**；旁路工具再按「会不会掐架」拆 venv；可视化优先「已有库 + 静态 HTML」。
- **牵连**：`scripts/overlay_hardneg_boxes.py`；`scripts/fiftyone_hardneg_browse.py`；`docs/LOCAL_DEBUG_TOOLS.md`；`analysis/p_side_tools_landed.md`；H-TOOL-2/4。
