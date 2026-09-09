"""Render the completed mainboard A-share experiment without rerunning research.

Source contracts: ashare_research.py JSON/CSV artifacts and ashare_imacd.py.
This module refuses incomplete final evaluation, reads no prices after 2025,
and neither selects parameters nor simulates new trades. It reconstructs only
the causal, previous-close trail for already-recorded natural trade examples.

Chart contract: a static Matplotlib global equity/drawdown comparison uses all
saved daily observations; an outcome-distribution histogram includes every
natural trade; four OHLC/IMACD case studies show the two best and two worst
recorded net returns, with 80 prior and up to 40 subsequent trading bars.
White backgrounds, named series and line styles keep comparison readable.
PNG and trace CSV artifacts accompany Markdown for the repository HTML tool.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.ashare_imacd import Parameters, indicators
from yoyo.evaluation.ashare_research import read_through


END = '2025-12-31'
INITIAL = 1_000_000.
BOARD_NAMES = {'main_sh':'沪市主板','main_sz':'深市主板','chinext':'创业板','star':'科创板'}
PARAM_NAMES = {'ma_length':'IMACD 均线长度','signal_length':'信号平滑长度',
               'focus_bars':'近零持续根数','band_atr':'近零 ATR 容差','quality':'启动质量模式',
               'structure_bars':'初始结构窗口','buffer_atr':'结构外 ATR 缓冲',
               'stop_atr':'初始止损最小 ATR 距离','trail_atr':'趋势跟踪 ATR 距离',
               'trail_activation_r':'跟踪激活 R 倍数'}
REASONS = {'initial_stop':'初始止损','entry_day_stop_T1':'买入日触止损，T+1 退出',
           'stop_delayed_limit_down':'止损遇跌停，延后退出','trend_close_exit':'收盘失守跟踪线，次日退出',
           'period_end_valuation':'期末估值','terminal_stale_valuation':'行情陈旧的期末估值'}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Any, digits: int = 2) -> str:
    if value is None or not np.isfinite(float(value)):
        return '不适用'
    return f'{float(value):,.{digits}f}'


def percent(value: Any, digits: int = 2) -> str:
    return '不适用' if value is None else number(float(value)*100,digits)+'%'


def table(headers: list[str], rows: list[list[Any]]) -> str:
    def clean(value):
        return str(value).replace('|','／').replace('\n',' ')
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+
                     ['| '+' | '.join(clean(value) for value in row)+' |' for row in rows])


def natural_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    mask = ~trades.reason.str.startswith('period_end') & ~trades.reason.str.startswith('terminal_')
    if 'is_terminal' in trades:
        mask &= ~trades.is_terminal.astype(str).str.lower().isin(['true','1'])
    return trades.loc[mask].copy()


def risk_distribution(trades: pd.DataFrame) -> list[dict]:
    """Summarize saved ledger risks, without treating extrema as profits.

    Initial distance includes all actual entries, including terminal marks.
    Holding sessions and R outcomes use natural exits only. peak_r is the
    maximum held CLOSE relative to frozen R, with entry as its zero floor;
    it is neither an intraday MFE nor an achieved trade return.
    """
    if trades.empty:
        return []
    natural = natural_trades(trades)
    choices = [
        ('全部实际入场：初始风险距离 / 入场价',trades.risk/trades.entry,'percent'),
        ('自然平仓：持有市场交易日',natural.holding_days,'days'),
        ('自然平仓：最高持有收盘收益 / 初始 R',natural.peak_r,'r'),
        ('自然平仓：扣成本实际损益 / 初始风险金额',natural.pnl/natural.risk_cash,'r'),
        ('自然平仓：最高收盘 R 减最终净 R',natural.peak_r-natural.pnl/natural.risk_cash,'r'),
    ]
    rows=[]
    for label,values,unit in choices:
        finite=pd.to_numeric(values,errors='coerce')
        finite=finite[np.isfinite(finite)]
        if not len(finite):
            rows.append(dict(label=label,unit=unit,n=0,p25=None,median=None,p75=None))
            continue
        q=finite.quantile([.25,.5,.75]).tolist()
        rows.append(dict(label=label,unit=unit,n=len(finite),p25=q[0],median=q[1],p75=q[2]))
    return rows


def risk_table_rows(rows: list[dict]) -> list[list]:
    output=[]
    for row in rows:
        formatter=percent if row['unit']=='percent' else lambda value:number(value,2)
        suffix=' R' if row['unit']=='r' else ' 日' if row['unit']=='days' else ''
        output.append([row['label'],row['n']]+[
            formatter(row[name])+suffix if row[name] is not None else '不适用'
            for name in ('p25','median','p75')])
    return output


def configure_plotting():
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import font_manager
    import matplotlib.pyplot as plt
    choices = [Path('/System/Library/Fonts/PingFang.ttc'),
               Path('/System/Library/Fonts/Hiragino Sans GB.ttc'),
               Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')]
    font = next((path for path in choices if path.is_file()),None)
    if font is None:
        raise RuntimeError('A Chinese font is required to render readable report charts')
    font_manager.fontManager.addfont(str(font))
    family = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({'font.family':family,'axes.unicode_minus':False,'font.size':10,
                         'axes.spines.top':False,'axes.spines.right':False,
                         'axes.edgecolor':'#cbd5e1','axes.labelcolor':'#334155',
                         'text.color':'#1e293b','xtick.color':'#64748b','ytick.color':'#64748b',
                         'savefig.facecolor':'white','figure.facecolor':'white'})
    return plt


def read_equity(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path,dtype={'date':str})
    if frame.empty or frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise ValueError(f'Invalid saved equity dates: {path}')
    if not np.isfinite(frame.equity).all() or (frame.equity <= 0).any():
        raise ValueError(f'Invalid saved equity values: {path}')
    if (frame.date > END).any():
        raise ValueError('Report may not load a future price period')
    return frame


def global_chart(results: Path, destination: Path, plt) -> dict:
    """Saved cash-portfolio curves only; random bands are pointwise quantiles."""
    import matplotlib.dates as mdates
    from matplotlib.ticker import PercentFormatter
    specs = [
        ('选中参数','selected/equity.csv','#2563eb','-',2.5),
        ('初始参数','baseline/equity.csv','#475569','--',1.45),
        ('紧止损参考','tight_reference/equity.csv','#94a3b8',':',1.35),
        ('选中参数·双倍滑点','double_slippage/equity.csv','#2563eb',':',1.25),
        ('同池等权持有','equal_weight_hold.csv','#a77a2f','-.',1.45),
        ('沪深300指数参考','csi300.csv','#64748b','-',1.2),
    ]
    fig,(ax,dd) = plt.subplots(2,1,figsize=(14,9),sharex=True,gridspec_kw={'height_ratios':[2.2,1]})
    fig.subplots_adjust(left=.08,right=.985,top=.84,bottom=.11,hspace=.10)
    fig.suptitle('沪深主板日线多头：样本外净值与回撤',x=.08,y=.965,ha='left',fontsize=20,weight='bold')
    fig.text(.08,.918,'2024–2025 · 初始资金 100 万元 · 按手数与交易限制模拟 · 含税费与滑点',fontsize=11,color='#64748b')
    datasets = {}
    for label,name,color,style,width in specs:
        frame = read_equity(results/'final'/name)
        x = pd.to_datetime(frame.date)
        values = frame.equity.to_numpy(float)
        peaks = np.maximum.accumulate(np.r_[INITIAL,values])[1:]
        ax.plot(x,values/INITIAL,label=label,color=color,ls=style,lw=width)
        dd.plot(x,values/peaks-1,color=color,ls=style,lw=width if width<2 else 1.9)
        datasets[label] = {'rows':len(frame),'first_date':frame.date.iloc[0],'last_date':frame.date.iloc[-1],
                           'path':str((results/'final'/name).resolve())}
    random_paths = sorted((results/'final').glob('random_*_equity.csv'))
    random = [read_equity(path).set_index('date').equity.rename(path.stem) for path in random_paths]
    aligned = pd.concat(random,axis=1)
    if aligned.isna().any().any():
        raise ValueError('Random control equity calendars do not align')
    x = pd.to_datetime(aligned.index)
    q05,median,q95 = (aligned.quantile(q,axis=1).to_numpy()/INITIAL for q in (.05,.5,.95))
    ax.fill_between(x,q05,q95,color='#b88732',alpha=.12,label='随机对照逐日 P5–P95')
    ax.plot(x,median,color='#b88732',lw=1.6,label='随机对照逐日中位数')
    ax.axhline(1,color='#94a3b8',lw=.8)
    dd.axhline(0,color='#94a3b8',lw=.8)
    ax.set_ylabel('资金净值（初始 1.00）')
    dd.set_ylabel('距历史最高净值')
    dd.yaxis.set_major_formatter(PercentFormatter(1,decimals=0))
    dd.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    dd.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    for axis in (ax,dd):
        axis.grid(axis='y',color='#e2e8f0',lw=.65)
        axis.set_axisbelow(True)
    ax.legend(loc='upper left',bbox_to_anchor=(0,1.15),ncol=4,frameon=False,fontsize=9)
    fig.text(.08,.037,'来源：冻结回测账本。随机区间为逐日横截面分位数，并非一条可投资组合；期末包含估值费用准备。',fontsize=9,color='#64748b')
    fig.savefig(destination,dpi=160)
    plt.close(fig)
    return {'path':str(destination.resolve()),'series':datasets,'random_paths':len(random_paths)}


def distribution_chart(trades: pd.DataFrame, destination: Path, plt) -> dict:
    """A histogram includes losses and all natural exits, without winner capping."""
    f = natural_trades(trades)
    if f.empty:
        return {'available':False,'reason':'没有自然平仓交易，不能绘制收益分布。'}
    values = f.return_net.to_numpy(float)*100
    fig,ax = plt.subplots(figsize=(12,4.6))
    fig.subplots_adjust(left=.08,right=.97,top=.78,bottom=.20)
    fig.suptitle('自然平仓交易的净收益分布',x=.08,y=.95,ha='left',fontsize=18,weight='bold')
    fig.text(.08,.865,f'2024–2025 · {len(f)} 笔 · 每笔等权，已扣成本；不含期末估值',color='#64748b')
    bins = min(32,max(8,int(np.ceil(np.sqrt(len(values))))))
    ax.hist(values,bins=bins,color='#5685c4',edgecolor='white',linewidth=.6)
    ax.axvline(0,color='#334155',ls='--',lw=1)
    ax.set_xlabel('单笔净收益（%）');ax.set_ylabel('交易笔数')
    ax.grid(axis='y',color='#e2e8f0',lw=.65);ax.set_axisbelow(True)
    fig.savefig(destination,dpi=160);plt.close(fig)
    return {'available':True,'path':str(destination.resolve()),'natural_trades':len(f)}


def prior_trail(frame: pd.DataFrame, trade: pd.Series, p: Parameters) -> pd.Series:
    """Reconstruct yesterday's usable trail for one existing ledger trade.

    Uses adjusted OHLC/ATR through the previous close, the recorded entry and
    frozen initial risk; no future high/low enters an earlier trail value.
    This draws an explanation of the ledger; it cannot create/reclassify trades.
    """
    values = pd.Series(np.nan,index=frame.index,dtype=float)
    selected = frame.index[frame.date.between(trade.entry_date,trade.exit_date)]
    best,trail = float(trade.entry),float(trade.stop)
    active,pending = False,False
    for index in selected:
        row = frame.loc[index]
        if active:
            values.loc[index] = trail
        if row.date == trade.exit_date:
            break  # Recorded exit happens before this session's close.
        if row.low <= trade.stop:
            pending = True  # A still-open later bar implies T+1/limit deferral.
        if not pending and active and row.close <= trail:
            pending = True
        best = max(best,float(row.close))
        if not pending and best >= trade.entry+p.trail_activation_r*trade.risk:
            active = True
            candidate = best-p.trail_atr*row.atr
            if candidate < row.close:
                trail = max(trail,float(trade.stop),float(candidate))
    return values


def case_chart(data: Path, trade: pd.Series, p: Parameters, destination: Path, label: str, plt) -> dict:
    from matplotlib.patches import Rectangle
    source = data/'daily'/f'{trade.code}.csv'
    frame = indicators(read_through(source,END),p)
    entry_match = frame.index[frame.date==trade.entry_date]
    exit_match = frame.index[frame.date==trade.exit_date]
    if len(entry_match)!=1 or len(exit_match)!=1:
        raise ValueError(f'Case ledger dates missing from source: {trade.code}')
    entry_index,exit_index = int(entry_match[0]),int(exit_match[0])
    if exit_index < entry_index:
        raise ValueError('Case exits before entry')
    frame['prior_trail'] = prior_trail(frame,trade,p)
    left,right = max(0,entry_index-80),min(len(frame),exit_index+41)
    window = frame.iloc[left:right].copy().reset_index(drop=True)
    x = np.arange(len(window))
    entry_x,exit_x = entry_index-left,exit_index-left
    signal_match = window.index[window.date==trade.signal_date]
    fig,(ax,osc) = plt.subplots(2,1,figsize=(16,9),sharex=True,gridspec_kw={'height_ratios':[3,1]})
    fig.subplots_adjust(left=.085,right=.985,top=.85,bottom=.12,hspace=.075)
    fig.suptitle(f'{label} · {trade.code} · 单笔净收益 {percent(trade.return_net)}',
                 x=.085,y=.967,ha='left',fontsize=19,weight='bold')
    fig.text(.085,.915,f'{trade.entry_date} 入场 → {trade.exit_date} 退出 · {REASONS.get(trade.reason,trade.reason)} · '
             f'前 {entry_x} 根 + 持有期 + 后 {len(window)-exit_x-1} 根',fontsize=11,color='#64748b')
    span = max(float(window.high.max()-window.low.min()),1e-8)
    min_body = span*.0013
    for i,row in window.iterrows():
        up = row.close>=row.open
        color = '#c95353' if up else '#198673'  # A-share convention: red up, green down.
        ax.vlines(i,row.low,row.high,color=color,lw=.75)
        height = max(abs(row.close-row.open),min_body)
        bottom = min(row.close,row.open) if height>min_body else (row.close+row.open-height)/2
        ax.add_patch(Rectangle((i-.32,bottom),.64,height,facecolor=color,edgecolor=color,lw=.35))
    ax.plot(x,window.sma20,color='#8d9caa',lw=.9,label='SMA20')
    ax.plot(x,window.sma60,color='#bac2cb',lw=.9,ls='--',label='SMA60')
    ax.plot(x,window.prior_trail,color='#2563eb',lw=1.7,ls='--',label='此前收盘已生效的跟踪线')
    ax.hlines(trade.stop,entry_x,exit_x,color='#b88732',lw=1.35,ls=':',label='冻结初始止损')
    ax.scatter([entry_x],[trade.entry],marker='^',s=90,color='#2563eb',edgecolors='white',lw=.8,zorder=5,label='实际次日开盘入场')
    ax.scatter([exit_x],[trade.exit],marker='x',s=80,color='#1e293b',lw=2,zorder=6,label='账本退出')
    if len(signal_match):
        signal_x=int(signal_match[0]);signal_y=float(window.loc[signal_x,'close'])
        ax.scatter([signal_x],[signal_y],marker='D',s=32,color='#b88732',zorder=5,label='前一日收盘启动')
    for axis in (ax,osc):
        axis.axvspan(entry_x,exit_x,color='#2563eb',alpha=.045)
        if exit_x<len(window)-1:
            axis.axvspan(exit_x+.5,len(window)-.5,color='#94a3b8',alpha=.10)
        axis.axvline(entry_x,color='#2563eb',lw=.7,ls=':')
        axis.axvline(exit_x,color='#64748b',lw=.7,ls=':')
        axis.grid(axis='y',color='#e2e8f0',lw=.65);axis.set_axisbelow(True)
    offset = span*.05
    ax.annotate(f'入场 {trade.entry:.4g}',(entry_x,trade.entry),xytext=(entry_x,max(window.high.max(),trade.entry)+offset),
                ha='left',fontsize=9,color='#2563eb',arrowprops={'arrowstyle':'-','color':'#2563eb','lw':.7})
    ax.annotate(f'退出 {trade.exit:.4g}',(exit_x,trade.exit),xytext=(exit_x,max(window.high.max(),trade.entry)+offset*2.0),
                ha='right',fontsize=9,color='#334155',arrowprops={'arrowstyle':'-','color':'#64748b','lw':.7})
    ax.set_ylim(min(float(window.low.min()),float(trade.stop))-span*.04,
                max(float(window.high.max()),float(trade.entry),float(trade.exit))+span*.18)
    ax.set_ylabel('后复权连续价格（非真实每股元）')
    ax.legend(loc='upper left',bbox_to_anchor=(0,1.10),ncol=4,frameon=False,fontsize=9)
    osc.plot(x,window.md,color='#3569b4',lw=1.6,label='IMACD 主线')
    osc.plot(x,window.sb,color='#b88732',lw=1.25,label='信号线')
    osc.axhline(0,color='#64748b',lw=1)
    osc.set_ylabel('IMACD / 零轴')
    osc.legend(loc='upper left',ncol=2,frameon=False,fontsize=9)
    ticks=np.unique(np.linspace(0,len(window)-1,min(9,len(window))).astype(int))
    osc.set_xticks(ticks);osc.set_xticklabels(window.date.iloc[ticks].tolist(),rotation=0,fontsize=9)
    osc.set_xlim(-1,len(window))
    fig.text(.085,.040,'红涨绿跌 · 价格为 HFQ 后复权坐标；箭头价不是当时人民币报价。阴影为持有期，退出后的灰区仅供复盘。',fontsize=9,color='#64748b')
    fig.text(.085,.017,'案例按全体自然平仓净收益两端选取；初始 R 固定，跟踪线只使用此前收盘信息。原始人民币价可查 source CSV。',fontsize=9,color='#64748b')
    fig.savefig(destination,dpi=160);plt.close(fig)
    trace = destination.with_suffix('.csv')
    keep = [name for name in ['date','open','high','low','close','raw_open','raw_high','raw_low','raw_close',
                              'md','sb','atr','prior_trail','signal','ready'] if name in window]
    window[keep].to_csv(trace,index=False)
    return {'label':label,'code':str(trade.code),'entry_date':str(trade.entry_date),'exit_date':str(trade.exit_date),
            'return_net':float(trade.return_net),'return_gross':float(trade.return_gross),'reason':str(trade.reason),
            'initial_risk_fraction':float(trade.risk/trade.entry),
            'peak_close_r':float(trade.peak_r),'net_realized_r':float(trade.pnl/trade.risk_cash),
            'holding_days':int(trade.holding_days),'prior_bars':entry_x,'future_bars':len(window)-exit_x-1,
            'path':str(destination.resolve()),'trace':str(trace.resolve()),'source_sha256':digest(source)}


def choose_cases(trades: pd.DataFrame) -> list[tuple[str,pd.Series]]:
    f = natural_trades(trades)
    if f.empty:
        return []
    f = f[np.isfinite(f.return_net)].sort_values(['return_net','code','entry_date'],kind='stable')
    chosen=[];used=set()
    for label,subset in [('相对最好',f.iloc[::-1]),('相对最差',f)]:
        count=0
        for index,row in subset.iterrows():
            if index in used:
                continue
            count+=1;used.add(index);chosen.append((f'{label} {count}',row))
            if count==2:
                break
    return chosen


def read_exclusions(data: Path, universe: dict) -> dict:
    """Read quality exclusions without reclassifying the frozen stock pool."""
    path=data/'exclusions.json'
    if not path.exists():
        return {}
    record=read_json(path)
    codes=record.get('codes')
    if not isinstance(codes,dict) or set(codes)-set(universe['codes']):
        raise ValueError('Quality exclusions must map frozen-universe codes to recorded reasons')
    if any(not isinstance(reason,str) or not reason.strip() for reason in codes.values()):
        raise ValueError('Every quality exclusion requires a nonempty recorded reason')
    return codes


def engineering_validation(results: Path) -> str:
    """Render recorded checks; this report does not execute or assume tests."""
    path=results/'engineering_validation.json'
    if not path.exists():
        return '最终工程验收记录文件未提供；本报告不沿用早期测试计数来宣称当前版本全部通过。'
    record=read_json(path)
    checks=record.get('checks')
    if not isinstance(checks,list) or not checks:
        raise ValueError('Engineering validation requires a nonempty checks list')
    rows=[]
    for check in checks:
        counts=[check.get(name,0) for name in ('passed','failed','skipped')]
        if not check.get('name') or any(type(value) is not int or value<0 for value in counts):
            raise ValueError('Engineering validation counts must be nonnegative integers')
        rows.append([check['name'],*counts,check.get('command','未记录命令'),check.get('notes','')])
    return (f"验收记录时间：{record.get('generated_at','未记录')}；验收来源提交：`{record.get('source_commit','未记录')}`。\n\n"+
            table(['检查','通过','失败','跳过','命令','说明'],rows)+
            f'\n\n[完整验收记录]({path.resolve()})。报告生成器只呈现该记录，不将记录之后的代码改动自动视为已验证。')


def data_audit(data: Path, universe: dict) -> dict:
    excluded=read_exclusions(data,universe)
    stats={'selected':len(universe['codes']),'loaded':0,'missing':[],'empty':[],
           'rows':0,'traded_rows':0,'suspended_rows':0,'st_rows':0,'first_date':None,'last_date':None,
           'fold_rows':{'development':0,'validation':0,'final':0},'delisted_by_end':[],
           'quality_exclusions':excluded,'research_available':0,'research_rows':0,
           'research_traded_rows':0,'research_st_rows':0,'research_suspended_rows':0,
           'research_fold_rows':{'development':0,'validation':0,'final':0}}
    for code in universe['codes']:
        source=data/'daily'/f'{code}.csv'
        if not source.exists():
            stats['missing'].append(code);continue
        f=pd.read_csv(source,usecols=['date','tradestatus','isST','volume'],dtype={'date':str,'tradestatus':str,'isST':str})
        if (f.date>END).any():
            raise ValueError('Source report snapshot extends beyond authorized experiment range')
        if f.empty:
            stats['empty'].append(code);continue
        stats['loaded']+=1;stats['rows']+=len(f)
        traded=(f.tradestatus=='1')&(f.volume>0)
        stats['traded_rows']+=int(traded.sum());stats['suspended_rows']+=int((~traded).sum())
        stats['st_rows']+=int((f.isST=='1').sum())
        usable=code not in excluded
        if usable:
            stats['research_available']+=1;stats['research_rows']+=len(f)
            stats['research_traded_rows']+=int(traded.sum());stats['research_st_rows']+=int((f.isST=='1').sum())
            stats['research_suspended_rows']+=int((~traded).sum())
        stats['first_date']=min(stats['first_date'] or f.date.iloc[0],f.date.iloc[0])
        stats['last_date']=max(stats['last_date'] or f.date.iloc[-1],f.date.iloc[-1])
        for fold,start,end in [('development','2020-01-01','2021-12-31'),('validation','2022-01-01','2023-12-31'),('final','2024-01-01',END)]:
            stats['fold_rows'][fold]+=int((f.date.between(start,end)&traded).sum())
            if usable:
                stats['research_fold_rows'][fold]+=int((f.date.between(start,end)&traded).sum())
    # Listing metadata is an audit-only sidecar, deliberately not part of the
    # historical selection contract. Never infer survival from absent fields.
    metadata_path=data/'listing_metadata.json'
    historical=read_json(metadata_path).get('records',[]) if metadata_path.is_file() else []
    stats['listing_metadata_historical_errors']=[row for row in historical
                                                  if row.get('error') and row.get('code') in universe['codes']]
    final_audit_path=data.parent/'mainboard_data_audit.json'
    if final_audit_path.is_file():
        finalized=read_json(final_audit_path)
        if finalized.get('complete') is not True or Path(finalized['data_directory']).resolve()!=data.resolve():
            raise ValueError('Final data audit is incomplete or belongs to a different snapshot')
        if finalized['universe_sha256']!=digest(data/'universe.json'):
            raise ValueError('Final data audit universe hash differs')
        exclusion_path=data/'exclusions.json'
        if finalized.get('exclusions_sha256')!=(digest(exclusion_path) if exclusion_path.exists() else None):
            raise ValueError('Final data audit exclusion hash differs')
        metadata=finalized['listing_metadata']
        root=Path(__file__).resolve().parents[2]
        for row in metadata:
            if row.get('metadata_complete'):
                source=Path(row['source'])
                if not source.is_absolute():
                    source=root/source
                if not source.is_file() or digest(source)!=row['source_sha256']:
                    raise ValueError(f"Final listing source is missing or changed: {row['code']}")
        stats['final_data_audit']=str(final_audit_path.resolve())
        stats['source_receipt_count']=finalized['source_receipt_count']
        stats['all_source_hashes_verified']=finalized['all_source_hashes_verified']
        for field,key in [('research_available','research_usable_stock_count'),('research_rows','research_usable_rows'),
                          ('research_traded_rows','research_traded_rows')]:
            if stats[field]!=finalized[key]:
                raise ValueError(f'Final data audit and current rows differ: {field}')
    else:
        if not metadata_path.is_file():
            raise ValueError('Separate listing metadata audit is required')
        metadata=historical
        stats['final_data_audit']=None
    selected=set(universe['codes'])
    relevant=[row for row in metadata if row.get('code') in selected]
    codes=[row['code'] for row in relevant]
    if len(codes)!=len(set(codes)):
        raise ValueError('Duplicate listing metadata rows')
    incomplete={row['code'] for row in relevant if row.get('error') or row.get('metadata_complete') is False}
    stats['listing_metadata_missing']=sorted((selected-set(codes))|incomplete)
    stats['listing_metadata_errors']=[row for row in relevant if row['code'] in incomplete]
    for row in relevant:
        if row['code'] not in incomplete and row.get('outDate') and str(row['outDate'])<=END:
            stats['delisted_by_end'].append({'code':row['code'],'outDate':row['outDate']})
    stats['research_delisted_by_end']=[row for row in stats['delisted_by_end'] if row['code'] not in excluded]
    errors=data/'fetch_errors.json'
    stats['fetch_errors']=read_json(errors) if errors.exists() else None
    stats['unexplained_missing']=sorted(set(stats['missing'])-set(excluded))
    return stats


def metric_row(label: str, metrics: dict) -> list:
    return [label,percent(metrics.get('net_return')),percent(metrics.get('max_drawdown')),
            number(metrics.get('trades'),0),percent(metrics.get('win_rate')),number(metrics.get('profit_factor')),
            percent(metrics.get('exposure'))]


def parameter_summary(p: dict) -> str:
    return f"MA {p['ma_length']} / 蓄势 {p['focus_bars']} / 带 {p['band_atr']} / 质量 {p['quality']} / 止损 {p['stop_atr']}ATR / 跟踪 {p['trail_atr']}ATR / 结构 {p['structure_bars']}"


def write_report(data: Path, results: Path, report: Path) -> dict:
    complete=results/'final_complete.json'
    if not complete.is_file():
        raise ValueError('final_complete.json is required; incomplete research cannot be reported as complete')
    summary=read_json(complete);frozen=read_json(results/'frozen_selection.json')
    if summary['freeze_sha256']!=digest(results/'frozen_selection.json'):
        raise ValueError('Completed evaluation and frozen selection do not match')
    if summary.get('holdout_consumed') is not False:
        raise ValueError('Unexpected holdout consumption state')
    if summary['selected']['parameters']!=frozen['chosen']['parameters']:
        raise ValueError('Selected final parameters differ from frozen validation winner')
    universe=read_json(data/'universe.json')
    if set(universe['by_board'])-{'main_sh','main_sz'}:
        raise ValueError('Owner scope is ordinary-investor mainboard only; other boards cannot be reported here')
    if summary['data_manifest_sha256']!=digest(data/'universe.json'):
        raise ValueError('Historical universe changed since final evaluation')
    inputs_path=results/'selection_inputs.json'
    if digest(inputs_path)!=frozen['selection_inputs_sha256']:
        raise ValueError('Frozen selection input fingerprint changed')
    inputs=read_json(inputs_path)
    exclusions_path=data/'exclusions.json'
    if inputs.get('exclusions')!=(digest(exclusions_path) if exclusions_path.exists() else None):
        raise ValueError('Data-quality exclusion snapshot changed after selection')
    development=read_json(results/'development_results.json');validation=read_json(results/'validation_results.json')
    if not development or len(validation)!=8:
        raise ValueError('Expected all development trials and exactly eight validation endpoints')
    for filename,key_name in [('development_results.json','development_results_sha256'),
                              ('validation_results.json','validation_results_sha256')]:
        if digest(results/filename)!=frozen[key_name]:
            raise ValueError(f'Frozen selection table changed: {filename}')
    random_rows=read_json(results/'random_control_results.json')
    if len(random_rows)!=summary['matched_random']['n'] or len(random_rows)<49:
        raise ValueError('Incomplete matched random controls')
    selected=summary['selected'];p=Parameters(**selected['parameters'])
    path=results/'final'/'selected'/'trades.csv'
    try:
        trades=pd.read_csv(path,dtype={'code':str,'entry_date':str,'exit_date':str,'signal_date':str})
    except pd.errors.EmptyDataError:
        trades=pd.DataFrame()
    natural=natural_trades(trades)
    risk_rows=risk_distribution(trades)
    random_trade_path=results/'final'/'random_00_trades.csv'
    random_risks=[]
    if random_trade_path.exists():
        try:
            random_risks=risk_distribution(pd.read_csv(random_trade_path))
        except pd.errors.EmptyDataError:
            pass
    audit=data_audit(data,universe)
    figures=results.parent/'figures';figures.mkdir(parents=True,exist_ok=True)
    plt=configure_plotting()
    chart_manifest={'generated_at':datetime.now(timezone.utc).isoformat(),'source_final_sha256':digest(complete),
                    'global':global_chart(results,figures/'equity_drawdown.png',plt),
                    'distribution':distribution_chart(trades,figures/'trade_distribution.png',plt),'cases':[],
                    'risk_distribution':risk_rows,'random_seed_91000_risk_distribution':random_risks}
    for i,(label,trade) in enumerate(choose_cases(trades),1):
        chart_manifest['cases'].append(case_chart(data,trade,p,figures/f'case_{i:02d}_{trade.code}.png',label,plt))
    (figures/'manifest.json').write_text(json.dumps(chart_manifest,ensure_ascii=False,indent=2)+'\n')
    rand=summary['matched_random'];diag=summary['fixed_score_diagnostic'];month=summary['monthly_excess']
    random_mean={name:float(np.mean([row[name] for row in random_rows if row.get(name) is not None]))
                 for name in ('net_return','max_drawdown','trades','win_rate','profit_factor','exposure')
                 if any(row.get(name) is not None for row in random_rows)}
    random_median={name:float(np.median([row[name] for row in random_rows if row.get(name) is not None]))
                   for name in ('net_return','max_drawdown','trades','win_rate','profit_factor','exposure')
                   if any(row.get(name) is not None for row in random_rows)}
    compare=[metric_row(label,summary[key]) for label,key in [('选中参数','selected'),('初始宽止损参数','baseline'),
                ('原始近零 + 1ATR 紧止损参考','tight_reference'),('选中参数 + 双倍滑点','double_slippage'),
                ('同池等权持有','equal_weight_hold'),('沪深300指数参考','csi300')]]
    compare.extend([metric_row('随机对照逐项均值',random_mean),metric_row('随机对照逐项中位数',random_median)])
    delta=selected['net_return']-summary['baseline']['net_return']
    edge=selected['net_return']-rand['mean_net']
    text=[
        '# Spike 沪深主板日线多头：冻结参数与样本外检验',
        f"本版面向普通股民的沪深主板，只做多头、只计算日线；排除创业板、科创板、北交所及信号/买入日的 ST 股票。"
        f"初始止损保留结构空间，盈利后沿趋势跟踪，不设置固定 3R 止盈上限。"
        f"验证期选中的配置在 2024–2025 年样本外净收益为 **{percent(selected['net_return'])}**，最大回撤 **{percent(selected['max_drawdown'])}**。"
        f"相对初始参数净收益差 **{number(delta*100)} 个百分点**，相对匹配随机对照均值差 **{number(edge*100)} 个百分点**。",
        '这里的“选中”仅指预先限定候选在验证期获胜，不能视为全 A 股、每只股票或未来行情的全局最优；已有数字资产指标与 Spike 扫描/通知均未修改。',
        '## 交付参数与交易方式',
        table(['参数','初始值','验证期选中值'],[[PARAM_NAMES.get(name,name),frozen['baseline'][name],value]
              for name,value in asdict(p).items()]),
        '质量模式 0 只看近零向上释放；1 另要求收盘站在六均线上方，前 12 根平均均线带宽不超过 3ATR；2 在模式 1 上再要求收盘突破原蓄势区上沿。',
        '日线收盘出现多头启动后，下个交易日开盘尝试买入；开盘涨停、ST、停牌或跌破冻结结构时跳过。'
        '初始止损取“信号日近期结构低点减 0.5ATR”和“实际入场减止损 ATR 倍数”中较低者。'
        '最高收盘达到 1.5R 后开启 ATR 跟踪，跟踪线只收紧；收盘跌破此前已生效的跟踪线，次日开盘尝试退出。'
        '买入日不能卖出，跌停/停牌会延后退出。止损较宽时按照相同风险预算减少股数。',
        '## 数据范围与切分',
        f"历史证券池固定在 **{universe['date']}**，原始选择 **{audit['selected']} 只**；有日线文件 **{audit['loaded']} 只**，"
        f"数据质量冻结排除 **{len(audit['quality_exclusions'])} 只**，实际可研究 **{audit['research_available']} 只**。"
        f"这是一组当时已上市的沪深主板股票，预注册抽样为沪市主板 100 只、深市主板 100 只。"
        f"它不代表今天的全部 A 股；未补换后来退市、无数据或表现差的股票。"
        '原计划的跨板块取数在任何参数比较之前，按 Owner“排除需要门槛的票”的要求停止；本轮使用独立主板数据快照。',
        table(['项目','数量／范围'],[
            ['原始日线范围',f"{audit['first_date']}～{audit['last_date']}"],['全部已存在源文件记录',number(audit['rows'],0)],
            ['冻结原始股票数',audit['selected']],['因数据质量排除（不补换）',len(audit['quality_exclusions'])],
            ['实际可研究股票数',audit['research_available']],['可研究股票源记录',number(audit['research_rows'],0)],
            ['可研究股票有成交记录',number(audit['research_traded_rows'],0)],
            ['可研究股票停牌／无成交记录',number(audit['research_suspended_rows'],0)],
            ['可研究股票历史 ST 记录',number(audit['research_st_rows'],0)],
            ['可研究开发期记录（2020–2021）',number(audit['research_fold_rows']['development'],0)],
            ['可研究验证期记录（2022–2023）',number(audit['research_fold_rows']['validation'],0)],
            ['可研究样本外记录（2024–2025）',number(audit['research_fold_rows']['final'],0)],
            ['样本外选中配置启动候选',number(selected['signal_count'],0)],['样本外自然平仓',number(selected['trades'],0)],
            ['自然平仓正收益比例',percent(selected.get('win_rate'))],['期末仍持仓／估值笔数',number(selected['open_at_end'],0)],
            ['自然平仓平均持有交易日',number(selected.get('mean_holding_days'))],
            ['全账本佣金、过户费与印花税',number(selected.get('fees'))+' 元'],
            ['全账本模拟滑点成本',number(selected.get('slippage'))+' 元'],
            ['期末行情陈旧持仓',number(selected['terminal_uncertain'],0)],
            ['截至2025年末有退市日期记录的样本',number(len(audit['delisted_by_end']),0)],
            ['其中仍纳入研究的退市样本',number(len(audit['research_delisted_by_end']),0)],
            ['缺失文件（含质量排除）',', '.join(audit['missing']) or '无'],
            ['未由质量排除解释的缺失文件',', '.join(audit['unexplained_missing']) or '无'],
            ['空文件',', '.join(audit['empty']) or '无']]),
        table(['板块','原始历史样本数'],[[BOARD_NAMES.get(board,board),count] for board,count in universe['by_board'].items()]),
        table(['冻结质量排除','记录原因'],[[code,reason] for code,reason in sorted(audit['quality_exclusions'].items())])
          if audit['quality_exclusions'] else '质量排除文件未列出股票。',
        '数据质量排除先于第一次策略收益比较：sz.001914 的同一后复权请求中混有原始价格标记；'
        'sh.600321 与 sh.600313 的复权变化和 raw_preclose 除权参考价之间的相对连续性误差超过 0.5%，权益变化无法可靠解释。'
        '排除没有使用策略收益，也没有补入其他股票。但审计查看了整个 2015–2025 数据段的元数据与连续性，'
        '因此可用性筛选并非完美前瞻可得；这项限制独立于入场特征的因果性，不能被“没有看收益”抹去。',
        '2015–2019 仅初始化指标；2020–2021 按固定顺序进行单变量比较；2022–2023 只比较初始配置和 7 个阶段端点；'
        '冻结 JSON 后才运行 2024–2025。各段从 100 万元现金重新开始，跨段持仓不继承。'
        f"本实验最终配置评估编号为 {summary['final_evaluation_number']}，程序调用／恢复次数为 {summary.get('invocation_attempts',1)}。"
        '**未读取或评分项目 2026-05-04 及之后的 holdout，消耗为 0。**',
        '### 作废结果与重跑边界',
        '首轮开发期及验证期结果因合成测试发现“止损对后复权整体尺度不保持不变”的实现错误而作废，'
        '原始结果完整保留在 `invalid_selection_01`。修复后采用相同时间切分与候选参数池重跑，未据作废收益调整参数范围。'
        '发现并修复该错误时尚未读取最终 2024–2025 样本外收益，因此这次更正不是第二次样本外评估。'
        f"[作废轮次执行日志]({(results.parent/'invalid_selection_01'/'selection.log').resolve()})；本报告所有结果表仅取更正后冻结的账本。",
        '## 样本外完整对照',
        table(['方案','净收益','最大回撤','自然平仓笔数','胜率','PF','平均资金暴露'],compare),
        '主动策略与随机入场组合使用同一历史股票池、100 万元资金、最多 10 笔持仓、每笔初始价格风险 0.75%、单股资金上限 15%。'
        '同日按证券代码排序分配资金。持有基线以冻结原始池等额分槽，首日无法买入及数据质量排除的槽位留现金；'
        f"质量排除所占 {len(audit['quality_exclusions'])}/{audit['selected']} 的基线资金不重新分配。"
        '沪深300为含模拟成本的非可投资指数参考。'
        '“紧止损参考”使用质量模式 0 和最小 1ATR 距离，仍保留结构外止损；它是整体方案参考，不能把差异单独归因于止损。'
        '“随机逐项均值/中位数”分别统计每项指标，不代表一条真实组合路径。',
        f"![样本外净值与回撤]({chart_manifest['global']['path']})",
        f"选中方案双倍滑点后的净收益 {percent(summary['double_slippage']['net_return'])}，"
        f"最大回撤 {percent(summary['double_slippage']['max_drawdown'])}。"
        f"按最后已知价估值的陈旧终端净值贡献约 {number(selected['terminal_uncertain_value'])} 元；"
        f"若仅这些不确定持仓零回收，组合净收益下界为 {percent(selected['zero_recovery_net_return'])}，"
        f"最大回撤为 {percent(selected['zero_recovery_max_drawdown'])}。该压力情景不混入自然交易胜率。",
        '## 匹配随机入场是否被超过',
        f"同股票 × 同季度 × 事前 ATR 波动桶，共 **{rand['n']}** 轮随机对照；每轮候选数量与同组真实信号相同。"
        '随机日期按该日自己的结构低点、ATR 与后续跟踪规则执行，不套用真实信号的绝对止损价。',
        table(['诊断','结果'],[['随机净收益均值',percent(rand['mean_net'])],['随机净收益中位数',percent(rand['median_net'])],
            ['随机净收益P5～P95',f"{percent(rand['net_05'])}～{percent(rand['net_95'])}"],
            ['选中组合胜过随机比例',percent(rand['percentile'])],['随机化单侧p',number(rand['randomization_p'],4)],
            ['月度超额符号置换p',number(month['p_one_sided'],4)],['月度平均超额',percent(month['mean_monthly_excess'])],
            ['月度块数量／置换次数',f"{month['months']}／{month['permutations']}"]]),
        f"随机化 p 的可达下限为 1/({rand['n']}+1)={number(1/(rand['n']+1),4)}；49 轮的下限是 0.02，"
        '因此不能用它证明 p<0.01。月度符号置换是在月度超额序列上的另一种零假设检验，不能替换为同一个证据。'
        '股票与波动桶匹配也不保证实际成交笔数、风险距离、资金暴露或资金利用率完全相同，不能把收益差全部归因于指标。',
        table(['实际差异','选中方案','随机均值','随机P5～P95'],[[label,formatter(selected.get(name)),
              formatter(np.mean([row[name] for row in random_rows])),
              f"{formatter(np.quantile([row[name] for row in random_rows],.05))}～{formatter(np.quantile([row[name] for row in random_rows],.95))}"]
              for name,label,formatter in [('signal_count','候选信号数',lambda v:number(v,0)),
                                          ('trades','自然平仓数',lambda v:number(v,1)),
                                          ('exposure','平均资金暴露',percent),
                                          ('open_at_end','期末未自然平仓',lambda v:number(v,1))]]),
        '## 止损实际有多宽、趋势最终留下多少',
        table(['账本统计口径','笔数','P25','中位数','P75'],risk_table_rows(risk_rows)),
        '初始风险距离使用所有实际入场，包含期末仍持有的交易；持有时长和收益 R 只使用自然平仓。'
        '持有天数是组合日历中的市场交易日，不是自然日。初始 R 在买入后冻结，不会随着跟踪线移动而缩小。'
        '最高收盘 R 是持仓阶段最高已知收盘价相对入场的距离，以入场为零下限；它不是盘中最高价，也不是可以保证兑现的利润。'
        '净 R 使用实际扣佣金、税费和滑点后的损益除以初始价格风险金额。两者差包含趋势回吐、开盘跳空与成本，不能只展示曾经达到的高倍数。',
        table(['首轮随机对照（seed 91000）','笔数','P25','中位数','P75'],risk_table_rows(random_risks))
          if random_risks else '首轮随机逐交易账本无可用记录，风险距离分布不能计算。',
        '逐交易随机分布仅来自保存明细的第 1 轮 seed 91000，不外推为全部 49 轮；49 轮整体的成交数与资金暴露分布见前表。',
        '## 固定形成质量评分与单特征对照',
        '评分固定为“启动前 12 根六均线带宽/ATR 均值的负数”，没有根据结果重新选特征。'
        '该评分只诊断已进入组合且自然平仓的交易；不是买入概率，也不是整池分类效果。'
        '全体自然平仓是未按该单特征筛选的基线；前十分位是该单特征排序对照。',
    ]
    if diag.get('available'):
        text.extend([table(['集合','笔数','毛收益均值','净收益均值','胜率'],[
            ['全体自然平仓',diag['natural_trades'],percent(diag['all_trades_gross']),percent(diag['all_trades_net']),percent(diag['all_trades_win'])],
            ['固定评分前十分位',diag['top_decile_n'],percent(diag['top_decile_gross']),percent(diag['top_decile_net']),percent(diag['top_decile_win'])]]),
            f"固定评分 AUC={number(diag.get('auc'),4)}，前十分位净收益置换 p={number(diag['permutation_p'],4)}。"
            '这是已被组合准入的交易间的诊断；单笔等权毛/净收益不同于组合收益，未平仓长趋势不进入该表。'
            '此处交易收益置换不保持时间簇相关，显著性应作为探索诊断；月度块检验另列。'])
    else:
        text.append('该项不适用：'+diag.get('reason','无可用自然平仓评分')+'。不生成虚构 AUC 或分位收益。')
    text.extend(['## 板块拆分',table(['板块','股票数','净收益','最大回撤','自然平仓','胜率','资金暴露'],[
        [BOARD_NAMES.get(row['board'],row['board']),row['stocks'],percent(row['net_return']),percent(row['max_drawdown']),
         row['trades'],percent(row.get('win_rate')),percent(row.get('exposure'))] for row in summary['boards']]),
        '每个板块单独用 100 万元和相同组合约束重放，不能将板块收益相加，也不等于全组合收益的贡献归因。',
        '## 完整自然交易分布与两端案例'])
    if chart_manifest['distribution'].get('available'):
        text.append(f"![全体自然平仓净收益分布]({chart_manifest['distribution']['path']})")
    else:
        text.append(chart_manifest['distribution']['reason'])
    text.append('案例事后按全体自然平仓单笔净收益排序，取相对最好两笔和相对最差两笔；用于解释路径，不能据此再调参。'
                '每张图显示入场前最多 80 根、完整持有期及退出后最多 40 根；退出接近数据末端时后续不足会注明。'
                '图中的后复权连续价格不是当时人民币每股报价，不能据此直接下单。')
    for case in chart_manifest['cases']:
        text.extend([f"### {case['label']}：{case['code']}",
                     f"{case['entry_date']} → {case['exit_date']}，持有 {case['holding_days']} 个交易日；"
                     f"毛收益 {percent(case['return_gross'])}，净收益 {percent(case['return_net'])}；"
                     f"{REASONS.get(case['reason'],case['reason'])}。初始风险距离 {percent(case['initial_risk_fraction'])}，"
                     f"最高持有收盘 {number(case['peak_close_r'])}R，最终扣成本 {number(case['net_realized_r'])}R。"
                     f"可展示退出后 {case['future_bars']} 根。",
                     f"![{case['label']}全局K线与IMACD]({case['path']})",
                     f"[逐日价格与因果跟踪线数据]({case['trace']})"])
    if len(chart_manifest['cases'])<4:
        text.append(f"仅有 {len(chart_manifest['cases'])} 个可用且不重复的自然交易案例，未补造四张图。")
    text.extend(['## 开发期：全部单变量试验',
        '以下按原试验顺序完整列出，包括未胜出配置；每个阶段以此前阶段胜出值为起点，只改一项参数。'
        '重复配置沿用已有结果，并标出缓存来源。至少 30 笔自然平仓才有资格参与该阶段选参。',
        table(['试验','参数','净收益','最大回撤','自然平仓','胜率','PF','复用来源'],[
            [row['label'],parameter_summary(row['parameters']),percent(row['net_return']),percent(row['max_drawdown']),
             row['trades'],percent(row.get('win_rate')),number(row.get('profit_factor')),row.get('cached_from','独立运行')]
            for row in development]),
        '## 验证期：初始配置与七个阶段端点',
        table(['端点','参数','净收益','最大回撤','自然平仓','胜率','PF','是否选中'],[
            [row['label'],parameter_summary(row['parameters']),percent(row['net_return']),percent(row['max_drawdown']),
             row['trades'],percent(row.get('win_rate')),number(row.get('profit_factor')),
             '选中端点' if row['label']==frozen['chosen']['label'] else '同配置' if row['config_id']==frozen['chosen']['config_id'] else '否']
            for row in validation]),
        '验证集 AUC 不作为选参指标：参数是交易规则而非预测概率，端点仅以验证组合净收益、回撤、自然平仓门槛排序。'
        '样本外固定形成评分的 AUC、毛/净收益与置换检验已单列，不把它当策略成功标准。',
        '## 风险与诚实声明',
        '1. 2020 年已上市的沪深主板历史样本并非全市场；后上市新股、创业板、科创板、北交所不在本轮范围。'
        '信号日及买入日 ST 禁入，持有之后才变为 ST 的股票不会被事后从账本删除；仅观察两年样本外，不证明未来持续有效。',
        '数据可用性另有筛选偏差：虽然三只质量排除不依赖策略收益，决定排除时看了整段价格元数据和复权连续性，'
        f"历史某一天并不知道后续多年数据会不会异常。原始 {audit['selected']} 只与研究可用 {audit['research_available']} 只必须区分，"
        '不能把本轮视为完全前瞻可得的无偏全市场检验。',
        '2. 宽止损降低容易被普通波动触发的程度，同时增加价格回撤容忍范围；现金风险通过股数约束，跳空和连续跌停仍可能超过计划风险。',
        '3. A 股 T+1、停牌和涨跌停按日线保守近似；没有订单队列和盘口，触价不等于保证成交。盘中止损收入不能资助更早的开盘买入。',
        '4. 后复权收益隐含分红再投资；未逐项模拟现金红利税、配股选择、股权到账和碎股处理。HFQ价格图不能当人民币报价图。',
        '5. 期末持仓按最后已知价格扣估计退出成本，属于估值，未冒充自然平仓；陈旧持仓、最大陈旧天数和零回收压力结果均须一起阅读。',
        '6. 当前成本为佣金双边0.025%且最低5元、按历史日期的过户费和卖方印花税，滑点双边各0.05%；双倍滑点为各0.10%。实际券商成本不同。',
        '7. 匹配随机保持股票、季度、波动桶与候选数量；不同日期的价格位置和止损宽度、准入与暴露不同。收益差不自动等于可稳定重复的超额收益。',
        '8. 最好/最差案例是固定规则的事后复盘，未以结果反选参数；成功图不能代替全样本、对照与回撤。',
        '9. 本研究不训练或推广 YOLO/LightGBM，不接入自动实盘执行，不修改任何币圈 Pine、Spike 通知、ACTIVE、仓位或订单。',
        '### 数据缺失、ST、退市与末端情况',
        f"读取器报告：{json.dumps(audit['fetch_errors'],ensure_ascii=False)}。"
        f"最终评估缺失文件：{', '.join(summary.get('missing',[])) or '无'}。"
        f"末端最大行情陈旧 {selected['terminal_stale_days_max']} 个自然日。"
        f"最终上市资料未完整：{', '.join(audit['listing_metadata_missing']) or '无'}；"
        f"未完整资料说明：{json.dumps(audit['listing_metadata_errors'],ensure_ascii=False)}。",
        f"[最终数据核验]({audit['final_data_audit']})：{audit.get('source_receipt_count',0)} 份来源回执，"
        f"全量来源哈希核验{'通过' if audit.get('all_source_hashes_verified') else '未全部通过'}。"
          if audit['final_data_audit'] else '没有最终数据核验文件，上市资料仅依据独立检索侧录。',
        f"历史上市资料失败日志：{json.dumps(audit['listing_metadata_historical_errors'],ensure_ascii=False)}。"
        '这部分保留最初请求失败，不等同于最终仍缺失；已有完整原始 basic 文件且哈希通过的证券，按最终核验记录报告。',
        table(['截至2025年末退市日期记录','日期'],[[row['code'],row['outDate']] for row in audit['delisted_by_end']])
          if audit['delisted_by_end'] else '历史证券资料中无截至2025年末的退市日期记录；这不等于证明源数据没有遗漏。',
        '退市日期来自当前检索的基本资料，仅作审计解释，不参与历史股票选择、入场特征或参数评分。',
        table(['执行跳过／延后原因','次数'],[[reason,count] for reason,count in sorted(selected.get('skips',{}).items())]),
        '## 工程验证及已知基线失败',
        engineering_validation(results),
        '先前边界与注册表检查曾记录 85 项通过、1 项失败；失败项 `test_holdout_consumption_is_declared_per_experiment_not_assumed` '
        '是原有消费名单与旧 macmonitor/barkmonitor 等实验记录不一致，不包含本次 A 股实验。'
        '相关测试文件与注册表在本轮开始前已有未提交修改，本轮未改写它们来消除失败，因此不宣称全仓测试全绿。',
        '真实历史前缀因果检查：sh.600182 的 2015–2021 共 1705 根日线，完整特征表与删除末尾 80 根后重算的共有前缀逐列一致。'
        '这项检查没有计算收益或参与选参，也不等于 Python 与 TradingView 跨平台数值已逐根一致。',
        '## 复现与文件',
    ])
    root=Path(__file__).resolve().parents[2]
    q=lambda path:shlex.quote(str(path))
    text.append('以下从空结果目录复现原流程；已有冻结或最终结果会拒绝覆盖，不应删除保护文件来事后调参。')
    text.append('```bash\n'+f'cd {q(root)}\n'+
                '.venv/bin/python -m pip install --dry-run --report /tmp/spike-ashare-bs093-report.json --target /tmp/spike-ashare-bs093 --no-deps baostock==0.9.3\n'+
                '.venv/bin/python -m pip install --target /tmp/spike-ashare-bs093 --no-deps baostock==0.9.3\n'+
                f'PYTHONPATH=/tmp/spike-ashare-bs093:. .venv/bin/python -m yoyo.evaluation.ashare_data --destination {q(data)} --universe-date 2020-01-02 --start 2015-01-01 --end 2025-12-31 --workers 4 --quotas '+
                shlex.quote(json.dumps({'main_sh':100,'main_sz':100},separators=(',',':')))+'\n'+
                f'.venv/bin/python -m yoyo.evaluation.ashare_research select --data {q(data)} --out {q(results)}\n'+
                f'.venv/bin/python -m yoyo.evaluation.ashare_research final --data {q(data)} --out {q(results)} --controls {rand["n"]}\n'+
                f'.venv/bin/python -m yoyo.evaluation.ashare_report --data {q(data)} --results {q(results)} --report {q(report)}\n'+
                f'.venv/bin/python scripts/md_to_html.py {q(report)} --out-dir analysis/html\n```')
    text.extend([f"冻结来源提交：`{frozen['source_commit']}`；冻结参数 SHA256：`{summary['freeze_sha256']}`。",
                 f"[冻结参数]({(results/'frozen_selection.json').resolve()}) · [最终结果JSON]({complete.resolve()}) · "
                 f"[图表与数据索引]({(figures/'manifest.json').resolve()}) · [完整自然与终端交易账本]({path.resolve()})",
                 '## 下一步',
                 '保留本次冻结参数做日线观察与纸面交易，记录每个启动、实际可成交性和退出；不使用这轮样本外结果继续反复调参。'
                 '若要扩展到较晚上市股票、需要另外开户条件的板块、其他成本或不同止损范围，应建立新的预注册实验，并由 Owner 确认范围与新验收期。',
                 '## 官方依据',
                 '[BaoStock官方数据接口](https://pypi.org/project/baostock/)；'
                 '[上交所交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)；'
                 '[印花税减半公告](https://www.mof.gov.cn/jrttts/202308/t20230828_3904235.htm)；'
                 '[上交所费用说明](https://one.sse.com.cn/onething/gptz/)。'])
    runtime=figures/'pine_baseline_runtime.png'
    if runtime.exists():
        text.insert(text.index('## 复现与文件'),
                    f'[独立 A 股 Pine 初始版运行验收截图]({runtime.resolve()}) 仅用于 TradingView 编译与视觉验收；'
                    '界面打开的是 2026 年当前图，不是 2024–2025 样本外案例，不参与本报告选参或收益统计。')
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text('\n\n'.join(text)+'\n')
    return {'report':str(report.resolve()),'figures':str(figures.resolve()),'cases':len(chart_manifest['cases']),
            'development_rows':len(development),'validation_rows':len(validation),'data_audit':audit}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(argv)
    print(json.dumps(write_report(args.data,args.results,args.report),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
