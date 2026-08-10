# 数据 seed 与训练 seed 是两份独立契约

- **问题**：P1 预注册只写了 `seed=20260807`，但 C3/B1 的 Ultralytics
  `args.yaml` 都记录 `seed: 0`。数据集确实由 20260807 构建，训练器却没有 seed CLI，
  因而静默采用框架默认值。一个字段掩盖了两个随机过程，文字配置与真实执行产生歧义。

- **死胡同**：只看 YAML 里的预期 seed，或因为各臂指标可复现就默认 trainer 一定消费了
  该值，都不能证明训练 seed。构建 manifest 的确定性也不能外推到权重初始化、batch 顺序和
  CUDA 算子的随机性；它们属于另一条随机链。

- **有效路径**：以模型目录里的 `args.yaml` 为执行事实源，分别核对 dataset builder 与
  trainer 的参数入口。当前三臂实际都用 dataset seed=20260807、training seed=0，公平比较
  未被破坏；随后把配置拆为两个字段，并让 trainer 和 3060 wrapper 显式接受、传递和记录
  training seed。已运行权重不因文档勘误而重写或冒充另一 seed。

- **通用规则**：任何 ML 实验冻结 seed 时，第一步列出所有独立随机过程，至少分开记录
  `dataset_seed` 与 `training_seed`；验收时从最终运行产物反查实际值，而不是只相信启动配置。
  若两者不符，先判断各实验臂是否仍保持同一实际 seed，再决定重训还是做诚实勘误。

- **牵连**：
  - `configs/local_signal_v2_p1.yaml`
  - `analysis/p1_local_signal_v2_prereg_20260810.md`
  - `src/detection/train.py`
  - `scripts/train_w20_midbox_on_3060.sh`
  - `analysis/output/p1_local_signal_v2/training/{B1,C3}/args.yaml`
  - 同族教训：[可复现性要分轴验证](reproducibility-is-per-axis-not-a-boolean.md)
