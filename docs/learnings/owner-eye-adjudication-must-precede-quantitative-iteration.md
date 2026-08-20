# 池子定义没过 owner 眼睛之前,一切定量迭代都是白算

- **问题**:yoyo-eth 连跑五轮(MVP → P02 网格 → iteration_v1 因果锚 → P03 复检),
  每轮都严谨——对抗审查、walk-forward、匹配对照、修 bug。owner 翻盲审图廊一眼:
  "基本都不行"。量化验证:q0.30 定义下 29% 的 K 线都算"密集",决策点里只有 ~30%
  坐在任何像平台的东西上。**五轮优化的对象从头就不是 owner 要的形态。**
- **死胡同**:MVP 文档明写"人工复核图是最重要的验收环节之一",但执行顺序上它被
  当成了每轮的"交付物之一"而非"下一轮的闸门"。三轮迭代全在给一个未经眼睛验收的
  池子换时点(zone_start→exit→anchored trigger)、换评估(单切分→walk-forward)、
  修统计 bug。下游每一步都对,上游定义错,全部白算。触发条件
  (close<ma_lower + ema20 下弯)在松池子里就是普通阴跌的同义词——语义修饰救不了
  定义性偏差。
- **有效路径**:owner 说"不行"后倒序重来:先只做定义(q0.10 + 核心 4–12 根 +
  段内幅度<3ATR + sma20 漂移<0.5ATR),330 个 episode 缩到 31 个候选,**不训任何
  模型**,只出盲审图请 owner 裁决命中率;过关才恢复建模(触发/walk-forward 基建
  全部现成可复用)。
- **通用规则**:形态类研究的第一个 milestone 不是 AUC 也不是管道跑通,而是
  **"owner 翻 50 张候选图,命中率过半"**。这一关不过,禁止进入任何模型/评估迭代。
  每当收到"宽松召回,别加条件"式的任务书,把"图廊验收"排为下一轮开工的前置闸门,
  而不是报告附件。等价于 fable-trading 的 Local Signal V2 纪律:语义金标先由
  owner 亲自确认,再谈训练。
- **牵连**:`/Users/zhangzc/yoyo-eth/scripts/scan_strict_platforms.py`、
  `reports/platform_v2/review_gallery.html`;呼应 memory
  `owner-instincts-deserve-experiments`(owner 一眼能裁决的事,别排在三轮实验后面)
  与 `zero-live-edge-labels-means-the-target-is-unverified`(目标未经验证 = 一切
  下游数字悬空)。
