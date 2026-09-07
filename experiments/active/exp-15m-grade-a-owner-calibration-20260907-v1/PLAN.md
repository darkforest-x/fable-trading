# Grade-A 人工校准与难负例替换准备

Owner 2026-09-07 授权：**“按照你的想法来做，去吧”**。本轮落实上轮建议的人工校准、
评估独立性排查和单变量训练准备。该授权不生成逐样本人工答案，也不包含 holdout 或实盘操作。

## 本轮执行顺序

1. 固定已有 5,083 行提案（其中旧口径新事件 5,053）及 close 32,000 图 manifest 哈希。
2. 在任何图像读取前审计日期与已有成员的完整已知依赖区间。跨 OKX/Binance、双方向、
   正负样本统一检查，窗口两侧各加现行 150-bar 缓冲。保留原队列，不改写历史 novelty。
3. 从合格训练时代提案中按 proposal bucket × direction 各取 60，半年分层，总计 240。
   这是有意平衡的难例校准，不是市场随机样本或训练/验证随机切分。选中同币窗口不相交。
4. 加 36 项盲重复，原项先于重复项至少 60 个位置；来源、模型框、置信度、类别与榜单信息
   只在 private ledger。公开页面仅加载裸输入图、编号和人工控件。
5. 从冻结来源重渲染原代表窗口，每张解码像素须与旧 input_pixel_sha256 一致。
   原图已有核心后确认上下文，保留其历史任务语义；代表窗口收盘时间单独记录，不套用
   可能早于该窗口的 episode first_available_at。公开目录绝不带更晚未来图。
6. 交付页面并实际验证显示、保存、刷新恢复、导入导出和手机版面。QA 答案只在独立 QA
   来源/浏览器存储中，不可混入 Owner 证据。Owner 页面初始必须 0 已完成。
7. 收到 Owner 导出后运行 score，报类别、核心和纵框重复一致性；缺项、模糊、重复矛盾
   均显式保留。本轮未冻结质量通过数值，因此不发明 κ 阈值或自动升级 Gold。
8. 按 training_plan.json 的匹配约束冻结下一版替换事件。先有人工裁决和可行匹配，后有
   数据集身份与训练；本轮不发起空跑、自动换标签或训练。

## 连续窗口评估设计

现有 2025-12～2026-05-03 val 已用于 best.pt 选轮、分辨率、语义门、颜色与 HL2 对比。
它继续用于同条件开发比较，不能改称独立测试。5,053 队列经过事后涨跌榜、envelope 和
模型出框筛选，未报出的正例不在分母，不能估召回。

独立评估当前状态：**not_established / evaluation_ready=false**。

- 先建立来源使用账本，区分数据下载、规则筛选、模型训练、选轮、候选挖掘、人工图审、
  指标比较和导致的决策。未发现记录只能记 unknown，不追认 never_seen。
- 连续窗口 roster 必须先固定源 SHA、币种与时间块，选取不依赖模型预测、语义门、
  同日涨跌或未来结果；来源和决策曝光证据尚不完整，本轮不冒称 roster 已冻结。
- 对整块的每个合法端点枚举 W18/W19，包含无框端点；不套 mining envelope 预筛。
  缺根、预热不足等记未知，保留分母。标签先于预测揭示，事件跨窗去重和 IoU 配对先冻结。
- 标签覆盖完整窗口序列，才能同时测事件召回、每千窗口误报、定位误差与首次检出延迟。
  原/新模型同设备同配方，按完整币种时间块计算不确定性，不能把同事件裁剪当独立样本。
- 新鲜 tip 的任务与本次 completed-history 任务不同，另需对应的标签与时间合同，不能
  靠截掉确认 K 后直接把此模型叫作已验收的 tip 检测器。
- 若没有证据证明存在未污染的 pre-holdout 时间块，则保持开发诊断级。不得自动读 holdout。

## 训练消融

优先变量仅为**等量难负事件的来源/人工确认质量**。原有正图、easy negatives、验证集、
1:3 比例、close 渲染和模型配方均固定。受审正候选主要校准语义，不能在这同一臂加入正例。
任何 Owner 判定正例的候选都不得作为难负例；“方向不同”“不符合规则”不等于背景。
确认核心、框义改变时先停在协议校准，不能借负例实验顺手改全部正框。

## 复现

```bash
git branch --show-current
.venv/bin/python -m pytest tests/test_grade_a_calibration.py -q
# 必须先提交 builder、template 与 preregistration，再运行：
.venv/bin/python -m yoyo.datasets.grade_a_calibration build
# 仅服务 public 目录，不公开 admin 来源与重复对应表：
.venv/bin/python -m yoyo.datasets.grade_a_calibration serve --port 8769
# Owner 导出答案后（当前无答案，不运行）：
.venv/bin/python -m yoyo.datasets.grade_a_calibration score \
  --answers /path/to/owner_export.json \
  --output experiments/active/exp-15m-grade-a-owner-calibration-20260907-v1/results/owner_score_v1.json
```

## 口径与诚实声明

- 本轮没有收益序列；AUC、top-decile 收益、交易胜率与匹配随机入场检验不适用。
  同等严格的工程零假设对照为原裸输入像素身份重放；人工质量零假设等答案后才可判断。
- 文件 SHA、成员排重、时间隔离与决策独立性是不同维度；本轮不把前三者当作最后一项。
- 币名只做明确的字面 venue 归一化；1000 倍合约、代币改名等未确认别名不擅自合并。
- 240 是本轮审核工作量，不是统计功效保证；类别比例和市场真实发生率不同。
- 无新增模型推理、阈值调参、训练、promote、部署、ACTIVE/frozen/forward 或真金改动。

## 浏览器 QA 后补充的本机保存

IAB 中 Blob 下载提示出现，但 download 事件未返回且没有确认文件落盘，因此不能据按钮提示
声称交付链路完成。增加显式「保存到本机」及「恢复本机进度」，仅向绑定 127.0.0.1 的
同源服务发送当前审核快照，写入本包 `answers/answers_<UTC>_<SHA>.json`，每次追加，
保留历史答案，不覆写 labels 或训练数据。离线 JSON 导入导出仍保留作外部浏览器备用。
这一补充不改变样本、顺序、图片、manifest 或评估协议；旧 HTML 与收据留在 ui_history。

## Owner修订：辅助审核与未来40根（2026-09-07）

Owner明确旧表单过于复杂且需要未来40根。当前交付改用 `assisted_v2_protocol.json`：
同一批240事件/36重复，三按钮确认原提案，原框/方向提示和额外40根可见；纠正为可选。
本修订覆盖上文要求不看更远未来及每张强制从空白标框的审核界面要求，原schema1证据保留。
未来独立目录/manifest，原输入与旧草稿286文件哈希不变；未来上下文不是训练图。
辅助一致率不能替代盲审κ；REJECT不自动映射NO_SIGNAL，需明确无目标裁决及后续匹配门。
未来扩大后的依赖隔离已独立核查，当前0重叠。任何后续出现重叠的候选不得进入训练替换。
服务命令改为 `.venv/bin/python -m yoyo.datasets.grade_a_assisted_review serve --port 8769`。
详细交付：`analysis/html/p1_15m_grade_a_assisted_future40_20260907.html`。
