# 延长审核未来，应升级独立参考图而不改冻结任务

- **问题**：Owner把本周1043题的未来参考从40扩到150根，但任务data、原图与预框已经冻结，旧2513还保存在同项目。
- **死胡同**：覆盖原future40 PNG会破坏原SHA；逐题PATCH data会让既有导出身份门拒绝。单靠XML按protocol做条件fallback也不可行，已装LS的visibleWhen不支持任务字段判断；`$review_id.png`还会被当成嵌套字段。
- **有效路径**：独立future150包按`images/$review_id/image.png`提供映射；新1043为新参考图，旧2513精确链接原40图。只改future Image绑定与说明，保留全部任务data、预测和答案。完整历史prefix必须先逐像素复现原主图，才延长参考；仅验证原PNG没变不足以证明新读行情仍相同。
- **通用规则**：审核上下文版本与训练输入、任务身份分开保存。长参考图自行注明实际根数和缺口原因；用独立manifest与部署时点记录Owner当时可能看到的上下文，不能把修改显示协议当作重新确认样本。
- **牵连**：`review_future_context.py`、`publish_future_context.py`、统一LS配置；1041×150、1×132缺口、1×13保留集边界，29181原文件前后SHA一致，未读取holdout OHLCV。
