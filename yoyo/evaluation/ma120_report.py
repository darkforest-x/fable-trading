"""Render frozen MA120 run CSVs as a descriptive, non-strategy report."""
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.ma120_runs import EXPERIMENT, ROOT, LABELS, stamp


def table(frame):
    lines=['|周期|口径|方向|连续根数|小时|开始（北京）|最后满足K线收盘（北京）|首尾收盘变化|',
           '|---|---|---|---:|---:|---|---|---:|']
    for _,r in frame.iterrows():
        lines.append(f'|{int(r.minutes)}m|{LABELS[r.definition]}|{"上方" if r.side=="above" else "下方"}|{int(r.bars)}|{r.duration_hours:g}|{stamp(r.start_utc)}|{stamp(r.end_close_utc)}|{r.price_change_pct:+.2f}%|')
    return '\n'.join(lines)


def main():
    out=EXPERIMENT/'results';meta=json.loads((out/'manifest.json').read_text())
    top=pd.read_csv(out/'longest_runs.csv');runs=pd.read_csv(out/'all_runs.csv.gz')
    full=top[top.scope.eq('full')]; common=top[top.scope.eq('common')]
    counts=runs.groupby(['scope','minutes','definition','side']).size().reset_index(name='intervals')
    counts.to_csv(out/'run_counts.csv',index=False)
    tails=runs.loc[runs.scope.eq('full') & runs.definition.eq('close') & runs.right_boundary.eq('cache_end')]
    tails.to_csv(out/'cache_tail_runs.csv',index=False)
    report=['# ETHUSDT.P：SMA120 / EMA120 同侧最长连续区间',
      '只研究 OKX ETH USDT 永续，5m / 15m / 1H。三个口径分别检索；不是 SPIKE 回测，不筛箭头，不代表可兑现收益。',
      '## 数据覆盖与边界',
      '|周期|K线数量|首根开盘（北京）|末根收盘（北京）|缺口|可用均线根数|',
      '|---|---:|---|---|---:|---:|']
    for c in meta['coverage']:
        report.append(f'|{c["minutes"]}m|{c["rows"]:,}|{stamp(c["start_utc"])}|{stamp(c["end_close_utc"])}|{c["gaps"]}|{c["valid_ma_bars"]:,}|')
    report.extend(['',
      '这次选择现有单一 OKX 5m 缓存与深度15m缓存，不拼接不同下载版本；1H由完整四根15m聚合。范围截止缓存尾部，不是截至今天的全历史或实时扫描。5m覆盖更短，所以另给共同窗口。',
      '## 每个周期自身全样本：收盘同侧最长段',table(full[full.definition.eq('close')]),
      '## 整根实体同侧：允许影线回踩',table(full[full.definition.eq('body')]),
      '## 连影线也不碰两线：更严格口径',table(full[full.definition.eq('wick')]),
      '## 相同时间范围对照',
      f'共同窗口：{stamp(meta["common_start"])} 至 {stamp(meta["common_end"])}，北京时间。保留各自此前真实数据用于均线预热，不在窗口开始重新播种EMA。',
      table(common[common.definition.isin(['close','body'])]),
      '## 各缓存截止时仍未结束的段',
      '这里只表示文件末尾仍满足条件，不能称今天仍在持续。未触发条件的周期没有尾部段；不向缺失的未来延长。',
      table(tails),
      '## 怎样读取这些区间',
      '- 收盘上方：close > max(SMA120, EMA120)；下方：close < min(SMA120, EMA120)。',
      '- 实体上方：min(open, close) > 两线最高值；下方：max(open, close) < 两线最低值。允许影线回踩。',
      '- 含影线上方：low > 两线最高值；下方：high < 两线最低值。等于任何边界即不满足。',
      '- 所有均线均以收盘计算。SMA至少120根；EMA首收盘播种，alpha=2/121。缺根时连续区间及均线重置；小时K线必须包含完整4根15m。',
      '- 开始时间为第一根满足K线的开盘时间；当时还不能确认，须等其收盘。结束时间为最后满足K线收盘；下一根不满足也要等收盘才知道连续段结束。',
      '- 长度为满足根数×周期，不包含下一根破坏条件的K线。第一根收盘到最后一根收盘的跨度会少一个周期。',
      '- 最大长度是事后结果。真实可用状态是截至当根已连续多少根，不能事先把最大段筛成入场信号。',
      '- 收盘同侧仍可有深影线甚至实体回穿；实体/影线口径的区间可能完全不同。最长不等于涨幅最大，也不等于中途没有回撤。',
      '- 起始预热边界、终点缓存边界、缺口截断全部在CSV记录left_censored/right_censored；共同窗口截断同样保守标记，不能视为自然起止。',
      f'- 36个最长行中，左边界截断 {int(top.left_censored.sum())} 条、右边界截断 {int(top.right_censored.sum())} 条。',
      '## 图表：浅色区就是满足条件的连续段',
      '黑灰=SMA120，蓝=EMA120；绿/红仅表示K线涨跌。背景高亮区以外各保留一定上下文。图中CST指北京时间（UTC+8）。'])
    for mode in ['close','body']:
        for m in [5,15,60]:
            for side in ['above','below']:
                image=out/f'{m}m_{mode}_{side}.png'
                report.extend([f'### {m}m · {LABELS[mode]} · {"上方" if side=="above" else "下方"}',f'![{m}m {mode} {side}]({image})'])
    report.extend(['## 复现与验证',
      '```bash',
      '.venv/bin/python -m yoyo.evaluation.ma120_runs --self-test',
      'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m yoyo.evaluation.ma120_runs',
      '.venv/bin/python -m yoyo.evaluation.ma120_report',
      '.venv/bin/python scripts/md_to_html.py analysis/p1_eth_ma120_longest_runs_20260914.md --out-dir analysis/html --embed-images',
      '```',
      f'Builder首次提交 `{meta["builder_commit"]}` 后才扫描；输入路径、SHA256、覆盖和绘图产物哈希见 manifest.json。',
      '合成自检覆盖严格比较/相等、实体与影线分歧、缺口断段、左右截断、完整重采样、120根预热及前缀计算不变。独立逐根核查另存 boundary_audit.json。',
      '没有分类器、预测标签、交易账户或入场策略，val AUC、top-decile净收益、匹配随机开仓、胜率和策略置换p不适用，不填造数。本任务的可证伪基线是同一数据的严格条件与逐根遍历：任一内部失败根或可向外延伸的邻根都会推翻“连续且极大”的声明。',
      '## 风险与诚实声明',
      '- 输入是既有历史缓存，未重新向交易所验证；不声称最新行情。完整期间不同不能直接作周期收益比较。EMA受文件起点播种影响，TradingView加载更早历史时极近边界可能有差异。',
      '- 本配置第1次使用owner允许的已研究历史（含2026-05-04之后），不是未触及样本外，不训练或推广。',
      '- 首尾变化是行情价格变化，不含入场延迟、费用、资金费、滑点和实际退出；没有R收益结论。',
      '- 这些长区间只能作为趋势形态标签候选；还没有证明能事前预测长度、提高SPIKE胜率或用于移动止损。',
      '- 未修改V1/V8、Pine、Bark、交易服务，已关闭的AI审查定时任务保持关闭。',
      '## 交付文件',
      f'- [最长段完整CSV]({out}/longest_runs.csv)',
      f'- [每组前20段CSV]({out}/top20_runs.csv)',
      f'- [全部连续段CSV.gz]({out}/all_runs.csv.gz)',
      f'- [输入与输出清单]({out}/manifest.json)',
      '## 下一步候选',
      '把这些区间作为事后趋势标签，逐一对照V1/V8首次箭头出现位置与提前退出位置。研究时只允许启动根及此前证据作为特征；不能把整段最终长度偷放进入场条件。本轮未执行该新增策略试验。'])
    path=ROOT/'analysis/p1_eth_ma120_longest_runs_20260914.md';path.write_text('\n'.join(report))
    print(path)


if __name__=='__main__':main()
