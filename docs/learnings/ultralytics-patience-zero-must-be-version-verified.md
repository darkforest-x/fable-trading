# 关闭早停前先核对已安装版本的零值语义

- **问题**：实验要求固定跑满 40 轮，但 `patience=0` 在不同库或不同版本里可能表示“立即停止”、
  “使用默认值”或“禁用早停”；只凭记忆写合同会让实际处理和预注册不一致。
- **死胡同**：把 `patience` 调成一个大于 `epochs` 的数字虽然通常也能跑满，但它没有准确表达
  “关闭早停”，还给未来修改 `epochs` 留下隐蔽耦合。
- **有效路径**：直接读取当前训练契约锁定的 Ultralytics 8.4.89 `EarlyStopping` 源码，确认
  `self.patience = patience or float("inf")`，并在远端运行日志里再次核对实际参数为
  `epochs=40, patience=0`。
- **通用规则**：凡是用 0、空值或负值关闭框架功能，预注册前先检查项目实际安装版本的实现，
  启动后再用框架自己的参数回显做第二道确认。
- **牵连**：`constraints-ci.txt`、`src/detection/train.py`、
  `scripts/train_15m_ma_launch_t3_on_3060.sh`、训练 `args.yaml` / 远端日志。
