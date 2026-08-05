# ⭐标杆图库必须以注册表和原始字节为双重真相

- **问题**：需要从一万多张人工标注图里只展示 owner 明确打过 `⭐标杆` 的高质量子集，同时不能把 v10 的裁剪、移框或方向筛选结果误称为原始手标图。
- **死胡同**：直接复用 v10 数据集会混入后续 `source=side` 正样本并展示重处理图；复用 `owner_side_review` 只覆盖与方向审阅相交的标杆；依赖旧的 8765 审阅服务还会因端口被其他服务占用而 404。历史归档目录也并不完整，按 stem 搜到训练裁剪图不等于找回原图。
- **有效路径**：用 `data/benchmark_exemplars.json` 固定 176 张注册标杆及原始归一化框；只接受与 stem 完全一致且可解码的历史图，将原始字节原样复制，并在独立预览副本上叠加手标框。找不到的 15 张明确列为缺失，不用 `_pad200`、tip clone 或训练重裁剪图补位。
- **通用规则**：制作人工金标审查页时，先锁定标注注册表，再校验源图 stem、可读性和字节哈希；原图缺失要显式报缺，任何派生训练图都不能静默冒充原始证据。
- **牵连**：`data/benchmark_exemplars.json`、`scripts/build_star_benchmark_original_gallery.py`、`analysis/output/star_benchmark_originals/`；当前额外原图归档来自 `/Users/zhangzc/Desktop/fable-3060/datasets/dense_owner_v7/images`。
