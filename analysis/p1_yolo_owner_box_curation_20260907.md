# YOLO 历史框审核：已装工具接入与重点复核队列

日期：2026-09-07。范围：已有 2,513 张历史人工框细化建议。本轮是数据审核流程建设，没有训练模型，也没有得到准确率提升的实测结果。

## 交付结论

Datumaro、CleanVision 已实际检查完整批次，FiftyOne 已持久化收录 2,513 张主审核图与建议框。Label Studio 继续使用原项目 77，不新增项目。人工入口是[先审：重点 50 张](http://127.0.0.1:8081/projects/77/data?tab=45)，打开标注按钮右侧下拉，选择 **Label Tasks As Displayed（按当前列表标注）**，再调整已有框。原来的 104 张精确星标另有[参考入口](http://127.0.0.1:8081/projects/77/data?tab=48)。

后台 [FiftyOne 重点视图](http://localhost:5151/datasets/fable_owner_box_curation_20260907_v1?view=priority) 提供统一的数据身份、建议框、审计标记和优先顺序；Owner 仍在 Label Studio 调整和提交。工具输出都是待核实线索。全部样本保留，删除 0，原标签修改 0，新 Gold 0。

## 数据口径及上一版对照

| 项目 | 上版：框细化导入 | 本轮：工具审计及队列 |
|---|---:|---:|
| 历史框建议 / 可审核主图 | 2,513 | 2,513，全部保留 |
| short / long | 1,361 / 1,152 | 1,361 / 1,152 |
| 原始精确星标 | 104 | 104 |
| 已知同范围别名候选 | 17 组 | 保留；主图字节完全重复亦为 17 组 |
| 主图完全重复 | 未以本轮 SDK 检查 | 17 组 / 34 身份，2,496 唯一 SHA |
| 感知哈希相同的近似候选 | 未检查 | 2 组 / 4 身份 |
| 与当前负图可见范围冲突 | 3 旧框 / 2 负事件 / 15 变体 | 复用冻结覆盖证据，纳入队列 |
| 建议框通过 Datumaro 格式往返 | 未检查 | 2,513；0 error |
| 人工第一批审核入口 | 原 104 星标优先 | 重点 50；另保留星标入口 |
| 模型训练 / 推理 / 新增确认框 | 0 / 0 / 0 | 0 / 0 / 0 |

主图时间范围为 2025-06-05 12:15 至 2026-05-03 12:15 UTC。原来源 train 1,884 / val 629 仅保留来源标记，不能视为重新验证过的训练切分。本轮实际模型验证样本数为 0。全部任务都有一个 long 或 short 建议框，因此“有框率”100%，不代表已确认正类率；当前批次没有背景负例，不能由它估计实际检测精确率。

输入固定为 `datasets/owner_box_refinement_20260907_v1/manifest.jsonl`，SHA-256 `7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641`。仅 `annotation_images/` 进入本轮 SDK 和 FiftyOne；原长图、编号对照、未来 40 根参考均未作为审计输入。右侧未来图沿用上一版供人工判断，不能作为模型输入。

## 工具结果应如何解释

**Datumaro 1.12.0：0 errors、28 warnings、2 infos。** 28 条警告对应 14 张不同图片：11 long、3 short。12 张宽高比、2 张较长边偏离同类别均值超过 5 个标准差；每张分别按类别和固定 role 属性报告一次，所以不是 28 张坏图。2 条 info 只是每个类别的 role 都只有一种值。所有坐标仍有限、宽高为正且不越界。14 张形状警告完整留在明细，未自动改框；它们不在当前重点 50 中，应在首批校准后另行复核。

**CleanVision 0.3.7：34 张完全重复、4 张近似候选，异常尺寸和画布比例均为 0。** 完全重复组没有跨方向或跨原始 split。近似候选只表示 64 位 pHash 相同，未计算语义嵌入，也未进行哈希距离搜索。两组分别是 FLOW / ENJ、ZIL / CHZ，均跨币种、均为空头，四张 SHA 不同，与完全重复身份没有交集。相似形态本来可能跨币种出现，不能据此删除、合并事件或推定同源。

**FiftyOne 1.21.0：2,513 个持久化样本，四个保存视图。** 框存为 `proposal_boxes`，没有 `ground_truth` 或 `predictions` 字段，也没有置信度。逐字段核验旧框 ID、任务 ID、方向、原 split、图像 SHA、归一化框与审计标记。重复发布新增 0、复用 2,513，没有多导一批。

**Cleanlab 2.9.0：已安装，本轮未评分。** 当前任务中的 prediction 是根据原 Owner 框派生的几何建议，并非模型预测。已核实适用于当前画布、事件隔离和模型选择条件的样本外检测预测覆盖为 0/2,513。原有分类质量报告及其他画布的检测预测不能直接移用，也不能让同一建议同时充当预测与真值。等有逐图对应、按时间隔离且未参与训练或模型选择的预测后再评分。[官方目标检测输入要求](https://docs.cleanlab.ai/stable/tutorials/object_detection.html#format-data-labels-and-model-predictions)。

## 重点 50 张怎样选、怎样用

冻结的排序依次优先：已有负图冲突、重复候选、建议框高度相对蜡烛包络高度的扩张比；最后用 review_id 打破平局。前 50 包含全部 3 个冲突框、38 个重复候选，再加 9 个高度扩张较大的建议；其中含 3 张原星标。该顺序用于节省审核时间，不是标签正确概率，也不是随机抽样，因此不能把这 50 张的修改率推广到全部数据。

| 同一个项目内的筛选页 | 数量 | 用途 |
|---|---:|---|
| [先审：重点 50 张](http://127.0.0.1:8081/projects/77/data?tab=45) | 50 | 首批调整已有框，校准核心起止和方向 |
| [复核：重复候选](http://127.0.0.1:8081/projects/77/data?tab=46) | 38 | 比较是否同源重复、不同形态或独立事件 |
| [复核：正负冲突](http://127.0.0.1:8081/projects/77/data?tab=47) | 3 | NIGHT 一个旧框、QTUM 两个旧框；后者不是两个独立事件 |
| [参考：原有星标](http://127.0.0.1:8081/projects/77/data?tab=48) | 104 | 原有高优先人工参照，不丢弃 |

四页是原任务的筛选，不是四个新数据集，数量有重叠，不能相加。FiftyOne 与 JSON 按风险排序；Label Studio 页内按任务 ID 排序，保证 50 张成员准确，但不承诺页内严格按风险先后。

Owner 只需判断方向和核心边界，拖动已有框后提交；可以看后续 40 根辨别形态，但不要只保留后来走出大行情的例子。工具本轮没有逐图重判“启动首根”，现有中央 4–7 根派生规则仍需要人工校准。

本机 Label Studio 的 **Label All Tasks 会主动移除筛选**，即使网址有 `tab=45`，仍会取全项目任务；实测误打开了不在重点集的 34763。**Label Tasks As Displayed** 保留当前筛选，实测打开正确的 34777；按安装版源码，正常提交后的新题也在过滤集合内。编辑器左右箭头用于项目历史，可能返回之前访问过的其他任务；仅浏览时回重点列表点具体行。没有通过修改服务或项目设置来改变这些内置行为。

## 验证、对照与复现

正式审计源码先冻结于 `301e1fe1670e3351528d5ceb47119a8fad325b6e`，随后执行完整 SDK 检查与发布。底层库实际版本、模块 SHA、逐图检查、完整 Datumaro 输出和重复组均保存在实验 results。主图生成前后的完整校验属于上一版；本轮再次在审计前后及发布前验证主图身份。没有把历史缓存存在误称为新推理完成。

初版 52 项聚焦测试通过，涵盖输入漂移、身份错联、越界/未来隔离、原建议语义、发布重入和视图写入边界。实际 SDK 合成对照使用 6 张合成图片：1 对已知相同图被识别，其余 4 张没有被报完全重复；6 个合法框 0 error；刻意构造的负宽框产生 1 error。全部 6 张保留，改标 0。合成对照在正式源码冻结之前的测试环节运行，收据明确记录，不冒充冻结后的市场实测。

实际浏览器发现初版筛选页一直加载：虽然后端支持 `Number/in_list` 并返回正确任务集合，Label Studio 1.13.1 前端的操作枚举不支持它，导致全部视图无法载入。此前 API 成功不等于页面可用。修复源码先冻结于 `c18a61fbc6d4345e1ebd944083bbbc68184e6360`，把同一 ID 集合改为 `or` 连接的 `Number/equal` 标量，只修复本轮创建的视图 45–48；不改变队列、任务和原 Default 视图。初版及重入收据保留，修复另存收据。兼容补丁的 18 项测试包括读取本机实际前端 source map 验证支持操作。

修复后联合 **59 项测试通过**。实际 Chrome 已验证表格显示 Tasks 50/2513、Predictions 50；使用 As Displayed 后，任务 34777 的建议框与独立未来 40 根图片均完整加载（两图各 1280×742），Submit 可见。FiftyOne 的 priority 页面也实测显示 50 张及建议框。没有伪造人工提交，因此“提交后新题过滤”以安装版本源码为证，未用假答案做端到端提交测试。前后核对项目 74/76/77 的任务、人工答案和预测数量不变，项目 74 原人工答案仍为 4 条。HTML 报告只做结构核验；真实界面验证针对 Label Studio 与 FiftyOne。

本轮没有预测收益、方向交易策略或模型评估，val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征收益基线、匹配随机入场对照均不适用。等价的错误检测零假设对照是“合法不相同样本不应被判成格式错误或完全重复”，并配合已知重复和坏框阳性控制；这验证工具链能区分输入，不验证形态语义或模型准确率。

复现应使用本仓 main 和已提交源码、现有隔离环境与冻结输入。源码与输入不一致时先停止；正式原始收据保留，重入另存文件。

```bash
cd /Users/zhangzc/fable-trading
git branch --show-current
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_owner_box_curation.py tests/test_label_studio_review_views.py tests/test_owner_box_quality_tools.py

# 首次完整 SDK 运行；已有正式产物时先核验，勿覆盖其证据。
PYTHONPATH=. /Users/zhangzc/.local/share/fable-trading/venvs/dataset-audit/bin/python -m yoyo.datasets.owner_box_quality_tools run
PYTHONPATH=. .venv/bin/python -m yoyo.datasets.owner_box_curation prepare
PYTHONPATH=. /Users/zhangzc/.local/share/fable-trading/venvs/fiftyone/bin/python -m yoyo.datasets.owner_box_curation publish
PYTHONPATH=. /Users/zhangzc/.local/share/fable-trading/venvs/fiftyone/bin/python -m yoyo.datasets.owner_box_curation publish --receipt experiments/active/exp-owner-box-curation-20260907-v1/results/fiftyone_reentry.json
# 当前兼容版本：首次创建或验证已存在视图，另存核验收据。
PYTHONPATH=. .venv/bin/python -m yoyo.datasets.label_studio_review_views --output experiments/active/exp-owner-box-curation-20260907-v1/results/label_studio_views_current_verification.json
# 仅修复本轮初版不兼容的 45–48；不适用于另建的或被修改过的视图。
# 本轮实际使用 --repair-legacy --output .../results/label_studio_views_repair.json
python3 scripts/md_to_html.py analysis/p1_yolo_owner_box_curation_20260907.md --out-dir analysis/html
```

## 风险与诚实声明

- 格式合法不代表形态定义正确。当前框仍是未确认建议，不能升级为新 Gold。
- 17 组完全重复不跨旧 split，不等于整个数据集已经排除时间依赖泄漏；合并训练前仍需检查同币相邻窗口、别名和事件依赖。
- 2 组 pHash 相同可能只是相似图形，未自动删除任何样本；通用模糊、亮暗、信息量阈值没有用作 K 线图淘汰条件。
- 旧宽框和当前负图的时间交集只是冲突线索，需要人工看方向及核心边界。没有擅自把负例改为正例。
- 训练资格和生产资格均为 false。无新训练、模型推理、holdout 行情读取、部署或 promote。
- 本轮可以开始人工复核，但准确率改善尚未验证。不能用工具检查通过替代新金标与独立时间验证。

## 下一步

先完成重点 50 张，读取 Owner 的真实提交，统计哪些方向及边界反复需要调整，再决定是否调整预框规则。随后复核 14 张几何警告及剩余星标。新标注应另建版本保存；冲突裁决、逐样本确认、时间依赖切分和训练前置门通过后，才安排一次单变量训练对照。Owner 的审核答案是下一步所需输入。
