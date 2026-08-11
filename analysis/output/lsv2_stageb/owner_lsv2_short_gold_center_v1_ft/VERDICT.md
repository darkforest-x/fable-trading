# VERDICT — owner_lsv2_short_gold_center_v1_ft/weights/best.pt

**状态：1:1 easy-negative baseline训练完成，仅可用于hard-negative mining；禁止promote、ACTIVE和部署。holdout未动用。**

## 绑定

- dataset：`datasets/owner_short_gold_center_v1`
- positive manifest SHA-256：`8f4119fbf634ec976077e8eb50b36e57ae3aa0471759cad04f2eaeaeacd6d21b`
- negative manifest SHA-256：`3a32bd618e6944036316a1244329f411bed9b3988740cb9cb54d03a6eaa3c6f0`
- Stage A base SHA-256：`c0e94f47df125e298b044d9f10acd0b8e4f525ccd6143ce34f8d174af802bf1a`
- best.pt SHA-256：`da278820f2d96a64006d9ff6358b7c98faec52249ec8a6f4fe6bf55254fc65b4`

## 训练结果

- 40/40 epochs，best epoch 30，总耗时1,833.54秒；
- epoch 30记录P/R/mAP50/mAP50-95：0.8619 / 0.9010 / 0.9244 / 0.7427；
- 3060最终best复验：0.8508 / 0.9035 / 0.9224 / 0.7302；
- Mac MPS独立best复验：0.8467 / 0.9024 / 0.9206 / 0.7294；
- 训练早期mAP50最低0.0799，曲线存在显著震荡，但best不在预热轮且两机复验一致。

## 诚实裁决

这些指标只基于202个Owner正例与200个随机干净背景组成的val。它证明第一版模型能够区分当前正例和easy negatives，不证明连续市场误报已经解决。交接规范要求下一臂使用1:2/1:3负例且hard negative占大头，并按event precision、首次识别delay3/4/5及FP/1000裁决。

本权重下一步只能冻结后扫描训练时间块连续窗口、收集高置信误火；不得读取holdout，不得改变生产配置。
