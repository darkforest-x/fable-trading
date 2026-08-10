# Local Signal V2 位置 shortcut 纠错（2026-08-11）

## 直接结论

Owner 观察正确：三张大图中的信号框全靠右不是拼图显示问题，而是 B2/P2 数据几何缺陷。
固定 30 根可见 K、窗口末端等于 decision、`confirm_delay ∈ {1,2}` 时，正框中心只能落在
`0.931034` 或 `0.948276` 两个横坐标。该数据未满足交接规范 Stage B“主要覆盖 65%–95%，
但不得固定为 95%/最后位置”的要求，模型存在明显位置 shortcut 风险。

刚启动的 3060 P2 训练已停止，且远端不存在 `best.pt`、`last.pt`、`results.csv`。P2 没有
形成有效实验结果或可用权重；旧 B2/P2 产物保留为失败证据，不覆盖、不 promote。

修复 builder 入库后，独立数据集 `local_signal_v2_p1_causal_blank_w30_v3` 已全量重建并通过
九道 P0 硬门；当前停止在“可训练、未训练”，没有新的模型结论。

## 关于“600 张人工审计”的口径纠正

此前“600 张可视化已人工审计”的说法不准确。实际产物是 **3 张 montage 大图**，每张嵌入
200 个 tile，共展示 600 个样本；不是 600 个独立图片文件，也没有逐 tile 完成人工签字审计。
Owner 对 montage 的目视复核反而发现了最关键的位置缺陷。因此正确口径是“600 个 tile 已渲染
进 3 张审计拼图，逐样本人工审计未完成”。

## 证据

| 项目 | B2/P2 当前值 | 交接规范要求 | 裁决 |
|---|---:|---:|---|
| 可见 K 线 | 30 | 20–30 | 符合 |
| `visible_end == decision` | 100% | 100% | 符合 |
| `future_bars` | 0 | 0 | 符合 |
| 正框中心 X 唯一值 | 2 个：0.931034 / 0.948276 | 65%–95% 范围内分散 | **失败** |
| 最右位置带占比 | 100% | 不得固定 95%/最后位置 | **失败** |
| P2 远端 checkpoint | 0 | 不适用 | 无污染 |
| holdout 读取 | 0 | 0 | 符合 |

位置来自确定的构造算术，而不是图片识别误差：

```text
decision_local = 29
confirm_delay = 1 → box center = 27.5 / 29 = 0.948276
confirm_delay = 2 → box center = 27.0 / 29 = 0.931034
```

随机 `win_len=20..30` 也只能在最右侧窄带移动，无法自然扩展至 65%。把窗口右端推到
decision 之后虽然能挪框，但会画入未来真实 K，违反无前视。

## 修复设计：因果数据不动，只增加空白画布槽位

新臂固定 B2 的 30 根真实可见 K，并在 decision 右侧按冻结 seed 均匀采样 0–12 个纯空白
槽位。空白槽位不是行情 bar，不改变 `visible_end`，也不填入合成或未来 K。正例、easy
negative 共享同一空白范围；后续 hard-negative 候选与推理也必须使用同一布局合同。

此时两种 confirm delay 的框中心覆盖约 65.85%–94.83%，落在规范 65%–95% 内。旧
`render.py` 不改；新增 opt-in V2 renderer，`right_blank_slots=0` 已通过逐像素等同旧 renderer
的单元测试。

## 单变量纪律与路线变化

冻结 P2 的唯一变量原本是 hard negatives。若直接修改 P2 图片布局，就会同时改变位置几何和
负例，无法归因。因此 P2 状态改为 `aborted_before_epoch_1_due_position_shortcut`，先新增
独立 P1 位置臂：

- 冻结：source events、W=30、Mode C、confirm delay、标签宽度、time split、150-bar purge、
  dataset seed、1:1 easy negatives、训练配方；
- 唯一变量：`right_blank_slots: 0 → seeded UniformInt[0,12]`；
- 数据门：框中心 65%–95%、至少 4 个位置桶、正负空白支持集一致、0 future、0 holdout、
  0 跨 split、manifest 守恒；
- 数据门通过后才训练；训练结果通过冻结事件尺后，才从该权重重新开始 P2 hard-negative mining。

## 当前数据统计与模型指标

本报告是训练前的数据合同纠错，不产生新的模型结果，因此 val AUC、置换检验、top-decile
收益、胜率、随机对照和 mAP 均为 **不适用**。不得拿旧 B2 指标冒充修复后结果。现有 P2
数据统计仍为 train 2,030 正例、2,030 easy negatives、2,263 hard negatives；val 358 正例、
358 easy negatives，另有 419 个 evaluation-only hard negatives。但由于位置门失败，这套
P2 数据不得继续训练。

新位置臂共 4,776 样本：train 2,030 正 + 2,030 负，val 358 正 + 358 负。框中心范围
0.658537–0.948276，四个等宽位置桶分别 732 / 625 / 572 / 459；正负样本均覆盖 0–12
全部空白槽。九道 P0 门全部通过，且同 seed 第二次重建后正例 manifest SHA 保持
`f82a49100949b7b10425cd6c822830083fc758f77a94db275cfd2213d6fb43a1`，负例 manifest SHA
保持 `8357528442c9e0c8e1d63a2fb2b4497f1cb96d51462bea7b4c5a0af008a594c8`。

## 复现命令

```bash
# 代码检查（不读取 holdout）
PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_local_signal_v2_stageb.py \
  ../yoyo-trading/tests/test_local_v2_render.py

# builder 提交后才可运行的数据预览
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_causal_blank_v3.py --preview 24

# 数据门通过前禁止训练；全量构建也必须使用新目录，不覆盖 B2/P2
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_causal_blank_v3.py \
  --out datasets/local_signal_v2_p1_causal_blank_w30_v3
```

## 风险与诚实声明

- 空白槽位能消除“只在最右边”的单点 shortcut，但不保证检测器学到正确结构；需要位置-only
  baseline、内容遮挡诊断和连续 causal-tip 密度回放。
- 右侧空白本身也可能成为新 shortcut，因此正负样本必须共享支持集，并按位置 bucket 报告精度。
- 本轮未读取 holdout，未改阈值、障碍、成本、新鲜度、ACTIVE，未 promote、未部署、未下单。
- 旧 B2 的历史指标仍是真实历史结果，但只适用于错误的固定最右几何，不再是后续 P2 基线。

## 下一步

1. V2 renderer、builder、测试和预注册：已完成；
2. 独立位置臂全量重建与 P0：已完成，九门全绿；
3. 下一步仅启动 3060 位置单变量训练，不加入 hard negatives、不改阈值；
4. 通过冻结事件尺与连续密度回放后，重做 P2 hard-negative mining。
