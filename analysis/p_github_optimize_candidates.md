# GitHub 开源候选 — 对本仓真实痛点的第二轮筛选

**日期**：2026-07-21  
**范围**：能优化 fable-trading（YOLO 2a + LGBM 2b + VPS 实盘）的开源项目  
**约束**：不改生产、不打断 v13 训练；ChartScanAI 类已评过（见 `p_chartscanai_review.md` / `p_realtime_yolo_within_bar.md`），本轮少重复。

---

## 结论先行

| 何时 | 值得做什么 | 不值得做什么 |
|------|------------|--------------|
| **现在**（纯工程 / 不碰训练主线） | ① 用 **FiftyOne** 对已有 tip 漏检/误检图做 hardness / FP 队列；② 确认 **Label Studio Community = Apache-2.0**（非 AGPL），继续现有打标入口；若 LS UX 卡，再试 **CVAT**（MIT + Ultralytics YOLO 导出）；③ **BTC dominance** 用 `pycoingecko` 做离线特征草稿（**不进 ACTIVE**，单变量实验需 owner 立项） | 换检测主线、接 ChartScanAI 权重、往脉冲塞 AL/HP 扫描 |
| **等 tip 起来后** | **ONNX Runtime**（跨平台）→ VPS 若是 Intel CPU 再试 **OpenVINO**；有 NVIDIA 再谈 TensorRT（已评） | tip_fire≈0 时赌推理加速抬出生率 |
| **等 v13 / 盘口打标包** | **FDAL / PPAL** 的主动学习**策略思路**（难例采样），挂在本仓 `train.py` 外，不引整栈 | AGPL 自动标平台（如 VLM-AutoYOLO）进主依赖 |
| **前向 100 笔后再立项** | **Basana** 的事件驱动/回测↔实盘同 API **思路**；组合熔断清单（Freqtrade Protections + CryptoGuardian showcase 模式） | 用 Jesse/Basana/Freqtrade **替换**本仓执行器 |

**一句话**：GitHub 上几乎没有「盘口 tip 几何」解药——那是本仓标签与渲染问题（A′ 已上线，v13 打标是正道）。本轮真正 ROI 在 **难例策展（FiftyOne/CVAT）**、**tip 起来后的 CPU 推理加速**、以及 **轻量 regime 特征 / 风控清单**。

---

## 痛点对照（筛选用）

1. K 线 YOLO：盘口 tip、无后文几何、贴边框  
2. 训练数据：难例挖掘、主动学习、标注（非 AGPL 重污染）  
3. 推理加速（仅 tip 起来后）：ONNX / TensorRT / OpenVINO → Linux VPS  
4. 回测 ↔ 实盘一致性、事件驱动执行  
5. 组合风控 / 熔断（Freqtrade Protections 已提过）  
6. 加密 regime：资金费率、dominance 等（轻量）

**本仓已知**：`src/data/fetch_funding.py` 已有 OKX 资金费率；打标入口已接 Label Studio；渲染是自研 cv2（`MARGIN=12`），不是 mplfinance。

---

## 入选 7 个（对口）

### 1. voxel51/fiftyone — 难例策展（痛点 2）

| 项 | 内容 |
|----|------|
| **一句话** | CV 数据集 App：可视化框、评估 YOLO、Brain `compute_hardness` 挖难例 / 近重复 |
| **许可证** | Apache-2.0 |
| **活跃度** | ~10.8k★；持续维护（docs + Ultralytics 集成） |
| **对口痛点** | 2（难例挖掘）；间接服务 1（把 tip 漏火/贴边 FP 排进人工队列） |
| **借鉴方式** | **可引库**：本机 `pip install fiftyone`，对 `data/` 下 tip 失败 PNG + YOLO pred 建 Dataset；**不要**塞进 VPS 脉冲 |
| **不适配** | 不解 tip 出生率；不替代金标语义；App 偏重，只做离线策展 |
| **对本仓 ROI** | **高（现在）**：v12/v13 漏检可视化比再搜一个「chart YOLO demo」有用 |

### 2. cvat-ai/cvat — 标注 + Ultralytics 导出（痛点 2）

| 项 | 内容 |
|----|------|
| **一句话** | 成熟图像标注平台；原生 YOLO / Ultralytics YOLO Detection 导入导出 |
| **许可证** | MIT（serverless 资产需自查第三方许可） |
| **活跃度** | ~16.2k★；2026-06 仍有 release（v2.69） |
| **对口痛点** | 2；周计划「盘口视角打标 300 张」的备选工具 |
| **借鉴方式** | **接口/流程**：Docker 起 CVAT → 导出 Ultralytics ZIP → 喂本仓 detection 训练；与现有 Label Studio **二选一或并存**，勿双写污染 |
| **不适配** | 不解决 tip 几何；运维比「本机 LS」重；Enterprise 功能与 Community 边界要分清 |
| **对本仓 ROI** | **中高**：LS Community 已是 **Apache-2.0**（非 AGPL）——**不必为许可证恐慌迁移**；仅当 LS 框编辑/批量 UX 卡住时换 CVAT |

### 3. TaiDuc1001/FDAL（+ 参考 PPAL）— 目标检测主动学习（痛点 2）

| 项 | 内容 |
|----|------|
| **一句话** | Feature Difficulty Active Learning；基于 Ultralytics YOLO 的难例/不确定采样实现 |
| **许可证** | Apache-2.0（FDAL）；PPAL（ChenhongyiYang/PPAL）亦 Apache-2.0，~104★，偏 MMDet |
| **活跃度** | FDAL ★极少（研究复现仓）；PPAL 2024 论文仓，更新已停 |
| **对口痛点** | 2（主动学习 / 难例优先级） |
| **借鉴方式** | **思路**：uncertainty / feature-difficulty 排序 unlabeled 池 → 只标 top-K；脚本挂在训练机，**单变量**扩标；**不** `pip install` 整仓进生产 |
| **不适配** | 星少、实验栈重；PPAL 绑 MMDetection 与本仓 Ultralytics 路径摩擦大 |
| **对本仓 ROI** | **中（等 v13 / 有 unlabeled tip 池后）**：有 300+ 盘口图预算时再立项；现在只读论文+策略清单即可 |

### 4. microsoft/onnxruntime — 跨平台推理加速（痛点 3）

| 项 | 内容 |
|----|------|
| **一句话** | ONNX 模型跨平台 Runtime；Ultralytics `model.export(format="onnx")` 一等公民 |
| **许可证** | MIT |
| **活跃度** | ~21k★；2026-07 仍高频推送 |
| **对口痛点** | 3（tip 起来后压 discover_wall） |
| **借鉴方式** | **可引库**：训练机 export → VPS 侧 `YOLO("best.onnx")` 或 onnxruntime 会话；与现有 predict 路径做 **A/B 延迟表**（同图同框） |
| **不适配** | **不抬 tip_fire**；脉冲瓶颈已证实是 YOLO 前向本体——无 tip 时加速 = 省空转 |
| **对本仓 ROI** | **中（等 tip）**：HANDOFF 记 discover ~500s；tip 后若仍 >600s 再做 |

### 5. openvinotoolkit/openvino — Linux CPU VPS 加速（痛点 3）

| 项 | 内容 |
|----|------|
| **一句话** | Intel 系 CPU/iGPU 推理优化；Ultralytics `export(format="openvino")` |
| **许可证** | Apache-2.0 |
| **活跃度** | ~10.5k★；持续维护 |
| **对口痛点** | 3（本仓 VPS ~2 核、未必有 CUDA） |
| **借鉴方式** | **可引库**：先确认 VPS CPU 厂商；Intel → OpenVINO；否则停在 ONNX Runtime |
| **不适配** | 非 Intel 收益不确定；TensorRT 路径见已评 `YOLOv8-TensorRT`（要 NVIDIA） |
| **对本仓 ROI** | **中（等 tip + 知硬件）**：比 DeepStream/Savant 更对口 |

### 6. gbeced/basana — 事件驱动加密框架（痛点 4）

| 项 | 内容 |
|----|------|
| **一句话** | Python async 事件驱动；回测 exchange + 实盘（Binance/Bitstamp/CCXT）同构 |
| **许可证** | Apache-2.0（LICENSE 明文；GitHub SPDX 显示 Other） |
| **活跃度** | ~853★；2026-07-20 仍有 push |
| **对口痛点** | 4（回测↔实盘一致性、事件驱动） |
| **借鉴方式** | **思路**：dispatcher / 事件边界 /「策略代码路径在回测与 live 尽量同一」；对照本仓 forward 标签 vs executor 入场时序 |
| **不适配** | **不可**整框替换 executor（OKX、tip 实时入账、三门、tiered sizing 是本仓专有）；无 YOLO 层 |
| **对本仓 ROI** | **中低（前向 100 后再立项）**：一致性审计清单有价值，迁移成本高 |

### 7. man-c/pycoingecko — BTC dominance / 全局 regime（痛点 6）

| 项 | 内容 |
|----|------|
| **一句话** | CoinGecko API 轻量 Python 包装；`/global` 可取 BTC dominance |
| **许可证** | MIT |
| **活跃度** | ~1.1k★；维护偏慢但 API 稳定够用 |
| **对口痛点** | 6（dominance regime）；**资金费率本仓已有**，勿重复造轮 |
| **借鉴方式** | **可引库或薄封装**：日频/小时 dominance CSV → 判断层单特征实验；严格无前视、时间切分 |
| **不适配** | 免费档限流；不是交易执行库；dominance ≠ tip 检测 |
| **对本仓 ROI** | **中（需 owner 立项）**：轻量；必须单变量进 2b，且不碰 holdout |

---

## 补充：风控（痛点 5）— 不单独占「可引库」席位

| 来源 | 说明 |
|------|------|
| **freqtrade/freqtrade** Protections | 已提过：MaxDrawdown、StoplossGuard、CooldownLoss 等清单仍可当**规格对照**（GPL-3.0 → **只抄思路不引依赖**） |
| **Jotanune/CryptoGuardian** showcase | 日损 soft/hard、连亏熔断、portfolio heat、kill switch + TG；**无 SPDX 许可证、★≈1** → **只读 showcase 模式**，勿当依赖 |

本仓已有 `executor_KILL`、tiered sizing、max_concurrent——缺口更像「日损%/连亏 N 自动冷却」的**产品决策**，不是再找一个大框架。

---

## 明确不入选 / 少写

| 仓库 | 原因 |
|------|------|
| ChartScanAI 及同类 Buy/Sell chart YOLO | 已评：事后形态 ≠ 密集启动 tip；警示样本 |
| VLM-AutoYOLO | AGPL + 重依赖；污染发布面 |
| Savant / DeepStream-Yolo | 视频流 + NVIDIA 栈；错配 OKX CSV 脉冲 |
| Jesse / Nautilus / Wisp | 过大或语言栈不对；与两层 YOLO+LGBM 无关的「整框换血」 |
| 纯 RL 炒股 / 美股因子大而全 | 用户排除 |
| kairos（Vekkris76） | MIT 但 ★=0、极新；parity 思路可记一笔，不入主表 |

---

## 推荐采纳排序

| 序 | 候选 | 类型 | 何时 | Owner 决策？ |
|----|------|------|------|--------------|
| 1 | FiftyOne 难例队列 | **纯工程** | **现在**（本机，不碰 v13/VPS） | 否（只读策展） |
| 2 | 维持 LS；必要时 CVAT | **纯工程** | 盘口打标包启动时 | 换工具时点头即可 |
| 3 | pycoingecko → dominance 特征草稿 | **需立项** | v13 后 / 判断层空窗 | **是**（特征=单变量实验） |
| 4 | ONNX Runtime 延迟对照 | **纯工程** | tip 稳定出生后 | 否（先 shadow 测速） |
| 5 | OpenVINO（若 Intel VPS） | **纯工程** | 4 有收益后再做 | 否 |
| 6 | FDAL/PPAL 采样策略 | **需立项** | 有 unlabeled tip 池 + 标注预算 | **是** |
| 7 | Basana 一致性清单 / 熔断规格 | **需立项** | 前向新鲜 ≥50–100 | **是**（涉执行与真金纪律） |

---

## 与本仓时间线对齐

```
现在 ──v13 训练中──► tip 打标/验证 ──► tip 起来 ──► 前向 100
 │                    │                 │            │
 │ FiftyOne 策展      │ LS/CVAT 盘口标   │ ONNX/OV    │ Basana 思路
 │ dominance 草稿立项 │ FDAL 策略可选    │ 压 discover │ 熔断规格立项
 └─ 禁止：脉冲加任务、自动 promote、换 ChartScan 权重
```

---

## 风险与诚实声明

- 本轮检索**再次确认**：公开 GitHub **没有**针对「无后文盘口 tip」的现成检测器；把 ChartScanAI 当解药会污染金标。  
- FiftyOne / CVAT / ONNX **不会**提高 tip_fire；它们优化的是标注效率与延迟。  
- Label Studio Community 为 **Apache-2.0**——此前「AGPL 恐慌」若针对 Community 版，属误读；Enterprise / 部分 ML 后端需单独核对。  
- 任何判断层新特征（dominance）必须遵守铁律 3–4，且 **holdout 不动**。  
- 推理加速与熔断均可能触达实盘纪律 7–11：改脉冲预算、真下单逻辑须 owner 逐次批准。

---

## 复现 / 核查命令（只读）

```bash
# 许可证与活跃度抽查（本报告撰写时）
curl -sL https://api.github.com/repos/voxel51/fiftyone | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['stargazers_count'],d['license']['spdx_id'],d['pushed_at'])"
curl -sL https://api.github.com/repos/cvat-ai/cvat | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['stargazers_count'],d['license']['spdx_id'],d['pushed_at'])"
curl -sL https://api.github.com/repos/microsoft/onnxruntime | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['stargazers_count'],d['license']['spdx_id'],d['pushed_at'])"
curl -sL https://api.github.com/repos/gbeced/basana/contents/LICENSE?ref=develop  # Apache-2.0 正文
```

**相关文档**：`analysis/p_chartscanai_review.md`、`analysis/p_realtime_yolo_within_bar.md`、`analysis/week_plan_20260720.md`、`HANDOFF.md`（A′ / tip 实时路径）。

---

## 下一步选项（需 owner）

1. **批准现在**：本机装 FiftyOne，只对 tip 失败样本建只读 Dataset（不改训练）。  
2. **批准立项**：dominance 单特征进 2b 实验（文件名带池名、不碰 holdout）。  
3. **暂缓**：ONNX/OpenVINO、FDAL、Basana/熔断——写进 backlog，等 tip / 前向门槛。  
4. **否决**：任何 ChartScan 类权重试验、脉冲内 AL、AGPL 自动标进主仓。
