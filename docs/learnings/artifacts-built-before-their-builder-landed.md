# 产物早于 builder 入库 = 复现性缺口，一条 git 命令就能查

- **问题**：`dense_owner_w20_midbox` 的 train/val 切分用 git 里的代码重现不出来。
  `split_of()` 是 `sha1(symbol) % VAL_MOD == 0`，纯确定性；
  `owner_eval.py` 自 2026-07-16 起没改过，比数据集构建还早。
  确定性函数 + 未改动的代码 + 同样的输入 = 结果却不同。哪来的自由度？

- **死胡同**：默认「代码没变所以规则没变」，于是把力气全花在**反推规则**上：
  穷举 `VAL_MOD` ∈ {3..10}、哈希输入换成 base_stem / 整 stem / `okx_`+symbol /
  去 `_SWAP`、换 md5、试种子化随机符号划分（seed × frac 8 组）——**全部不吻合**。
  这些尝试的共同前提是「跑出数据的就是现在这份代码」，而那个前提是错的。

- **有效路径**：比时间戳。
  ```bash
  # 产物什么时候生成的
  jq -r .generated_at datasets/dense_owner_w20_midbox/w20_summary.json
  #   2026-08-06T16:57
  # builder 什么时候第一次入库
  git log --diff-filter=A --format='%ad' --date=iso -- scripts/build_w20_midbox_dataset.py
  #   2026-08-07 13:48
  ```
  **产物比 builder 早了 21 小时。** 跑出这批数据的是一个未入库的版本，
  git 里这份是事后落地的，两者在 split 逻辑上不同。
  规则找不回来了（那份代码不存在于任何地方），但**结论到此已经确定**，
  不必再猜。

- **通用规则**：任何「这份产物由这段代码生成」的推理，
  **第一步先比 `产物 generated_at` vs `git log --diff-filter=A` 的首次入库时间**。
  产物更早 = 生成它的代码不在 git 里 = 一切复现声明未经验证，
  这时候反推参数是纯浪费。这一步是 O(1) 的，反推是 O(搜索空间) 的。
  推论：**先提交 builder，再跑构建**；反过来做，产物落地那一刻复现性就已经丢了。

- **牵连**：
  - `scripts/build_w20_midbox_dataset.py`（首次入库 `bed5e64` 2026-08-07 13:48）
  - `datasets/dense_owner_w20_midbox/w20_summary.json`（`generated_at` 2026-08-06 16:57）
  - `src/detection/owner_eval.py:84` `split_of()`，`VAL_MOD = 5`
  - 后果与补救见 [可复现性要分轴验证](reproducibility-is-per-axis-not-a-boolean.md)
  - 同族教训 [清除记录是主张，不是事实](purge-records-are-claims-not-facts.md)
