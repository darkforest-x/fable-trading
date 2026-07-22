# AGENTS.md — fable-trading 工作规范

一句话：两层架构验证"双均线密集启动"信号——YOLO 检测层（2a）+ LightGBM 判断层（2b），
2026-07 起进入 **VPS 实盘阶段**（执行层 + 前向 100 笔新鲜裁决）。
当前进度与下一步看 `HANDOFF.md` 顶部"当前真相"；各阶段结论看 `analysis/p*_report.md`；
本周执行计划看 `analysis/week_plan_20260720.md`；路线图（历史）看 `PROJECT_PLAN.md`。

## 铁律（违反 = 返工，没有例外）

1. **holdout 纪律**：holdout（≥2026-05-04）只在最终验收时评估，每次动用必须先获得项目
   所有者在对话中的明确批准，并在报告里记录"这是该配置第 N 次消耗 holdout"。
   训练/调参/特征选择的任何环节不得读取 holdout；`train.py` 不加 `--eval-holdout` 即安全。
2. **时间切分**：所有评估按时间切分，禁止随机切分，禁止跨切点的样本进入训练。
3. **无前视**：特征只能使用信号 bar 及之前的数据；只有标签允许看未来。
   新增特征必须在 docstring 写明用到的列与窗口。
4. **单变量纪律**：一次实验只改一个变量；结果无论成败都写入报告。
   多变量打包改动需项目所有者批准并在 PROJECT_PLAN 记录（先例：2b-v2 三项打包，2026-07-07）。
5. **YOLO 增强禁用**：fliplr/flipud/mosaic/mixup/hsv 全关——它们破坏时间方向和红绿 K 线语义
   （旧项目 180 版失败的病因之一，见 README）。
6. **数据**：`data/` 不入 git；`data/kline_cache` 是旧项目缓存的只读软链接；
   新数据用 `python3 -m src.data.fetch_okx`（可断点续传，需本机网络）。

## 实盘纪律（2026-07-20 起，与铁律同级）

7. **新鲜度三门同值**：执行器 max_signal_age_min / TG 过滤 / 看板 FRESH_DETECT_MIN
   当前 30min，由管道时序推导（15 bar + 7 脉冲/扫描 + 余量）；改动必须附延迟预算表
   且三处同改（见 `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`）。
8. **脉冲预算 <15min**：禁止往 forward 脉冲加扫描窗或新任务；阶段耗时看
   discover_wall / phase2_wall 日志，>600s 要查因。
9. **VPS 是唯一写者**：K 线与 forward_log.csv 只在 VPS 写；deploy 不推 data/kline_fetched。
10. **不自动 promote**：models/ACTIVE 与 frozen 默认配置的切换需 owner 点头；
    forward_log 不清空（清账 = owner 决策）。
11. **真金操作**（下单/撤单/kill 开关/改仓位/改 API key）只有 owner 亲手做或明确逐次授权。
12. **检测只认盘口**（owner 2026-07-23）：live 扫描只扫 tip/tip-1/tip-2 窗；凡"只能产出
    事后信号"的路径（回看窗、事后模型、非盘口分布数据集）一律不得存在。pre-v16 检测器
    权重已三机清除（仅存 COCO yolo11 底座）；检测器晋升唯一门 = 真 tip 金标 + tip-smoke，
    自家 val/mAP/旧 frozen-F1 永不作裁决。无验证过的检测器时管道诚实空转（detector=none）。

## 弱模型在本仓库最容易犯的错（每条都真实发生过或差点发生）

- **把 AUC 当成功标准** → 本项目成功标准是 top-decile 扣 0.2% 往返成本后的净收益为正
  且置换检验 p<0.01；v1 的教训就是 AUC 0.59 照样亏钱。AUC 只是参考量。
- **在 holdout 上"看一眼"** → 看一眼就是消耗一次，见铁律 1。
- **重跑 build_dataset 覆盖别的池的数据集** → 输出文件名必须带池名
  （`judgment_dataset_v2_strict.csv` / `..._expanded.csv`），tag 必须带池名。
- **顺手调 strict/expanded 阈值预设** → 阈值是项目所有者决策，改动需批准。
- **只汇报好消息** → 报告必须含"风险与诚实声明"节；隐瞒失败的实验记录等于污染实验日志。
- **默认拉全部币种重新 fetch** → 先检查 `data/kline_fetched/` 已有 `okx_*_15m_*.csv`，
  fetcher 会自动跳过已完成币种。
- **把 val/accept PF 当实盘** → 确认级只有前向新鲜 100 笔；v11 accept PF 高仍要前向终审。
- **改一道新鲜度门忘了另两道** → 三门必须同值，见实盘纪律 7。
- **往脉冲里塞实验扫描** → 超 15min 节拍 = 结构性挡 tip；见实盘纪律 8。
- **自动 promote / 清 forward_log** → 禁止；owner 点头。

## 质量标准（可检查，不是形容词）

每轮实验的交付物是 `analysis/pXX_report.md`，必须包含：

- [ ] 复现命令（从零跑通的完整命令序列）
- [ ] 数据统计（候选数 / 正类率 / 时间范围 / val 样本数）
- [ ] 结果表，且与上一版本同表对照
- [ ] 必报指标：val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征基线对照
- [ ] 解读（每个数字变化的归因）
- [ ] 风险与诚实声明
- [ ] 下一步选项（标注哪些需要项目所有者决策）

代码标准：python3 + pandas/lightgbm/ultralytics，无新增重型依赖；模块级 docstring
说明来源与决策依据（现有代码都是这个风格，照着写）。

## 不确定时的升级规则

- 涉及 **holdout、阈值预设、障碍参数（TP/SL 倍数、atr 下限）、成本假设（0.2%）** 的任何
  改动 → 停下来问项目所有者，不要"先试试"。
- 涉及 **新鲜度门、脉冲预算、ACTIVE/frozen 切换、清空 forward_log、promote owner_best、
  真下单/改仓** → 同上，见实盘纪律 7–11。
- 数据源不可用或返回结构变化 → 如实报告现象，不要静默换数据源或造数据。
- 结果好得反常（AUC 突然 >0.7、净收益突然翻倍、accept PF 夸张）→ 第一假设是泄漏或 bug，
  写最小复现验证后再汇报；确认级只认前向新鲜样本。
- 项目所有者用中文交流，汇报用中文；代码与注释用英文。

## learning law

每解决一个非平凡问题（修 bug、架构决策、反直觉结论），先运行 extract-approach skill
在 `docs/learnings/` 留下笔记再继续。没有 learnings 笔记的解决方案视为未完成的工作。
