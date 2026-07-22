# 正负样本必须出自同一条渲染管线,否则模型学风格不学内容

- **问题**:v14/v15(pad200 系)自家 val mAP 0.72,但真实盘口 tip 上密集全漏
  (0/6)、空背景乱开火(58%)——训练看似成功,部署行为完全错乱。
- **死胡同**:先后怀疑训练超参(v14 MAD 开关)、val 协议(v15 tip-align val)、
  验收不公(slice-MA / 脏分母)。验收确有瑕疵且已修(`p_v15_revalidate_fair.md`),
  但修完 v15 仍败——说明病不在验收。
- **有效路径**:抽样对比训练集正负样本的 stem:正样本 100% 是 `_pad200` 重渲图,
  负样本 100% 是旧式原图。两条管线的渲染风格本身可分 → 模型走捷径
  "pad200 风格→右缘画框",完美拟合训练分布,却从未学"密集 vs 非密集"。
  关键判断:当"val 高分 + 部署错乱"并存时,先查**正负样本是否存在与标签
  相关的非语义系统差**(管线、来源、时代、压缩参数),再怀疑模型和验收。
- **通用规则**:构造检测/分类数据集时,正负样本必须经过**逐字节相同的生成
  管线**,唯一允许的差异是标签本身;改造正样本(重渲/裁剪/对齐)时,负样本
  必须做同样的改造。验收集要能捕获这种捷径:用与部署同管线的真实样本,
  同时测"应开火命中"和"应沉默误火"两个方向。
- **牵连**:`datasets/dense_owner_v14_pad200` / `dense_owner_v15_tipval`(病灶);
  v16 规格见 `analysis/p_v15_dataset_confound.md`;公平验收脚本
  `scripts/eval_v15_fair_tip.py`;真 tip 采集 `scripts/collect_v13_tip_previews.py`。
  相关:[tip 验收要分母纯净+full-MA](tip-eval-needs-split-denominator-and-full-ma.md)、
  [检测延迟是模型侧,看框位置不能只看 conf](detector-lag-is-model-side-check-box-position-not-just-conf.md)
