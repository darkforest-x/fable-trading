"""Package the reviewed Pine indicator and compiler receipt; no price replay.

Source semantics are exp-imacd-ma-mtf-20260907-v3 plus the explicit conservative
HTF boundary delay. Reads local source/receipt only. No training, performance
evaluation, execution, or modifications to historical research reports.
"""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'yoyo/evaluation/pine/imacd_dense_mtf_v1.pine'
OUT=ROOT/'experiments/active/exp-imacd-ma-mtf-20260907-v3/pine_indicator'


def main():
    source=SOURCE.read_text();digest=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    receipt=json.loads((OUT/'compiler_receipt.json').read_text())
    assert receipt['source_sha256']==digest
    report=ROOT/'analysis/p0_imacd_pine_indicator_20260907.md'
    report.write_text(f'''# IMACD 零轴密集启动 · 多周期趋势 V1

已按本轮系统定义编写为Pine v6指标，保留IMACD副图，并把六均线和启动/结束标记画到主图。

[打开完整Pine源码]({SOURCE})。复制全部内容，在TradingView的Pine编辑器新建空白指标，粘贴后添加到图表。
若账户指标名额不足，可以用此指标替换原IMACD；它已经自带六均线显示，不必额外叠加同样的均线。

## 三种模式

| 模式 | 入场提示条件 | 用途 |
|---|---|---|
| 密集启动（默认） | 前12根六MA宽度/ATR均值≤3、交织≥2，本根md首次离零 | 对应研究P02的核心条件，不启用多周期过滤 |
| 密集＋共振 | 最近34根出现密集，本根首次离零且close离开六MA带，高周期许可 | 当根确认，条件不足则跳过 |
| 密集＋共振等待 | 启动时锁定密集资格，最多再等9根首次共振 | 期间md回零/反向取消，不能事后补密集资格 |

六均线固定为close的SMA20/60/120及EMA20/60/120；形成窗口不包含启动当根。IMACD默认34/9。
高周期许可可选“主线同向”“主线或动量同向”“同向或零轴”。默认许可方式允许已知md=0。
可再打开低周期主线同向，缺少低周期数据时不发新启动。

## 怎么看

- 黄色小圆点：md在零轴，且满足当前六MA密集。
- 主图绿色向上三角“多启动”：多头启动在该根收盘确认。
- 主图红色向下三角“空启动”：空头启动在该根收盘确认。
- 副图浅绿/浅红背景：指标正在跟踪一段多头/空头趋势。
- “多结束/空结束”叉号：md回零或反向；不会因为一次md/sb反向交叉就提示结束。
- 面板显示模式、趋势、高低周期状态、形成宽度和交织次数。“持有”是指标状态，不是读取了你的实际账户仓位。

启动/结束标记画在确认K线上，意味着收盘后才知道；本版不是策略回测，没有把标记价格冒充下一根实际成交价。
在TradingView创建警报时，选本指标的多启动、空启动、趋势结束或等待共振，并选择“每根K线收盘一次”。本轮没有代你建立警报或下单。

## 周期与初始设置

可先在OKX:ETHUSDT.P或OKX:BTCUSDT.P的普通4小时蜡烛图查看，保持默认“密集启动”。
要观察共振，在设置中切到“密集＋共振等待”，保留“同向或零轴”；这只是第二种观察模式，不代表已证明比默认更赚钱。
4小时自动对应日线背景、1小时低周期；1小时对应4小时/15分钟；15分钟对应1小时/5分钟。
分钟及秒级图若启用低周期，数据可用性还受交易所与TradingView套餐限制；1秒图没有更低自动周期。
非普通时间图表（如Heikin Ashi、Renko、tick）会提示换图，以免合成价格被当成原始OHLC。

预热至少340根，每个所用周期独立计算；调整IMACD长度后取max(340,10×长度)。图上历史不足时会显示预热/数据不足。
“启动前至少连续零轴根数”默认1，调高会改变研究定义和信号集合；没有强制“横盘越久越好”。

## 收盘确认与研究差异

高周期采用官方建议的expression[1]＋lookahead_on，所有入场/退出/等待状态仅在当前图表收盘更新。
高周期与当前周期恰好同时收盘时，新高周期状态会在下一根当前周期K线才参与判断，因此可能比Python研究晚一根图表bar。
低周期用security_lower_tf已闭合子K线数组；缺数据不假装共振。原IMACD柱线仍会在当前未收盘K线上变化，启动与结束警报不会在盘中据此触发。
[TradingView官方时钟说明](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)。

这份脚本没有资金费率、TP/SL、保证金、自动下单或收益表。本轮只移植和验证显示/编译，不重新回测、不沿用Python统计冒充Pine逐笔收益。

## 验证记录

- 源SHA256：`{digest}`。
- [编译及运行记录]({OUT}/compiler_receipt.json)。
- 官方编译：`{receipt['official_compiler_passed']}`；逐笔TradingView/研究账本对齐：未运行。
- 使用独立只读审查核对形成窗口、等待锁定、零轴计数、图表类型、高低周期时钟；修复了预热零轴计数和小周期自动映射边界。
- 本轮不是收益实验：AUC、收益、胜率、置换p与随机入场对照均不适用。对照是原始公式/冻结状态契约、官方编译器与实际图表分支检查，不能据此推广盈利结论。
- 所有源码与记录training_eligible=false、production_eligible=false；本轮未切换任何仓库模型或生产配置。

## 复现交付

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.evaluation.imacd_pine_delivery
```

官方编译步骤：普通OKX蜡烛图 → Pine → 空白指标 → 粘贴下方完整代码 → 添加到图表；依次检查默认模式、共振模式与等待＋低周期模式。

## 完整代码

```pine
{source.rstrip()}
```
''')
    subprocess.run(['python3','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    (OUT/'delivery_manifest.json').write_text(json.dumps(dict(source_path=str(SOURCE.relative_to(ROOT)),source_sha256=digest,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        report_path=str(report.relative_to(ROOT)),html_path='analysis/html/'+report.stem+'.html',
        historical_pnl_replay=False,training_eligible=False,production_eligible=False),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
