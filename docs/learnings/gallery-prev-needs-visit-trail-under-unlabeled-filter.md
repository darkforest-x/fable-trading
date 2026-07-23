# 过滤队列 + 标完出队时，「上一张」必须靠 visit trail

- **问题**：多空审阅画廊默认「仅未标」；标完自动下一张后，P /「上一张」要么原地不动（队首），要么跳到更早的未标项，回不到刚标的那张。
- **死胡同**：只在过滤后的 `queue[idx±1]` 上加减——标完当前项已出队，`idx` 原地留给「下一张」，再 `-1` 语义已不是「刚才那张」。把 P 和按钮写成两套 idx 算术也会漂移。
- **有效路径**：维护 `navTrail`（离开过的 box_id 栈）+ 可选 `viewId` 覆盖显示；label / goNext 离队前 `pushTrail`，goPrev 弹栈——若已不在过滤队列仍从 `items` 复查。按钮与 P/← 共用 `goPrev`/`goNext`。
- **通用规则**：凡「过滤视图会因操作移除当前项」的画廊，前进可改 idx，后退必须有独立历史；不要假设 `idx-1` 仍是用户心智上的上一张。
- **牵连**：`scripts/_owner_side_gallery.html`（模板）与 `analysis/output/owner_side_review/gallery.html`（serve 拷贝，改完须同步）；不改标注写盘逻辑。
