# YOLO 人工审核简化与未来 40 根对照

2026-09-07 · `assisted_future40_v2` · 原实验 `exp-15m-grade-a-owner-calibration-20260907-v1` 的 Owner 协议修订。

Owner 指出“这个审核对人工来说过于复杂，且人工审核需要看到未来 K 线”，并选择后续 **40 根（10 小时）**。本轮据此改为辅助审核：直接展示原候选方向与框，主要回答“是 / 不是 / 拿不准”，纠正操作可选。原图之外的未来仅供人看，原输入保持不变。

[打开新版人工审核页](http://127.0.0.1:8769/)；旧地址已切换新版。

## 使用变化

| 项目 | 旧版 | 新版 |
| --- | --- | --- |
| 默认图像 | 原 18 根 | 原 18 根＋右侧 40 根 |
| 主要操作 | 类别、核心首末、纵坐标、框义、保存 | 三选一后保存并下一张 |
| 框 | 每张从空白填写 | 展示原提案，需要时纠正 |
| 辅助字段 | 主表单必填多项 | 折叠、可不填 |
| 保存 | 每题保存，再手动备份 | 主选择自动本机备份；保留手动恢复/导入导出 |
| 统计含义 | 无提案提示的类别/几何盲审 | 看到提案和未来的辅助判断，独立 schema 2 |

“不是”只表示这个提案有误，不能自动成为整图负例。可选“整个原始输入都没有目标形态”才记录明确无目标的判断；仍需后续数据与匹配审查。修改方向和框只作用于人工答案，未经后续处理不会改任何训练标签。

## 数据与隔离证据

保持原来的 **240 个不同事件＋36 个重复项，共 276 题**，顺序不变。候选抽样正负比例是提案分层，Owner 确认正类率仍未知。本轮没有独立 val，val 样本数为 0；不是一次模型训练或市场准确率评估。

所有未来窗口实际检查为 **58 根、57 个连续 15m 间隔**，第一/第18/第58根分别对应旧起点、旧终点与新增终点。最晚新增 K 线为 **2025-10-24 17:30 UTC**，收盘 **17:45 UTC**。原样本起点最早为 2021-07-17 01:45 UTC。

| 构建检查 | 结果 |
| --- | ---: |
| 原 W18 输入像素重放 | 240 / 240 通过 |
| 原包、已保存草稿、原数据契约文件哈希 | 286 / 286 保持一致 |
| 未来扩展后与已有成员的 150-bar 保护区重叠 | 0 / 240 |
| 新增模型推理 / 训练 / holdout 读取 | 0 / 0 / 0 |

新版路径为 `datasets/grade_a_owner_assisted_20260907_v2/`：原图复制在 `public/input_images/`，未来图和独立 manifest 在 `review_future_only/`，后者禁止 labels。扩展图会根据完整 58 根重算纵轴，因此不能拿它左侧的截图当原训练图；独立保留的 W18 文件才是原输入。

框使用原 YOLO 连续坐标，经旧图像素→bar/price→新图像素转换。没有依据未来走势重画候选，也没有把 K 线极值冒充原 YOLO 纵框。人工修正框坐标固定在原 W18 图中。

## 复现与使用

```bash
# 在 fable-trading 根目录，使用已有锁版本的 .venv。
git branch --show-current
.venv/bin/python -m pytest tests/test_grade_a_assisted_review.py tests/test_grade_a_calibration.py tests/boundaries/test_layer_imports.py -q

# 首次构建：需已有旧审核包和 protocol 中冻结的本地源归档；已存在的包拒绝覆盖。
# 先提交 builder/protocol，再执行；图片与 UI 可分别 source-first 构建。
.venv/bin/python -m yoyo.datasets.grade_a_assisted_review build-images
# 提交模板后挂接 UI，不重建图像或改变答案身份。
.venv/bin/python -m yoyo.datasets.grade_a_assisted_review publish-ui

# 打开已有包：
.venv/bin/python -m yoyo.datasets.grade_a_assisted_review serve --port 8769
# http://127.0.0.1:8769/

# 收到真实答案后使用实际文件名，当前不产生 Owner 评分：
.venv/bin/python -m yoyo.datasets.grade_a_assisted_review score \
  --answers /path/to/assisted_answers.json \
  --output /path/to/new_assisted_score.json

python3 scripts/md_to_html.py analysis/p1_15m_grade_a_assisted_future40_20260907.md --out-dir analysis/html
```

图像生成源码提交 `295a3b8`。`assisted_v2_protocol.json` 固定 61 份源代码/元数据哈希；`results/assisted_v2/build_receipt.json` 记录输出身份与后续 UI 提交。

## 风险与诚实声明

本轮是审核工具和数据隔离改进，未产生收益序列。因此 val AUC、收益排序置换 p、top-decile 毛/净收益、交易胜率、单特征收益基线及匹配随机入场对照不适用；工程零假设是“新增人工未来上下文不改变原输入”，逐文件哈希与原 W18 像素重放均通过。未来图对人有帮助，不等于模型获得了可在实时预测使用的未来信息。

27 项新增后端测试及合计 122 项聚焦测试通过。独立复核用空闲 TCP 连接验证图像服务不会被浏览器预连接堵住；迟到旧快照、同时间戳不同内容和重复 lineage 均被拒绝。浏览器实测三按钮自动保存/切题、撤回、刷新/本机恢复、原图拖框均通过；缺失未来图禁止确认，恢复图像后可重试。手机 390×844 下页面宽 390、大图宽 1080，实际横滚 480px；窗口覆盖已恢复。独立产物 QA 检查 276 对原图/未来图、36 重复对、全部时钟及 286 旧文件哈希通过。具体证据见 `results/assisted_v2/browser_qa.json` 与 `physical_qa.json`。

新旧审核协议分开保存，旧浏览器里未完成的多头草稿已经备份，没有当成已确认正例。辅助审核的一致率只能解释当前带提示的任务，不能冒充旧盲审 κ 或独立市场准确率。未升级 Gold、训练资格或生产资格。

额外执行注册表测试为 15 通过、1 失败：共享工作区的 holdout 消耗集合比测试期望多一个既有实验 `exp-btcusdtp-owner-k1k2-genuine-flow-20260907-v36`。该失败在本轮注册表修改前已出现，本轮未改它的声明或放宽守门测试。此次新增审核产物全部保持 `pre_holdout`，训练与生产资格为 false；交付提交中的注册表另行检查加载和引用完整性。

## 下一步

使用新版页面逐图判断即可。获得真实答案后，先汇报分歧与可用样本，再继续已规划的数据整理；不把点击“不是”自动翻译为训练负标签。
