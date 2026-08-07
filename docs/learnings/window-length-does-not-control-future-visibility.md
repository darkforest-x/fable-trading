# 缩短窗口不等于因果化——决定看得见多少未来的是窗口右端落在哪里

**日期**：2026-08-07
**类型**：审计/反直觉结论（P0 局部信号 V2 交接规范审计时发现）

- **问题**：2026-08-07 owner 协议把训练图从 200 根收缩到 20–30 根，围绕旧框中心取
  mid±half 的小框，窗口起点随机。直觉上"局部窗 = 只看信号附近 = 更接近盘口"。
  实测 `datasets/dense_owner_w20_midbox` 的 2635 个正样本：
  **95.33% 的窗口右端晚于 decision bar，未来 K 中位 9 根、最多 25 根**。
  一张 24 根的图里，信号后可见的 K 和信号前一样多（左侧上下文中位也是 9 根）。

- **死胡同**：构建脚本、`w20_summary.json`、验收 `MORNING_README.md` 三处产物都看不出这件事。
  脚本记了 `win_start` / `win_len` / `small_bars`，**每个字段都对，组合起来的那个量没人算**。
  验收报的是 val F1 0.403 / mAP50 0.2812 + "PASS"——一个 Stage A 数字，
  被放在了本该由盘口口径回答的位置。同期唯一的 tip smoke 是 PF 0.266 / 匹配 lift −241bp（n=11）。
  从 200 缩到 24，未来可见量从中位 97 根（499 ⭐标杆的量法）降到中位 9 根，
  **降了一个数量级，但离 0 还差 9 根**，而 2026-08-05 的依赖曲线正说明 0 根和 20 根之间
  差着 10% vs 39% 的复现率——恰好是这 9 根所在的区间。

- **有效路径**：把规范里那句 `visible_end_bar <= decision_bar` 从散文变成一个可跑的门。
  实现只有一行算术：`future = (win_start + win_len - 1) - (anchor + confirm_delay)`。
  真正的判断在于**先确认字段映射**——`mid_global` 就是 anchor、`half` 就是 confirm_delay、
  框右边界恒等于 decision bar——映射一旦钉死，违规量是算出来的，不是看出来的。
  成因也随之定位到构建脚本一处：窗口起点只约束"小框完整落在窗内"
  （`w0_lo = s1 - win_len + 1`），从来没约束过窗口右端。

- **通用规则**：**问"这张图的最后一根 K 是哪一根"，不要问"这张图有多长"。**
  窗口长度是视野宽度，窗口右端才是时间戳。任何以"缩小/局部化/聚焦"为名的改动，
  第一步先算 `visible_end - decision` 的分布，再看指标；分布不是 0 就只能叫 Stage A，
  它的 val 数字不能进验收表。推论：数据集构建脚本的收尾必须调用不变量审计，
  不通过就不落盘——否则"已实现"会被读成"已验证"。

- **牵连**：`scripts/build_w20_midbox_dataset.py:245-250`（窗口起点采样）、
  `scripts/audit_w20_midbox_causality.py`（本轮新增的门）、
  `tests/test_w20_midbox_causality.py`（27 passed，钉死边界：窗口结束在 decision 当根算 causal）、
  `analysis/p0_local_signal_v2_audit_20260807.md`、
  `reports/future_dependency_report_20260805.md`（0/20/99 根未来 → 10%/39%/62% 复现率）。
  同一枚硬币的推理侧：[[full-mode-causality-is-behavioral-not-structural]]。
  监督目标本身的未来依赖：[[zero-live-edge-labels-means-the-target-is-unverified]]。
  铁律 12（检测只认盘口）与规范 Stage A 的冲突需 owner 裁决，见报告 §7 C-1。
