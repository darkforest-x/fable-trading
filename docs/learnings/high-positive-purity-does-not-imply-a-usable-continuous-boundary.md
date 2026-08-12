# 高正样本纯度不代表连续市场判别边界可用

- **问题**：Local Signal V2的Owner语义审核中，旧Positive Pool有85%被确认YES，但当前Canary只有11% YES；R2新生候选更是0/25通过。正样本语义基本成立，模型在连续市场仍大量越界。
- **死胡同**：把候选过密一概归因于positive污染，或看到Canary的89个NO后继续等量追加hard negative并开R3，都没有解释R2为何新生0%通过、同时抑制了20%的R1有效信号。R1到R2已经证明“更多hard negative”不自动改善语义边界。
- **有效路径**：用同一Owner的YES/NO二分类同时审Positive与连续Canary，并在完成后才解盲common retained、R2 new、R1 suppressed。85%对11%的断层将问题定位为情况B：positive定义大体正确，主要问题在连续分布的表示、类间不可分或decision boundary。
- **通用规则**：检测器进入下一训练臂前，先同时测positive purity和连续候选语义率；只有证明错误随某一可控变量分层变化，才允许把该变量单独立臂。不能把“收集到新的NO”本身当作重训理由。
- **牵连**：`analysis/output/local_signal_v2_positive_semantic_review200_v2/`、R1/R2 Canary、模型输入6%最小纵轴、禁止自动R3/R4与holdout纪律。
