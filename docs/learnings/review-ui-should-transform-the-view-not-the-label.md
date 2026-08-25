# 审核图太小时应变换视图，不能重画标签或另造一套 K 线

- **问题**：900×521 的 Owner 长图按原尺寸放进超宽审核容器后，绿色框只占画面几个百分点，K 线与六均线细到无法连续判断；原图正确，但展示尺度错误。
- **死胡同**：单纯把整图拉伸会保留巨大无关上下文，重新从 OHLC 渲染又可能改变窗口、纵轴和 Owner 当时看到的几何，另做一张图还会重演“左右图不是同一 K 线”的混乱。
- **有效路径**：继续读取逐字节不变的 Owner 原图，用原 `yolo_xc/yolo_yc/yolo_w/yolo_h` 只在浏览器 canvas 上裁取绿色框附近并高 DPI 放大；不生成新图片、不改框、不改 manifest、答案 schema 或训练输入。
- **通用规则**：人工审核首先保证目标形态在视口中有足够像素；需要放大时只做可复现的 view transform，并以原始标注几何为锚，绝不借 UI 改进重新定义标签。
- **牵连**：`yoyo/datasets/ma_rope_review.py`、`tests/test_ma_rope_review.py`、`analysis/output/owner_side_review/review_sheet.csv`；审核图仍是 review-only，未来内容不得进入模型输入。
