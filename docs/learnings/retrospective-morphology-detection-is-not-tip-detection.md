# Retrospective morphology detection is not tip detection

- **问题**：Local Signal V2 的时间语义被持续套用成盘口 tip 检测，导致 4–7 根核心框被强行推到
  真实 K 线最右端；随后又把临时讨论的20–30根窗口和10根最大延迟误写成最终合同。Owner最终
  收紧为动态最短上下文，并要求核心结束后3–5根内确认，精确度优先。
- **死胡同**：把 `visible_end=decision` 当成所有检测任务的先验；用右侧纯空白把框的画布坐标
  做到 65%–95%，却没有改变框相对真实 K 线的位置；随后又准备把已完成的 Stage A 宽位置表征
  降级为只供严格因果 Stage B 初始化。这既没有对齐 Owner 的形态目标，也差点丢掉已有价值。
- **有效路径**：先冻结检测任务的时间合同，再谈数据几何。Local Signal V2 核心约4–7根，红框
  只在Owner标出的两条形态边界之间；输入从最短充分窗口开始动态变化，首轮约14–22根；框后只留
  3–5根，3优先、5封顶。框位置不是标签，必须随上下文自然变化而非固定居中/最右。保留 Stage A 宽位置数据与 `best.pt`，
  用 Owner ETH 图复核核心语义，再用模型 false positives 与既有 candidate ledger 构造 hard
  negatives，以解决精确度而不是重新制造位置 shortcut。
- **通用规则**：“使用核心事件之后的数据”不自动等于泄漏；关键是模型声称在哪个时间点输出。
  对事后形态识别，后文可以是合法输入，信号时间必须是完整输入窗右端；对新鲜盘口执行，同样的
  后文就是不可用未来信息。研究检测器与生产实时检测器必须分别声明时间戳、验收尺和资格，不能
  用一条盘口规则覆盖两种任务，也不能把延迟模型冒充新鲜信号。
- **牵连**：`AGENTS.md`、`CLAUDE.md`、`HANDOFF.md`、`PROJECT_PLAN.md`；
  `datasets/local_signal_v2_stagea_randomcrop_v1`；Stage A 3060 权重/日志/位置诊断；P2 hard-negative
  candidate ledger；causal blank 与固定右侧失败臂；后续正例复审、难负例重渲染、fine-tune 与
  独立 pre-holdout 验收。
