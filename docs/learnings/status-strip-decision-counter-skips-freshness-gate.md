# 看板顶栏 15/100 与 forward 页 0/100 不一致：status-strip 不做新鲜度过滤

- **问题**：`/api/status-strip` 显示 decision_trades=15/100，而 `/api/forward` 显示
  decision_trades=0/100、hindsight_excluded=15。owner 看顶栏会以为已有 15 笔有效裁决。
- **死胡同**：以为两处读的是不同日志或不同时间点——实际同一份 `data/forward_log.csv`。
- **有效路径**：对读两段代码。`forward_payloads.forward_payload` 在 closed & maker_filled 之后
  还按 `lag ≤ FRESH_DETECT_MIN(30)` 过滤（2026-07-19 加的事后排除）；
  `status_strip._forward_progress` 只数 closed & maker_filled，没跟上这道门。
  当前 15 笔全部 lag>30，所以真实进度是 0/100。
- **通用规则**：同一指标出现在多个 payload 时，加新过滤门必须搜全所有计数点同步改
  （与"新鲜度三门同值"同理）；看板两个数字打架时先 diff 两处的过滤链，而不是怀疑数据。
- **牵连**：`src/webapp/status_strip.py::_forward_progress`（缺 freshness 过滤，待 owner 决定是否修）、
  `src/webapp/forward_payloads.py::forward_payload`、
  `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`。
