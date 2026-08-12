# yoyo-trading — Dataset V3 Gold Core 训练前验收

生成日期：2026-08-12

协议：`yoyo_dataset_v3_gold_core_v1_20260812`

方向：仅 SHORT · 类别：`ma_dense_core` · 窗口：W12–19 动态 center-crop

状态：**数据集已构建、机器质量门全过；两道人工门未过（一致率/kappa、错标率抽查），
训练臂被 3060 阻塞。未训练、未 promote、未读 holdout。**

## 结论先行（Owner 八问）

| # | 问题 | 回答 |
|---|---|---|
| 1 | 旧模型哪些继续用了 | R1 `029f80a5…` 作 R3A 初始化与全部对照基线；R2 `52cd38fd…` 只作对照；Stage A `c0e94f47…` 保留为血统起点；官方 `yolo11s.pt` `85a76fe8…` 作 R3B 冷启 |
| 2 | Dataset V3 修掉了哪些错标 | 916 个 Owner-long 镜像不再当负例；1,370 个从未被人看过的模型挖掘硬负例被移出；100 个与金标框重叠的审核事件被隔离（其中 15 个是 Owner-NO，本来会压在金标正例上） |
| 3 | 正/负/ignore 和金框数量 | 正 1,345（train 1,143 / val 202）、负 2,108（train 1,908 / val 200，含 765 硬负例）、ignore 池 2,411；**human_gold 框 0**、rule_verified 1,345、model_proposed 0 进训练 |
| 4 | 数据审核一致率是否达标 | **未知——这道门只有 Owner 能过**：需分层抽 10%–15% 盲重复审核算一致率与 kappa。审核包尚未生成（等你点头再做） |
| 5 | R3A 与 R3B 谁更好 | **未训练**：3060（192.168.1.4）ping 通但 SSH 无响应 |
| 6 | 连续错误候选从多少降到多少 | 尚无 R3 数字。冻结基线：baseline 732 → R1 331（am 快照）；R1 223 → R2 195（pm 快照） |
| 7 | 正样本召回是否保持 | 尚无 R3 数字。冻结基线：R1 val recall 0.7770、R2 0.7525、1:1 baseline 0.9024 |
| 8 | 模型是否可进入 L2 集成 | **否**。`production_eligible=false`，无 R3 结果 |

## 复现命令

```bash
cd /Users/zhangzc/yoyo-trading
PYTHONPATH=. /Users/zhangzc/fable-trading/.venv/bin/python tools/freeze_baseline.py
PYTHONPATH=. /Users/zhangzc/fable-trading/.venv/bin/python tools/smoke_parity.py --per-class 15
PYTHONPATH=. /Users/zhangzc/fable-trading/.venv/bin/python tools/audit_legacy_labels.py
PYTHONPATH=. /Users/zhangzc/fable-trading/.venv/bin/python tools/build_dataset_v3.py \
  --builder-commit "$(git rev-parse --short HEAD)"
PYTHONPATH=. /Users/zhangzc/fable-trading/.venv/bin/python -m pytest tests -q
```

测试：**205 passed**（yoyo-trading）。fable-trading 侧未改动，仍 709 passed / 2 skipped。

## 1. 冻结基线（先冻结，再动数据）

`manifests/frozen_baseline_r1_r2_v1.json`，SHA `2258566017153390…`。

| 模型 | 权重 SHA | val P / R / mAP50 | am canary 事件 | pm canary 事件 |
|---|---|---|---:|---:|
| 1:1 baseline | `da278820…` | .8467 / .9024 / .9206 | 732 | — |
| **R1** | `029f80a5…` | .8626 / .7770 / .8980 | 331 | 223 |
| **R2** | `52cd38fd…` | .8475 / .7525 / .8774 | — | 195 |

canary 契约（两快照一致）：conf 0.25 / NMS 0.7 / event_gap 5 bars / W12–19 / 215 币 /
pre-holdout。冻结工具会拒绝冻结跨契约的比较。

R1 训练配方（R3A/R3B 必须逐项相同）：40 epoch / patience 10 / batch 8 / imgsz 960 /
AdamW / rect / seed 0；增强全 0（translate 0.02、scale 0.1 为原值）。

## 2. Smoke parity：本仓能逐字节复现旧数据集

| 项 | 结果 |
|---|---:|
| 抽样样本 | 45（15 正 / 4 易负 / 26 硬负） |
| 图片 SHA256 完全一致 | 45/45 |
| 标签 SHA256 完全一致 | 45/45 |
| YOLO 框数值不一致 | 0 |

后续整库构建又把这个证明扩大到 **2,688 个复用样本、0 个 SHA 漂移**。
"复用旧数据"因此不是声明，是可验证的事实。

## 3. 旧标签迁移表

`manifests/legacy_label_migration_v3.json`。

| 群体 | n | KEEP_POSITIVE | KEEP_NEGATIVE | MOVE_TO_IGNORE | REBOX_REQUIRED | RELABEL_REQUIRED |
|---|---:|---:|---:|---:|---:|---:|
| 旧正例 | 1,345 | 1,345 | — | — | — | — |
| 旧易负例 | 1,343 | — | 1,343 | — | — | — |
| 旧硬负例 | 2,286 | — | — | 916 | — | 1,370 |
| Owner 审核事件 | 1,200 | — | 765 | 200 | 235 | — |

三条关键判断：

1. **916 个 Owner-long 镜像 → IGNORE。** 它们是 Owner 亲手画、亲口判为"多头"的真实结构。
   按 `docs/learnings/unconfirmed-mirror-is-neither-positive-nor-negative.md`，未确认的镜像
   既不进正例也不进负例。R1 把它们当负例训练，等于教检测器"这个密集不是密集"。
2. **1,370 个模型挖掘硬负例 → RELABEL_REQUIRED（移出训练）。** 按 (symbol, 时间重叠) 联结，
   只有 15 个能对上 Owner 的 NO、2 个对上 Owner 的 YES，其余 1,353 个从未被人看过。
   V5 §6.4 要求硬负例必须是 **shape NO**，未审样本无法认证。
3. **765 个 Owner 确认的 shape-NO → 新硬负例。** 来自五个审核包，覆盖 190 个币，
   全部落在 train 时间段内、且不与任何金标框（含 12 根护栏）重叠。

被隔离的 200 个审核事件：100 个 decision 落在冻结 val 期（val 必须逐字节不动），
100 个与金标框重叠（85 个是 YES——模型重新检出了已知金标，是好现象；15 个是 NO——
Owner 在含金标框的窗口上说了 NO，属真实分歧，不能当负例）。

**注意**：审核包自带的 `touches_owner_box_guard` 对 1,200 条全是 false，是从正例 manifest
重建框区间后才抓出这 100 个重叠。**不要信任上游的护栏标记。**

## 4. shape verdict vs outcome verdict（V5 §6.2）

**六个审核包全部带 48 根未来对照图，所以 1,200 条裁决没有一条是纯因果的。**
能量化的只有"裁决是否与后市一致"（decision 后 16 根 close-to-close）：

| 裁决 | 与后市一致 | 与后市相反 |
|---|---:|---:|
| YES（329） | 281 | **48** |
| NO（869） | 510 | **359** |

"相反"的那 407 条是可以确信为形态判断的——Owner 在后市打脸的情况下仍坚持了裁决。
"一致"的不能反推为 outcome-driven：整个候选池本身就向下漂（16 根中位 −14.1bp），
一致是基率使然。**因此本轮不把 outcome 一致性当作标签权重，只作为记录。**

## 5. Dataset V3 组成

`manifests/dataset_v3_gold_core_v1.json`，manifest SHA `db9ca3eef7b1900b…`。

| 项 | train | val | 合计 |
|---|---:|---:|---:|
| 正例 | 1,143 | 202 | 1,345 |
| 易负例 | 1,143 | 200 | 1,343 |
| 硬负例（Owner shape-NO） | 765 | 0 | 765 |
| **负例合计** | **1,908** | **200** | **2,108** |
| 负正比 | **1.67 : 1** | — | — |

- 与 R1 相比：正例、窗口协议、renderer、**整个 val split 逐字节不变**；唯一变量是训练负例；
- 负正比 1.67:1 落在合同要求的 1:1 ～ 1:2 内（R1 是 1:3，且其中 2,286 个硬负例未经确认）；
- 框状态：`rule_verified` 1,345、`model_proposed` 进入训练 **0**、`human_gold` **0**
  （Owner 原框经中心截取规则派生，不是原框本身——所以诚实标为 rule_verified）；
- 235 个 Owner-YES 事件**没有**变成正例：类别已确认、几何仍是模型提议，等 Owner 审框。

### 覆盖度

| 轴 | 分布 |
|---|---|
| 币种 | 215（负例 214、硬负例 190） |
| 月份 | 2025-06 ~ 2026-05 共 12 个月，最少 192、最多 483 |
| 窗口长度 | W12 639 / W13 787 / W14 591 / W15 374 / W16 469 / W17 245 / W18 125 / W19 223 |
| 因果纵向跨度 | <1% 101 / 1–2% 784 / 2–4% 1,456 / ≥4% 1,112 |
| **正例核心水平位置** | **middle 1,345、left 0、right 0** |

最后一行是本轮最该盯住的结构性缺陷：center-crop 让 100% 正例的核心落在窗口中三分之一
（中位 0.577）。**位置 shortcut 本轮修不掉**（Owner 2026-08-12 裁定沿用 W12–19），
左/右召回必须在评估里照报，不得声称已解决。

### 机器质量门

| 门 | 结果 |
|---|---:|
| 复用样本逐字节复现 | 2,688 / 2,688 |
| SHA 漂移 | 0 |
| 重复窗口（symbol × win_start × win_end） | 0 |
| 跨 split 的 event_id | 0 |
| `visible_end_bar <= decision_bar` | 3,453 / 3,453 |
| `future_used_for_l1_label` | 全 false |
| val 中的硬负例 | 0（val 保持 R1 原样） |
| train 末 → val 首的 purge 间隔 | 162 根 15m bar（约 1 天 16.5 小时） |
| 最晚 decision 时间 | 2026-05-02 23:45 UTC |
| holdout 读取 | 0 |
| Owner-NO 事件解析失败 | 0 / 765 |

### 未过的门（只有 Owner 能过）

1. **重复审核一致率与 Cohen's kappa**：需分层抽 10%–15%（约 350–520 张）盲重复审核。
   审核包尚未生成，等你点头。
2. **每 split 的标签抽查错标率**：同上依赖。

按合同，这两道门不过就不能宣布数据集"准确"。当前只能说：**机器可验证的部分全部通过，
人工一致性未知。**

## 6. 阻塞

1. **3060 训练机**：`192.168.1.4` ping 通、SSH 无响应（本轮探测挂起 >25 分钟）。
   R3A/R3B 无算力。Mac MPS 跑完整 W12–19 训练历史上已放弃过一次，不作默认替代。
2. **人工一致性审核**：见上。

## 7. 风险与诚实声明

1. **正例一根未换。** V3 修的是负例。如果 Owner 的正例里本来就有错标，本轮不会发现它——
   一致性抽查正是为此设计的，而它还没做。
2. **框不是 human_gold。** 1,345 个框来自"取 Owner 原框中央一半、夹到 4–7 根"的规则。
   规则是 Owner 批准的，但框本身没有被逐个确认过。
3. **765 个新硬负例的窗口是重建的**：由 decision_time 反查全局 bar 下标再渲染，
   不是复制旧像素；它们没有 SHA 对照物，只能靠因果门与去重门保证。
4. **移除 1,370 个模型硬负例可能降低对某类背景的抑制。** 这是把"未经确认的负例"换成
   "经确认但更少的负例"的交换，代价要在连续回放里看，不能预先声称是改进。
5. **outcome 一致性统计受候选池向下漂影响**，不能当作 shape/outcome 的判定器。
6. 本轮没有训练、没有评估模型、没有 promote、没有部署、没有读 holdout。

## 8. 下一步（需 Owner 决策）

| 选项 | 内容 | 我的建议 |
|---|---|---|
| A | 开 3060（或授权替代算力），跑 R3A/R3B 快速筛选臂 | **最高优先**，其余都在等它 |
| B | 生成 10%–15% 盲重复审核包，你花 15–25 分钟过一遍，拿一致率与 kappa | **推荐**，这是"数据集准确"唯一缺的证据 |
| C | 审 235 个 Owner-YES 事件的框，把类别确认升级为几何确认，扩充正例 | 推荐，但排在 A/B 后 |
| D | 若你认为 916 个 Owner-long 镜像应当继续当负例，我按你的口径回滚这一条 | 需要你明确点头 |
| E | 把 1,370 个模型硬负例送进审核队列，逐步转成确认过的 shape-NO | 中期方案 |
