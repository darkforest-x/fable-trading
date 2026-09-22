# 负样本必须在训练加载清单里核对，不能靠整个数据集的总数

- **问题**：MA 3R 第一轮 A1506、B3012 张训练图全部是正例，1130/829 张空标签只在 val/test。Owner 询问才明确说明；此前 plan 的“positive only as requested”是助手错误解释，不是 Owner 要求。
- **死胡同**：格式、哈希、容量、因果检查全部通过，仍不能证明训练总体正确。旧审计甚至把训练空标签当错误，固化了错误假设。把“只用原版”理解成不要反例，以及只检查全数据集有空标签，都漏掉了实际 loader 的监督构成。
- **有效路径**：按每组实际 train txt 逐张读取标签，分别记录正/负图片和去重事件；本轮保留旧正图与评估字节，只加入原候选总体中已解析的训练反例。目标仍是 profitable_dense，SL/TIMEOUT 只表示未达收益目标，不能反写成形态不成立。UNKNOWN/INVALID 排除，人工旧标签保护，所有退出理由留存。
- **通用规则**：开训前核对类别定义 × split × arm × 实际 loader 路径 × 实际标签内容。需要反例的实验若任一训练组负例为零必须拒绝启动。比例不靠挪动 val/test、复制图片、放松金标禁入区或把未知当负例凑齐。
- **牵连**：`yoyo/datasets/ma_profit_negative_redo.py`、`scripts/windows/train_ma_profit_negative_redo.py`、`experiments/active/exp-ma-profit3r-negatives-20260922-v2/plan.json`。这是训练总体修复，并非盈利能力已经得到证明；旧失败结果保留。
