# Owner 的“确认”必须绑定动作范围，不能自动扩权

- **问题**：同一句“确认”同时出现在 venue 选择、覆盖未保存编辑器文本、官方编译和未来 paper 规划的上下文中；若只存一个 `owner_confirmed=true`，以后很容易把 compile-only 许可误当作 forward 或订单许可。
- **死胡同**：在协议生成器里硬编码“未确认”，或在收到回复后直接把所有 Owner 门一起改成通过。前者丢失真实决策，后者扩大了授权边界。
- **有效路径**：保存窄范围确认记录，精确写入 symbol、timeframe、允许动作和明确排除项；生成器同时校验 `compile_only=true` 与 `paper_forward_activation_approved=false`。venue 门通过，但 prospective activation 门仍独立阻塞。
- **通用规则**：Owner 的短回复必须和它所回答的具体问题一起持久化；只解除问题中明确列出的门，其余审批保持 fail closed。
- **牵连**：`experiments/active/exp-pine-eth-15m-forward-v2/owner_venue_confirmation.json`、Paper V2 activation timestamp、P0/P1 阶段门、警报/订单/参数/holdout 权限边界。
