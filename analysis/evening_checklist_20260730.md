# 本晚问题梳理与处理清单（2026-07-30 → 07-31）

> **用途**：把你晚上在 Grok 会话里提的需求逐项对账——做了什么、证据在哪、还剩什么。  
> **铁律未动**：holdout 不偷读 · 不自动 promote · 不改 ACTIVE · 不清 forward_log · 真金需 owner 逐次授权。  
> **验收页**：`http://127.0.0.1:8642/`（硬刷新；`app.js?v=20260731arch1`）。

**状态图例**

| 标记 | 含义 |
|------|------|
| ✅ 已完成 | 代码/数据已落地，可按「如何验」复现 |
| ⚠️ 部分完成 | 主路径通，缺配置/部署/owner 动作 |
| ⏳ 待你确认 | 需本机硬刷新肉眼看一眼 |
| 🚫 未做 / 需 owner | 刻意没做或铁律卡住，要你点头 |
| ℹ️ 解释类 | 只回答了「为什么」，无代码改动 |

---

## 一、当晚需求总表（按时间）

| # | 你提的问题 / 指令 | 状态 | 结果摘要 | 如何验 / 证据 |
|---|-------------------|------|----------|----------------|
| 1 | 继续 **1–4 打包**（纸面闭环、研究提交、Pi 清理、A2 maker 试跑·无真金） | ⚠️ | 纸面脚本/文档/A2 账本隔离有代码；Pi 冲突曾处理；**git 大量未提交**；VPS 纸面 timer 未上机 | `docs/run_v10_paper_live.md`；`a6b554c` pack commit；工作区仍 dirty |
| 2 | 前端 **点不动 / 数据对不上**；用 **v10 往前跑 + TG/Bark** | ⚠️ | 看板 API/导航多处修过；v10 纸面扫描有产物（7 新/7 总）；**Bark key 缺则 no-op**；TG 看本机 `tg_config` | 总览「v10纸面」芯片；`analysis/output/live_signals_v10/last_scan.json`；`docs/run_v10_paper_live.md` |
| 3 | 进度？ | ℹ️ | 当时口头汇报 | — |
| 4 | **v10 回测过吗？有逐笔吗？** | ℹ️/✅ | tip-replay / 判断池有逐笔与 PF；不是「v10 检测器实盘 100 笔已回测完」 | `#backtest` tip-replay；`judgment_v10_wide` 研究结论见 HANDOFF |
| 5 | **回测历史数据全清掉** · `#backtest` | ✅ | 旧前视净值/PF6.x 火花线下线；tip-replay 主导 | `#backtest`；`dashboard_payloads` sparkline_retired |
| 6 | **`#signals` 数据不对** | ✅ | 标记源改为 tip-replay；入场价/ATR/短障碍回填 | `#signals` 选 BABY；API `/api/chart/okx/BABY_USDT_SWAP` |
| 7 | tip-replay **PF 0.784 为什么** | ℹ️ | 盘口因果、做空 TP5/SL2、maker 成本、holdout 窗；非前视乐观 | 对话结论；`#backtest` 协议说明 |
| 8 | **前向数据不对**（事后剔除 / 净收益红框） | ℹ️/✅ | 1/100 新鲜 + 事后剔 26 是纪律正确呈现，不是 bug | `#forward`；铁律 7/新鲜度 30min |
| 9 | **`#probe` 执行检测报错** | ✅ | probe 超时/权重回退等修过；缺 `owner_best` 时诚实提示 | `#probe` 点执行；权重见 `models/` |
| 10 | **去掉 `#labeling` 菜单** | ✅ | 导航已去 labeling 入口 | 侧栏无「打标」 |
| 11 | **`#shorttf` → ETH 3m pilot** | ✅ | 短周期页接 eth3m 诊断/pilot 载荷 | `#shorttf` |
| 12 | **`#radar` 更新数据/模型** | ⚠️ | UI 叠层已统一 Claude 风；雷达是**规则多 TF**（非 YOLO/LGB）；全量重扫需你点「重新扫描」 | `#radar`；文案已标明非主线模型 |
| 13 | 总览小图 **历史前视数据别再显示** | ✅ | 验收净值 +245% 类前视图下线/标注废弃 | `#overview` 无旧 PF6 曲线 |
| 14 | Grok Build **插件 / 对话历史难翻** | ℹ️ | 说明过 resume/history 能力；非仓内功能 | 产品能力，非 fable 代码 |
| 15 | **盘口检测 · 历史一年 → HTML 报告** | ✅ | `scripts/probe_history.py` + `/api/probe-history` + 前端入口 | `#probe`「历史检测」；`analysis/output/probe_history/` |
| 16 | **为什么 WINDOW=200？如何提高检出准确度？** | ℹ️ | 写过因果窗/延迟预算说明；**未改训练窗**（改窗=新实验，需单变量+owner） | 对话结论；铁律 4 |
| 17 | **信号图叠层丑且不准，对齐 Claude** | ✅ | 做空：蓝↓ + `入场…空` + 绿止盈下 / 红止损上；后端 short TP/SL | `#signals` 点侧栏成交；硬刷新 |
| 18 | **前端所有 K 线统一优化** | ✅ | `chart_theme.js`（FableChart）统一 signals / explore / radar | 三页 K 线同色同叠层 |
| 19 | **`#overview` 写清架构·层·版本 · ClauseOS 好看** | ✅ | 架构板 L1→L2→L3→裁决 + 明细表 + 实时 ACTIVE | `#overview`；`paintArchitecture` |
| 20 | **整理文档，确保每项处理好** | ✅ | 即本文 | `analysis/evening_checklist_20260730.md` |

---

## 二、按主题深挖（避免「以为做了其实半截」）

### A. 纸面 / 通知 / 100 笔

| 项 | 状态 | 说明 |
|----|------|------|
| v10 tip-only 扫描脚本 | ✅ | `scripts/live_signal_tg.py --tip-only`；`USE_STOP=True`（TP5/SL2） |
| 前端展示 last_scan | ✅ | `/api/live-paper`；总览面板 + 状态条「v10纸面」 |
| TG 推送 | ⚠️ | 依赖 `data/tg_config.json`（本机有则可用） |
| Bark 推送 | ⚠️ | **`data/bark_config.json` 缺** → 打印提示、不推 |
| 15min 循环冲 100 笔 | ⚠️ | 有 `run_v10_paper_loop.sh` 类脚本/文档；**VPS 无 fable 部署 / 无 timer** → 本地可跑，不是 7×24 |
| 写 forward_log | 🚫 | 纸面**故意不写**主账本（隔离） |
| 真金 100 笔前向 | 🚫 | 现裁决 **1/100** 新鲜；检测实盘 **owner_best 未挂** |

### B. 看板数据诚实性

| 页 | 状态 | 现状 |
|----|------|------|
| 回测 | ✅ | 主展示 tip-replay holdout（PF 0.784）；旧前视已归档/下线 |
| 信号 | ✅ | tip-replay 成交标记 + short 障碍价 |
| 前向 | ✅ | 新鲜门 / 事后剔除语义正确 |
| 总览 | ✅ | 架构 + tip-replay 磁贴；无 +245% 前视主图 |
| 盘口检测 | ✅ | 即时 probe + 历史 HTML |
| 短周期 | ✅ | ETH 3m pilot 叙事 |
| 雷达 | ⚠️ | 规则雷达；叠层统一；池数据要「重新扫描」刷新 |

### C. 图表渲染（你最在意的视觉）

| 项 | 状态 | 合同 |
|----|------|------|
| 共享主题 | ✅ | `src/webapp/static/chart_theme.js` → `FableChart` |
| 蜡烛 | ✅ | 绿涨 `#059669` / 红跌 `#dc2626` |
| 做空叠层 | ✅ | 蓝↓ `入场 {价} 空`；止盈绿虚线下；止损红虚线上 |
| 接入 | ✅ | `app.js`（signals/explore）+ `scout_mtf_app.js`（radar） |
| 缓存 | ✅ | `?v=20260731kline1` / `arch1` |
| **肉眼终验** | ⏳ | 请你硬刷新后点 BABY 一笔对照 Claude 参考图 |

### D. 架构展示

| 层 | 看板应显示（API 现态） | 状态 |
|----|------------------------|------|
| L1 检测 | 实盘 **none**（无 owner_best）；旁路 **v10 纸面** | ✅ 架构板如实画 |
| L2 判断 | **ACTIVE** `…yolo_v11_reg…` · 阈 ≈0.0202 | ✅ |
| L3 执行 | 前向日志 + 纸面 · 门 30min · 不 promote | ✅ |
| 裁决 | n/100 · tip-replay PF 参考 | ✅ |

### E. 1–4 打包与外围

| 项 | 状态 |
|----|------|
| A2 maker 试跑脚本/账本隔离（无真下单） | ✅ 代码侧（见 pack commit / plan HTML） |
| research + USE_STOP 回 True | ✅ 在 pack 路径 |
| Pi 扩展冲突 / grok-4.5 | ⚠️ 部分；后改「先不用 pi」 |
| VPS Xray/VPN | 🚫 未完成；你说先不管 |
| **push origin** | 🚫 main **ahead 2**，大量本地未提交未 push |

---

## 三、仍未闭环（请你决策）

1. **硬刷新验收**  
   - `#signals` 叠层是否等于 Claude  
   - `#overview` 架构板是否清晰  
   - 全站 K 线是否一致  

2. **Bark**  
   - 写 `data/bark_config.json` 或设 `BARK_KEY`（否则通知只剩 TG/日志）  

3. **纸面 15min 循环**  
   - 本机 cron / 手动 loop，或 **VPS deploy + timer**（VPS 当前无 fable 树）  

4. **检测实盘**  
   - promote 验证过的 tip 检测器（**需 owner 点头**）；现状诚实空转 + v10 纸面旁路  

5. **Git**  
   - 工作区 **大量 M/??**（看板、probe_history、live_signals 图、archive…）  
   - 是否打包 commit / push：需你说一声（默认不 force-push）  

6. **雷达全量刷新**  
   - 打开 `#radar` →「重新扫描」（拉 OKX，1–3 min）  

7. **WINDOW=200 改窗实验**  
   - 未开；若要做必须单变量 + 报告，且不碰 holdout  

---

## 四、关键文件索引（本晚相关）

```
src/webapp/static/chart_theme.js      # K 线统一主题
src/webapp/static/app.js              # signals/explore/overview 架构
src/webapp/static/scout_mtf_app.js    # 雷达 K 线
src/webapp/static/clauseos.css        # 架构板视觉
src/webapp/static/index.html          # 总览 arch-board / 菜单
src/webapp/dashboard_payloads.py      # tip-replay 标记回填 short TP/SL
scripts/live_signal_tg.py             # v10 纸面
scripts/probe_history.py              # 历史盘口 HTML
docs/run_v10_paper_live.md            # 纸面操作说明
analysis/arch_overview_20260730.md    # 架构与研究结论（认知）
analysis/evening_checklist_20260730.md # 本文
```

---

## 五、5 分钟自检脚本（可选）

```bash
# 1) 前端资源版本
curl -s http://127.0.0.1:8642/ | grep -E 'app.js|chart_theme|clauseos|arch-board'

# 2) 层状态
curl -s http://127.0.0.1:8642/api/status-strip | python3 -m json.tool | head -80

# 3) 纸面
curl -s http://127.0.0.1:8642/api/live-paper | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('available'), d.get('n_fresh'), d.get('n_fired'))"

# 4) 信号 short 价
curl -s 'http://127.0.0.1:8642/api/chart/okx/BABY_USDT_SWAP?bars=3000' | python3 -c "
import sys,json
ms=json.load(sys.stdin).get('markers') or []
m=next((x for x in ms if x.get('traded') and x.get('entry_price')), None)
print({k:m.get(k) for k in ['entry_price','tp_price','sl_price','side','outcome']} if m else 'no marker')
"
```

**期望**：`det exists=false` 但纸面有数；判断 ACTIVE 含 `v11_reg`；BABY 满足 `tp < entry < sl`；HTML 含 `arch-board` 与 `chart_theme.js`。

---

## 六、一句话结论

| 类别 | 结论 |
|------|------|
| 看板数据诚实 / tip-replay 主线 | **已处理** |
| Claude 式 K 线 + 全站统一 | **代码已处理，待你硬刷新确认** |
| 总览架构与版本 | **已处理** |
| v10 纸面扫描展示 | **已处理** |
| TG/Bark 真推 + 15min×100 | **缺 Bark 配置与/或 VPS 部署** |
| 检测实盘开火 | **未挂权重，诚实空转** |
| Git 提交推送 | **未完成，需 owner** |

**没有「假装做完」的项**：凡标 ⚠️/🚫 都写了卡点；标 ⏳ 的只差你浏览器一眼。

---

生成：`analysis/evening_checklist_20260730.md` · 会话 2026-07-30 晚 → 07-31 凌晨  
若你勾完「硬刷新三页」且要提交代码，再说一声我按铁律整理 commit 清单（仍不 promote / 不 push 除非你明确要）。
