# Zero-fill quota refunds require a terminal exchange receipt

- **问题**：FOK 限价单未成交取消后仍消耗每日次数，用户无法区分申请占用和实际开仓。
- **死胡同**：网络失败就返还会把已成交但回执丢失的订单当作失败；按每次轮询扣减又会重复返还。
- **有效路径**：提交时预占，只有原订单编号确认零成交取消、拒绝或确定未发送才返还。用订单主键记录返还，绑定原北京时间日期；部分成交和未知结果继续占用。常规限价保留挂单和撤单入口。
- **通用规则**：影响额度的失败必须先区分业务终态与通信异常；额度变动和去重凭证在同一事务提交。
- **牵连**：`yoyo/discipline_app/book.py`、`live_book.py`、`managed_orders.py`、`tests/discipline_app/test_managed_orders.py`。
