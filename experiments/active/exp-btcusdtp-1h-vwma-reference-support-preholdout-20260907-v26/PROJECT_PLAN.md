# V26 — SMA40与VWMA40入口参考：全时钟支持审计

## 单一变化与目标

BTCUSDT.P 1h大实体/吞没直接穿线入口，把SMA40(HL2)的等时间权重改为
VWMA40(HL2)=sum(HL2*volume,40)/sum(volume,40)。40、HL2、形态门、下一5mOPEN、
K1止损、20bp与既有退出SMA规则均不变；本阶段不运行退出或收益。
这是重新生成全部入口，不是给V25剩下9笔放宽事件门。V25支持不足、V24入口持续性
未通过、V19停止纯退出微调结论保留。量权不等于主动买卖量/持仓成本或盈利证据。

原V1 config.baseline逐项固定，尤其max_cross_count=999与max_extension_atr=99，
不能被默认inf替换。ma_side、slope、cross_count和extension随各臂参考一致重算，
price/ATR/shape/volume_ratio等共同字段保持一致。旧冻结hourly_impulse.py不修改。

## 数据、时钟与先后顺序

仅V20保存完整小时OHLCV、V4全部原mothers及来源checkpoint/config；不得读旧收益文件、
V24labels、原始5m归档或2025+价格。锁定输入SHA与历史git源码字节，查V20失败/恢复
同一featurefreeze来源链。volume为原12根5m的和；本轮不独立证明聚合或交易所真实性。

各fold全时钟E∈[start,end−72h)，S=E−1h，每小时两方向均保留；形态合格子账本另外保存。
先对保存trace只读时间列核UTC/唯一/顺序/hour grid/pre2025及覆盖完整允许边界，
不能仅以复原旧251断言覆盖全部新增入口。缺失内部小时在完整机会账本为unknown；
所有滚动特征只按真实hourgap重置，不在半年/月边界重新预热。

源码/config/plan/tests先提交，results拒绝覆盖。运行开始写started并核输入；
先仅计算SMA，通过冻结函数全部commonfeatures及原251每列parity、55/66/55/75分段
与130/121方向核对后写baseline_reproduced。此前不得计算新VWMA。原基准不符先停。
然后计算VWMA与机会账本；写结果前再次核输入及源码收据，冻结支持结果，不进入收益。
异常保留failure及已有检查点，不覆盖、不自动重跑。实际运行环境为项目.venv。

## 三值语义与计数

OHLC不合法或时间身份非法直接失败。VWMA要求40连续小时、40份实际量有限非负、
总量>0；零总量/缺失/非法volume让相应新臂参考unknown，不填0/1、不前填、不删旧SMA。
volume_ratio也只能用有效实际量；未启用量比门不能因volume_ratio缺失删SMA资格。
无效volume记录质量标记，不假称可执行的零成交。SMA价格资格与VWMA参考可观察性分开。

以上非法量语义是纯特征/账本的边界契约。本次固定十项来源的实际runner还必须通过
旧函数的完整OHLCV复现；如果冻结原源含非法量或缺volume列，旧函数拒绝时整轮失败，
不能宣称成功验证了该数据集的SMA。不会伪造量去通过parity；接入其他数据源需另行审计。

shape定义只用当前OHLC、ATR14与前一实体；规范化缺失为unknown，满足large-or-engulf及
收盘位置为qualified，否则not_qualified。已知shape为false时，入场逻辑AND为abstain，
即便参考不可用（参考未知另列）；shape合格时参考或活跃cross_count/extension缺失为unknown。
require_ma_slope=false不能增加43根预热门；未知side的flip沿用冻结0编码定义，不能改策略。

每个全时钟/方向分别记录两臂accepted/abstain/unknown，所有accepted必须与原
make_entries集合一致。重叠分为both_accepted/sma_only/vwma_only/both_abstain/any_unknown；
任一unknown不能冒充均不接受。全机会和shape-qualified两种分母均报全体/四半年/
双方向/24月份；零月保留。另报线差/ATR，仅描述不作为新门或选择长度。

## 固定继续屏障

新臂>=80accepted、每半年>=12、>=12活跃月、每半年>=3活跃月。
这是实务支持屏障，不是统计功效或盈利条件。若新旧accepted集合完全相同则
no_entry_change；支持不足则insufficient_support_no_outcomes，均不读新臂收益或扫描长度。
支持通过仅允许另预注册新增母单的背景支持与经济阶段：旧248三控不能覆盖新入口；
新配对按全部新入口>=90%而非旧226绝对数，单seed冻结后才能读标签。主4h诊断与V18
实际执行路径须在各自阶段事前固定，不根据更好的时钟/退出挑选。无本轮随机分配。

## 验证、交付、限制

synthetic覆盖SMA完整parity、均匀量/量缩放、单高权重、缺根/零量/非法量、前缀因果、
方向镜像、精确边界、反例损坏、全机会/未知/原251身份。保存结果须再独立计数与复核
关键参考公式，不将主函数测试冒充独立审计。报告包含前后对照、候选/未知/支持分母、
逐类变化原因、来源和复现命令。无收益/分类模型，AUC/PF/p/top-decile/胜率不适用；
严格零假设实现对照为等量时SMA=VWMA及损坏因果/身份输入必须失败。

按仓库要求先MD立即转HTML，再canonical报告包装和实际可用的验证；完整CLI/CSV/tests
代替重复notebook，读者可从冻结源码逐步复核。记录未完成的浏览器/手机QA，不夸完成。
使用experimental-design固定比较与分母、source-driven-development查API、数据质量
技能审来源/未知。项目Python3.9.6、pandas2.3.3、numpy2.0.2，不安装DOE依赖或重排时间。

参考：
https://www.tradingview.com/support/solutions/43000592293-volume-weighted-moving-average-vwma/
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.rolling.html
HL2是刻意保持原价格源；不是官方默认close。不采用Session/周锚VWAP的额外时钟变化。
2023–2024是多次使用的开发期；2025–早2026也不是干净独立验收。后续仍需配置冻结后
真正新前向样本，100笔只是项目起点非功效保证。无holdout/训练/ACTIVE/TV/实盘变动，
盈利目标保持active且未达成。
