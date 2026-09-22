# 相同 rect=True 不保证训练、验证和预测使用相同画布

- **问题**：MA morphology v6 的 A 组标准验证 mAP50 为 0.0862，但同权重逐图预测命中 174/175 个验证正例，背景 0/175 误报。同一幅原图与标签没有变化。
- **死胡同**：先把差异归为“AP 与 any-hit 口径不同”。口径确实不同，但已有逐图预测仅 176 个框、174 个命中，不能解释数倍差距。只看到三个入口均为 `rect=True` 就认定预处理一致，也漏掉了底层 padding 参数。
- **有效路径**：查看本机实际安装的 Ultralytics 8.4.89：`build_yolo_dataset` 在训练使用 `pad=0.0`、验证使用 `pad=0.5`。`set_rectangle` 对每个维度计算 `ceil(shape * imgsz / stride + pad) * stride`；1280×742 原图在训练/逐图预测为宽1280高768，在标准验证为宽1312高768，左右各多16像素。固定同一 A 权重和 BAND 验证图，只有左右补边改变：无补边得到正确空头框，置信度0.974375、IoU0.992522；补边后出现错类框，同类最大IoU0.272790；移除补边完全恢复。使用同一标准 AP 计算器、只切换 dataset pad 的全验证/测试对照进一步量化差异。
- **通用规则**：评估指标冲突时，先冻结权重/图片/标签，记录实际模型输入张量、缩放与补边，再用可逆单变量实验。参数同名和原图尺寸一致都不能证明张量一致。修正评估可以消除测量差异，但不能消除模型对位移的真实脆弱性；对固定位置图得到的高分不得冒充真实扫描泛化。
- **牵连**：`scripts/windows/run_ma_morphology_redo.py`；`scripts/research/diagnose_ma_morphology_padding.py`；`experiments/active/exp-ma-morphology-negatives-20260922-v3/evaluation_diagnosis/one_image_padding_probe.json`。当前进行中的 B 训练和冻结输入未修改；原生验证指标保留，未更换最佳权重或宣称盈利。
