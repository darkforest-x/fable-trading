# 真实 tip 金标要 Owner 审，不能靠 pad200

- **问题**：要给 tip 模型准备「成败」对照，但本机 K 线盖不住 live 账本，且 pad200 中段裁右已被 Owner 否定。
- **死胡同**：把历史中段金标 remap 到右缘当 tip GT；或只跑 YOLO 有无框、不叠密集规则——分不出 tip-miss-dense vs tip-empty-ok。
- **有效路径**：在 VPS（K 线覆盖信号）对真实 tip 窗（200、右缘=盘口、无后文）叠「密集规则 ∩ tip_edge KEEP」四类预标 + index.html，交给 Owner 改判；scout 只补形态，不当 live PnL。
- **通用规则**：tip 金标采集 = 真 tip 几何 + 规则预标 + Owner 目视；开训门槛是审阅共识，不是张数凑够。
- **牵连**：`scripts/collect_v13_tip_previews.py`；`analysis/output/v13_real_tip_preview/`；`analysis/p_real_tip_collect_started.md`；对照 `mid-gold-right-align-is-not-labelable-tip.md`。
