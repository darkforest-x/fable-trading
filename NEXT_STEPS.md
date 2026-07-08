# NEXT_STEPS — 2026-07-09 工单（写给 Codex / 任何接手的 agent）

先读 `AGENTS.md`（工作纪律，违反=返工），再读 `HANDOFF.md`（项目状态）。
本工单由前一个会话（Claude，2026-07-08 深夜）写就；一台 Mac 上有离线管道
（`scripts/offline_pipeline.sh`，日志 `logs/offline_run.log`）正在无人值守跑收尾。

**环境须知（踩过的坑，别再踩）：**
- 系统 `python3` 用于数据/训练以外的一切；**YOLO 相关必须用 `.venv/bin/python`**（torch 只装在那里）；
- datetime → epoch 秒一律用 Timedelta 除法，禁用 `astype(int64)//1e9`（pandas 版本差异会差 1000 倍，见 docs/learnings/pandas-datetime64-unit-portability.md）；
- OKX 请求要带浏览器 User-Agent（fetch_okx.py 已处理），限速 ≤8 req/s；
- 提交信息英文、正文汇报中文；每完成一步 git commit + push（远程 origin 已配好）。

---

## 第 0 步：确认离线管道的产出（必做，5 分钟）

```bash
tail -20 logs/offline_run.log
cat OFFLINE_RESULTS.md   # 若存在，说明管道跑完了
```

- **跑完了** → 直接进第 1 步；
- **中途死了** → 按日志判断死在哪个阶段，手动补跑该阶段（管道脚本里 5 个阶段的命令都是独立可执行的），补完再进第 1 步。

## 第 1 步：YOLO 验收判定（依据 OFFLINE_RESULTS.md 的 mAP50）

- **mAP50 ≥ 0.90**：写规则一致率脚本完成正式验收。规格：
  新建 `src/detection/consistency_check.py`——对 `datasets/dense_15m_full` 的 val split
  每张图，取 `src/detection/auto_label.py` 的规则框为真值，取 best.pt 预测
  （conf=0.30）为待检，IoU≥0.5 算匹配；输出逐框一致率（匹配框数 ÷ 规则框数）
  与误报率。一致率 ≥95% 且 mAP50 ≥0.90 → 在 `analysis/p2a_detection_report.md`
  末尾追加"正式验收通过"节（含复现命令与数字）。
- **mAP50 < 0.90（含 yolo11s 重训后仍不到）**：不再加大模型。在 p2a 报告追加
  "全量训练结果"节，如实记录封顶值与结论（"nano/small 容量下该任务收敛于 X"），
  标注正式验收未达成、非关键路径、暂停。**不要**为凑 0.90 去调 conf/IoU/增强。

## 第 2 步：合约复制性检验的判读（依据 OFFLINE_RESULTS.md 的表格）

判定标准（写死，不许放宽）：
- **复制成立** = tp5_sl2 在合约 val 上 perm_p < 0.01 **且** top-decile 净@maker0.06% > 0；
- 成立 → 在 `analysis/p2b_v3_barrier_sweep.md` 追加"合约宇宙复制性检验"节
  （表格 + 与现货结论的同向性说明），并把 HANDOFF 的"当前主线宇宙"改为 SWAP；
- 不成立 → **停**。在报告里如实记录，在 HANDOFF 标注"复制失败，待 owner 决策"，
  不许做任何为了让它通过的调参。

## 第 3 步：均线 20/60/120 对比实验（owner 悬而未决的问题，本工单核心增量）

背景：owner 三次提及策略应基于 SMA/EMA 20/60/120（共 6 条线），而现行流水线用
EMA 8/13/21/34/55+144/200。用实验裁决，**只做加法，不改现有模块**：

1. 新建 `src/judgment/candidates_v206.py`：计算 SMA20/60/120 + EMA20/60/120 六线；
   密集定义参照 `src/detection/auto_label.py` 已验证的规则起步：
   `fast_spread`=(SMA/EMA20/60 四线 max-min)/close ≤ 0.0028×1.6，
   `full_spread`=(六线 max-min)/close ≤ 0.0055×1.6，连续 ≥5 根（×1.6 对应 expanded 池
   的放宽系数）；其余门槛（volume_ratio、pre_range 等）从 candidates.py 原样复用；
2. 特征：能直接复用的（volume、atr、ret、drawdown 类）原样复用；均线相关特征
   （spread 系列、close_vs_ema 等）改为基于六线计算，特征名保持同构；
3. 标签 TP5/SL2 h72（`label_candidate(tp_mult=5, sl_mult=2)`），数据用 SWAP 宇宙
   （若第 2 步复制成立）否则现货；train.py 流程原样，**val only**；
4. 交付：`analysis/p2b_ma206_comparison.md`——同表对比 8-55 版 vs 20/60/120 版的
   候选数 / val AUC / perm_p / top-decile 毛利与净收益，明确写出哪套更强、差多少，
   结论交 owner 裁决。**不要自行替换主线**。

## 第 4 步：收尾

- 所有改动 commit + push；
- 更新 `HANDOFF.md`"当前状态一句话"和"未决队列"（完成的划掉，新发现的加上）；
- 若改了 `src/webapp`，跑 `bash scripts/deploy_vps.sh` 同步看板。

## 禁止事项（红线，AGENTS.md 的具体化）

- 禁止评估任何 holdout（`--eval-holdout` 不许出现）；
- 禁止对 2026-05-04 后的窗口做任何参数调优（已消耗两次）；
- 禁止重构/重命名现有模块、升级依赖、改 scheduled task、动 `.venv`；
- 结果不好 = 如实记录，不好看的数字也要写进报告。
