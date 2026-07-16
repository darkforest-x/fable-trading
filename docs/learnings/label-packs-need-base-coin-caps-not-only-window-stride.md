# 打标包去重不能只靠 window stride

- **问题**：round8 声称 stride=WINDOW 已「零重叠」，owner 仍感觉 chunk 里大量和 r6/r7「重复」、同币刷屏，无法高效打标。
- **死胡同**：
  1. 只查 **exact stem** / 同文件名 → 全 0，误判「没有重复」。
  2. 只查同 symbol 的 **bar 下标** → SWAP 与现货 stem 不可比，也是 0。
  3. 生成脚本只修了 r7 的 **窗内 75% 重叠**（stride 50→200），就以为「和历史也不会撞」——实际旧池虽多在 2025，但 **同一 base 币**（BTC/MON/ETH…）在 r7 已狂标，r8 再上合约图仍是同质劳动。
  4. 按 **不确定 conf** 从大池抽 2000 张、无每币上限 → 长序列币自然进包十几次（MON 18 窗）。
- **有效路径**：
  1. 用 **base 币**（剥 `_USDT`/`_SWAP`/尾部 bar）统计历史曝光与本包曝光。
  2. 用 **墙钟时间窗**（kline `ts`）查与历史标签的真实重叠；stem 不同仍可重叠。
  3. 硬约束：`MIN_GAP`（同币内容不重叠）、每币上限、避开 chunk 已用 stem；软约束：优先从未标/轻历史 base。
  4. 重出包用 **新 LS 项目名**（`chunk3_v2`），勿覆盖 owner 正在标的 chunk1/2。
  5. 合格窗不够 1000 时 **宁缺毋滥**（723 干净优于 1000 注水）。
- **通用规则**：下次做 label pack，验收清单必须含：
  1. exact stem ∩ 历史
  2. base 币分布 + 每币 max/count
  3. 墙钟重叠（同 base）
  4. 与「进行中 chunk」的 stem/gap  
  **禁止**只报「stride=window → 无重复」。
- **牵连**：
  - `scripts/make_round8_packs.py`（原 round8，无 base cap）
  - `scripts/make_round8_chunk34_v2.py`（修复版 3/4）
  - `output/label_studio/tasks_round8_chunk*.json` / `*_v2.json`
  - 历史 export：`export_round6_all.json`、`export_round7_all.json`
  - 图库：`datasets/dense_2026h1/images/train`
