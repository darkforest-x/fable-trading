# 审核未来图必须随 K 线根数加宽，不能塞进训练画布

- **问题**：Owner 审核形态需要看见检测之后的整体涨跌；把未来 K 接到短窗图上之后，如果画布仍是训练用的 1280×742，后续几十到上百根会被压成细线，看起来还是“没看到走势”。
- **死胡同**：只换目录、不换画布——`review_future_only` 与训练图物理隔离了，但仍用 `IMG_WIDTH=1280` 去画 window+48 未来，等于把未来压缩进 12–19 根的训练布局。另一条死路是把未来画进原短窗 PNG：训练输入 SHA 无法证明未变。
- **有效路径**：训练短窗及其 SHA 冻结不动；审核图另写目录、禁止 `labels/`；橙框按当时 YOLO 核心 K 线在新画布上重映射，不要手画、也不要把短窗 `x1n` 直接乘到加宽图上。画布宽度随根数增加（本轮 1920–4480），未来画到 snapshot 最后一根；不够 40 根就标明截断。
- **通用规则**：人工要比模型多看的信息，必须同时满足路径隔离和显示尺度隔离。目录分开只防误收训练；尺度不加宽则 Owner 仍看不见未来。
- **牵连**：`docs/learnings/human-review-future-context-must-be-physically-separated-from-training-input.md`；`scripts/build_owner_short_hardneg_canary_review.py` 的 `render_human_review_chart`；`analysis/output/yoyo_r3a_v3gold_ft_r1_holdout_losers3d_20260813/viz_future/`。
