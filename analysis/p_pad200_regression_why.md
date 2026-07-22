# 为什么「昨天修过 stem」v13 还是错窗 — 2026-07-22

**一句话**：Owner 没记错。昨天修的是 **preview 路径**（stem→窗用 MAD，多数非 okx 定为 `end_incl`）；**建 v13 全量时同晚故意关了 MAD**，bulk 盲用 `end_incl`，把 `okx_*`（实为窗起点）切错。

---

## 时间线（commit / 日志）

| 何时 | 证据 | 发生了什么 |
|---|---|---|
| 7/21 ~23:05 | `9f81bd9` / `44aca7c` | 修「stem 当窗起点」→ 优先 `end_incl`；learning `stem-index-is-window-end-not-start.md`。**preview 永远喂存档 PNG → MAD 消歧**。 |
| 7/21 23:00 | `logs/v13_pad200_pipeline.log` L1+ | 第一次 pipeline：**MAD 开着**（满屏 `SKIP high MAD ADA_...`）。 |
| 7/21 23:08 | 同 log：`===== RESTART no-MAD =====` | **人为重启、关掉 MAD**（省 16GB / 少 skip）。 |
| 7/21 23:08–23:13 | log：`mad_gate=False`；`pad200_summary.json` | 全量 bulk 完成：`mad_gate: false`，`win_index_default: end_incl`。 |
| 7/21 23:25 | `d2b2286` | 把 `--mad-gate` 做成 **opt-in、默认关**；pipeline 注释写明「No --mad-gate: end_incl + close-corr only」。 |
| 7/22 | Owner 抽查「框不对」→ `bdde170` | 审计：`okx_*` 实为 `start`；关 MAD = 把 start 当 end。MAD **改回默认开**。 |

预览对照（昨天「修好」的那批，无 okx_）：`analysis/output/pad200_try/README_pad200.txt` 全是 `mode=end_incl mad=0.0`——那条路径一直对。

---

## 昨天到底修了什么

- **默认语义**：`resolve_win_start` 候选序改成 `end_incl` 优先（修「把窗末当窗起点」）。
- **消歧**：有存档图时用像素 MAD（preview / 初版 bulk 都传 PNG）。
- **文档**：`docs/learnings/stem-index-is-window-end-not-start.md`。
- **没有**修成「全库只有 end_incl」——v11 本来就是混合约定；只是预览样本碰巧几乎全是非 `okx_*`。

---

## v13 数据集是哪次命令建的

落盘摘要（磁盘事实）：

```json
"generated_at": "2026-07-21T15:13:32+00:00",
"mad_gate": false,
"win_index_default": "end_incl"
```

管道日志：

```
===== RESTART no-MAD Tue Jul 21 23:08:01 =====
bulk start resume=False mad_gate=False ...
bulk start resume=True  mad_gate=False already_ok=1706 ...
```

→ **显式关门**（当时 CLI 是 `--mad-gate` opt-in；不传 = false）。谁关的：同晚为 16GB 存活改的 pipeline / `d2b2286`（注释原文：*keep MAD disambiguation off by default so 16GB machines survive*）。

---

## 是否「只修了某条路径」

是。

| 路径 | 行为 | 结果 |
|---|---|---|
| `--preview` / 手工对照 | 始终 `stored_img` → MAD | 昨天修完后看起来对 |
| bulk（v13 pipeline） | `orig = img if mad_gate else None` → **None** | 盲取 `cands[0]=end_incl` |
| 无 MAD 时 | 无第二道断言挡 `okx_*`+`end_incl` | ~31% 正样本（1228 okx）框罩错 K 线 |

close-corr≥0.999 **挡不住**：错窗内部仍自洽。

---

## 责任点（一句话）

**预览把「end 当 start」修对了；bulk 为省 RAM 关掉唯一能区分混合 stem 的 MAD 门，把 `okx_*` start 窗一律按 end_incl 切，毒死了 v13 训练集。**

代码已在 `bdde170`/`8227c82` 把 MAD 默认开回 + 无 MAD 时 skip `okx_*`+`end_incl`。**未**重建数据集、**未**重训。详见 `analysis/p_pad200_cut_audit.md`。
