# ultralytics 的 optimizer='auto' 会在 epoch 3 炸掉续训模型

日期: 2026-07-16
影响范围: **本项目至今所有 YOLO 模型(v3 ~ v7)**

## 现象

看 owner_v7_chain 的训练曲线,它是当时的生产模型(frozen-F1 0.625):

    epoch:  1     2     3     4     5     6     7     8     9    10    11
    mAP50: 0.383 0.343 0.000 0.000 0.287 0.005 0.333 0.326 0.029 0.000 0.173
           ↑最好                     11 轮里 5 轮直接崩到 0

`best.pt` = **epoch 1**。也就是说 v7_chain ≈ v6_chain + 一个预热轮次。

## 病因(已用两次独立训练交叉验证)

`src/detection/train.py` 没传 optimizer/lr0,ultralytics 默认 `optimizer='auto'`。
auto 的公式是:

    lr0 = round(0.002 * 5 / (4 + nc), 6)

本项目 nc=1(只有 dense_cluster 一类)→ **lr0 = 0.002**,并配 AdamW。
这是**从零训练**的学习率。拿它续训一个已收敛的检测器 = 灾难性遗忘。

配合 `warmup_epochs: 3.0`,曲线形状就完全解释了 —— 把 lr 和 mAP 并排放:

    owner_v7_chain                      owner_v7_holdout
    epoch  lr        mAP50              epoch  lr        mAP50
      1   0.000665  0.3832  ←最好         1   0.000665  0.7235  ←最好
      2   0.001299  0.3432                2   0.001310  0.6175
      3   0.001900  0.0000  ←崩           3   0.001933  0.0414  ←崩
      4   0.001852  0.0004                4   0.001901  0.0036

**两次独立训练,都在 epoch 3、lr 爬到 0.0019 的那一刻精确崩溃。**
epoch 1 之所以最好,正是因为 warmup 还没把 lr 拉上去——**模型在训得最少的时候最好**。

## 连带被推翻的结论

- **"v6 0.595 → v7 0.625 证明加标注有效"** —— 不成立。这 0.03 来自一个 epoch,
  不是来自 round6 那 2000 张新标注被学会。加标注可能真的有效,但**这条曲线证明不了**。
- **owner_best.json 里的 clean_curve** —— 记录的是"每一版的第一个预热轮次",
  不是学习曲线。
- **决定性 A/B(owner_v7_holdout)** —— 双重无效:
  1. 基础权重是 v7_chain,它训过那 30% "留出" 币种 → 和 v5 的 0.663 是同一种泄漏
  2. 训练本身崩了,best.pt = epoch 1 ≈ 就是那个污染的基础模型

## 修复

`src/detection/train.py` 加 `FINETUNE_OPT`,续训时自动启用:

    FINETUNE_OPT = dict(optimizer="AdamW", lr0=1e-4, lrf=0.01, warmup_epochs=0.5)

按 `--model` 是不是 `yolo11*.pt` 自动判断冷启动/续训,也可以用 `--finetune/--no-finetune`
强制。冷启动仍然走 auto(那本来就是它的正确场景)。

## 教训

**"默认值是安全的"是个危险假设。** `optimizer='auto'` 名字听起来像"它会帮你选对",
实际上它只知道 nc,不知道你在续训还是冷启动 —— 这两种场景的合理 lr 差 20 倍。

**而且这个 bug 不崩溃、不报错、不留下任何异常日志。** 它只是安静地让每个模型都停在
第一个 epoch,然后 early stopping 把这个"最好的"权重保存下来,一切看起来都很正常。
F1 0.625 是真实测量出来的,只是它测的是一个从没训练过的模型。

**排查入口是 results.csv,不是最终报告。** 最终报告只给一个 fitness 数字;
逐轮曲线才能看出模型是"稳步爬升"还是"崩了又爬、爬了又崩"。
以后每次训练完,第一件事是看曲线形状,不是看最终分数。

相关: [[nice-does-not-isolate-gpu-contention]] —— 同一天发现,都是"看起来在正常工作
的东西其实没在工作"这一类。
