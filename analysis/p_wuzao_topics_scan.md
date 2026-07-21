# 无噪（wuzao）全站 topics 扫描 — 对本仓可迁移性

**日期**：2026-07-22  
**入口**：[https://www.wuzao.com/topics/](https://www.wuzao.com/topics/)  
**范围**：整站主题分类 → 相关度过滤 → 只深挖「高 + 中」  
**约束**：不耗 holdout、不 promote、不改 LIVE、不杀 v13、不抢 MPS；**不**建议大前端重构。  
**对照已读**：`HANDOFF.md` 顶部、`src/webapp/`（FastAPI + Lightweight Charts）、`analysis/p_github_optimize_candidates.md`、`analysis/p_yolo_external_sources.md`、`docs/RESEARCH_AGENDA_DETECT.md`、`src/webapp/static/DESIGN-REFERENCES.md`。

---

## 结论先行

| 结论 | 内容 |
|------|------|
| 全站噪音很大 | 后端框架大全 / 移动端 / 游戏 / 多数 LLM App / 炫技前端 ≈ **无关或暂缓** |
| 真正能碰的很少 | ≤8 个项目（见 §4）；多数「量化」「可视化」星标项目对本仓 tip 主线 **无解药** |
| 前端答案 | **能用**：加深现有 LWC（已在看板）+ 运维指标灯（Grafana/uptime **思路**）。**不能用**：另起 Streamlit/Superset/ECharts 交易终端、换 React/Next 全家桶 |
| 检测答案 | PyTorch 页上 **ultralytics / supervision / netron** 有用；Stable Diffusion / ComfyUI / vLLM = 噪音 |
| 主线 | 仍等 **v13 tip-smoke**；本报告假设均为旁路 H-FE / H-TOOL，**不抢** H-DET-1 |

**发现级已做**（无 GPU）：用 3 张 hardneg tip 预览窗，从 `okx_*_15m_*.csv` 重画 LWC 对照 →  
`analysis/output/wuzao_lwc_tip_compare/`（`compare.html` + `verdict.json`）。结论：CSV→LWC **零后端改动可交互调试**；**禁止**把 LWC 截图喂 YOLO。

---

## 1. 抓取说明

- topics 首页、quant、data-visualization、pytorch、grafana、prometheus、docker、pandas、command-line：**WebFetch 成功**。
- `/topics/cli/` 404；命令行正确 slug = `/topics/command-line/`。
- 条目简介以站点摘要 + 已知 GitHub 身份为准；未逐仓 clone。与既有 `p_github_optimize_candidates` 重复的（FiftyOne/ONNX/Freqtrade Protections）标「已评，不重复立项」。

---

## 2. 全部分类相关度一览

| 大类 | 主题 | 约项目数 | 相关度 | 一句 |
|------|------|----------|--------|------|
| 数据科学 | **量化交易** | 77 | **高** | 本仓语境核心；多数是回测/多智能体 → 深挖后大半暂缓 |
| 数据科学 | **数据可视化** | 145 | **高** | 只评「增强现有看板/调试」，不另起终端 |
| 人工智能 | **PyTorch** | 215 | **高** | 本仓 YOLO/Ultralytics 栈；挑 CV 工具，避开 SD/LLM |
| 运维 | **Grafana** | 19 | **中高** | 面板密度 / 状态灯 / 脉冲耗时思路；不替换 fable-dashboard |
| 运维 | **Prometheus** | 37 | **中** | discover_wall / 进程存活指标；VPS 小机要克制 |
| 运维 | **Docker** | 303 | **中** | 已用 systemd+rsync；uptime-kuma / netdata 可作运维旁路 |
| 数据科学 | **Pandas** | 60 | **中** | 已是底座；页内多为教程/表格 UI，少新积木 |
| 工具 | **命令行** | 248 | **中低** | ripgrep/tqdm/pyenv 已熟；`nvitop` 对本机训有用 |
| 运维 | **安全** | 332 | **中低** | Authelia/trivy 级加固可远期；与 tip 无关，暂缓 |
| 前端 | **React** | 675 | **低** | 可抄组件节奏（已有 shadcn 笔记）；禁止整站重写 |
| 前端 | Vue / Next / Nuxt / Angular / Bootstrap | — | **低** | FreqUI=Vue 仅布局参考（已在 DESIGN-REFERENCES） |
| 后端 | Flask | 24 | **低** | 本仓 FastAPI；Streamlit/Dash 在可视化页已评 |
| 后端 | Django / Express / Laravel / .NET / Rails / Spring | — | **无关** | |
| 移动端 | Flutter / React Native | — | **无关** | |
| 人工智能 | TensorFlow | 116 | **低** | 本仓不迁 TF |
| 人工智能 | **大语言模型** | 518 | **低** | AutoGPT/Dify/Ollama 与 tip 几何无关；打标 LLM 另议且不抢训 |
| 数据科学 | Kafka / Spark | — | **无关** | 体量错配 |
| 运维 | Kubernetes | 258 | **无关** | 单 VPS systemd 够用 |
| 游戏 | Unity | — | **无关** | |
| 工具 | Git / VS Code | — | **低** | 开发者卫生，无迁移假设 |
| 其他 | Awesome | 327 | **低** | 二次索引；`awesome-quant` 可当书签 |
| 其他 | 区块链 / 算法 | — | **无关/低** | 算法页非检测 CV；链上与 OKX 合约脉冲无关 |

---

## 3. 高+中深挖：项目表

图例：相关度 **高/中/低**；「可用前端？」= 能否增强本仓已有信号/前向/检测调试 UI 或静态报告（不是另起终端）。

### 3.1 量化交易（高主题 · 项目多数暂缓）

| 名称 | 链接 | 一句话 | 相关度 | 可用前端？ | 建议动作 |
|------|------|--------|--------|------------|----------|
| tradingview/**lightweight-charts** | https://github.com/tradingview/lightweight-charts | 本仓看板已用；K 线+MA+markers | **高** | **是（已用）** | 加深 tip/硬负调试叠框（H-FE-1）；勿换库 |
| freqtrade/**freqtrade** | https://github.com/freqtrade/freqtrade | 加密交易机器人；Protections 有规格价值 | 中 | WebUI 可参考布局，勿换栈 | **已评**：只抄 Protections 规格，禁 pip（GPL）；暂缓实现至前向≥50 |
| hummingbot/**hummingbot** (+ dashboard) | https://github.com/hummingbot/hummingbot | 做市/高频框架 | 低 | Streamlit 壳已在 DESIGN-REFERENCES | **暂缓**：不接策略引擎 |
| ccxt/**ccxt** | https://github.com/ccxt/ccxt | 统一交易所 API | 中低 | 否 | 本仓 OKX 自研 fetch；**暂缓**换栈 |
| microsoft/**qlib** / nautechsystems/**nautilus_trader** / backtrader / lean / zipline / vnpy / abu / finrl / qbot | 各 GitHub | 回测/投研/RL 大框架 | **低** | 否 | **暂缓**一句：与 tip/两层架构无关 |
| openbb-finance/**openbb** | https://github.com/OpenBB-finance/OpenBB | 金融数据工作台 | 低 | 只学 widget 分流（已记） | **暂缓** |
| tauricresearch/**tradingagents** 等 LLM 交易 | — | 多智能体研报/交易 | **无关** | 否 | **暂缓** |
| timescale/**timescaledb** / questdb / tdengine | — | 时序库 | 低 | 否 | CSV+VPS 体量不需要；**暂缓** |
| wilsonfreitas/**awesome-quant** | https://github.com/wilsonfreitas/awesome-quant | 量化资源列表 | 低 | 否 | 书签即可 |

### 3.2 数据可视化（高主题 · 严筛）

| 名称 | 链接 | 一句话 | 相关度 | 可用前端？ | 建议动作 |
|------|------|--------|--------|------------|----------|
| tradingview LWC（同上） | 见上 | — | 高 | 是 | 主路径 |
| apache/**echarts** | https://github.com/apache/echarts | 通用交互图 | 中低 | 可叠 PF/权益，非 K 线主图 | **暂缓**：LWC 已够；勿双图库 |
| streamlit/**streamlit** / plotly/**dash** / gradio-app/**gradio** | 各 GitHub | Python 快速 App | 中低 | 可做离线分析小页 | **暂缓**：与 FastAPI 看板双栈成本；Hummingbot 已否决整站换 |
| grafana/**grafana** | https://github.com/grafana/grafana | 可观测看板 | 中高 | **运维面板是**；交易图否 | H-TOOL-1 思路；不替换业务看板 |
| apache/**superset** / metabase / redash / dataease | — | BI 拖拽 | 低 | 否 | **暂缓** |
| d3/**d3** / airbnb/**visx** / pixijs | — | 底层/炫技 | 低 | 否 | **暂缓** |
| matplotlib/**matplotlib** | https://github.com/matplotlib/matplotlib | 已用于 TG 图/分析脚本 | 中 | 静态报告是 | 保持；**勿**替代 YOLO cv2 渲染 |
| netdata/**netdata** | https://github.com/netdata/netdata | 主机秒级监控 | 中 | 运维 | 与 Grafana 二选一旁路；**暂缓上线**至 tip 通 |

### 3.3 PyTorch（高主题）

| 名称 | 链接 | 一句话 | 相关度 | 可用前端？ | 建议动作 |
|------|------|--------|--------|------------|----------|
| ultralytics/**ultralytics** | https://github.com/ultralytics/ultralytics | 本仓检测底座 | **高** | 否（训练） | 已用；跟官方 export/ONNX 文档即可 |
| roboflow/**supervision** | https://github.com/roboflow/supervision | CV 标注可视化/数据集工具 | **中高** | 离线预览可 | H-TOOL-2：难例 PNG 批注叠框；**不进脉冲** |
| lutzroeder/**netron** | https://github.com/lutzroeder/netron | 模型结构可视化 | 中 | 是（调试页） | tip 起来后看 ONNX 图；低成本 |
| open-mmlab/**mmdetection** | https://github.com/open-mmlab/mmdetection | 检测工具箱 | 低 | 否 | **暂缓**换栈；与 Ultralytics 摩擦 |
| Stable Diffusion / ComfyUI / vLLM / DeepSpeed / Ray… | — | 生成式/LLM/分布式 | **无关** | 否 | **暂缓** |
| voxel51/fiftyone（不在本页顶栏但同生态） | 见 `p_github_optimize_candidates` | 难例策展 | 高 | App 离线 | **已立项思路**；不重复 |

### 3.4 Grafana / Prometheus / Docker / 命令行（中）

| 名称 | 链接 | 一句话 | 相关度 | 可用前端？ | 建议动作 |
|------|------|--------|--------|------------|----------|
| grafana/**grafana** | 同上 | 指标+告警 | 中高 | 运维 | H-TOOL-1 |
| prometheus/**node_exporter** + 自打点 | https://github.com/prometheus/node_exporter | 主机指标 | 中 | 否 | 可选；先看 journal 阶段耗时是否够 |
| xuehaipan/**nvitop** | https://github.com/XuehaiPan/nvitop | GPU 进程监视 | 中 | CLI | 本机 v13 训旁路；**不进 VPS 脉冲** |
| louislam/**uptime-kuma** | https://github.com/louislam/uptime-kuma | 自托管存活探针 | 中 | 状态页 | H-TOOL-3：dashboard/forward/executor HTTP 探活；owner 批后再装 |
| stefanprodan/**dockprom** | https://github.com/stefanprodan/dockprom | Docker+Prom+Grafana 全家桶 | 低 | — | VPS 过重；**暂缓** |
| ohmyzsh / thefuck / warp… | command-line 页 | 终端糖 | 低 | 否 | **无关** |

### 3.5 低/无关整类带过

后端全家桶、移动端、Unity、K8s、Kafka/Spark、LLM App 大全、区块链、算法题库、Git/VS Code 教程：**不深挖**。React/Vue 只保留「布局节奏」已有笔记，不新开假设。

---

## 4. 真正能拿来用的 ≤8（Owner 速览）

| # | 项目 | 用在哪一层 | 何时碰 |
|---|------|------------|--------|
| 1 | [lightweight-charts](https://github.com/tradingview/lightweight-charts) | 看板/检测调试前端 | **现在可加深**（发现级已证 CSV 可直接喂） |
| 2 | [ultralytics](https://github.com/ultralytics/ultralytics) | 检测 2a（已用） | 跟版本；不换 |
| 3 | [supervision](https://github.com/roboflow/supervision) | 离线难例/框可视化 | tip 漏检策展；不抢 GPU |
| 4 | [netron](https://github.com/lutzroeder/netron) | 模型调试 | ONNX 导出后 |
| 5 | [grafana](https://github.com/grafana/grafana)（或更轻 netdata） | VPS 运维可观测 | tip 通或脉冲经常 >600s 后再议 |
| 6 | [uptime-kuma](https://github.com/louislam/uptime-kuma) | 服务存活 | 可选旁路；owner 批 |
| 7 | [nvitop](https://github.com/XuehaiPan/nvitop) | 本机训 GPU | 现在可 `pip` 自用 |
| 8 | Freqtrade **Protections 规格**（不引依赖） | 执行/风控清单 | 前向样本够后再写门槛；GPL 不 copy |

**明确噪音**：TradingAgents/OpenBB/Qlib/Nautilus/Backtrader/FinRL、Superset/Metabase/ECharts 换主图、Streamlit 整站、ComfyUI/SD、K8s 全家桶、LLM Agent 交易。

---

## 5. 发现级验证（已做）

**假设**：LWC 能直接消费本仓 `okx_*_15m_*.csv` 时序，用时间带标 hardneg 中段框，提升相对静态 YOLO PNG 的调试可读性——且**不改** FastAPI 后端。

**做法**：取 `hardneg_mid_cluster` 预览 3 窗（TAO / LQTY / AVAX），切 200 根 → 静态 `charts.json` + `compare.html`（本机 vendor LWC）。

**结果**（`analysis/output/wuzao_lwc_tip_compare/verdict.json`）：

1. CSV→unix 秒 OHLC→LWC：**可行，零后端改动**。  
2. 交互缩放比纯 PNG 更易判断「中段簇 vs 右缘 tip」。  
3. LWC 的 MA20/60 **≠** YOLO 六均线 cv2 渲染 → **禁止**截图进检测训练。  
4. 精确 YOLO 框仍要 series-primitive 或保留 PNG；时间带够「发现级可读性」。

**未做 / 为何**：ECharts 重画、Streamlit 小 App、Grafana 装 VPS——依赖或运维面超出「便宜验证」，且与 tip 主线冲突风险更高。

---

## 6. 登记假设（旁路；全文见 RESEARCH_AGENDA）

| ID | 一句话 | 状态 |
|----|--------|------|
| **H-FE-1** | LWC 时间带/primitive 增强 tip·hardneg 调试可读性（不改训练渲染） | 🟡 发现级部分通过（CSV 通路）；primitive 精框未做 |
| **H-FE-2** | 前向/信号表「密集窗」用 LWC markers 统一语义（已有部分 markers） | ⚪ 等 tip_fire>0 再打磨 UX |
| **H-TOOL-1** | 脉冲 `discover_wall` 等阶段耗时进轻量指标（Grafana 思路，未必装 Grafana） | ⚪ 日志已有；导出面板暂缓 |
| **H-TOOL-2** | supervision 叠框批注 hardneg/漏检 PNG 队列 | ⚪ 不抢 v13 |
| **H-TOOL-3** | uptime-kuma 探活 dashboard/forward/executor | ⚪ 需 owner 批 VPS 旁路容器 |

**不立项**：换 Streamlit/React、接 Qlib/Nautilus、ECharts 替换 LWC、Prometheus 全家桶上 VPS（在 tip≈0 时）。

---

## 7. 风险与诚实声明

- wuzao 是星标聚合，**≠** 对本仓痛点排序；本报告用「tip / 前向 / 看板」硬过滤。  
- 发现级 LWC 对照的框坐标是 **left/right 分数近似**（忽略 MARGIN），不能当金标几何审计。  
- 未安装 Grafana/Kuma；运维项仅规格级。  
- 与 `p_github_optimize_candidates` / `p_yolo_external_sources` 重叠处刻意不重复开训假设。

---

## 8. 下一步（需 Owner 决策的标出）

1. **主线不变**：等 v13 终局 → `eval_v13_vs_v12_tip.sh` / tip-smoke。  
2. 若要继续前端旁路：批准把 H-FE-1 的时间带接到看板 explore/label 页（小改 `app.js`，仍不碰 LIVE 配置）。  
3. H-TOOL-3 装 Kuma：**明确 Owner 批准**后再动 VPS。  
4. 不要安排「大前端重构」或「可视化选型周」。
