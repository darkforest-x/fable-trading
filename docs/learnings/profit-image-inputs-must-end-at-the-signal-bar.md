# 盈利形态图片必须在信号时刻截断

- **问题**：事后截图里的均线密集和后续突破同时可见，看起来很容易分类，但实时推理时
  右侧突破尚未发生；直接用完整图训练方向或盈利类别会把未来走势变成输入。
- **死胡同**：继续提高通用 `dense_cluster` 检测 mAP 只能更准确地复刻密集规则；把完整
  历史窗口改标 long/short 又会产生视觉前视，离线准确率越高反而越可疑。
- **有效路径**：候选和 TP5/SL2 标签仍由固定数值规则生成，但每张分类图片严格取
  `[signal_i-lookback+1, signal_i]`；用修改所有未来行后图片字节不变的测试锁死边界。
- **通用规则**：凡是用图像预测未来结果，先证明输入在 signal bar 截断，再讨论模型、
  类别和 mAP；未来只能进入标签，不能进入像素或特征。
- **牵连**：`src/detection/direction_dataset.py`、MA206 long/short/no_trade 挑战者、
  judgment holdout purge、后续 YOLO classification 数据集。
