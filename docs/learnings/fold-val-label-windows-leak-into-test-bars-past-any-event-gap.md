# 折内 val 的标签窗会伸进 test 段——事件 gap 护不住标签右端

- **问题**：yoyo-eth P02 的 anchored walk-forward:fold 内 train/val/test 都按
  决策 bar 位置加了 164 根 embargo gap,自认为隔离完备。
- **死胡同**：只盯"事件的特征回看不碰上一段的标签 bar"这一个方向(MVP 审查
  时补过的 164 = 最大特征回看 139 + horizon 24 + 1)。漏掉的是反方向的另一条边:
  val 事件最大 pos = train_end−1,它的**标签窗**(pos+1..pos+24)落进
  [test_lo, test_lo+23]——early stopping 的 best_iteration 选择用到了 test 段前
  24 根 bar 的价格。gap 加在"下一段的事件起点"上,对"上一段事件的标签右端"
  没有任何约束。
- **有效路径**：对抗性审查代入真实 fold 边界逐条验证四类窗(train 标签右端、
  val 标签右端、test 特征左端、test 标签左端)与三个切点的相交关系,发现
  val 标签右端越界。修法一行:val 归属条件右缩 horizon
  (`pos < train_end - horizon_bars`)。
- **通用规则**：审 purged-CV 隔离时,把**每个 split 的四条边**列成表逐一对切点
  验证:特征回看左端、决策 bar、标签窗右端 ×(与前一段、与后一段)。
  gap 只作用于"事件落点",标签右端要靠 split 自身右边界收缩 horizon 来保证。
  口诀:前段护标签,后段护特征,两头都要查。
- **牵连**：`/Users/zhangzc/yoyo-eth/src/yoyo_eth/walkforward.py`(np.select 切分)、
  `tests/test_mvp.py::test_walkforward_split_assignment`;相关笔记:
  [rolling-max-of-cumulative-streak-is-not-in-window-streak](rolling-max-of-cumulative-streak-is-not-in-window-streak.md)
  (同一项目上一轮审查产物——两轮都是"自测全绿,审查抓边界")。
