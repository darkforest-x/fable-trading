# A 组评估冲突：验证补边不同，固定画布模型对 16 像素位移脆弱

2026-09-22，A组已在RTX3060完成40轮，B组继续原冻结任务。本次定位到两套评估的主要差异：同一 `best.pt`、同一图片和标签、同一Ultralytics AP，只改变验证dataset的padding，验证mAP50从0.08650变为0.99465，测试从0.05496变为0.99500。没有重新训练或更换权重。统一预处理解决指标不可比问题，但不消除模型对小位移的真实脆弱性。

## 数据、来源与复现

- 实验：`exp-ma-morphology-negatives-20260922-v3`。诊断源码先以`67e83a572f`提交，再执行。
- 原数据：`datasets/ma_launch_owner1500_morph_v6`。训练1506正+1506负；验证175正+175负，2026-01-01至04-30；测试160正+160负，2026-05-01至08-31。各split正类率50%。诊断使用全部350张验证和320张测试，没有挑图。
- 670张图片、原标签、隔离复制的标签均逐文件核对manifest SHA；两份清单与manifest集合相等，尺寸均宽1280高742。独立子代理复核相同集合及评分代码，无预测阶段使用GT捷径。
- 权重SHA：`17aee5679b73dfd9183339c580c2caac323fc2b6c0759da38827834d72f2742b`。来自原远端`arm_A/weights/best.pt`，下载后与training_receipt一致。
- Ultralytics8.4.89 / torch2.8.0，Mac CPU4线程、batch8；B在3060继续，诊断不占GPU。`conf=.001, iou=.7, imgsz=1280, rect=True`保持一致。

```bash
mkdir -p experiments/active/exp-ma-morphology-negatives-20260922-v3/evaluation_diagnosis
scp Administrator@192.168.1.2:C:/fable/runs/ma_launch_owner1500_morph_v6/arm_A/weights/best.pt experiments/active/exp-ma-morphology-negatives-20260922-v3/evaluation_diagnosis/arm_A_best.pt
scp Administrator@192.168.1.2:C:/fable/runs/ma_launch_owner1500_morph_v6/training_receipt.json /tmp/ma_morph_training_receipt.json
.venv/bin/python - <<'PY'
import json
from pathlib import Path
p=Path('experiments/active/exp-ma-morphology-negatives-20260922-v3/evaluation_diagnosis/remote_snapshot.json')
if not p.exists():
    p.write_text(json.dumps({'receipt':json.loads(Path('/tmp/ma_morph_training_receipt.json').read_text())}))
PY
.venv/bin/python -u -m scripts.research.diagnose_ma_morphology_padding
```

保留已有输出，不覆盖旧run；重放时Ultralytics自动另建目录，脚本读取validator实际返回目录。本次没有二次运行或挑选输出。诊断数据目录只建立原图软链接和标签副本，原冻结数据未变。首轮源码67e83a572f后仅补充SHA/集合断言和重放输出目录定位；不改变推理或评分。

## 直接原因与单变量结果

本机库`data/build.py`设置训练`pad=0`、验证`pad=.5`；`BaseDataset.set_rectangle()`计算`ceil(shape * imgsz / stride + pad) * stride`。本批图片由此在训练与单图predict得到高768宽1280，而原生val得到高768宽1312；`scaleup=False`不拉伸图像，左右各补16灰像素，上下仍各13。

官方说明predict采用最小矩形补边：[Ultralytics predict](https://docs.ultralytics.com/modes/predict/)。本次具体16像素差异依据本地8.4.89源码及实际张量记录，而非根据新版本文档推断。

| 同权重、同标准AP | 原CPU验证pad=.5 | 对齐训练/predict的pad=0 |
|---|---:|---:|
| 验证 mAP50 | 0.08650225 | 0.99465000 |
| 测试 mAP50 | 0.05495779 | 0.99500000 |
| 验证 mAP50–95 | 0.05515698 | 0.99113791 |
| 测试 mAP50–95 | 0.03337398 | 0.99292337 |
| 实际宽×高 | 1312×768 | 1280×768 |
| 左右补边 | 各16像素 | 0 |

CPU默认测试mAP50与原3060回执逐数相等；验证mAP50比原0.08623583差0.00026642，存在跨设备数值差异，本实验不把它误认为逐字节重现。padding两臂均在同CPU上运行，归因不依赖跨设备比较。

原common evaluator的174/175、160/160是“至少一个同类框IoU≥.5”的命中率，本来就不等于AP，而且没有统计正图内额外框。但实际val350图总共只有176个高置信度框、test320图161个，不能据此把巨大差异全部归为FP口径。本轮统一使用原生一对一匹配与完整AP后仍恢复高分，确认补边是主要直接原因。

## 可逆否证对照

对同一BAND验证图`event_a02b0e9a1302e2c6d1a4579b0c7b96ea_A.png`和同权重，固定阈值及原图像素：

1. 原画布：正确空头框conf0.97437489、IoU0.99252235。
2. 只在左右各增加16灰像素：出现错误多头框；同类框最大IoU0.27278991，失去命中。
3. 去掉补边：第一步框坐标、confidence、IoU完全恢复。

这比只比较两个汇总数字更能排除权重改变或随机状态的解释。非方向性预处理诊断不使用收益标签，val收益AUC、置换收益p、top-decile毛/净收益、胜率、单特征收益基线及匹配随机入场不适用；以该可逆扰动与完整670图单变量对照作为等效否证检验。

## 风险与诚实声明

- 99%来自本批精选启动正例对明确非密集背景；未覆盖真实扫描分布或困难边界，不能宣称泛化已合格、可以赚钱或可实盘。
- A组1506训练正例的框横向中心只有两个值：704张为0.608984、802张为0.614844。位置分布过窄是**已确认的数据事实**；模型是否通过位置捷径学习是推测，未被本实验单独证明。已确认的是小补边扰动足以使预测失效。
- 训练各轮验证同样使用额外补边，因此原`best.pt`的选择受此验证口径影响。原日志最佳mAP50–95落在第6轮；没有按测试集重新挑权重。`last.pt`与原`best.pt`均保留。
- 原始失败指标不删除；既有training_receipt和正在运行的B代码不修改。新诊断值另存，不能让自动交付遗漏旧值与失效案例。
- 标签仍非新增Owner逐样本金标，模型仍含core+5确认，不是实时tip模型，生产资格不变。

## 下一步

统一A/B的训练、验证、预测张量口径，并分别报告原始与对齐结果；B结束后做相同对照，先不据A的高分改训练。随后用保持时间方向的原OHLC重新裁窗检查位置/缩放稳健性及困难样本，禁止用翻转、mosaic或未来K线增强掩盖问题。需要重训或修改数据方案时另记录实验变量；本次只诊断，不重启训练、不自动promote。

原始证据在`experiments/active/exp-ma-morphology-negatives-20260922-v3/evaluation_diagnosis/`：`remote_snapshot.json`、`input_integrity.json`、`one_image_padding_probe.json`、`padding_comparison.json`、四组`runs/*/geometry.json`与`predictions.json`。
