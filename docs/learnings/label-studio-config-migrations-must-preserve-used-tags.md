# Label Studio 界面简化必须兼容已使用的标签名

- **问题**：一个尚无成功 annotation 的项目要从双标签界面改成“是/不是”，Label Studio 仍拒绝删除 `box`、随后拒绝删除 `shape`。原因是导入 prediction 和一次未提交草稿已经引用这些 tag，项目计数显示 0 annotations 并不代表配置可随意删字段。
- **死胡同**：直接用全新的最小 XML 覆盖配置会返回 400；只保留 prediction 的 `box` 仍不够，因为 draft 可能引用其他旧控件。依赖项目的 `num_tasks_with_annotations=0` 做迁移判断会漏掉 prediction/draft 依赖。
- **有效路径**：新建唯一可见的 `is_target = 是/不是` 控件和 review 图，同时在 `display:none` 容器中保留旧 tag 的原类型与合法取值，并取消旧控件的 `required`。这样既不丢 prediction/draft，也不让旧问题出现在 owner 界面；更新后必须在真实浏览器刷新检查可见控件。
- **通用规则**：Label Studio 配置迁移先盘点 annotation、draft、prediction 三类 tag 引用。若目标只是简化 UI，优先隐藏兼容而不是删除；“API 更新成功”之后还要做真实任务页的可见性验收。
- **牵连**：Label Studio project 53、`scripts/build_eth_3m_v10_prebox200.py`、`datasets/eth_3m_v10_prebox200/label_studio/label_config.xml`、`analysis/p_eth_3m_v10_prebox200.md`。未修改任务、模型、阈值或 holdout。
