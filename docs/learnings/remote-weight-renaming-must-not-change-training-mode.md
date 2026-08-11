# Remote weight renaming must not change training mode

- **问题**：3060 上传脚本会把任意本地 base 权重统一重命名为 `yolo11s_w20.pt`，而训练器旧逻辑又通过文件名是否以 `yolo` 开头推断冷启动或微调。Stage A 的 `best.pt` 经上传后因此会被误判为冷启动，使用约 `0.002` 的学习率，而不是预注册的 AdamW `1e-4`。
- **死胡同**：只核对本地 `best.pt` 路径和 SHA-256，或只看远端模型能否加载，都无法发现训练模式已经被中转文件名悄悄改变。远端固定文件名本身也不应承担模型谱系语义。
- **有效路径**：让上传 wrapper 接受显式 `--finetune` / `--no-finetune`，把选择原样传给仓库训练器；Stage B-from-A 命令强制带 `--finetune`，日志必须出现 `finetune=True` 和 AdamW `lr0=0.0001` 后才算真正开训。
- **通用规则**：凡复制、缓存或远端传输会改名的模型，训练模式、类别映射和谱系必须作为独立显式参数传递；不得从中转文件名推断。启动后必须复核运行日志里的最终解析参数，而不只复核调用命令。
- **牵连**：`scripts/train_w20_midbox_on_3060.sh`、`src/detection/train.py`、所有经该 wrapper 从历史 `best.pt` 链式微调的实验及其预注册记录。
