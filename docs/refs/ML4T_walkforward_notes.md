# ML4T 只读对照 — 时间切分 / 无前视检查清单

**日期**：2026-07-22  
**upstream**：[stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading)  
**本仓用法**：只读书/ notebook 思路，**不** submodule、**不** pip 进训练 `.venv`。  
**对照铁律**：`CLAUDE.md` / `AGENTS.md`（时间切分、无前视、holdout、单变量）。

---

## 为何只读

ML4T 讲的是通用「特征 → 模型 → 回测」卫生，不是本仓 YOLO tip 几何。对本仓价值在于
**交叉检查 2b 建库/训练有没有偷看未来**，不是换训练栈。

---

## 对照清单（5–10 条）

| # | ML4T 常见提醒 | 本仓应有的对应 | 自检命令/位置 |
|---|---------------|----------------|---------------|
| 1 | **按时间切分**，禁止随机 shuffle 再切 train/val | `train.py` 时间切点；禁止随机切分 | 训练日志切点；铁律 2 |
| 2 | 验证集必须在训练集**之后** | val 窗口晚于 train | 切点表 / report |
| 3 | 标签可用未来，特征**不可** | 特征只用信号 bar 及之前；只有 label 看未来 | 特征 docstring；铁律 3 |
| 4 | 滚动/walk-forward 时，每折重训不得用折后数据 | 本仓主路径固定时间切；滚动实验须单独立项 | 新实验需 owner |
| 5 | holdout / test 只碰一次验收 | holdout ≥2026-05-04；每次消耗记报告 | 铁律 1；无 `--eval-holdout` 默认安全 |
| 6 | 标准化/分位数阈值只在 train 拟合 | 判断层 sidecar 阈值来自 train/val 约定路径，勿用 holdout 重拟合 | ACTIVE sidecar |
| 7 | 成本/滑点进评估，别只报毛收益 | 成功标准含 0.2% 往返后净收益 + 置换 p | CLAUDE「弱模型错」 |
| 8 | 交叉验证泄漏（同一实体跨折） | 同币同窗勿跨切点泄漏；YOLO 图与 2b 行按时间对齐 | build_dataset / tip 池 |
| 9 | 特征工程用未来 rolling 终点 | 任何新特征 docstring 写清列与窗口 | 铁律 3 |
| 10 | 「好看 AUC」≠ 可交易 | 本仓不以 AUC 为成功标准（v1 教训） | top-decile 净收益 |

---

## 刻意不做什么

- 不 clone 全书 notebook 进仓（体积大、与 tip 无关）。
- 不把 ML4T 的回测引擎接进 `executor` / `forward`。
- 不在本机训 YOLO 时跑 ML4T GPU 示例。

需要深读时：浏览器打开 upstream README → 搜 *walk-forward* / *purged* / *embargo* 章节，对照上表打勾即可。
