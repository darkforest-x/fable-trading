# 无噪 topics：前端之外还有哪些对本仓真正好用

**日期**：2026-07-22（Owner 纠正：不要只答可视化）  
**入口**：[topics 首页](https://www.wuzao.com/topics/) · 本轮深挖：[quant](https://www.wuzao.com/topics/quant/) / [pytorch](https://www.wuzao.com/topics/pytorch/) / [grafana](https://www.wuzao.com/topics/grafana/) / [prometheus](https://www.wuzao.com/topics/prometheus/) / [docker](https://www.wuzao.com/topics/docker/) / [security](https://www.wuzao.com/topics/security/) / [pandas](https://www.wuzao.com/topics/pandas/) / [command-line](https://www.wuzao.com/topics/command-line/) / [data-visualization](https://www.wuzao.com/topics/data-visualization/)  
**对照**：`analysis/p_wuzao_topics_scan.md`（昨夜整仓 16+ 条）· `analysis/p_wuzao_a_tier_done.md` · `analysis/p_github_optimize_candidates.md`  
**约束**：不杀 v13、不抢 MPS、不 promote、不动 LIVE 真金、不大改前端。

---

## 结论先行

昨夜 A 档把「能立刻落地」偏成了 **LWC/叠框/LS/规格**——对，但不够。  
Owner 要的是：**检测调试 / 标注 / 训练监视 / 判断对照 / 执行风控 / 数据 / 运维** 整条链上，topics 里还有什么值得碰。

本轮相对昨夜清单的增量，不在「再列一遍 Ultralytics」，而在：

| 标记 | 含义 |
|------|------|
| **★增量** | 昨夜未入主清单，或只一笔带过、本轮值得强调 |
| （已评） | 昨夜 A/B/C 已有，此处按**用途**重排，避免纯复读 |

**现在能否碰**：`能` = 本机旁路、不抢 GPU；`等 tip` = tip_fire/前向样本够再有 ROI；`要批 VPS` = 动生产机需 Owner 点头。

---

## 1. 前端之外：最值得关注的 12 条（按用途）

### 检测调试 / 难例看图

| 项目 | 链接 | 本仓怎么用一句 | 能否碰 | 备注 |
|------|------|----------------|--------|------|
| **supervision**（已评） | https://github.com/roboflow/supervision | 离线 hardneg PNG 叠框批注；脚本已有 `--prefer-supervision` | **能** | 不进脉冲；训 .venv 可另装 |
| **FiftyOne**（已评） | https://github.com/voxel51/fiftyone | tip 漏火 / 贴边 FP 建 Dataset 策展队列 | **能** | 昨夜改走 LS 小包；App 偏重但 ROI 仍在 |
| **Netron**（已评） | https://github.com/lutzroeder/netron | export ONNX 后看图结构，核对 head/stride | **能**（勿 mid-run export） | 命令已记 `docs/LOCAL_DEBUG_TOOLS.md` |

### 标注

| 项目 | 链接 | 本仓怎么用一句 | 能否碰 | 备注 |
|------|------|----------------|--------|------|
| **Label Studio**（已评） | https://github.com/HumanSignal/label-studio | 已接；hardneg 发现小包可继续扩 | **能** | Community=Apache-2.0 |
| **CVAT**（已评） | https://github.com/cvat-ai/cvat | LS UX 卡住时备选；Ultralytics ZIP 导出 | **能**（Docker 本机） | 勿与 LS 双写污染 |

### 训练监视（本机 MPS，不进 VPS 脉冲）

| 项目 | 链接 | 本仓怎么用一句 | 能否碰 | 备注 |
|------|------|----------------|--------|------|
| **nvitop**（已评） | https://github.com/XuehaiPan/nvitop | 看 GPU/进程是否还活、谁占 MPS | **能** | 与 `scripts/v13_train_status.sh` 互补 |
| **Ultralytics 自带结果图**（已评） | https://github.com/ultralytics/ultralytics | 继续读 `results.csv` / `results.png`；勿另起 W&B 全家桶抢训 | **能** | topics 里 wandb 星高，对本仓 mid-run **不建议**新开云同步 |

### 判断层（2b）对照 / 数据卫生 — ★多挖

| 项目 | 链接 | 本仓怎么用一句 | 能否碰 | 备注 |
|------|------|----------------|--------|------|
| **★ stefan-jansen/machine-learning-for-trading** | https://github.com/stefan-jansen/machine-learning-for-trading | 只读 walk-forward / 特征无前视 / 报告口径 notebook，对照本仓时间切分与 top-decile 叙事 | **能**（只读） | quant 页高位；**不** pip 进 ACTIVE |
| **★ pycoingecko**（GitHub 已评，wuzao 量化侧少提） | https://github.com/man-c/pycoingecko | BTC dominance 等轻量 regime → 离线特征草稿 | **能**草稿；进 ACTIVE **要批** | 单变量；资金费率本仓已有勿重复 |
| **★ ydata-profiling 族**（pandas 主题常见：fg-data-profiling） | https://github.com/ydataai/ydata-profiling | 对 `judgment_dataset_*.csv` 一键质量报告（缺失/漂移/正类率） | **能** | 建库前后卫生；不碰 holdout |

### 执行 / 风控 / 前向对照

| 项目 | 链接 | 本仓怎么用一句 | 能否碰 | 备注 |
|------|------|----------------|--------|------|
| **Freqtrade Protections**（已评） | https://github.com/freqtrade/freqtrade | 只抄熔断规格清单；规格已在 `docs/EXEC_PROTECTIONS_SPEC.md` | 规格**能**；上线阈值 **等 tip/前向** | GPL 禁 pip 替换 |
| **★ Lean / vnpy 事件语义**（quant 页；昨夜捆在「回测框架」里） | [LEAN](https://github.com/QuantConnect/lean) · [vnpy](https://github.com/vnpy/vnpy) | 对照「回测↔实盘同事件边界」写审计表，对齐 tip 实时入账时序 | **能**读规格 | **不**换执行器；Basana 同族 |

### 运维 / 可观测 / VPS 卫生 — ★多挖

| 项目 | 链接 | 本仓怎么用一句 | 能否碰 | 备注 |
|------|------|----------------|--------|------|
| **uptime-kuma**（已评） | https://github.com/louislam/uptime-kuma | 探活 dashboard / forward / executor HTTP | **要批 VPS** | 探针 ≠ 新鲜度门 |
| **★ Grafana Loki + Fluent Bit**（grafana/prometheus 页） | [Loki](https://github.com/grafana/loki) · [Fluent Bit](https://github.com/fluent/fluent-bit) | 把 `discover_wall` / phase2 日志聚起来查 >600s，比先上完整 Prom 栈更贴「脉冲查因」 | **要批 VPS** | 昨夜只写了 Grafana/netdata/exporter；**日志侧缺口** |
| **★ OpenObserve**（grafana/prometheus 页） | https://github.com/openobserve/openobserve | 单二进制日志+指标，作小机上 Grafana 全家桶的轻替代候选 | **要批 VPS** | 与 netdata **三选一**，勿叠装 |
| **★ Caddy 或 acme.sh**（security / command-line） | [Caddy](https://github.com/caddyserver/caddy) · [acme.sh](https://github.com/acmesh-official/acme.sh) | 若看板外网暴露：自动 HTTPS 反代 | **要批 VPS** | 与交易逻辑无关，属暴露面卫生 |
| **★ how-to-secure-a-linux-server**（security） | https://github.com/imthenachoman/how-to-secure-a-linux-server | VPS SSH/防火墙/审计对照清单 | **能**读；改机 **要批** | 非交易工具，实盘后工程卫生 |

### 数据 / API 调试 — ★多挖

| 项目 | 链接 | 本仓怎么用一句 | 能否碰 | 备注 |
|------|------|----------------|--------|------|
| **★ mitmproxy**（security） | https://github.com/mitmproxy/mitmproxy | 本机拦截看 OKX fetch / 下单请求形态（只读调试） | **能**（本机） | **禁止**对着 LIVE key 乱改包；只观察 |
| **★ marimo / pygwalker**（data-visualization / pandas） | [marimo](https://github.com/marimo-team/marimo) · [pygwalker](https://github.com/Kanaries/pygwalker) | 本机对 forward_log / judgment CSV 做可复现切片，不进 VPS 主站 | **能** | 比再起一套 Streamlit 站更轻；git 友好选 marimo |

---

## 2. 前端相关（克制一小段）

| 项目 | 链接 | 一句 | 能否碰 |
|------|------|------|--------|
| lightweight-charts | https://github.com/tradingview/lightweight-charts | 主 K 线已锁定；加深时间带/markers 即可 | **能**（另有 agent 收土味） |
| ECharts | https://github.com/apache/echarts | 仅叠 PF/权益等非 K 线；**不**换主图 | **等 tip/前向** |
| jsoncrack | https://github.com/AykutSarac/jsoncrack.com | 偶发看 sidecar / 任务 JSON 树 | **能**（浏览器） |

不做：Superset/Metabase 换看板、Streamlit 整站、React/Next 重写。

---

## 3. 明确暂缓 / 噪音（扫过再砍）

| 类别 | 代表（topics 高星） | 为何砍 |
|------|---------------------|--------|
| LLM 交易 Agent | TradingAgents、vibe-trading、daily_stock_analysis | 与几何 tip + LGBM 两层无关 |
| 换整机 | OpenBB 整站、Hummingbot 做市、Qbot、FinRL、vnpy/LEAN **整框** | 吞带宽；本仓 OKX tip/三门/tiered 专有 |
| 换检测栈 | MMDetection、ComfyUI/SD、vLLM | 增强禁忌 + 栈摩擦 |
| 重 BI / 炫技图 | Superset、Metabase、DataEase、D3/visx 底层 | 与信号流不对口 |
| 重监控全家桶 | dockprom、kube-prometheus、Thanos、VictoriaMetrics 集群 | 单 VPS systemd 过重 |
| 数据换源 | ccxt 换 OKX fetch、yfinance/akshare 主路径、Timescale/QuestDB 上 VPS | 已有 fetcher；体量不需要 |
| 终端糖 | ohmyzsh、thefuck、Warp、nerd-fonts | 零交易 ROI |
| 渗透玩具 | sqlmap、PayloadsAllTheThings、Sherlock | 非本仓用途；nuclei 仅在「暴露面扫描」且 Owner 批后才议 |

---

## 4. 和当前主线（v13 tip-smoke）怎么并行

```
v13 训满 / 早停 → eval tip-smoke →（不 auto-promote）
        │
        ├── 现在就能并行（不抢 MPS）
        │     · 标注/难例：LS 小包、supervision 叠框、FiftyOne 策展
        │     · 本机：nvitop、Netron（训后）、mitmproxy 看 API、marimo 切片
        │     · 只读：ML4T notebook、Protections/Lean 规格、linux harden 清单
        │     · 前端土味：另 agent；本清单不抢
        │
        ├── tip_fire / 前向有样本后再拧
        │     · ONNX Runtime（± OpenVINO）压 discover
        │     · ECharts PF/权益；Protections 阈值上线
        │     · dominance 等 regime 单变量立项
        │
        └── 动 VPS = 必须 Owner 批
              · Kuma / netdata|Grafana|OpenObserve 三选一
              · ★优先议：Fluent Bit→Loki（或 OpenObserve）查脉冲日志
              · Caddy/acme.sh、安全加固、nuclei 暴露面
```

**不杀 v13**：任何「装监控 / 换推理 / 扩特征」都排在 tip-smoke 之后或明确旁路。

---

## 5. 相对昨夜 A 档：增量一览（避免复读同一 16 条）

| ★增量项 | 用途层 | 昨夜状态 | 本轮动作建议 |
|---------|--------|----------|--------------|
| machine-learning-for-trading | 判断纪律 | 未入清单 | 书签 + 抽 2–3 个 walk-forward 笔记对照本仓切分 |
| Loki + Fluent Bit | 运维·日志 | 只写了 Grafana/netdata/exporter | 写入 `VPS_OBSERVABILITY_PENDING` 候选（待批） |
| OpenObserve | 运维·轻栈 | 未提 | 与 netdata 并列「小机三选一」 |
| Caddy / acme.sh | 运维·HTTPS | 未提 | 仅当看板公网暴露时批 |
| how-to-secure-a-linux-server | 运维·加固 | 未提 | 对照清单，改机另批 |
| mitmproxy | 数据·API 调试 | 未提 | 本机只读调试 OKX |
| marimo / pygwalker | 数据·离线分析 | 只提了 Streamlit/Gradio | 优先 marimo（纯 py、可 git） |
| ydata-profiling | 数据·建库卫生 | 未提 | judgment CSV 出报告 |
| Lean / vnpy 单点强调 | 执行·事件语义 | 捆在「回测框架」一句 | 与 Basana 并列规格对照 |

昨夜已落地、此处**不重复立项**：LWC 批量图层、叠框画廊、LS hardneg 小包、Protections 规格文档、nvitop/Netron 命令笔记。

---

## 6. 建议同步的小改（文档）

- 本文件：`analysis/p_wuzao_more_useful.md`
- `analysis/p_wuzao_topics_scan.md` 顶部加指针（见同提交）
- 可选：把 Loki/OpenObserve/Caddy 三行补进 `docs/ops/VPS_OBSERVABILITY_PENDING.md`（仍待批、未装机）

---

## 7. 风险与诚实声明

- wuzao 按星标排序，**高星 ≠ 对本仓痛点**；本轮刻意按子系统用途重排。  
- 「能碰」≠「现在就该装」：tip≈0 时运维装机 ROI 仍低。  
- 判断层 notebook / regime 特征仍受铁律约束（时间切分、无前视、单变量、不耗 holdout）。  
- 未在本机 pip 新装上述增量库；未 SSH VPS。  
- 前端另有会话收土味；本报告故意压前端篇幅。
