# MA3R 负例语义纠正：停止 v5 训练，保留失败记录

2026-09-22 Owner 指出负样本多数仍呈现均线密集。本轮 confirmed_blocker 是：仅按未来未达收益目标产生空标签，不能作为 Owner 所要的形态检测背景。此前把任务命名为 profitable_dense 并披露语义差异，仍未解决目标偏离。原始报告保留在 `analysis/p1_ma_profit3r_negatives_redo_20260922.md`，本记录为后续纠正。

## 已核实的依据与处置

- `yoyo/datasets/ma_profit_negative_redo.py` 的 eligible 仅要求 train、profit.retained=false、SL/TIMEOUT；被选记录明确为 `morphology_label=unadjudicated_rule_candidate`。
- 7839 个收益非赢家排除 15 个保护区冲突，训练负例 7824=5595 SL+2229 TIMEOUT。没有逐图形态否定依据。代码来源解释为什么会出现相似形态，不等于已逐图统计实际形态比例。
- A 训练 1506 正/7824 空标签；B 3012 正/15648 空标签。共同 val 175 正/1130 空标签；test 160 正/829 空标签；这些收益标签同样不能作为形态验收金标。
- 停止前实际 A results.csv 完成 8 轮，B 0 轮。核对 PID5360 命令及创建时间后，仅停止本实验进程树；taskkill 返回 0，随后无该实验匹配进程。证据在实验目录 `label_semantics_before_stop.json`、`label_semantics_stop.json`。这是中止实验，不是完整 40 轮结论。
- 原图片、标签、计划、冻结源码、已产生权重保留；Mac 和 Windows 数据/实验目录添加语义否决标记，注册表拒绝复用。标记是状态说明，不伪称所有历史脚本都会自动读取它。现有运行锁与输出保留，禁止重启该配方。
- 监控继续关闭。没有新训练、部署、promote 或账户操作。

## 修复口径

1. 形态与收益分开：符合目标形态的事件即使未达3R，也不能仅据此清空检测框。3R筛选仍保留为收益属性。
2. 普通背景和相似难负例均须证明在当前可见窗口不含目标；有其他目标则完整标注。均线密集本身既不是充分正例条件，也不是充分负例条件。
3. 本批形态未判定的事件隔离待审，不批量反标为正例。优先复用适用的原人工裁决与坐标，核对窗口、方向和类别协议。
4. 训练、验证、测试一起修正，沿用事件谱系与时间隔离。实际负例数量不得靠收益反标、跨切点或侵犯金标保护区凑齐。

## 数据对照与验证

| 项目 | 原 positive-only v4 | 补收益负例 v5 | 本次纠正 |
|---|---|---|---|
| A训练正/负 | 1506/0 | 1506/7824 | 暂停复用，未生成新标签 |
| A/B完成轮数 | 40/40 | 停止前8/0 | 不继续该配方 |
| 检测负例依据 | 缺失 | 未来SL/TIMEOUT | 要求独立形态证据 |
| 形态泛化结论 | 不成立 | 不成立 | 尚待审核与重新验证 |

收益、AUC、置换p、top-decile与随机交易对照不适用于本次代码语义审计，没有重算或伪造。可复核的否证是 eligible 条件不读取形态裁决，而 selected 明确保留 unadjudicated：它不能证明这些是形态负例；也没有声称全部7824都是形态正例。不以8轮中间指标评价模型最终收益。

只读复核命令：

```bash
sed -n '101,137p' yoyo/datasets/ma_profit_negative_redo.py
cat experiments/active/exp-ma-profit3r-negatives-20260922-v2/selection/receipt.json
cat experiments/active/exp-ma-profit3r-negatives-20260922-v2/label_semantics_stop.json
```

## 风险与诚实声明

没有完成逐张重标，没有新的合格数据集，也没有纠正后的训练结果。停止进程可能丢失正在进行的一轮，现有文件未删除，保留作废实验追溯。Owner 对3R盈利筛选的要求未撤销；本次只否定“未达3R等于检测背景”的推导。下一步为小批量形态正/负/待审对照及统一标签协议，不自动恢复监控或启动重训。
