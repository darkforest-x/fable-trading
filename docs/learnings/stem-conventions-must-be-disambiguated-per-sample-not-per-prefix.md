# stem 约定要逐样本消歧，不能按前缀归类

- **问题**：Task 3b 要从 `dense_owner_v9` val 侧的 owner 手标框回算 signal 位置。
  按「stem 数字 = 窗末」解析后，496 个样本里 354 个 MAD≈10 被 gate judged 错位，
  只剩 134 个可用，A′ 组塌到 9 个、统计功效归零。

- **死胡同**：
  1. 先怀疑是币种缺 K 线——查出缺的都叫 `okx_ADA_USDT_SWAP`，才发现是 stem 带
     `okx_` 前缀而 `list_series` 以裸 symbol 为键。剥掉前缀后 `no_symbol` 从 361 降到 7，
     **但样本量一个没涨**：它们只是从「找不到币」变成了「MAD 不过」。
  2. 差点据此写成「这批数据与当前 K 线已不一致」——那会把一个解析 bug 记成数据损坏，
     并永久丢掉 73% 的样本。

- **有效路径**：`grep` 本仓 learnings 找到姊妹两条——`stem-index-is-window-end-not-start`
  与 `pad200-mad-gate-off-corrupts-okx-start-stems`。后者写明「`okx_*` 几乎全是 start，
  end_incl MAD≈10」且「把 start 当 end 与把 end 当 start 同样致命」。
  照其处方改为**两种解释各重渲一次、取 MAD 小者**，不看前缀。
  结果：488/496 通过，MAD 不过者 **0**，实测约定分布 start 354 / end 134。

- **通用规则**：混合约定的数据集里，「按前缀分支」和「统一按某一种解释」是同一个错误的
  两种写法——前者假设前缀可靠，后者假设约定唯一。有存档图就让像素说话：
  枚举全部候选解释，用 MAD 选，选不出就丢。**MAD gate 的价值不在于挡住坏样本，
  而在于它能把「解析错」和「数据坏」区分开**——本次两者的表现完全一样（MAD≈10），
  只有换一种解释重算才能分辨。

- **牵连**：`yolo-xx/scripts/exp_teacher_dissection_3b.py`；
  `datasets/dense_owner_v9`（val 侧 start/end 混合）；
  姊妹坑 [stem-index-is-window-end-not-start](stem-index-is-window-end-not-start.md)、
  [pad200-mad-gate-off-corrupts-okx-start-stems](pad200-mad-gate-off-corrupts-okx-start-stems.md)。
