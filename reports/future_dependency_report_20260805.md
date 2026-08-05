# 检测器未来依赖验证实验 — owner_v10_chain / owner_v12_htip

**日期**：2026-08-05 · **性质**：只读验证，未训练、未改权重/标签/数据集/阈值、未做收益回测
**对应**：YOLO-XX V3 路线图 Phase 1（「owner_v10_chain 到底是 A. 真识别形态，还是 B. 识别后续启动」）

---

## 0. 标的更正（必读）

任务书指定验证 **v11**。**v11 的权重不存在，本实验无法以 v11 为标的。**

逐机核查结果：

| 位置 | v11 |
|---|---|
| `fable-trading/models/`、`runs/` | 无 |
| git | 无（`.gitignore:7` = `*.pt`，从未入库） |
| Time Machine / APFS 快照 / 废纸篓 | 无（TM 从未配置） |
| Windows 3060 `C:\fable` 全盘 | 无 |

原因：v11 是 2026-07-18 在 Mac 上用 MPS 训的（训练脚本注释：3060 unreachable while
the owner travels），**从未上过 3060**，而 Mac 那份在 07-23 被清除。它是目前唯一
不可恢复的模型。详见 `docs/learnings/weights-live-only-where-they-were-trained.md`。

**替代标的**：

- **`owner_v10_chain`** — v11 的直接前身，今天从 3060 取回（18.3MB，2026-07-17 02:30）。
  训练配置 `model: base_v9.pt` / `data: dense_owner_v9` / imgsz 960 / lr0 1e-4。
  v11 就是它续训 + round9 的 2566 张标注（`dense_owner_v11` = v9 池 + round9）。
  **同一套渲染、同一套标注规范、同一种「框在图中间」的历史窗分布**，是 v11 现存最好的代理。
- **`owner_v12_htip`** — v11 的直接后继（07-20 从 `owner_best.pt`=v11 续训），
  但换成 H-TIP 盘口分布数据集重训，专为修「tip 检不出」。作对照组。

v11 的真实数值无法测得。以下结论对 v10/v12 成立，对 v11 只能按血统推断。

---

## 1. 实验设计

单变量：**只改信号右侧的未来 K 线数量**，其余全部冻结。

```
future=0    窗口 = [sig-199 .. sig  ]   实盘 tip，signal 就是右边界
future=k    窗口 = [sig-199+k .. sig+k]
future=99   窗口 = [sig-100 .. sig+99]  signal 居中，即训练视角
```

冻结项：

| 项 | 值 |
|---|---|
| 窗口长度 | 200 bar（**所有 arm 一致**，只是 signal 在图中的位置不同） |
| 渲染 | `src/detection/render.py::render_chart`，与训练同一函数 |
| 均线 | `add_mas` 在**完整序列上算一次再切片** → 信号 bar 处的 MA 值在所有 arm 中完全相同 |
| conf / iou | 0.30 / 0.70，项目冻结值，全程未调 |
| bar | 15m |
| 匹配容差 | 框右边缘映射回 signal_i ± 2 bar（与实盘 tip-edge 门同值） |

**样本选择（关键）**：样本取自 **FULL（居中）视角下模型自己开火的点**，100 个，
覆盖 34 个币种，同币最多 3 个且间隔 ≥18 bar。

之所以不从 tip 视角取样：那样 future=0 的检出率必然接近 100%，实验自证。
从最容易检出的 arm 取样，再问「未来信息减少时它还认不认得」，才是公平提问。

---

## 2. 结果

### 2.1 主表

| future bars | 实际时长 | v10_chain 复现率 | v12_htip 复现率 | v10 conf | v12 conf | v10 框/图 | v12 框/图 |
|---|---|---|---|---|---|---|---|
| 0 | 0 | **10%** | **9%** | 0.449 | 0.490 | 0.22 | 0.29 |
| 5 | 1.25h | 21% | 19% | 0.472 | 0.457 | 0.35 | 0.41 |
| 10 | 2.5h | 20% | 23% | 0.501 | 0.540 | 0.39 | 0.46 |
| 20 | 5h | 39% | 41% | 0.511 | 0.546 | 0.55 | 0.60 |
| 40 | 10h | 48% | 51% | 0.521 | 0.554 | 0.59 | 0.73 |
| 99 | 24.75h | **62%** | **72%** | 0.497 | 0.536 | 0.73 | 0.88 |

n=100 样本 / 34 币种 / 每模型 600 次推理。

### 2.2 相对各自 FULL 归一化

| future | v10_chain | v12_htip |
|---|---|---|
| 0 | 16.1% | **12.5%** |
| 20 | 62.9% | 56.9% |
| 40 | 77.4% | 70.8% |
| 99 | 100% | 100% |

---

## 3. 任务书四问

**问题 1：FULL 和 TIP 检测数量差多少？**

v10_chain **62 → 10**（少 84%，6.2 倍）；v12_htip **72 → 9**（少 88%，8.0 倍）。
每张图的框数同步塌陷（v10 0.73→0.22，v12 0.88→0.29），说明不是「框错了位置」，
而是整张图基本不开火。

**问题 2：confidence 下降多少？**

**几乎不下降。** v10 0.497→0.449（−9.7%），v12 0.536→0.490（−8.6%）。
这是本实验信息量最大的一个数字：失效模式不是「越接近实时越犹豫」，而是
**要么认出、要么完全看不见**。缺的不是信心，是判据——右侧那段启动本身就是判据的一部分。

**问题 3：需要多少未来 bars 才能恢复检测？**

恢复到各自 FULL 的一半，两个模型都需要 **约 16–20 根 bar（4–5 小时）**；
恢复到 ~75% 需要 **40 根（10 小时）**；即使给满 99 根也只有 62%/72%。
这个量级与 2026-07-19 forward_log 实测的检出延迟（78–768 分钟，中位数约 8 小时）同阶。

**问题 4：属于 A 实时形态检测器 / B 完整形态识别器 / C 后续启动确认器 / D 其他？**

**两个模型都强烈偏 B/C，且 B 与 C 在本实验中不可分离。**

- 排除 A：实时 tip 只剩 9–10%，不能称为实时形态检测器。
- B 与 C 的区分需要「形态已完成但尚未启动」的样本作对照，本实验的样本集不含该分层，
  所以只能说「依赖信号右侧的信息」，**不能断定它依赖的一定是"启动"而非"形态的后半段"**。
  要分离，需要按信号后走势方向/幅度分层重跑（见第 6 节）。

对 V3 路线图 Phase 1 的回答：**假设成立方向的证据充分，但 A/B 二选一的精确归类尚缺一个对照。**

---

## 4. 最重要的发现（超出任务书范围）

**H-TIP 重训没有减轻未来依赖。**

v12_htip 是 07-20 专门为修「v11 在 tip 检不出」而用盘口分布数据集重训的模型。
它在 future=0 的绝对复现率 9%，与未经修复的 v10_chain 的 10% **无差别**；
按各自 FULL 归一化后 **12.5% vs 16.1%，反而更低**。

两条曲线在全部 6 个 arm 上形状一致（差值 −3pp ~ +2pp，除 full arm 的 10pp）。

这意味着 07-20 的修复动作**改变了训练图的构成，但没有改变模型对未来信息的依赖**。
V3 文档「tip 裁剪会破坏任务」这一判断，本实验予以**证实**：
把图裁到最右不是一个可以事后施加的变换，模型的判据本身就长在被裁掉的那部分上。

---

## 5. 风险与诚实声明

1. **标的不是 v11。** v11 无法测量，上述数字对 v10/v12 成立，对 v11 只是血统推断。
2. **本实验测的是自洽性，不是准确率。** 样本是模型自己在 FULL 视角下开火的点，
   不是人工金标。「复现率」= 同一个点换个窗口位置还认不认得，**不等于**检测正确率。
   因无 gold label，未计算 precision / recall / F1（任务书列为「如果存在」）。
3. **FULL arm 不是 100%（62%/72%）。** 样本发现于 stage A 的任意窗口位置，
   stage B 的 full arm 是固定的 sig+99 窗口，两者边界不同，故有 28–38% 未复现。
   这也说明**该模型对窗口位置本身就不稳定**——这是一个独立于未来依赖的问题，本实验未展开。
4. **未做收益验证。** 全程无 L2、无 TP/SL、无成本、无 PF。检出与否 ≠ 赚钱与否。
5. **样本偏差**：34 个币种按字母序取前若干个（0G/1INCH/2Z/A*），非随机抽样，
   未做币种分层。100 个样本对 6 个 arm 的估计，单点标准误约 ±5pp。
6. **未消耗 holdout。** 信号搜索窗为最近 30 天（2026-07 至 08-05），全部在 holdout 之后
   的**实盘时段**，不属于训练/验证集，也未读取任何 holdout 评估路径。

---

## 6. 下一步选项（标注需 owner 决策的项）

1. **分离 B 与 C**（低成本，建议优先）：把 100 个样本按信号后 20 bar 的走势分层
   （已启动 / 横盘 / 反向），看未启动样本的 FULL 复现率是否同样高。
   若「形态完整但未启动」也能被检出 → B；只有已启动的能被检出 → C。
2. **窗口位置稳定性**（低成本）：固定 future，只平移窗口起点，量化第 5.3 条发现的抖动。
3. **把 v8/v9 也纳入曲线**（中成本）：3060 上 v7–v16 全系已取回本地
   `models/archive_3060/`，可画出「未来依赖 vs 版本演进」的完整轨迹，
   判断这个缺陷是从哪一版引入或是否一直存在。
4. **Formation Model 方向验证**（需 owner 决策）：V3 文档第三层提出用前置窗口
   （T0-T30 / T0-T50 / T0-T70）训练「当前是否将形成经典形态」。
   本实验的 ablation 曲线可直接作为该方向的基线——目前 future=0 的 9–10%
   就是「不做任何改造直接上实盘」的天花板。

---

## 7. 复现命令

```bash
cd /Users/zhangzc/fable-trading

# v12_htip
PYTHONPATH=/Users/zhangzc/yoyo-trading:. .venv/bin/python scripts/exp_future_dependency.py \
  --samples 100 --gallery-cases 12 \
  --weights runs/detect/runs/detect/owner_v12_htip/weights/best.pt --tag v12_htip

# v10_chain（权重取自 3060: C:/fable/runs/detect/runs/detect/owner_v10_chain/weights/best.pt）
PYTHONPATH=/Users/zhangzc/yoyo-trading:. .venv/bin/python scripts/exp_future_dependency.py \
  --samples 100 --gallery-cases 12 \
  --weights models/owner_v10_chain.pt --tag v10_chain
```

## 8. 产物

| 文件 | 内容 |
|---|---|
| `reports/v10_chain_future_dependency_full.json` | 实验1 FULL：样本集与发现 conf |
| `reports/v10_chain_future_dependency_tip.json` | 实验2 TIP：future=0 逐样本结果 |
| `reports/v10_chain_future_dependency_curve.json` | 实验3 ablation 曲线 + 逐样本 6 arm 明细 |
| `reports/v12_htip_future_dependency_{full,tip,curve}.json` | 同上，v12_htip |
| `reports/v10_chain_future_dependency_gallery/` | 12 案例 × 6 arm = 72 张人工检查图 |
| `reports/v12_htip_future_dependency_gallery/` | 同上，72 张 |
| `scripts/exp_future_dependency.py` | 实验脚本 |

画廊命名：`sample_XXX_{tip,future5,future10,future20,future40,full}.png`；
橙框 = 匹配到该信号的框，灰框 = 图中其他位置的框。
