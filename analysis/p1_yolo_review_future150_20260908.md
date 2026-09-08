# 本周YOLO审核已扩展到150根未来K线

2026-09-08。Owner要求“后续走势参考k线起码要100-150根k”，当前1043题默认改为150根15分钟K线（37.5小时）。1041题完整150，1题因行情缺口132，1题因保留集边界13。实际数量与原因写在右图顶部。

在[本周审核入口](http://192.168.1.4:8081/projects/77/data?tab=44)提交当前题后刷新。项目现名“YOLO 本周审核 · 1043题 · 未来150根”；继续原预框调整和右键提交。原40根导入说明移到折叠区，避免把它误认为当前右图的长度。

## 范围与前后对照

| 项目 | 原参考 | 当前参考 |
| --- | --- | --- |
| 当前待审事件 | 1043 | 1043，身份不变 |
| 完整目标长度 | 1042题×40根 | 1041题×150根 |
| 行情缺口 | 40根范围内未触及 | STORJ一题132根，停止于首个缺口 |
| 保留集边界 | USELESS一题13根 | 同题13根，不越界补足 |
| 原训练正图/标签 | 8000对 | 8000对前后SHA一致 |
| 历史资料 | 2513题原40根参考 | 原图字节精确链接，不要求重审 |
| 项目总库存 | 3556题 | 3556题，无新建任务 |

新1043代表图来自原888 train /155 val、488多/555空事件；这些是旧来源分组和自动proposal类别，不是新增人工确认。原代表图时间范围为2020-03-16T03:15:00+00:00至2026-05-03T20:30:00+00:00。此次不改变成员、split、框或分类。未来只在独立审核包中生成，训练资格和生产资格继续false。

## 构建与保护

builder `2bee44e`及预注册先冻结后执行。499个行情源使用有上界的原始prefix读取，最多到原图末根之后150根的闭合时刻，并在2026-05-04保留集边界前停止。SMA/EMA20/60/120继续按HL2和完整安全历史初始化。每个新事件先逐像素复现原主图，1043/1043通过，才渲染更长参考。

新图仍1280×742：顶部32px为计数条，其下完整绘制输入和未来K线，分隔线标出原图末端。没有裁掉底部K线。原训练正图/标签和新旧审核素材合计29181个文件前后摘要相同：`14d11766a7e8b17d70abd2eac8ae1a4699f3bf0fc9aed3f1d9b90c7e9ef09d71`。

独立包`datasets/owner_review_future150_20260908_v1`包含3556个按review_id对应的参考路径，新1043为新PNG、旧2513为指向原future40的精确软链。manifest SHA为`7f7a6c430bd43b46c2b52be593af2574e9212cf5d3ae65c79c876d97cce94696`。包中没有训练labels。

## 发布和真实页面验收

发布器`bbd35db`补齐来源身份门后，首次发布因服务端XML空白规范化被拦，发生在任何项目写入前。`7369d7b`改为canonical XML比较并保留控件/主图合同检查后发布成功。只创建所需静态图片存储连接，并修改项目标题和label_config；任务data、原预测、答案和草稿API写入均为0。发布前后3556任务身份与原预测核对通过，人工答案计数均413。

Mac Chrome真实任务37322显示原主图及预框，右图顶部`INPUT 18 | FUTURE 150 / 150 | 15m`清晰可见；Undo/Redo/Reset禁用，右键提示保留，未拖框或提交。1470×670窗口内图顶190px，左右图底536/610px，均在624px起的提交栏上方。Windows浏览器本轮未远程点击；沿用原LAN同源入口，Owner刷新加载新配置。

旧参考、13根边界图和150根完整图的实际HTTP读取均200且SHA匹配。构建25项、发布器30项、既有队列/命名20项，共75项聚焦检查通过，非全仓测试。

## 复现

```bash
git branch --show-current
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_review_future_context.py tests/test_publish_future_context.py tests/test_label_studio_queue_entry_install.py tests/test_label_studio_dataset_union.py
PYTHONPATH=. .venv/bin/python -m yoyo.datasets.review_future_context build
PYTHONPATH=. .venv/bin/python -m yoyo.review.publish_future_context
python3 scripts/md_to_html.py analysis/p1_yolo_review_future150_20260908.md --out-dir analysis/html
```

源码必须先提交在main，未知输出、源数据变化、错映射、不同软链、未来时间漂移或未完成包都会拒绝。发布前配置备份与收据位于`output/offline_tasks/label_studio_future150_20260908/20260908T152912985659Z/`。若需回退，只恢复该备份中的label_config和title；不删除数据包或重导任务，不回滚人工工作。

父实验`exp-yolo-dataset-consolidation-20260908-v1`的results下保存future150构建、发布与浏览器验收收据；原始manifest、逐行情prefix审计和媒体摘要留在独立包admin中。

## 风险与诚实声明

这是人工审核上下文扩展，没有模型或收益实验。val AUC、交易置换p、top-decile毛净收益、胜率、单特征收益基线与匹配随机交易对照不适用，未计算。替代的严格否定对照包含：历史或主图内OHLCV变异必须导致原图重放失败，保留集/缺口/源末端截短，输入绑定变化/身份遗漏/错链/哈希和来源漂移拒绝；同时保留29181原文件前后相等对照。

没有读取holdout OHLCV、模型推理、训练或promote。150根可能让人的判断方式不同于此前40根，不能把显示协议变化冒充重复标注稳定性或逐样本金标；后续整理须保留部署时点与原始答案。132根缺口没有补造数据，13根边界没有越界。旧2513仍属历史资料，其显示保持原40根；当前Owner只继续1043批次。
