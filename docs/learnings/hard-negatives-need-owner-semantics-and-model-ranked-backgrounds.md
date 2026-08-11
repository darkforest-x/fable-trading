# Hard negatives need Owner semantics and model-ranked backgrounds

- **问题**：Owner-short compact YOLO 的1:1 baseline只有普通空背景。直接把随机空窗扩成3倍，负样本数量会对，但大量样本并不“难”；反过来只取模型高分空窗，又可能把未穷举标注的真实空头形态当负例，污染短空语义。
- **走不通的路**：不能用“未来没有下跌/没有赚钱”定义hard negative，那是结果标签，不是形态语义；也不能用某个新置信度门筛选，否则在hard-negative构建阶段就偷做了阈值调参。把val高分误报回流训练同样破坏固定尺子。
- **有效路径**：先使用Owner明确判为`long`、且不碰任何short框保护区的compact crops作为短空模型的语义难负例；剩余名额只从原train时间块、所有Owner框±12根之外的背景池中挖。背景候选按冻结baseline分数排序而不设选择阈值；最终按W12–19分别补齐，使hard-negative直方图严格等于train正例的2倍。旧1:1数据集和完整val逐文件字节不变。
- **通用规则**：方向模型的hard-negative bank应同时具备“人工确认的相似反类”和“当前模型最容易误报的安全背景”。数量、时间块、窗口分布和排名规则必须在看分数前冻结；holdout、未来收益、val误报都不能成为训练样本来源。
- **本轮证据**：1143个train正例对应2286个hard negatives；916个来自Owner-long，1370个来自6858个train背景的模型排序；总负正比3:1，hard占负样本2/3，W分布逐档精确匹配，5377个base图/标签字节一致，val不变，duplicate image+label SHA为0。
- **牵连文件**：`scripts/build_owner_short_gold_center_hardneg.py`、`tests/test_build_owner_short_gold_center_hardneg.py`、`datasets/owner_short_gold_center_hardneg_candidates_r1/`、`datasets/owner_short_gold_center_hardneg_r1/`。
