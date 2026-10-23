"""Chinese CSV exports from existing study results; no new simulations."""
from __future__ import annotations
import pandas as pd
from yoyo.evaluation.monday_body_study import EXP


def main():
    f=pd.read_csv(EXP/'body-n3-trades.csv',float_precision='round_trip')
    f['direction']=f.side.map({1:'多',-1:'空'})
    f['group_name']=f.symbol.map(lambda s:'第一组' if s in ('BTCUSDT','ETHUSDT','XAUUSD') else '第二组山寨')
    f['cost_basis']=f.symbol.map(lambda s:'毛收益，费用未知' if s=='XAUUSD' else '扣OKX Lv1费率，未计滑点资金费')
    f['exit_kind']=f.reason.map({0:'数据边界未决，不计胜负',1:'插针极值止损',4:'近侧周一边沿止盈'})
    f['positive_then_sl']=f.reason.eq(1)&f.mfe_r.gt(1e-10)
    f['one_r_then_sl']=f.reason.eq(1)&f.mfe_r.ge(1.)
    mapping={'group_name':'分组','symbol':'品种','direction':'方向','cost_basis':'成本口径',
        'week':'周一锚点周','level':'1618价格','pin_time':'插针开始UTC','entry_time':'入场UTC',
        'entry':'入场价','stop':'止损价','target_price':'止盈价','planned_rr':'计划盈亏比',
        'risk':'单位风险价差','risk_bp':'风险距离bp','exit_kind':'退出类型','exit_price':'出场价',
        'exit_interval_start':'出场5m区间开始UTC','exit_interval_end':'出场5m区间结束UTC',
        'gross_r':'毛收益R','net_r':'净收益R','fee_r':'手续费R','gross_bp':'毛价格收益bp','net_bp':'净价格收益bp',
        'holding_hours':'持仓小时约数','mfe_r':'最大毛浮盈R','mfe_net_taker_r':'最大估算净浮盈R',
        'mae_r':'最大毛浮亏R','mfe_time':'最大浮盈所在5m开始UTC','mae_time':'最大浮亏所在5m开始UTC',
        'mfe_before_exit_bar_r':'出场K之前最大毛浮盈R','positive_then_sl':'曾有毛浮盈后止损',
        'one_r_then_sl':'曾至少1R后止损','ambiguous_bar':'出场K双触','stop_first_net_r':'同K止损优先净R',
        'engine_origin':'成交结果来源','trade_key':'交易唯一键'}
    out=f[list(mapping)].rename(columns=mapping)
    for col in ('pin_time','entry_time','exit_interval_start','mfe_time'):
        out[mapping[col].replace('UTC','北京时间')]=pd.to_datetime(f[col],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    out.to_csv(EXP/'交易明细-新规则3根.csv',index=False,encoding='utf-8-sig')
    m=pd.read_csv(EXP/'metrics.csv')
    m['period']=m.period.map({'all':'全期','earlier':'2023年前选参','later':'2023年起检验'})
    m['rule']=m.config_id.map(lambda v:'原规则影线也不能碰' if v=='strict-n3' else '新规则实体不能越线')
    cols={'rule':'规则','confirmations':'确认根数','group':'组或品种','period':'区间','basis':'毛净口径',
        'closed':'已判定笔数','censored':'未决笔数','win_rate':'胜率','mean_r':'平均每笔R','total_r':'独立交易合计R',
        'mean_bp':'平均价格收益bp','mean_win_r':'平均盈利R','mean_loss_r':'平均亏损R',
        'realized_payoff_ratio':'实际盈亏比','planned_rr_median':'计划盈亏比中位数','profit_factor':'利润因子PF',
        'paired':'配对样本数','paired_signal_r':'配对信号平均R','paired_random_r':'配对随机平均R',
        'sl':'止损笔数','tp':'止盈笔数','sl_after_positive_mfe':'毛浮盈后止损笔数',
        'sl_after_positive_net_mfe':'净浮盈后止损笔数','sl_after_half_r':'曾0.5R后止损',
        'sl_after_one_r':'曾1R后止损','sl_after_two_r':'曾2R后止损',
        'holding_hours_median':'持仓小时中位数','holding_hours_p90':'持仓小时P90'}
    m[list(cols)].rename(columns=cols).to_csv(EXP/'参数与盈亏统计.csv',index=False,encoding='utf-8-sig')
    print(f'Exported {len(out)} baseline trades and {len(m)} metric rows')


if __name__=='__main__':main()
