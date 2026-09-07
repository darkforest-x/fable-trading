# Label Studio 后端支持的筛选操作未必能被前端加载

- **问题**：在 Label Studio 1.13.1 为已有项目创建任务 ID 集合视图后，API 返回的成员集合完全正确，但真实浏览器一直停留在加载动画，无法显示表格。
- **死胡同**：只依据后端 `data_manager/managers.py` 支持 `Number/in_list`，并验证创建、分页成员与重入均成功，就认为视图可以交付。后端序列化器接收该操作名，却没有验证前端的数据模型；这些 API 检查无法证明页面可用。
- **有效路径**：读取本机发布包 `web/dist/libs/datamanager/main.js.map` 中的实际源码，定位 `Tabs/tab_filter.js` 的 `types.enumeration(operatorNames)`。`Filters/types/Number.jsx` 仅支持标量等值和数值区间，整个操作集合不含 `in_list`，加载持久视图时因此发生契约冲突。将 ID 集合表达为 `or` 连接的 `Number/equal` 标量；空集合用不可能的任务 ID `-1`。正式修复须先冻结源码，仅在项目、协议、来源 manifest、原筛选与授权视图 ID 全部匹配时替换筛选，并保留原视图 ID、展示配置和旧收据。最终交付仍须由真实浏览器验证。
- **通用规则**：程序创建由前端消费的持久配置时，要同时核对后端接口与前端反序列化合同。API 查询结果正确，只能证明后端语义正确；至少还需一次真实页面加载验收。前端依赖可从已部署资源及 source map 取证，无须装进训练环境。
- **牵连**：`yoyo/datasets/label_studio_review_views.py`、`tests/test_label_studio_review_views.py`、项目 77 的视图 45–48。修复仅允许视图配置写入，不修改任务、人工标注、草稿或预测，也不把排序和复核队列当作 Gold 裁决。
