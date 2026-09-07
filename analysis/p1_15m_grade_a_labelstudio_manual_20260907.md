# 完整 YOLO 数据已转入 Label Studio 手工标注

2026-09-07 · `exp-15m-grade-a-labelstudio-manual-20260907-v1`

Owner要求把数据集导入Label Studio逐个手标，并指出240张不是完整数据。本轮覆盖当前32,000张训练/开发验证图对应的全部4,172个事件组，再保留另一个240候选＋36重复的补充项目。图上不提供旧框或模型预测，由Owner从空白画框。

- [完整训练集：4,172个事件](http://127.0.0.1:8081/projects/76/data)
- [补充候选：240个样本＋36重复，共276题](http://127.0.0.1:8081/projects/74/data)

进入项目后点 **Label All Tasks**。左图选择“多头”或“空头”并拖框，右图看后续走势；无目标或不确定可直接选择相应选项。点击 **Submit** 保存并继续。更多前文可以展开查看。

## 到底有多少数据

| 范围 | 图片／任务数 | 事件组数 | 是否新增独立样本池 |
| --- | ---: | ---: | --- |
| 当前close训练与验证图 | 32,000 | 4,172 | 主数据池 |
| 其中现有正标签图 | 8,000 | 1,043 | 主池子集，旧标签不等于本轮Owner确认 |
| 其中现有负标签图 | 24,000 | 3,129 | 主池子集，空标签不等于Owner确认无目标 |
| HL2对照版本／960与1280训练运行 | 同一批样本的对照或运行 | 同上 | 不另计 |
| 旧审核页与未来40根新版 | 两版各276题 | 同一240事件＋36重复 | 同一个候选池 |
| 本轮Label Studio导入 | **4,448个任务** | 4,172主池事件＋240候选事件 | **2个项目** |

32,000图中train 27,200图／3,552事件，val 4,800图／620事件。每个事件对应7或8个位置变体；本轮让人标一个代表图，完整保存其余变体映射。事件组去重并不意味着统计独立。

主池代表原窗范围为2020-02-07 11:30至2026-05-03 20:30 UTC；候选池范围为2021-07至2025-10。Label Studio导入前已有50个历史项目、30,314个任务、12,965个完成任务；不少项目是历史分块，不能解释成50个独立数据集。这些项目未做清理、迁移或改标。

## 未来图与坐标

主图逐字节复制原训练PNG。代表图选择最多后文的现有18/19根变体；可选前文图取同组最早起点的原PNG。后文参考图从代表图原起点延伸最多40根，灰竖线标出原输入结束处。参考图没有绘框控件。

| 未来覆盖 | 主池事件数 |
| --- | ---: |
| 完整40根 | 4,169 |
| 30根，保留集边界截断 | 1 |
| 13根，保留集边界截断 | 1 |
| 33根，现有数据已到末尾 | 1 |

所有不足40根的情况在任务标题说明。边界加载器仅物化2026-05-04之前的OHLCV；遇到边界行只检查时间，不解析该行OHLCV，没有为补足未来而打开保留集。

主池产物在 `datasets/grade_a_manual_events_20260907_v1/`：`input_images/`和`context_images/`保留原PNG，`future_only/`有独立manifest且没有labels；`admin/lineage.jsonl`记录全部32,000变体的时间、split、SHA及逐图ChartTransform。旧候选包图片不重建，只生成空白Label Studio任务引用。

一个归一化框不能直接复制给其他裁剪，因为时间位置和纵轴尺度不同。后续要先转换到bar／price坐标，再检查各变体是否容纳该框；没有在本轮自动回写标签。人工改变核心范围、画出多个核心、无目标同时画框、空提交或拿不准，都需要后续解释，不能默认变成可训练Gold。

## 验证与复现

源码先冻结在 `fea34e1`，再生成图片。79项聚焦测试通过，覆盖分组、时间边界、缺根、不可覆盖输出、重复导入、数据漂移与多图资源读取。4,172个代表输入像素重放全部一致，32,000原图及标签文件SHA在构建前后均匹配。

导入任务数分别为4,172和276，预测数均为0。再次运行导入新增0题，说明重复执行不会再创建一批任务。每个项目首、中、尾任务的三个图片字段均经HTTP读取并核对SHA。真实Chrome页面验证两个项目图片可见；独立QA项目中手动画框并Submit，保存结果绑定原始1280×742的`image`，未绑定未来图。该临时QA项目随即删除，未向Owner项目添加合成答案。

本机实际运行的Label Studio文档根目录是 `reports/`，与历史Docker配置不同。当前使用 `reports/label_studio/`下的两个相对软链指向专用数据包，并在目标项目注册图片存储；未重启服务或修改旧项目。图片输入与绘框目标的配置遵循[Image文档](https://labelstud.io/tags/image)和[RectangleLabels文档](https://labelstud.io/tags/rectanglelabels)。

```bash
# 在 fable-trading 根目录；主环境版本保持不变。
git branch --show-current
.venv/bin/python -m pytest tests/test_grade_a_manual_pack.py tests/test_label_studio_import.py tests/boundaries/test_layer_imports.py -q

# builder、配置、preregistration先提交；生成可续建，但不覆盖不同内容。
.venv/bin/python -m yoyo.datasets.grade_a_manual_pack build
.venv/bin/python -m yoyo.datasets.grade_a_label_studio build-candidates

# 复用现有本机Label Studio账号和服务；仅缺失任务会被补导。
.venv/bin/python -m yoyo.datasets.grade_a_label_studio import --which full
.venv/bin/python -m yoyo.datasets.grade_a_label_studio import --which candidates

python3 scripts/md_to_html.py analysis/p1_15m_grade_a_labelstudio_manual_20260907.md --out-dir analysis/html
```

导入收据、幂等复核与浏览器QA在本实验 `results/`；主池生成收据在数据包 `admin/build_receipt.json`。服务需保持现有配置与数据包可访问；这是本机入口。

独立实物复核另检查了12,516张PNG的解码与尺寸、全部4,172组原图／前文／未来哈希、32,000条坐标映射、未来时钟与分隔线，结果全部通过。逐事件续建缓存留在本机，最终lineage、未来manifest及源前缀审计入库，避免把缺少PNG的克隆误当作已经构建完成。

## 风险与诚实声明

本轮是完整标注队列交付，不是训练实验。新的人工正类率和模型准确率尚未产生；val 4,800图属于既有开发集，未进行新评分。val AUC、收益置换p、top-decile毛／净收益、胜率、单特征收益基线和随机入场对照不适用。工程零假设是“人工未来参考扩展不改变模型原输入”，用逐图像素重放、全部文件SHA和重复导入新增0的对照验证。

额外40根是人工标签信息，不是实时模型输入。本轮没有新训练、推理、promote、生产资格变更或模型标签回写。未来窗口扩大后的分区依赖、不同协议答案兼容性、重复一致性及坐标回写资格，必须在后续构建训练数据前重新审核。标准Label Studio界面在较矮窗口内需要上下滚动查看完整图像。

## 下一步

Owner可从完整项目开始逐个标。收到真实标注后，先统计完成量、明确正负／不确定、重复分歧和核心边界，再整理可用金标及与旧标签的差异；训练和数据资格仍保持false。
