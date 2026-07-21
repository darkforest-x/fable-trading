# 无噪（wuzao）全站 topics 扫描 — 对本仓可迁移性

**日期**：2026-07-22（同日 **v2 口径改正**）  
**入口**：[https://www.wuzao.com/topics/](https://www.wuzao.com/topics/)  
**对照已读**：`HANDOFF.md`、`src/webapp/`、`analysis/p_github_optimize_candidates.md`、`analysis/p_yolo_external_sources.md`、`analysis/backlog_future_optimizations.md`、`docs/RESEARCH_AGENDA.md`、`src/webapp/static/DESIGN-REFERENCES.md`。

---

## ⚠️ 口径变更（Owner 纠正 · 必读）

| 项 | 错误口径（首版） | 正确口径（本稿） |
|----|------------------|------------------|
| 「能拿来用」 | 对 **tip 主线**有没有解药 | 对本仓 **任一子系统**有明确用法：检测 tip、判断 2b、执行/风控、看板前端、运维、数据、标注/调试工具 |
| tip 的角色 | 唯一用途过滤器 → 无 tip 解药就删出清单 | **优先级最高**，另列「是否服务 tip 主线」= 是 / 否 / 间接；**不因无 tip 解药删除** |
| 清单规模 | 强行 ≤8 | **12–20 条有真用法**即可；仍砍纯炫技 / 换栈全家桶 |
| 分类相关度 | 用 tip 硬滤压低「回测 / 看板非 K 线 / 运维」 | 按整仓重评（回测→2b/前向对照；可视化→调试；运维→VPS） |

首版把筛选收成「tip 解药」是**过度收窄**，不是「聚焦」。本稿原地改正；发现级产物 `analysis/output/wuzao_lwc_tip_compare/` 仍有效。

**约束不变**：不耗 holdout、不 promote、不改 LIVE 真金、不杀 v13、不抢 MPS；**不**建议大前端重构 / 换检测栈。

---

## 结论先行

| 结论 | 内容 |
|------|------|
| 全站仍噪音很大 | 后端框架大全 / 移动端 / 游戏 / 多数 LLM App / 炫技前端 ≈ **D 层** |
| 真正能用的不止 tip | 整仓约 **16** 条有真用法（§4）；含判断对照、执行风控规格、看板非 K 线图、标注策展、VPS 运维 |
| tip 仍最高优先 | v13 tip-smoke 是关路径；A/C 层旁路**可并行**，B 层排队等 tip / 前向，**≠** 其它都不能碰 |
| 前端 | **加深**已有 LWC +（可选）ECharts 叠 PF/权益；**不**另起 Streamlit/Superset 交易终端、不换 React/Next |
| 检测 | ultralytics / supervision / FiftyOne / netron / Label Studio·CVAT 有用；SD / ComfyUI / vLLM = 噪音 |
| 与既有报告 | FiftyOne / ONNX / Basana / Protections / CVAT 与 `p_github_optimize_candidates` **交叉引用**，不重复开训假设 |

**发现级已做**（无 GPU）：
- 3 窗 hardneg CSV→LWC → `analysis/output/wuzao_lwc_tip_compare/`
- **本夜加深**：10 窗图层批量 → `analysis/output/wuzao_lwc_hardneg_batch/`；叠框画廊；LS 小包；Protections 规格  
  详见 `analysis/p_wuzao_a_tier_done.md` / `analysis/p_overnight_20260722.md`。

口径改正本身只改文档；A 档落地在 Owner「可以做的先做掉」授权后完成（仍不抢 v13）。

---

## 1. 抓取说明

- topics 首页、quant、data-visualization、pytorch、grafana、prometheus、docker、pandas、command-line：**WebFetch 成功**。
- `/topics/cli/` 404；命令行正确 slug = `/topics/command-line/`。
- 条目简介以站点摘要 + 已知 GitHub 身份为准；未逐仓 clone。
- 与 `p_github_optimize_candidates` 重叠的标「已评」——仍列入可用清单（整仓用法），不重复立项。

---

## 2. 全部分类相关度一览（整仓重评）

| 大类 | 主题 | 约项目数 | 相关度 | 一句 |
|------|------|----------|--------|------|
| 数据科学 | **量化交易** | 77 | **高** | 覆盖 2a/2b/执行；多数项目是「换整机」→ 用法在规格/对照，不在 pip 替换 |
| 数据科学 | **数据可视化** | 145 | **高** | 看板调试 + 前向复盘图 + 运维面板；主 K 线已锁定 LWC |
| 人工智能 | **PyTorch** | 215 | **高** | YOLO/Ultralytics 栈；CV 工具进 A；SD/LLM 训练栈进 D |
| 运维 | **Grafana** | 19 | **高** | VPS 脉冲耗时 / 存活；不替换 fable-dashboard |
| 运维 | **Prometheus** | 37 | **中高** | discover_wall / 进程指标；装栈需 owner（小机克制） |
| 运维 | **Docker** | 303 | **中** | 本仓主路径 systemd+rsync；uptime-kuma / netdata 可旁路 |
| 运维 | **安全** | 332 | **中** | Authelia / trivy 级加固对 VPS 有用；与 tip 无关但属工程卫生 |
| 数据科学 | **Pandas** | 60 | **中** | 已是底座；页内多为教程/表格 UI，少新积木 |
| 工具 | **命令行** | 248 | **中** | ripgrep/tqdm 已熟；`nvitop` 对本机训有用 |
| 前端 | **React** | 675 | **低–中** | 可抄组件节奏（已有 shadcn 笔记）；禁止整站重写 |
| 前端 | Vue / Next / Nuxt / Angular / Bootstrap | — | **低** | FreqUI=Vue 仅布局参考（DESIGN-REFERENCES） |
| 后端 | Flask | 24 | **低** | 本仓 FastAPI；Streamlit/Dash = 离线小工具可议、整站换 = D |
| 后端 | Django / Express / Laravel / .NET / Rails / Spring | — | **无关** | |
| 移动端 | Flutter / React Native | — | **无关** | |
| 人工智能 | TensorFlow | 116 | **低** | 不迁 TF |
| 人工智能 | **大语言模型** | 518 | **低** | 交易 Agent = D；远期打标辅助另议且不抢训 |
| 数据科学 | Kafka / Spark | — | **无关** | 体量错配 |
| 运维 | Kubernetes | 258 | **无关** | 单 VPS systemd 够用 |
| 游戏 | Unity | — | **无关** | |
| 工具 | Git / VS Code | — | **低** | 开发者卫生，无迁移假设 |
| 其他 | Awesome | 327 | **低** | 二次索引；`awesome-quant` 书签 |
| 其他 | 区块链 / 算法 | — | **无关/低** | |

---

## 3. 高+中深挖：项目表（整仓用法）

图例：**服务 tip** = 是 / 否 / 间接。相关度按**对本仓任一子系统**，不是 tip 唯一。

### 3.1 量化交易

| 名称 | 链接 | 相关度 | 服务 tip | 用在哪 | 怎么用（一句） | 风险（一句） | 层 |
|------|------|--------|----------|--------|----------------|--------------|----|
| tradingview/**lightweight-charts** | https://github.com/tradingview/lightweight-charts | **高** | **是** | 看板 / 检测调试 | 加深时间带·markers·primitive；已用，勿换库 | 截图喂 YOLO 会污染几何 | A |
| freqtrade/**freqtrade** | https://github.com/freqtrade/freqtrade | **中高** | **否** | 执行/风控 | **只抄 Protections 规格清单**（已评） | GPL 禁 pip；过早上熔断会挡本来就稀的 tip 单 | B |
| gbeced/**basana**（GitHub 已评；量化同族） | https://github.com/gbeced/basana | **中** | **否** | 执行 / 前向对照 | 借「回测↔实盘同事件语义」写对照表，不换执行器 | 整框引入 = 换栈 | B |
| nautechsystems/**nautilus_trader** / mementum/**backtrader** / microsoft/**qlib** | 各 GitHub | **中** | **否** | 2b / 前向对照 | 借事件驱动与报告指标口径做**离线对照设计**，不替换本仓 train/forward | 当「下一交易引擎」会吞带宽；许可证/栈摩擦 | B（规格）/ D（换机） |
| hummingbot/**hummingbot** | https://github.com/hummingbot/hummingbot | **低** | **否** | — | 做市引擎与本仓两层架构不对口 | Streamlit 壳已否决整站换 | D |
| ccxt/**ccxt** | https://github.com/ccxt/ccxt | **低** | **否** | 数据 | 本仓 OKX 自研 fetch 已够 | 换栈无收益、接口漂移 | D |
| openbb-finance/**openbb** | https://github.com/OpenBB-finance/OpenBB | **低** | **否** | 看板参考 | 只学 widget 分流（已记 DESIGN） | 体量过大 | D |
| tauricresearch/**tradingagents** 等 LLM 交易 | — | **无关** | **否** | — | — | 与几何 tip / LGBM 无关 | D |
| timescale/**timescaledb** / questdb / tdengine | — | **低** | **否** | 数据 | CSV+VPS 体量不需要 | 运维面 > 收益 | D |
| wilsonfreitas/**awesome-quant** | https://github.com/wilsonfreitas/awesome-quant | **低** | **否** | 索引 | 书签 | 无代码迁移 | D |
| ranaroussi/**yfinance** / akfamily/**akshare** | — | **低** | **否** | 数据旁路 | 非 OKX 合约主路径；regime 用 `pycoingecko` 等更贴（见已评） | 数据语义错配 | D / 见 B 特征 |

### 3.2 数据可视化

| 名称 | 链接 | 相关度 | 服务 tip | 用在哪 | 怎么用（一句） | 风险（一句） | 层 |
|------|------|--------|----------|--------|----------------|--------------|----|
| LWC（同上） | — | **高** | **是** | 看板 | 主 K 线路径 | — | A |
| apache/**echarts** | https://github.com/apache/echarts | **中高** | **否** | 看板 / 前向复盘 | 叠 PF、权益、分位条；**不**换 K 线主图 | 双图库体积；勿与 LWC 抢主图 | B |
| matplotlib/**matplotlib** | https://github.com/matplotlib/matplotlib | **高** | **间接** | TG 图 / 分析报告 | 保持现状；脚本静态图 | **勿**替代 YOLO cv2 训练渲染 | A |
| grafana/**grafana** | https://github.com/grafana/grafana | **高** | **否** | VPS 运维 | 脉冲耗时/告警面板**思路**；可不装全栈 | 小机资源；**需 owner** 才上 VPS | C |
| netdata/**netdata** | https://github.com/netdata/netdata | **中高** | **否** | VPS 运维 | 与 Grafana 二选一轻量主机监控 | 仍占资源；**需 owner** | C |
| streamlit/**streamlit** / plotly/**dash** / gradio-app/**gradio** | 各 GitHub | **中** | **间接** | 离线分析小页 | 本机难例/前向切片 App，**不**进 VPS 主看板 | 双栈维护；禁止整站替换 FastAPI | B（离线）/ D（换站） |
| apache/**superset** / metabase / redash / dataease | — | **低** | **否** | — | — | BI 拖拽与本仓信号流不对口 | D |
| d3/**d3** / airbnb/**visx** / pixijs | — | **低** | **否** | — | — | 底层炫技，ROI 低 | D |

### 3.3 PyTorch / 检测生态

| 名称 | 链接 | 相关度 | 服务 tip | 用在哪 | 怎么用（一句） | 风险（一句） | 层 |
|------|------|--------|----------|--------|----------------|--------------|----|
| ultralytics/**ultralytics** | https://github.com/ultralytics/ultralytics | **高** | **是** | 检测 2a | 已用；跟 export/ONNX 文档 | 勿换 MMDet 全家桶 | A |
| roboflow/**supervision** | https://github.com/roboflow/supervision | **高** | **间接** | 标注/调试 | 离线难例 PNG 叠框批注 | **不进**脉冲 | A |
| voxel51/**fiftyone**（已评） | https://github.com/voxel51/fiftyone | **高** | **间接** | 标注/策展 | hardness / FP 队列；本机 App | 偏重；不抬 tip_fire | A |
| HumanSignal/**label-studio** / cvat-ai/**cvat**（已评） | 各 GitHub | **高** | **间接** | 标注 | LS 已接；CVAT 仅 LS UX 卡住时备选 | 双写污染；AGPL 工具勿进主依赖 | A |
| lutzroeder/**netron** | https://github.com/lutzroeder/netron | **中** | **间接** | 模型调试 | export ONNX 后看图 | 无则跳过 | A |
| microsoft/**onnxruntime** / openvino（已评） | 各 GitHub | **中高** | **间接** | 检测推理 / 脉冲 | tip 通后压 discover_wall | **不抬** tip_fire；无 tip 时加速=省空转 | B |
| open-mmlab/**mmdetection** | — | **低** | **否** | — | — | 与 Ultralytics 摩擦 | D |
| Stable Diffusion / ComfyUI / vLLM / DeepSpeed / Ray… | — | **无关** | **否** | — | — | — | D |

### 3.4 Grafana / Prometheus / Docker / 命令行 / 安全

| 名称 | 链接 | 相关度 | 服务 tip | 用在哪 | 怎么用（一句） | 风险（一句） | 层 |
|------|------|--------|----------|--------|----------------|--------------|----|
| prometheus/**node_exporter** + 自打点 | https://github.com/prometheus/node_exporter | **中** | **否** | VPS 运维 | 先确认 journal 是否够「>600s 查因」再议导出 | **需 owner**；全家桶过重 | C |
| louislam/**uptime-kuma** | https://github.com/louislam/uptime-kuma | **中高** | **否** | 运维 | 探活 dashboard/forward/executor | 探针≠新鲜度门；**需 owner 批 VPS** | C |
| xuehaipan/**nvitop** | https://github.com/XuehaiPan/nvitop | **中** | **间接** | 本机训 | `pip` 看 GPU；旁路 v13 | **不进** VPS 脉冲 | A |
| aquasecurity/**trivy**（安全主题常见） | https://github.com/aquasecurity/trivy | **中** | **否** | 运维卫生 | 镜像/依赖漏洞扫描（若用容器旁路） | 误报噪音；不改交易逻辑 | C |
| stefanprodan/**dockprom** | — | **低** | **否** | — | — | VPS 过重 | D |
| ohmyzsh / thefuck / warp… | command-line 页 | **低** | **否** | — | — | 终端糖 | D |

### 3.5 低/无关整类带过

后端全家桶、移动端、Unity、K8s、Kafka/Spark、LLM 交易 Agent、区块链、算法题库：**不深挖**。React/Vue 只保留布局节奏笔记。

---

## 4. 分层清单（整仓可用 · Owner 速览）

每条：**用在哪 · 怎么用一句 · 风险一句**。服务 tip 仅作排队，不删项。

### A. 现在就能用 / 便宜发现级（不抢 v13 GPU）

| # | 项目 | 服务 tip | 用在哪 | 怎么用 | 风险 | **本夜状态** |
|---|------|----------|--------|--------|------|-------------|
| 1 | [lightweight-charts](https://github.com/tradingview/lightweight-charts) | 是 | 看板/检测调试 | 加深时间带/primitive；CSV→LWC | 禁止 LWC 截图进 YOLO | ✅ 批量图层 `analysis/output/wuzao_lwc_hardneg_batch/`（+旧 3 窗对照） |
| 2 | [ultralytics](https://github.com/ultralytics/ultralytics) | 是 | 检测 2a | 已用；跟官方 export | 勿换检测栈 | 已用；训中不 export |
| 3 | [matplotlib](https://github.com/matplotlib/matplotlib) | 间接 | TG/分析报告 | 保持静态图脚本 | 勿替代 cv2 训练渲染 | ✅ 叠框后端 `hardneg_overlay_gallery/` |
| 4 | [supervision](https://github.com/roboflow/supervision) | 间接 | 标注/调试 | 离线 hardneg 叠框 | 不进脉冲、不抢 MPS | ⏭ 未装（避污染训 .venv）；脚本 `--prefer-supervision` 可切换 |
| 5 | [fiftyone](https://github.com/voxel51/fiftyone) | 间接 | 难例策展 | 本机 Dataset | App 偏重 | ⏭ 改走 LS 小包（同目标更轻） |
| 6 | Label Studio / [CVAT](https://github.com/cvat-ai/cvat) | 间接 | 标注 | LS 已接；CVAT 备选 | 双写污染 | ✅ `output/label_studio/tasks_hardneg_discovery.json`（24 条） |
| 7 | [netron](https://github.com/lutzroeder/netron) | 间接 | 模型调试 | ONNX 后看结构 | 训中 export 可能碰 MPS | 📝 一键命令 `docs/LOCAL_DEBUG_TOOLS.md`（未 export） |
| 8 | [nvitop](https://github.com/XuehaiPan/nvitop) | 间接 | 本机训 | pip 自用看 GPU | 不进 VPS | 📝 说明+alias 同文档；`scripts/v13_train_status.sh` |

**B 档提前只做规格**：Freqtrade Protections → `docs/EXEC_PROTECTIONS_SPEC.md`（未改 executor）。  
**C 档**：`docs/ops/VPS_OBSERVABILITY_PENDING.md`（待批，未装机）。

短报告：`analysis/p_wuzao_a_tier_done.md` · 夜间总纪要：`analysis/p_overnight_20260722.md`。

### B. tip 过关后再用（判断 / 执行 / 前向）

| # | 项目 | 服务 tip | 用在哪 | 怎么用 | 风险 |
|---|------|----------|--------|--------|------|
| 9 | Freqtrade **Protections 规格**（不引依赖） | 否 | 执行/风控 | 前向≥50 后写熔断门槛清单 | GPL；过早上线挡单 |
| 10 | Basana / Nautilus / Backtrader / Qlib **思路** | 否 | 2b·前向对照 | 事件语义与报告口径对照表；**不 pip 换机** | 当成下一引擎会吞带宽 |
| 11 | [apache/echarts](https://github.com/apache/echarts) | 否 | 看板·前向复盘 | PF/权益/分位交互图，K 线仍 LWC | 双库体积 |
| 12 | [onnxruntime](https://github.com/microsoft/onnxruntime)（± OpenVINO） | 间接 | 检测推理 | tip 通且 discover 仍>600s 再 A/B 延迟 | 不抬 tip_fire |
| 13 | Streamlit/Gradio **离线**小页 | 间接 | 本机分析 | 难例/前向切片；不进 VPS 主站 | 双栈；禁止换 FastAPI |
| 14 | BTC dominance 等轻量 regime（`pycoingecko` 等，已评） | 否 | 判断 2b | 离线特征草稿；**不进 ACTIVE** 除非 owner 单变量立项 | 特征泄漏/持有纪律 |

### C. 运维 / 工程卫生（随时可议；动 VPS 需 owner）

| # | 项目 | 服务 tip | 用在哪 | 怎么用 | 风险 |
|---|------|----------|--------|--------|------|
| 15 | Grafana **思路** / [netdata](https://github.com/netdata/netdata) | 否 | VPS 可观测 | 先榨 journal；再议装面板 | **装 VPS = owner 批** |
| 16 | [uptime-kuma](https://github.com/louislam/uptime-kuma) | 否 | 服务存活 | 探活三服务 HTTP | **需 owner 批**；≠新鲜度门 |
| 17 | Prometheus node_exporter | 否 | 主机指标 | 可选；小机克制 | **需 owner 批** |
| 18 | [trivy](https://github.com/aquasecurity/trivy) | 否 | 依赖/镜像扫描 | 旁路容器时扫一次 | 误报；不改交易 |

### D. 噪音 / 暂缓（真正无关或许可证/栈冲突）

TradingAgents / OpenBB 整站 / Hummingbot 做市引擎 / FinRL / Qbot / Superset·Metabase 换看板 / ECharts **替换** LWC 主图 / Streamlit **整站** / ComfyUI·SD·vLLM / K8s·dockprom 全家桶 / ccxt 换 OKX fetch / Timescale 上 VPS / MMDetection 换栈 / React·Next 重写看板。

---

## 5. 发现级验证（已做 · 仍有效）

**假设**：LWC 能直接消费本仓 `okx_*_15m_*.csv`，用时间带标 hardneg 中段框，提升相对静态 YOLO PNG 的调试可读性——且**不改** FastAPI 后端。

**结果**（`analysis/output/wuzao_lwc_tip_compare/verdict.json`）：

1. CSV→unix 秒 OHLC→LWC：**可行，零后端改动**。  
2. 交互缩放比纯 PNG 更易判断「中段簇 vs 右缘 tip」。  
3. LWC 的 MA20/60 **≠** YOLO 六均线 cv2 渲染 → **禁止**截图进检测训练。  
4. 精确 YOLO 框仍要 series-primitive 或保留 PNG；时间带够发现级可读性。

**本轮未新开发现级**：口径改正优先；supervision/FiftyOne 等 A 层可另开，但不抢 v13。

---

## 6. 登记假设（整仓旁路；相对 tip **排队**，不删除）

全文见 `docs/RESEARCH_AGENDA.md` § E。摘要：

| ID | 一句话 | 相对 tip | 状态 |
|----|--------|----------|------|
| **H-FE-1** | LWC 时间带/primitive 增强 tip·hardneg 调试 | 可并行（不抢 GPU） | 🟡 批量图层已过 `wuzao_lwc_hardneg_batch/` |
| **H-FE-2** | 前向/信号表 LWC markers 统一语义 | tip_fire>0 后 | ⚪ |
| **H-FE-3** | ECharts 叠 PF/权益（不换 K 线主图） | tip/前向有样本后更有用 | ⚪ |
| **H-TOOL-1** | 脉冲耗时轻量指标（Grafana 思路） | 可议；装栈排队 | ⚪ 待批清单已写 |
| **H-TOOL-2** | hardneg 叠框（matplotlib≡supervision） | 可并行离线 | 🟢 `hardneg_overlay_gallery/` |
| **H-TOOL-3** | uptime-kuma 探活 | 随时可议；**需 owner 批 VPS** | ⚪ 待批 |
| **H-TOOL-4** | FiftyOne 难例策展队列 | 可并行离线 | ⚪ 本夜用 LS 小包代替 |
| **H-TOOL-5** | ONNX Runtime（± OpenVINO）压 discover | tip 通且仍慢再做 | ⚪；netron 命令已记 |
| **H-JUDG-WUZAO-1** | 轻量 regime 特征草稿（dominance 等） | tip 通后单变量；不进 ACTIVE | ⚪ |
| **H-EXEC-WUZAO-1** | Protections / 事件语义对照规格 | 前向样本够再上线 | 🟡 `docs/EXEC_PROTECTIONS_SPEC.md` |

**不立项（仍噪音）**：Streamlit/Dash/Superset **换栈**、ECharts **替换** LWC 主图、Qlib/Nautilus/Backtrader **替换**本仓、TradingAgents、K8s、React/Next 重写看板。

---

## 7. 风险与诚实声明

- 首版用 tip 硬过滤，**漏报**了判断对照、执行规格、非 K 线可视化、运维卫生——已按 Owner 纠正改正。  
- wuzao 是星标聚合，**≠** 对本仓痛点排序；「高星」仍可能是 D。  
- 发现级 LWC 对照框坐标是 left/right 分数近似（忽略 MARGIN），不能当金标几何审计。  
- 未安装 Grafana/Kuma；运维项仅规格级。  
- B 层「回测框架」= **规格/对照**，不是批准换执行引擎。  
- 与 `p_github_optimize_candidates` / backlog 重叠处刻意交叉引用，避免双开训假设。

---

## 8. 下一步（需 Owner 决策的标出）

1. **主线不变**：等 v13 终局 → `bash scripts/v13_train_status.sh` → `bash scripts/eval_v13_vs_v12_tip.sh` / tip-smoke。  
2. **A 档本夜已落地**：LWC/叠框/LS/规格 — 见 `p_wuzao_a_tier_done.md`；挂看板 explore（H-FE-1 余量）仍可议。  
3. **H-TOOL-3 / Grafana·netdata 上 VPS**：**明确 Owner 批准**后再动（清单 `docs/ops/VPS_OBSERVABILITY_PENDING.md`）。  
4. B 层大项（ECharts PF / ONNX Runtime / regime 特征）默认等 tip 通；Protections **规格已有**，上线阈值另批。  
5. 不要安排「大前端重构」或「可视化选型周」。
