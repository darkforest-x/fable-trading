# YOLO 重放必须固定训练时的渲染器

- **问题**：准备对 Grade-A full40 权重运行最新 OKX 行情时，当前主线 `render.py` 的 SHA 与该模型最近一次冻结扫描记录的训练渲染器 SHA 不同。若直接调用当前层模块，脚本虽然能运行，输入像素却不再有模型训练/既有回放所依据的来源保证。
- **死胡同**：把“路径仍叫 `yoyo/layers/l1_detection/render.py`”当成内容不变的证据。路径没有变，但 2026-10-13 主线版本哈希为 `def5b731…`，已不等于模型所需的 `0962812f…`；这时直接用当前模块继续推理会把代码漂移误当成模型结果。
- **有效路径**：先比较模型登记的 renderer SHA，再从训练代码提交 `ba1c321adcdbac59a24df414eaf5ff2c5ccc84f6` 取回文件；确认 `render.py` 哈希为 `0962812f…`，训练用 `data.py` 与当前哈希 `bce9af28…` 一致。把这两个历史文件按原字节放入独立的 Grade-A 输入包，并在抓取行情前校验权重、标签清单、渲染器和均线模块身份。
- **通用规则**：重放冻结视觉模型时，先用登记的 SHA 对照模型训练时的预处理代码；若当前源码漂移，从训练提交恢复并隔离固定。只有模型权重哈希相同还不足以保证推理输入兼容。
- **牵连**：`exp-15m-ma-launch-owner-grade-a8000-neg24000-train1280-full40-v1`、`experiments/active/exp-15m-ma-launch-model-compare-all3d-20260831-v1/preregistration.json`、`yoyo/evaluation/grade_a_full40_training_input_renderer/`、`yoyo/evaluation/grade_a_two_month_replay.py`。
