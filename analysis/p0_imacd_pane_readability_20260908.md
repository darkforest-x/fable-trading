# IMACD V2.6：让副图启动点与文字分开

**已保存至原 TradingView 云脚本，版本 12，09/08 23:56；浅色、黑色背景及多空启动点均完成实图验收。** 用户指出的 USELESS 1H 副图启动处，现在使用小圆点、留有间距的文字和更细的光晕。源码先于实图验证提交：`662922bb369ee7e3e659620917eec6cb28002493`，09/08 23:54:32。实验编号 `exp-imacd-pane-readability-20260908-v1`；本轮界面检查跨日至 09/09 00:06（北京时间）。

## 改了什么

| 显示项 | V2.5 | V2.6 |
| --- | --- | --- |
| 启动锚点 | 小菱形，与标签尖头重叠 | 更小的实心圆点，仍位于原释放 K 线的 md 值 |
| 副图文字 | 半透明带尖头标签，紧贴曲线 | 背景全透明，多头文字向下留一行、空头向上留一行；显示“↑ / ↓ 启动 · N 根” |
| 光晕线宽 | 外光 / 柔光 / 主线 10 / 6 / 3 px | 6 / 4 / 2 px，外光和柔光更淡 |
| 信号、主图与坐标 | V2.5 原值 | 完全一致，未移动释放时间或 md 锚点 |

只变更六条副图显示语句及一行版本注释。零轴、蓝橙双线、六条主图均线、主图标签与盈亏比参考、参数、生命周期和警报条件保持。没有增加标签或 plot 调用，也没有修改 YOLO、前端、TG、Bark、监控服务或执行器。

透明标签保留 `label_up/down` 的相对定位，用文本行高分开圆点和文字；没有用 ATR 偏移纵坐标，避免后续大行情扩大副图量程后间距又被压扁。标签定位及换行机制参考 [TradingView 官方文字与形状文档](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/)。

## 工程验收

| 检查 | 结果 | 证据与范围 |
| --- | --- | --- |
| 完整源码对照 | 通过 | 精确排除六条显示语句及一行版本注释后，V2.5 / V2.6 剩余源码逐字节一致 |
| 标签预算与主图 | 通过 | `label.new` 调用 13 → 13；80 段队列、驱逐规则、`max_labels_count` 及主图代码未变 |
| V2.5 保护测试 | 4 项通过 | `tests/test_imacd_pine_v25_contract.py`，未修改原测试或基线 |
| 原云脚本编译及保存 | 通过 | “IMACD 零轴密集启动 · 多周期趋势 V1”，保存版本 12 |
| 编辑器全文回读 | 一致 | 归一化 CRLF 后 SHA256 与本地 V2.6 一致 |
| OKX XAUUSDT.P 1H 浅色 | 通过 | 黄金 USDT 永续，原生 TradingView 实看 30 根蓄势后的多头启动，圆点、文字与光晕分离 |
| USELESS 1H 浅色 | 通过 | 8 月 27 日至 9 月 4 日窗口，复查用户标出的 16 根蓄势启动处 |
| OKX KAITOUSDT.P 1H 浅色 | 通过 | 实际可见 8 月 28 日至 9 月 8 日；8 月 30 日空头 23 根、9 月 5 日多头 33 根均可辨认 |
| OKX KAITOUSDT.P 1H 黑色背景 | 通过 | 同一窗口临时从白色切换黑色背景；空头文字在圆点上方，与左侧蓄势标签分离，多头文字在下方；双线与零轴清晰 |

以上是原生 TradingView 工具输出中的截图观察记录，没有另存为本地 PNG。周期范围仅为本轮 1H 样式验收，不将其他周期写为已验证。

检查完成后已通过撤销恢复白色背景并截图确认，最终返回 USELESSUSDT.P 1H、8 月 27 日至 9 月 4 日窗口。

V2.6 全文 SHA256：`dcc51503e32152c560b969560a171c9167c1ace981cda6cc188fbb55c7524741`。

排除六条显示语句及一行注释后的共同源码 SHA256：`404ee7162ebbe8922c7abcc344b6667b2a3b9cdff72a704c6ff2b9f1dd136b34`。

## 复现

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/test_imacd_pine_v25_contract.py
shasum -a 256 yoyo/evaluation/pine/imacd_dense_mtf_v2_6.pine
.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib
p = Path('yoyo/evaluation/pine')
a = (p / 'imacd_dense_mtf_v2_5.pine').read_text()
b = (p / 'imacd_dense_mtf_v2_6.pine').read_text()
keys = ['string paneText =', 'label paneTag =',
        'plot(glowMd, "释放 · 外光"', 'plot(glowMd, "释放 · 柔光"',
        'glowMain = plot(glowMd',
        'plotshape(showFocus and focusRelease ? md : na,']
for key in keys:
    assert sum(line.lstrip().startswith(key) for line in a.splitlines()) == 1
    assert sum(line.lstrip().startswith(key) for line in b.splitlines()) == 1
note = '// V2.6 changes only pane release typography, the marker and glow widths.\n'
assert b.count(note) == 1
b = b.replace(note, '', 1)
def frozen(source):
    return ''.join(line for line in source.splitlines(True)
                   if not any(line.lstrip().startswith(key) for key in keys))
assert frozen(a) == frozen(b)
print(hashlib.sha256(frozen(a).encode()).hexdigest())
PY
python3 scripts/md_to_html.py analysis/p0_imacd_pane_readability_20260908.md --out-dir analysis/html
```

在原 TradingView 云脚本中粘贴 V2.6 全文、更新图表并保存；检查浅色 / 深色及多头 / 空头释放处，确认圆点仍对应原 K 线、文字没有与计数标签重叠。完整读回后只归一化 CRLF，再核对上述全文哈希。原 V2.5 文件和云端版本 11 保留为对照。

## 风险与诚实声明

这是显示层工程修复，没有候选池、正类率、训练 / val 样本或收益统计。因此 val AUC、置换检验 p、top-decile 毛 / 净收益、胜率、单特征收益基线及匹配随机交易对照均不适用。工程零假设是“显示改动之外代码不变”，对照为冻结 V2.5 全文的精确字节比较，并结合 TradingView 实编和真实图表视觉检查；没有用图例推断收益改善。

Owner 在当前对话已允许任意日期数据。**这是该配置第 1 次暴露 / 使用 holdout 时段**，仅为同一轮界面检查，没有评分、训练或调参。已核对的 USELESS 图窗覆盖 2026 年 8 月至 9 月；KAITO 日期范围输入为 8 月 27 日至 9 月 8 日，实际可见 8 月 28 日至 9 月 8 日。初始 XAUUSDT.P 历史窗口未完整记录精确边界，不编造统一的起止时间或样本数量。结构化验收记录位于 `experiments/active/exp-imacd-pane-readability-20260908-v1/results/verification.json`。

浅色与黑色背景、多头与空头都已实际查看；有限的窗口不能覆盖所有缩放和字体组合。后续如果要改变哪些 K 线出信号，应单独做规则与效果评估，本轮不涉及该决策。
