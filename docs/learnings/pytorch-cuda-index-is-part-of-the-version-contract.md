# PyTorch 的 CUDA index 是版本契约的一部分

- **问题**：Windows 重装脚本锁定 `torch==2.8.0`，却从 `cu124` index 安装；该 index 没有对应的 cp39/win_amd64 wheel，后续依赖解析可能补进 CPU torch，表面版本仍是 2.8.0。
- **死胡同**：只核对 `torch.__version__.split('+')[0]`，或假设“CUDA index 能自动找到相邻 CUDA 版本”。基础版本一致不能证明 wheel 平台与 CUDA flavor 一致。
- **有效路径**：安装前在目标 index 对目标 Python/OS 做 `pip install --dry-run --report`；把 `torch==2.8.0` 与匹配的 `torchvision==0.23.0` 一起从 `cu126` 安装，随后核对完整版本、`torch.version.cuda`、CUDA NMS 和实际矩阵乘法。
- **通用规则**：跨机 ML 契约至少包含包版本、wheel index、Python ABI、操作系统架构和运行时 smoke；缺任一轴都不能声称环境可比。
- **牵连**：`scripts/windows/setup_3060.ps1`、`docs/ops/RECONNECT_3060.md`、`scripts/train_on_3060.sh --check`。
