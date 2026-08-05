# 权重只住在训练它的那台机器上——换机训练 = 那一版没有副本

- **问题**：3060 上 59 个权重完好，唯独 v11、v12、v13 一个都没有。为什么偏偏
  是这三版，而且偏偏 v11 是项目里唯一真正找不回的模型？

- **死胡同**：先怀疑是清除脚本的路径匹配问题（比如只删了某个命名模式），
  查下来不成立——3060 上 `owner_v14_pad200`、`owner_v16_tipuni_cold` 都在，
  命名模式跟 v11/v12/v13 没有区别。

- **有效路径**：读 `scripts/_archive_pretip/train_owner_v11_from_round9.sh` 第 3 行
  的注释：「the 3060 is unreachable while the owner travels」。v11 是本机 MPS 训的
  （`device=mps`，6.5 小时），v12/v13 同期同理。**权重的分布不是备份策略决定的，
  是「哪台机器跑的训练」决定的**——训练产物默认只在执行机上落盘，没有任何
  自动同步。3060 训的版本自带一份异地副本纯属副作用；Mac 训的版本则是单点。
  v12/v13 侥幸活着只因为 Mac 那次清除漏了 `runs/`；v11 连这份侥幸都没有。

- **通用规则**：换机训练时，那一版的副本数从 2 掉到 1，且**没有任何提示**。
  训练脚本的 promote 步骤应当同时落一份异地副本；在那之前，凡是 owner 说
  「这版重要」的模型，训完立刻手动拷走。判断一个模型有几份副本，看的是
  `args.yaml` 里的 `device` 和训练日志的执行机，不是看 `models/` 目录。

- **牵连**：v11 训练时 promote 那步还撞上 `OSError: [Errno 28] No space left on
  device: models/owner_best.pt`（`logs/owner_v11_train.log:502`），磁盘满导致拷贝
  失败、日志无重试记录——所以连「07-18 到 07-23 之间 `owner_best.pt` 里究竟是
  哪一版」都已无法验证。单点 + 静默失败叠加，事后无从取证。
  参见 [[purge-records-are-claims-not-facts]]。
