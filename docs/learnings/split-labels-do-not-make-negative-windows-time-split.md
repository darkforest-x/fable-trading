# Split 标签不会让负样本窗口自动成为时间切分

- **问题**：Stage-B 正样本先按时间分为 train/val，负样本随后按 `(symbol, split)` 的正样本数量抽取，却从该币整个 pre-holdout 历史选窗口。结果是 317 个 train negatives 出现在 train 截止之后，296 个 val negatives 出现在 val 起点之前；split 字符串正确，时间语义错误。
- **死胡同**：只对正样本的 `end_time` 做 `train.max < val.min`，再把 `summary.is_time_split=true` 当证据。这个审计完全看不见负样本；按 symbol/split 分组也只控制数量，不控制候选窗口所属时间块。
- **有效路径**：先从正样本冻结 `train_end_max / val_start_min / val_end_max`，负样本在落盘前检查完整 `[window_start, window_end]`；train 必须结束于 train block 内，val 必须从 val block 内开始且在其内结束。审计独立重建所有正负窗口的 start/end，旧数据应 fail、新数据应 pass，并让失败命令返回非零退出码。
- **通用规则**：时间切分必须验证每个实际模型输入的完整时间区间，不能验证一类样本后把 split 标签传播给其余样本。`split=train/val` 是声明，`window_start/window_end` 与冻结边界的包含关系才是证据。
- **牵连**：所有后采样 negatives、hard negatives、augmentation crops 与 replay cache；数据门、manifest schema、训练权重绑定和 downstream acceptance 都必须按全样本窗口重审。旧权重即使图片可复现，只要绑定的数据集未过新审计，就不能继承新的 P0 结论。
