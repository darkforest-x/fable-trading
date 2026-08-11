# 协议确认不等于逐样本确认

- **问题**：Owner确认“只做空”和绿/橙/红代表板方向后，需要把流程扩到200张。这个确认足以冻结
  类别本体与审查方法，但没有逐张确认200个样本及其框。
- **死胡同**：把一句协议确认批量写成`owner_confirmed=true`，会让Codex一审和旧框自动升级为
  Owner金标；特别是61张仍需逐图改框，训练会直接继承已知错误几何。
- **有效路径**：把确认拆成`owner_protocol_confirmed=true`与`sample_owner_confirmed=false`两层。
  200张可以重渲染、分桶和排入改框队列，但全部保持`training_eligible=false`，直到样本标签与边界
  获得明确的Owner授权或确认。
- **通用规则**：任何批量标注流程先问“确认的是规则、代表例，还是每个实例”。只把确认传播到它
  明确覆盖的层级，禁止从协议级确认推导实例级金标。
- **牵连**：`owner_confirmation_receipt.json`；`scripts/build_owner_eth_shortdelay_review200.py`；
  `scripts/review_owner_eth_shortdelay_review200.py`；动态review200 manifest；后续61张逐图改框与训练门。
