# pad200 bulk 关 MAD 会把 okx_ start 窗切成 end_incl

- **问题**：Owner 看 v13 pad200 抽查图说「很多框不对」；初看像学崩，实为切割用错 stem→窗。
- **死胡同**：只查叠画 xywh、只信 close-corr=1.0「金标自洽」、或死记「v11 全是 end_incl」——错窗内部相关仍完美，挡不住 okx_*。
- **有效路径**：对存档 PNG 做候选窗重渲 MAD：`okx_*` 几乎全是 `start`（MAD=0），`end_incl` MAD≈10；bulk `mad_gate=false` 一律 end_incl → ~31% 正样本框罩错 K 线。修复：MAD 默认开；重建需 Owner 点头。
- **通用规则**：v11 stem 约定是混合的；有存档图必须 MAD 消歧，禁止为省 RAM 默认关闸。自洽门（corr）只证「同窗映射」，不证「找对窗」。
- **牵连**：`scripts/build_crop_pad200_dataset.py`；`datasets/dense_owner_v13_pad200`（已污染，勿当净数据）；`analysis/p_pad200_cut_audit.md`；对照 `analysis/output/v13_train_sample20_corrected/`；姊妹坑 [stem-index-is-window-end-not-start.md](stem-index-is-window-end-not-start.md)。
