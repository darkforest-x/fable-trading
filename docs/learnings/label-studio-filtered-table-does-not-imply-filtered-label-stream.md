# Label Studio 的筛选列表不保证审核队列也受筛选约束

- **问题**：Label Studio 1.13.1 的项目 77 已正确显示重点视图的 50/2513 张任务，但点击主按钮 `Label All Tasks` 后，打开了不在重点集合内的任务 34763。URL 仍带 `tab=45`，不能据此认定审核范围没有扩大。
- **死胡同**：只验证表格数量和视图成员，或认为表头“全选 50”就会把后续审核限制到这些任务。实际前端主按钮将 `dm:labelstream:mode` 设为 `all`，调用 `next_task` 时主动删掉筛选；未选择时还会删掉选择和排序。表头全选内部表示 `{all:true,excluded:[]}`，删除筛选后也不能作为封闭的 50 个 ID 集合。
- **有效路径**：依据本机发布包的真实 source map，使用主按钮右侧下拉菜单 `Label Tasks As Displayed`，其 `filtered` 模式保留筛选和排序。主线程真实浏览器已验证该入口打开重点集合首项 34777 / `d94b586fcc04caa4585e49d8`，建议框和独立 future40 均加载。源码显示，在正常审核流里提交后取新题继续使用当前筛选；后端在筛选后的 queryset 中取题，延后及跳过队列也与这个 queryset 相交，耗尽后没有自动转全项目的分支。本次没有点击 Submit、Skip 或历史箭头，因此连续提交行为是源码结论，尚不是浏览器实测。
- **通用规则**：把“列表成员”“取新题队列”“历史导航”作为三条不同合同验收。编辑器左右箭头读取的是项目级历史并按具体任务 ID 直接加载，不保证属于当前 50 张；只查看时从筛选列表逐行进入。连续审核用 `Label Tasks As Displayed`，不能仅凭 URL、按钮显示的数量或表格筛选推断队列范围。
- **牵连**：本机 `web/dist/libs/datamanager/main.js.map` 的 `LabelButton.jsx`、`AppStore.js`、`tab_selected_items.js`、`lsf-sdk.js`，以及 `web/dist/libs/editor/main.js.map` 的历史导航代码和后端 `projects/functions/next_task.py`。确切 SHA、源码路径、行号和实测边界存于 `experiments/active/exp-owner-box-curation-20260907-v1/results/label_studio_label_stream_contract.json`。本次仅文档取证，没有修改应用源码、服务、项目设置、视图、任务数据或标注；普通审核流导航本身可能由 Label Studio 维护任务锁和项目历史，不能把“未提交标注”扩大表述为服务完全无状态变化。
