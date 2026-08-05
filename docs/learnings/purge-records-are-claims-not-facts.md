# 清除记录是主张，不是事实——资产盘点必须逐机核查

- **问题**：owner 问 07-19 主线用的模型还在不在。CLAUDE.md 纪律 12 与
  HANDOFF.md 都白纸黑字写着「pre-v16 检测器权重**三机全删**(Mac/VPS/Windows,
  含现役 v12 与回滚备份;仅存 COCO yolo11 底座)」，据此回答「全没了」是最省事的。

- **死胡同**：
  1. 只查 Mac 的 `models/` 和 `runs/`，看到没有 v11 目录就准备结案——但同一次
     `find` 里 `owner_v12_htip/weights/best.pt`（19MB, 7-20）明明在。记录说 v12
     也删了，实物却在手边。**这个矛盾当时就该触发全面核查，我却先把它当成边角
     异常报了一句了事。**
  2. 查 git：`*.pt` 在 `.gitignore` 第 7 行，从未入库，恢复无门——这条是真的，
     但它只证明「git 里没有」，不证明「世界上没有」。
  3. 查 Time Machine（`No destinations configured`）、APFS 本地快照（空）、
     废纸篓（空）——三条全空，此时「全没了」的结论已经有四条证据支撑，
     **而它仍然是错的**。

- **有效路径**：`ping` 一下 3060（192.168.1.2）发现在线，`ssh` 上去
  `Get-ChildItem -Recurse -Filter *.pt`。**59 个权重完好无损**，包括
  v7_chain / v8_chain / v8_coco / v8_star3 / v9 四版 / v10_chain / v10_coco /
  v14_pad200 / v15_tipval / v16_tipuni_cold / short_star v6–v10 / side_short 系列。
  「三机全删」实际只删了两机。关键判断：**当记录与眼前的一个实物冲突时，
  要重验的是记录，不是把实物当例外。**

- **通用规则**：回答「X 还在吗」永远是一次**跨机核查**，不是一次文档查询。
  顺序：本机实物 → git → 本机备份（TM/快照/废纸篓）→ **其他机器**。
  最后一步最容易漏，因为它需要离开当前 shell。清除类操作的记录只说明
  「当时打算删」，不说明「三台都删成了」——多机删除没有事务性，
  一台离线/一个路径没匹配上，记录就与事实分叉，且不会有人发现。

- **牵连**：`CLAUDE.md` 纪律 12 与 `HANDOFF.md` 2026-07-23 节的「三机全删」
  表述已与事实不符（见 [[weights-live-only-where-they-were-trained]] 解释
  为什么偏偏 v11 是真没了）。3060 = `zzc@192.168.1.2`，`C:\fable`，PowerShell
  不是 cmd（`dir /b` 报 UNC 错，要用 `Get-ChildItem`）。
