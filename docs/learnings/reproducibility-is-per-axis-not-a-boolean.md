# 可复现性要分轴验证——哈希对上不等于数据集可复现

- **问题**：提交 `4b5f48b` 把 800MB 的 `datasets/*/images` 排除在 git 外，
  理由写在提交信息里：「regenerable from manifests + builders」。
  要验证这句话，最自然的做法是同 seed 重跑一遍、比 sha256。

- **死胡同**：
  1. 先跑了 `--limit 30`，30/30 图片和标签 sha256 全中，当场差点结案。
     **样本太小且全取自排序头部（都是 train），什么都没证明。**
  2. 换全量 `--limit 0`，2635/2635 图片 + 2635/2635 标签逐字节一致。
     这次证据强了，但**我仍然只验了一个轴**。
  3. 真正的破绽在重建脚本自己打印的 summary 里：`counts: {train: 2635, val: 0}`，
     而原数据集是 `train 2230 / val 405`。**哈希全对，切分全错。**
     如果只比哈希、不比落点，这个缺口永远不会被发现。

- **有效路径**：把「数据集可复现」拆成互相独立的几个轴，逐个比：
  **① 像素/标签内容 ② split 落点 ③ 样本集合（有无多缺）**。
  本例 ① 全过、③ 全过、② 405 个不符。
  找规则失败后（穷举 `VAL_MOD` 3–10、多种哈希输入、md5、种子化随机划分全不吻合），
  改用**把结果钉死**替代**把规则找回**：`manifest.jsonl` 逐样本记录真实 split
  + `image_sha256`，重建后按 manifest 重新分目录即可还原。
  **「规则可复现」降级为「数据可复现」，代价小，立刻生效。**

- **通用规则**：声称一批产物「可从代码重建」时，第一步不是比哈希，
  而是**列出这批产物有几个自由度**。渲染内容、切分、顺序、文件名、目录结构
  都是数据集的一部分；只验证其中一个就宣布可复现，等同于没验证。
  重建脚本自己打印的计数（counts / n_manifest / skip_reasons）是最便宜的第二个轴，
  先看它再动手比哈希。

- **牵连**：
  - `scripts/backfill_dataset_manifests.py`（回填 + 三项不变量审计）
  - `tests/test_manifest_backfill.py`
  - `datasets/dense_owner_w20_midbox/manifest.jsonl` / `manifest_audit.json`
  - 报告 `analysis/p_w20_manifest_traceability_20260810.md`
  - 根因见 [产物早于 builder 入库](artifacts-built-before-their-builder-landed.md)
  - 同族教训 [清除记录是主张，不是事实](purge-records-are-claims-not-facts.md)
