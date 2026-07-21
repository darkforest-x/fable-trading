# YOLO「bar 内实时推理」路线图 — 2026-07-21

> 只读分析 + 可入库报告。**未**改生产默认、**未**部署、**未**打断本机 v13 pad200 训练、
> **未**耗 holdout、**未** promote。成功标准仍是 tip 新鲜入账 / 前向 100 笔，不是 GitHub star。

## 结论先行

**真正卡 tip_fresh 的不是「推理引擎不够快」，而是「模型在无后文 tip 窗上贴边框出生率≈0」+「信号龄从 bar open_time 起算」的结构算术。**

三层「事后」叠在一起：

| 层 | 是什么 | 今日状态 | 对 tip_fresh 的贡献 |
|----|--------|----------|---------------------|
| L1 收盘等待 | 脉冲只吃**已收盘** 15m bar | 结构性；timer `:01/:16/:31/:46` | tip 开火时龄已 ≥15min |
| L2 多窗回看 | live ≤6 窗，框可映射到窗内任意 bar | 已缩到 6；`TIP_EDGE_BARS=2` 挡中段 | 挡事后入账，**不产生** tip 框 |
| L3 几何语义 | 框右缘落在「启动 bar」而非盘口 tip | v12 tip_hit 高、盘口 tip_fire≈0 | **主瓶颈**（见 `p_box_to_bar_lag` / tip-smoke） |

GitHub 候选里：**几乎没有一个解决 L3**。ChartScanAI / VLM-AutoYOLO / autotrain-yolo 是图检测或标注训练工具；Savant / DeepStream-Yolo / TensorRT 是视频流部署加速——本项目 VPS（多半 Linux + OKX 15m CSV，~2 核脉冲，未必有可用 NVIDIA DeepStream 栈）收益有限，且不治出生率。

**推荐路径（务实）**：

1. **今晚/本周**：不碰部署栈；等 v13 pad200 训完 → 本机 tip 窗对照（pad200 vs v12）→ 小样预览过 Owner 目视。  
2. **并行低成本 A′′**：可选 shadow tip-only **只压缩窗数**（CPU），不指望抬 tip_fire（已证伪）。  
3. **真正「bar 内」**：等检测层在收盘 tip 上稳定 tip_fire>0 后再试点 **B（未收盘 K 渲染）**；C（1m/5m 触发）排最后，且必须过 owner 批（判断层/新鲜度语义会变）。  
4. TensorRT / DeepStream：**暂缓**，除非先证明 VPS 有 CUDA 且 discover_wall 是唯一阻塞新鲜门的项（目前不是——最坏落账龄已 <30，缺的是框）。

---

## 1. 现状时序：信号年龄从哪算起

### 1.1 定义（三门同值 30min）

| 门 | 位置 | 年龄定义 |
|----|------|----------|
| 执行器 | `ExecutorConfig.max_signal_age_min=30` | `now - signal_time`，`signal_time` = 映射到的 bar 的 **`open_time`** |
| TG | `forward.py` 硬编码 30 | 同上，只推 `status=open` |
| 看板裁决 | `FRESH_DETECT_MIN=30` | `detected_at - signal_time`（lag） |

来源与推导：`docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`。

**要点**：龄从 bar **开盘**起算，不是收盘。一根刚收完的 tip bar，开盘已过去 **15min**，再加脉冲对齐 + 扫描，tip 路径落账目标是 **16–23min**（07-20 盘口当场入账之后）。

### 1.2 检测最早能在何时开火

```text
15m bar 开盘 ──(形成中)── 收盘 ── :01 脉冲 ── update_okx ── discover(YOLO) ── phase2(LGBM) ── 入账/TG ── executor
                 ↑              ↑                                    ↑
              今日无推理      数据才有 OHLC                      tip 待入场可当场写账本
```

| 时刻 | 能否检测 | 备注 |
|------|----------|------|
| bar 进行中（未收盘） | **否**（生产） | `iter_series` / fetch 只给完整 bar；无「当前未完成 K」路径 |
| 收盘后 ~1min（`:01` 等） | **最早生产开火点** | timer `OnCalendar=*:01,16,31,46` |
| discover 结束 | 候选索引就绪 | 实测 discover_wall ~500s（~8min）量级；瓶颈 YOLO 前向 ~0.24s/窗 × 窗数 |
| tip 映射到最新收盘 bar | 可当场入账 | entry 代理 + 下脉冲回填（07-20 tip realtime path） |

**最早可交易 tip**：信号 bar 收盘后约 **1 + update + discover + phase2** 分钟；设计预算内最坏 ~26min < 30 门。

### 1.3 「事后」卡在哪几层

1. **收盘等待（结构性）**  
   不用未完成 K → 信号龄底座 +15min。这是「bar 收盘后脉冲」语义，不是 bug。

2. **多窗回看（工程层，已止血）**  
   live：tip + tip−1 + tip−2 + stride，≤6 窗。框经 `right_edge_to_bar` 可落到窗内旧 bar。  
   `TIP_EDGE_BARS=2`：只收 `bar_in_win ≥ window-2`。KORU/EDEN 类中段框不再进账本——**过滤≠产生 tip**。

3. **映射旧 bar / 几何语义（主因）**  
   模型常框「带后文的启动区」，右缘停在 tip−k。强制 tip 扫描 27 币仍 **0/27** 开火（`p_tip_only_smoke.md`）。  
   离线 tip_hit≠盘口 tip_fire。

4. **（已消）等入场 bar**  
   旧 tip/full 要求 `entry_i` 存在会再挡 15min；07-20 已改为 tip 当场入账 + merge 回填。

延迟预算表（当前，三门必须对齐此表）：

| 项 | 分钟 | 说明 |
|----|------|------|
| bar 开盘→收盘 | 15 | 龄底座 |
| 脉冲对齐 | ≤1 | `:01` 相对收盘 |
| update + discover + phase2 | ≤7–10 | 实测 ~10min 墙钟，预算按 ≤7–10 |
| 余量 | ~2–5 | → 门 = **30** |
| ~~等下一根入场~~ | ~~+15~~ | 已消（tip path） |

改门必须附新预算表且三处同改。

---

## 2. GitHub 候选逐个评估

评估轴：延迟 / 图检测 / 部署 · 许可证 · VPS 落地 · 抄/不抄。

| 仓库 | 解决哪一块 | 许可证 | VPS 落地 | 抄什么 | 不抄什么 |
|------|------------|--------|----------|--------|----------|
| **ChartScanAI** ([Omar-Karimov](https://github.com/Omar-Karimov/ChartScanAI)) | **图检测 demo**：K 线图 → YOLOv8 Buy/Sell；Streamlit「实时分析」= 拉数画图再 infer，非 bar 内流式 | MIT | 可跑 demo，但标签语义是通用买卖形态，**≠本项目密集启动框**；会污染金标 | 「CSV→渲染→YOLO」故事与我们同构——**我们已有更严的因果渲染** | 其权重/BuySell 类、Roboflow 杂标、增强默认、当检测主线 |
| **autotrain-yolo** ([MacroMan5](https://github.com/MacroMan5/autotrain-yolo)) | **训练运维**：数据集校验、HP tune、CVAT active learning、ONNX export | MIT | 本机 Mac 训练可参考流程；与 VPS 脉冲无关 | 实验记账 / export 检查清单思路 | 自动改增强；绕过本仓 `train.py` 纪律；把 HP 扫进脉冲 |
| **VLM-AutoYOLO** ([Somnusochi](https://github.com/Somnusochi/VLM-AutoYOLO)) | **标注/训练平台**：VLM 自动标框 → SAM → YOLO；**不解决实盘延迟** | **AGPL-3.0** | 重依赖（LocateAnything-3B、PG、前端）；AGPL 牵连发布面；VPS 不适合 | 「难例人工过目」流程可对照 v13 tip 预览 | 整仓接入；用 VLM 自动标替代 owner 金标语义；AGPL 进主线依赖 |
| **YOLOv8-TensorRT** ([triple-mu](https://github.com/triple-mu/YOLOv8-TensorRT)) | **部署加速**：ONNX→TRT，降 GPU 前向 ms | MIT | **需要 NVIDIA + TensorRT**；VPS 若无 CUDA/TRT 则零收益；有 GPU 可压 discover 里的 predict | 将来若 VPS 有 GPU：`export engine` + 单进程 batch 对照表 | 未证实 GPU 前先拆管道；指望 TRT 抬 tip_fire；DeepStream 插件当默认 |
| **Savant** ([insight-platform](https://github.com/insight-platform/Savant)) | **视频流 CV 框架**（DeepStream 上层） | Apache-2.0 | 强依赖 DeepStream/NVIDIA；输入是 RTSP/视频帧，**不是 344 路 OKX CSV 渲染图** | 无（架构错配） | 整框架；把 K 线脉冲改成视频 pipeline |
| **DeepStream-Yolo** ([marcoslucianops](https://github.com/marcoslucianops/DeepStream-Yolo)) | DeepStream 上跑 YOLO 的 **nvinfer 配置/parser** | MIT | 同上：要 DeepStream SDK + GPU；解决摄像头吞吐，不解决 tip 几何 | 无（除非未来专用 GPU 推理机） | 当本仓检测层；往 forward 脉冲塞 DS |

**一句话取舍**：候选多在「训得快 / 推得快 / 视频流」。本项目缺口是 **「无后文 tip 上有没有贴边框」**（训练分布 + 入账几何），不是 star 榜上的部署栈。

---

## 3. 「bar 内实时」可选架构（人话 + 利弊）

### A. 仍等收盘，tip-only + 更快推理（TensorRT 等）

**做法**：收盘后脉冲不变；`FABLE_YOLO_MODE=tip`（1 窗/币）和/或 TRT/半精度压 predict。

| 利 | 弊 |
|----|----|
| 实现小；tip-only 已有开关；不改新鲜度语义 | tip-smoke：**不抬 tip_fire**；只省 CPU（6→1 窗） |
| TRT 在有 GPU 时缩短 discover_wall | 无 GPU = 无效；有 GPU 也只压缩脉冲尾，龄底座仍 15min |
| 不碰判断层特征定义 | 永久 tip-only 已建议否（`p_tip_only_smoke`） |

**与三门/判断层/执行器**：门可维持 30（或 discover 明显下降后再议收紧，须新预算表）。判断层特征仍基于**已收盘**信号 bar——对齐。执行器仍按 `signal_time` 龄。

**定位**：脉冲预算保险阀 / 可选 shadow；**不是**「bar 内实时」本体。

### B. 未收盘 bar 上渲染「当前未完成 K」，盘中多次推理（真 bar 内）

**做法**：bar 内每 N 分钟（如 3–5min）拉 forming candle（OKX 未完成 15m 或 1m 合成），右缘=「正在走的 tip」，渲染 tip 窗 → YOLO → 过阈则**预警或试入账**；收盘后再确认/改价。

| 利 | 弊 |
|----|----|
| 唯一真正缩短「等收盘」的路径；龄可从 <15min 起 | **分布漂移**：训练全是完整 K；forming OHLC 抖动 → 假火/漏火 |
| 可与 tip 贴边语义一致（右缘永远是「现在」） | 特征/ATR/密集度是否用未收盘值？判断层未训过 → 分数不可信 |
| | 脉冲预算：盘中多轮 ×344 币易炸 <15min 铁律；需缩宇宙或 tip-only 子集 |
| | 新鲜度门语义要重写预算（可能要从 close 起算或改 signal_time 定义）— **须 owner 批** |
| | 执行：未收盘开火 = 入场价代理更噪；与 maker 回填假设冲突 |

**对齐**：

- 三门：若 `signal_time` 仍用该 15m 的 open，bar 内开火会让龄更小（更容易过门）——但也可能过早入场，**与回测「下一根开盘入场」不对齐**。  
- 判断层：至少要「收盘确认再开仓」或单独 forming 特征集（大实验，单变量难）。  
- 执行器：建议 **检测可 bar 内、开仓仍等收盘确认**（两段门），否则真金路径与 val 脱节。

**定位**：检测层 tip_fire 治好之后的 **第二阶段试点**；今晚不做。

### C. 更短周期（1m/5m）只做触发，15m 确认

**做法**：1m/5m 规则或小 YOLO 喊「可能启动」→ 只扫相关币的 15m tip 窗确认 → 过判断层再执行。

| 利 | 弊 |
|----|----|
| 缩扫描宇宙，省 discover；可能更早「盯上」形态 | 多周期标签/无前视极易泄漏；P1.5 曾证伪部分 5m 扩张 |
| 与「辅助触发」故事清晰 | 两套检测阈值；脉冲里加扫描窗违反「脉冲预算 <15min / 禁止塞实验」除非独立 timer |
| | 成功标准仍是 15m tip 新鲜——触发层不进裁决分子，但会占工程带宽 |

**对齐**：15m 确认后的 `signal_time`/三门/判断层可保持现状；触发层必须 **旁路影子**，禁止写主 `forward_log` 除非 owner 批。

**定位**：排在 B 之后或并行影子；**不要**进主脉冲。

### 对照小结

| 方案 | 压缩什么 | 治 tip 出生率？ | 今晚值不值得 |
|------|----------|-----------------|--------------|
| A | 脉冲墙钟 | 否 | 可选 tip-only shadow；TRT 等 GPU 证据 |
| B | 收盘等待 | 间接（右缘=现在）但分布风险大 | 否（等 v13 tip 证据） |
| C | 扫描宇宙 | 否（只触发） | 否（独立影子，勿进脉冲） |
| **v13 / 真 tip 金标** | L3 几何 | **是（主路径）** | **是（等训完验证）** |

---

## 4. 推荐执行顺序（务实，不大而全）

### 今晚（不打断 v13）

1. **不动** v13 pad200 训练进程；不改 VPS 默认；不 promote。  
2. 读完本报告 + 确认 tip-smoke / A′ 结论仍成立。  
3. 若有余力：只准备 **训完后的对照清单**（命令见下），不跑重扫描。

### 本周（v13 训完后，单变量）

1. **本机**用同一 tip 窗协议对比 `owner_v13_pad200` vs `owner_best`(v12)：强制 tip 扫描 / tip_edge 后开火率（**不**动 holdout；不自动切主线）。  
2. 按 `analysis/p_v13_real_tip_collect_plan.md` 出 **带框预览小样** → Owner 目视。  
3. 仅当 tip 窗贴边开火率有肉眼级改善，再议：影子权重上 VPS / 是否缩短 live 窗数（A）。  
4. **禁止**：为「感觉更快」上 DeepStream/Savant；禁止把 forming-bar（B）直接接执行器。

### 训完后最小验证命令（草案，待跑）

```bash
# 本机；权重换成 v13 best；勿 --eval-holdout
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
  --weights models/owner_v13_pad200/weights/best.pt \
  --out analysis/output/diag_tip_smoke_v13.json
```

成功标准（检测层）：强制 tip 扫描开火率相对 v12 **明显 >0**，且过 `TIP_EDGE_BARS=2`；再谈前向新鲜分子。  
确认级：仍是 VPS 前向新鲜 100 笔——**不**用 val/accept PF 替代。

### 明确延后

| 项 | 条件 |
|----|------|
| TensorRT / Ultralytics engine | VPS `torch.cuda.is_available()` 为真且 discover_wall 成新鲜门瓶颈 |
| Savant / DeepStream-Yolo | 专用 NVIDIA 推理机 + 视频输入场景（本仓无） |
| B forming-bar 开仓 | tip_fire 已正 + owner 批新鲜度重预算 + 开仓仍收盘确认 |
| C 短周期触发进主脉冲 | 永不默认；最多独立 timer 影子 |

---

## 5. 风险与诚实声明

- 本报告**未**在 VPS 上实测 GPU/DeepStream 有无；落地判断基于 HANDOFF「~2 核」、脉冲 CPU 叙事与代码「有 CUDA 才用」。若 Owner 确认 VPS 有可用 GPU，A 的 TRT 项可上调优先级，仍次于 tip 出生率。  
- v13 pad200 是分布实验（右缘 padding），**可能**改善 tip 几何，**不保证**；未验证前不切主线。  
- 「bar 内实时」若定义为 forming candle 开火，会与现有 30min 预算、maker 入场假设、判断层特征因果同时冲突——必须当**新实验**，不能当配置开关。  
- 未消耗 holdout；未改阈值/TP·SL/三门。

---

## 6. 下一步选项（标 Owner 决策）

| # | 选项 | 需 Owner？ |
|---|------|------------|
| 1 | v13 训完后只做 tip-smoke 对照 + 预览，不切主线 | 否（默认） |
| 2 | v13 改善明显后影子权重上 VPS | **是**（不自动 promote） |
| 3 | 主线永久 tip-only | **是**；报告建议 **否** |
| 4 | 试点 B：盘中 forming 检测、收盘才开仓 | **是**（新鲜度预算 + 执行语义） |
| 5 | 收紧三门（如 30→25）因脉冲变快 | **是**（须新延迟预算表三处同改） |
| 6 | 引入 TRT/DeepStream | **是**；先证明 GPU 与瓶颈 |

---

## 参考

- `HANDOFF.md`（07-20 tip path / 三门 30 / 脉冲耗时；07-21 A′）  
- `analysis/p_box_to_bar_lag.md`、`analysis/p_tip_only_smoke.md`、`analysis/p_v13_real_tip_collect_plan.md`  
- `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`  
- `docs/learnings/tip-only-scan-does-not-raise-tip-birth-rate.md`  
- `docs/learnings/tip-rows-record-first-backfill-entry-later.md`  
- `deploy/fable-forward.timer`、`src/judgment/yolo_candidates.py`、`src/judgment/forward_scan.py`
