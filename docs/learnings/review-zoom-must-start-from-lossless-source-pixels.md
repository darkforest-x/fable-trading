# 审核页局部放大必须从无损原始像素开始

- **问题**：六均线审核页把绿色框附近放满屏幕后明显发糊，Owner 无法可靠判断均线交叉和 K 线实体穿束。
- **死胡同**：继续调 Canvas 的 `imageSmoothingQuality`、放大倍率或浏览器尺寸都不能补回细节；页面输入本身是从 1280×742 原图缩到 900×521、再以 JPEG quality 82 保存的预览，局部只剩约数百像素，二次放大必糊。
- **有效路径**：先查像素谱系而不是 CSS。旧 PNG 软链接虽已失效，但 `dense_owner_side_short/images/*/*.npy` 仍逐样本保存原始 1280×742 BGR 画布；从这些冻结像素生成 lossless PNG，只做轻度聚焦，并用原 Owner YOLO 坐标在 Canvas 画回绿色框。低清 JPEG 仅作加载失败时的回退。
- **通用规则**：审核图模糊时先比较 `naturalWidth/naturalHeight`、实际裁区像素和 CSS 显示尺寸；任何局部放大都必须从最高分辨率、无损、可验证的源像素出发，不能从缩略 JPEG 反复放大。
- **牵连**：`yoyo/datasets/ma_rope_review.py`、`tests/test_ma_rope_review.py`、`datasets/dense_owner_side_short/images/*/*.npy`、`datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/`；只改变 review-only 视图，不改 manifest、标签、split、排序、holdout 或 `training_eligible`。
