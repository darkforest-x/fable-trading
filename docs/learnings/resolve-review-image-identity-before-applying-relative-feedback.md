# 先锁定审核图身份，再解释“这张 / 第一张 / 往右”

- **问题**：Owner 附了一张 TG 截图并说“我给你的第一张明显应该框右边一点”。截图正文实际显示 `40/50 | GRT_USDT_SWAP | SHORT`，但反馈一度被错误应用到来源编号 01。
- **死胡同**：只按自然语言里的“第一张”匹配批次序号；这种做法忽略附件自身的编号、币种、方向和 manifest sample ID，会把正确的逐图裁决写到另一张图上。
- **有效路径**：先从附件可见标题解析 `40/50 + GRT + SHORT`，再联结冻结 manifest 得到 sample ID `e9578d1f834c6c5e2fa33fe3`；把 01 恢复原边界，并只对 40 比较逐根候选后提出 `t-9..t-5`。
- **通用规则**：任何“这张 / 上一张 / 第一张 / 第 N 张”的标注反馈，第一步都要用附件可见编号、symbol、side、time 至少两项与 manifest 联结；身份未唯一解析时，禁止修改坐标。
- **牵连**：`experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/preregistration.json`、Review50 source order、Telegram caption、逐图 rebox 决策。
