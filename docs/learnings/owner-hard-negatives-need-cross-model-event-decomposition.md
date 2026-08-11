# Owner确认难负例也必须做跨模型事件分解

- **问题**：用531个Owner确认误报替换同W桶hard negative后，固定val mAP50-95保持0.741，
  但新连续块事件只从223降到195，仍约398 events/day。
- **死胡同**：只看val mAP或净事件数223→195，会把“模型学会拒绝误报”说得过头；净数没有显示
  R2压掉60个旧事件的同时又产生32个新事件，且自身195个事件中163个仍是旧问题。
- **有效路径**：在从未用于选样的非重叠连续块上让R1/R2使用同币、同endpoint、同W、同conf和同
  去重规则同场扫描；随后按同币核心中点±5根做一对一配对，同时报告retained、suppressed、new。
- **通用规则**：主动学习模型的连续验证不能只报总量降幅；第一张结果表必须同时给出旧问题保留率、
  被抑制事件、新生事件和净变化。大量新生事件意味着决策边界迁移，不等于负例泛化成功。
- **牵连**：`scripts/compare_owner_short_canary.py`；
  `analysis/p2_owner_short_gold_center_hardneg_r2_canary_20260812.md`；conf=0.25、NMS=0.70、W12–19、
  core-mid±5 bars去重；holdout未读取，R2不得promote。
