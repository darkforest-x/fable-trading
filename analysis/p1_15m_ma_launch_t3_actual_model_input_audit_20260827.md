# 15m t-3 YOLO：模型实际训练输入审计

## 结论先行

- 磁盘训练源文件是 **1280×742 PNG**，文件本身没有被离线压缩、重编码或拉伸另存。
- 1280 本轮模型实际接收的预归一化 RGB 张量是 **1280×768**；它不是源 PNG 的逐像素副本。
  `rect=true` 保持宽高比并补到 stride 整数倍，训练端还保留 `scale=0.1 / translate=0.02`，所以会看到
  灰色补边以及轻微缩放/位移。
- 数据加载后，`preprocess_batch` 只把 uint8 RGB 张量搬到 GPU、转 float 并除以 255；
  `multi_scale=0`，之后没有再次改变图片尺寸。因此本报告中间列的 `*_model_input.png` 是模型输入像素
  的无损可视化，网络实际数值就是这些 0–255 像素除以 255。
- 正样本的框以独立 target tensor 送入损失函数，**不会画在模型输入图上**。右列黄框只是为 owner 查看而生成
  的副本。负样本 label 为空，所以没有框。
- 昨夜运行用了 `plots=false`，没有保存历史 `train_batch*.jpg` 或逐 batch 哈希。这里是在同一台 3060、同一数据、
  同一版本和同一确定性配置下的首轮 loader 重放；连续独立重放两次，8/8 输入 SHA、样本路径和变换后 targets
  全部一致，但不能把它冒充为“昨夜已独立存档的历史截图”。

## 正样本：源图、模型输入、标签可视化

三列含义固定：左列是磁盘源 PNG；中列是模型真正接收的预归一化像素；右列是同一张模型输入的黄框可视化副本。

![4 个正样本的实际模型输入](../experiments/active/exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1/results/model_input_audit_3060/positive_actual_model_inputs.png)

## 负样本：源图、模型输入、空标签

easy 与 hard 各抽 2 个。中列和右列看起来相同是正确现象：负样本标签文件为空，模型只收到背景图，不收到框。

![4 个负样本的实际模型输入](../experiments/active/exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1/results/model_input_audit_3060/negative_actual_model_inputs.png)

## “压缩过”与“原图训练”到底指什么

| 层级 | 旧 960 训练 | 新 1280 训练 | 是否改写磁盘文件 |
|---|---:|---:|---|
| 数据集源 PNG | 1280×742 | 1280×742 | 否 |
| train loader / 模型输入 | 960×576 | **1280×768** | 否，只在内存处理 |
| GPU 前归一化 | RGB uint8 | RGB uint8 | 否 |
| GPU 数值 | float RGB / 255 | float RGB / 255 | 否 |

因此，之前“被压缩”指旧 `imgsz=960` 在内存里把长边从 1280 降到 960；“源图没有压缩”指磁盘上的
1280×742 PNG 没有被改写。新 1280 训练消除了水平方向的 1280→960 下采样，但没有取消 letterbox 和
`scale/translate`。此前没有明确说出这三个层级，表述不合格。

## 数据与抽样身份

| 项目 | 数值 |
|---|---:|
| 冻结数据集总量 | 36,812 |
| train / val | 33,872 / 2,940 |
| train 正 / 负 | 8,468 / 25,404 |
| 本次读取 split | train only |
| loader batch / 首轮检查批次 | 8 / 前 4 批 |
| 展示正样本 | 4（long 2、short 2） |
| 展示负样本 | 4（easy 2、hard 2） |
| 源图尺寸 | 8/8 为 1280×742 |
| 模型输入尺寸 | 8/8 为 1280×768 |
| dataset manifest SHA-256 | `dd55246938b03c4b2013d159cdfee94b4e9db56ecb298ad30145eb5d1bc2bc3a` |
| 训练 args SHA-256 | `70affb53d9736caebcbe52291a1cba73b27d22725e297f8c08a7a1293c0b1ff7` |

样本明细：

| 类别 | 币种 | sample_id | 首轮 batch/slot | target 数 |
|---|---|---|---:|---:|
| positive long | ADA | `89f092171a762ef5f164be76` | 1 / 0 | 1 |
| positive long | SHELL | `0c9e7b62f5252aaa4ba3824f` | 1 / 1 | 1 |
| positive short | SUI | `db44c9ff2f61d53787affa46` | 1 / 7 | 1 |
| positive short | BONK | `522bfb2d9a5704f376b8b2e3` | 4 / 1 | 1 |
| negative easy | RSR | `9235488bced3c14a2ae25125` | 1 / 4 | 0 |
| negative easy | APT | `41afadd6b9743cfbc3144c27` | 2 / 2 | 0 |
| negative hard | ETC | `51e83b999ea03b94aa267dbd` | 1 / 2 | 0 |
| negative hard | ARB | `af8aa16073d7b1e4d2a60068` | 1 / 3 | 0 |

## 配方核对

| 参数 | 实际值 |
|---|---:|
| 主机 / GPU | `Administrator@192.168.1.5` / RTX 3060 |
| ultralytics | 8.4.89 |
| imgsz | 1280 |
| rect requested / effective | true / true |
| seed / deterministic / workers | 0 / true / 2 |
| scale / translate | 0.1 / 0.02 |
| multi_scale | 0 |
| flip / HSV / mosaic / mixup | 全 0 |

## 零假设对照与复现性

这是渲染/数据管线审计，没有方向收益，因此 val AUC、置换收益检验、top-decile 净收益、胜率和匹配随机入场
都不适用，不能编造。对应的严格零假设是：“同一冻结输入与确定性配方的独立重放会产生不同训练输入”。

在原 3060 上独立执行两次，逐项比较 `source_relative_path`、源 SHA、模型输入 SHA 和变换后 targets：

| 对照 | 结果 |
|---|---:|
| 重放次数 | 2 |
| 比较样本 | 8 |
| 路径一致 | 8/8 |
| 模型输入 SHA 一致 | 8/8 |
| 变换后 target 一致 | 8/8 |
| 零假设 | 拒绝 |

验证回执：`model_input_audit_3060/replay_verification.json`。

## 完整复现命令

```bash
# 本地语法检查
.venv/bin/python -m py_compile scripts/audit_15m_ma_launch_t3_model_inputs.py

# 把受控审计脚本送到原训练主机
scp -o BatchMode=yes -o ConnectTimeout=15 -q \
  scripts/audit_15m_ma_launch_t3_model_inputs.py \
  Administrator@192.168.1.5:/C:/fable/audit_15m_ma_launch_t3_model_inputs.py

# 在同一冻结 train split 上重放，不启动训练、不读取 holdout
ssh -o BatchMode=yes -o ConnectTimeout=15 Administrator@192.168.1.5 \
  "C:/fable/.venv/Scripts/python.exe C:/fable/audit_15m_ma_launch_t3_model_inputs.py \
  --dataset-dir C:/fable/datasets/ma_launch_t3_10000_v1_imgsz1280_input \
  --args-yaml C:/fable/runs/detect/ma_launch_t3_10000_v1_y11s_ft_imgsz1280/args.yaml \
  --out-dir C:/fable/audits/ma_launch_t3_imgsz1280_actual_inputs_v1 \
  --workers 2 --max-batches 128"

# 转 HTML
python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_t3_actual_model_input_audit_20260827.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- 这是确定性重放，不是历史 runtime snapshot；昨夜没有留下可与本次 SHA 对照的 batch 证据。
- 两次同机重放一致证明当前冻结配方可复现，也证明这里展示的是训练代码路径的真实输出；它不能逆向创造昨夜未保存的证据。
- 联系表为了排版会缩放显示；目录中的单张 `*_model_input.png` 才是原尺寸 1280×768 文件。
- 没有读取 ≥2026-05-04 holdout，没有训练、改权重、调阈值、promote、部署或改实盘状态。
- 此弱标签数据集仍不是 gold，`training_eligible=false`、`production_eligible=false` 不变。

## 下一步选项

1. 仅保存本审计结果，不改训练配方。
2. 以后每次训练强制保存首轮真实 batch 输入、targets 与 SHA，杜绝事后只能重放。
3. 若 owner 想测试取消 `scale/translate`，必须作为新的单变量实验另行批准；本轮不擅自改。
