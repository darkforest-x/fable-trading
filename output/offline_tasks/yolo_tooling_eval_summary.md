# YOLO Tooling Eval Summary

- dataset: `/Users/zhangzc/fable-trading-codex/datasets/dense_15m_full`
- weights: `/Users/zhangzc/fable-trading-codex/runs/detect/runs/detect/dense_15m_full_s/weights/best.pt`

## fiftyone_import_probe
- ok: `True`
- samples: `1255`

## direct_yolo_sample_eval
- ok: `True`
- sample_size: `80`
- gt_boxes: `97`
- pred_boxes: `106`
- matched_iou50: `77`
- recall_like_iou50: `0.7938`
- pred_per_gt: `1.0928`

## sahi_sliced_sample_eval
- ok: `True`
- sample_size: `80`
- gt_boxes: `97`
- pred_boxes: `178`
- matched_iou50: `75`
- recall_like_iou50: `0.7732`
- pred_per_gt: `1.8351`
