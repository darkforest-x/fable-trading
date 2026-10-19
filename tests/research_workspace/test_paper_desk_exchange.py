"""Exchange-like paper contracts: aggregation, liquidity fees, risk and history."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from yoyo.research_workspace import paper_desk as book, paper_desk_limits as engine
from yoyo.research_workspace import paper_desk_risk as risk, paper_desk_analytics as analytics
from yoyo.research_workspace.paper_desk_api import PublicQuotes, PublicSymbols


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = [int(datetime(2026, 10, 15, 23, 59, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)]
    for module in (book, engine, risk, analytics):
        monkeypatch.setattr(module, 'clock_ms', lambda: now[0])
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account('Exchange QA', '1000', 'create-account-qa')['id']
    def mark(px='100', symbol='BTC-USDT-SWAP', **overrides):
        return dict(symbol=symbol, bid=px, ask=px, source_ms=now[0], received_ms=now[0],
                    mark_price=px, mark_source_ms=now[0], risk=dict(contract_size='0.01', fetched_ms=now[0],
                    tiers=[dict(maxSz='1000000', mmr='.004', maxLever='100')]), **overrides)
    def opening(quantity='1', px='100', side='long', leverage=100, key='open-request-qa', symbol='BTC-USDT-SWAP', stops=None):
        m=mark(px,symbol);q=desk.save_quote(m)
        marks={s:mark(symbol=s) for s in desk.symbols(account)};marks[symbol]=m
        return desk.open(account,side,quantity,q['id'],'',key,marks,leverage,**(stops or {}))
    return SimpleNamespace(desk=desk,account=account,now=now,mark=mark,opening=opening,limits=engine.LimitOrders(desk))


def test_add_after_partial_close_uses_remaining_weight_and_preserves_entry_fee_basis(env):
    e=env
    p=e.opening('2',stops={'take_profit':'150','stop_loss':'80'})['position']
    q=e.desk.save_quote(e.mark('110'))
    e.desk.close(p['id'],'1',q['id'],'partial-close-qa')
    e.now[0]+=1
    added=e.opening('3','120',key='add-request-qa')['position']
    assert added['id']==p['id']
    assert D(added['entry_price'])==115  # (1*100 + 3*120)/4, not lifetime quantity.
    assert D(added['remaining'])==4 and D(added['quantity'])==5
    assert added['take_profit']=='150' and added['stop_loss']=='80'
    assert D(added['entry_fees_remaining'])==D('.23')
    q=e.desk.save_quote(e.mark('130'))
    e.desk.close(p['id'],'4',q['id'],'final-close-qa')
    out=e.desk.export(e.account)
    assert D(out['account']['cash'])==D('1069.405')
    assert D(out['positions'][0]['realized'])==70
    assert D(out['positions'][0]['entry_fees_remaining'])==0
    assert len(out['fills'])==4


def test_same_symbol_multiple_resting_orders_then_fills_merge_once(env):
    e=env;m=e.mark('101');q=e.desk.save_quote(m)
    ids=[]
    for i in range(2):
        o=e.limits.place('open',e.account,'10','100',q['id'],f'limit-request-{i}',{},'long',100)['order']
        ids.append(o['id'])
    assert D(e.desk.snapshot({})['accounts'][0]['order_reserved'])==D('20.4')
    e.now[0]+=1;m=e.mark('99')
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda _: e.limits.match({m['symbol']:m}),range(3)))
    out=e.desk.export(e.account)
    assert len(out['positions'])==1 and len(out['fills'])==2
    assert D(out['positions'][0]['remaining'])==20
    assert D(out['account']['cash'])==D('999.6')
    assert all(f['fee_role']=='maker' and D(f['fee_rate'])==D('.0002') for f in out['fills'])


@pytest.mark.parametrize('side,limit,execution',[('long','105','101'),('short','95','99')])
def test_marketable_limit_is_taker_with_price_improvement_and_receipt(env,side,limit,execution):
    e=env;m=e.mark();m.update(bid='99',ask='101');q=e.desk.save_quote(m)
    args=('open',e.account,'1',limit,q['id'],'cross-limit-qa',{},side,100)
    first=e.limits.place(*args)
    assert first['order']['status']=='filled'
    assert e.limits.place(*args)==first
    f=e.desk.export(e.account)['fills'][0]
    assert f['price']==execution and f['fee_role']=='taker'
    assert D(f['fee'])==D(execution)*D('.0005')


def test_marketable_limit_protection_is_relative_to_actual_fill_price(env):
    e=env;q=e.desk.save_quote(e.mark('100'))
    # TP 102 is above the executable 100, although below the maximum bid limit 105.
    order=e.limits.place('open',e.account,'1','105',q['id'],'cross-protected-qa',{},'long',100,take_profit='102')['order']
    assert order['status']=='filled'
    assert e.desk.export(e.account)['positions'][0]['take_profit']=='102'


@pytest.mark.parametrize('symbol',['SOL-USDT-SWAP','DOGE-USDT-SWAP','PEPE-USDT-SWAP'])
def test_altcoin_cap_enforced_for_direct_market_limit_and_preview(env,symbol):
    e=env;m=e.mark(symbol=symbol);q=e.desk.save_quote(m)
    for call in [lambda:e.desk.open(e.account,'long','1',q['id'],'','cap-open-request',{},30),
                 lambda:e.limits.place('open',e.account,'1','90',q['id'],'cap-limit-request',{},'long',100),
                 lambda:e.desk.size_by_equity(e.account,'long',q['id'],50,10,{})]:
        with pytest.raises(ValueError,match='20 倍'):call()
    e.desk.open(e.account,'long','1',q['id'],'','allowed-open-20x',{},20)
    assert len(e.desk.export(e.account)['fills'])==1


def test_same_symbol_opposite_side_and_different_leverage_remain_distinct(env):
    e=env
    e.opening()
    e.opening(side='short',key='short-request-qa')
    e.opening(leverage=20,key='different-leverage-qa')
    assert len(e.desk.export(e.account)['positions'])==3


@pytest.mark.parametrize('side,adverse,expected',[('long','90','-45.5'),('short','110','-54.5')])
def test_cross_liquidation_is_mark_based_atomic_cancels_all_and_replays_once(env,side,adverse,expected):
    e=env;p=e.opening('100',side=side)['position']
    other=e.desk.create_account('Other','500','other-account-qa')['id']
    q=e.desk.save_quote(e.mark('100'))
    # Pending close and entry are both cancelled when the account is liquidated.
    e.limits.place('close',p['id'],'10','150' if side=='long' else '50',q['id'],'reserved-close-qa')
    e.limits.place('open',e.account,'1','50' if side=='long' else '150',q['id'],'reserved-open-qa',{'BTC-USDT-SWAP':e.mark()},side,100)
    e.now[0]+=1000;m=e.mark(adverse)
    status=risk.account_risk(e.desk.export(e.account)['account'],[p],{m['symbol']:m},e.now[0])
    assert status['breached']
    with ThreadPoolExecutor(3) as pool:list(pool.map(lambda _:e.limits.match({m['symbol']:m}),range(3)))
    out=e.desk.export(e.account)
    assert len(out['fills'])==2 and len(out['liquidation_events'])==1
    assert all(o['status']=='cancelled' for o in out['orders'])
    assert out['fills'][-1]['exit_reason']=='liquidation'
    assert D(out['fills'][-1]['clearance_fee'])==D(adverse)*100*D('.004')
    assert D(out['account']['cash'])==D(expected)
    assert D(e.desk.export(other)['account']['cash'])==500
    assert D(out['positions'][0]['remaining'])==0


def test_price_estimate_satisfies_full_account_equation_and_shared_collateral(env):
    e=env;p=e.opening('100')['position']
    e.opening('1','100',symbol='ETH-USDT-SWAP',key='eth-open-qa')
    out=e.desk.export(e.account);marks={s:e.mark(symbol=s) for s in ['BTC-USDT-SWAP','ETH-USDT-SWAP']}
    status=risk.account_risk(out['account'],out['positions'],marks,e.now[0])
    price=status['liquidation_prices']['BTC-USDT-SWAP']
    marks['BTC-USDT-SWAP']=e.mark(price)
    edge=risk.account_risk(out['account'],out['positions'],marks,e.now[0])
    assert abs(D(edge['margin_ratio'])-1)<D('1e-20')
    # Changing ETH's mark changes BTC's conditional liquidation price.
    marks['ETH-USDT-SWAP']=e.mark('200','ETH-USDT-SWAP')
    shifted=risk.account_risk(out['account'],out['positions'],marks,e.now[0])
    assert D(shifted['liquidation_prices']['BTC-USDT-SWAP'])<D(price)


@pytest.mark.parametrize('failure',['missing_mark','stale_mark','missing_tiers','stale_tiers','pre_entry'])
def test_unusable_risk_never_liquidates(env,failure):
    e=env;e.opening('100');e.now[0]+=1000;m=e.mark('80')
    if failure=='missing_mark':m.pop('mark_price')
    if failure=='stale_mark':m['mark_source_ms']-=16000
    if failure=='missing_tiers':m.pop('risk')
    if failure=='stale_tiers':m['risk']['fetched_ms']-=3600001
    if failure=='pre_entry':m['mark_source_ms']-=1000
    e.limits.match({m['symbol']:m})
    assert len(e.desk.export(e.account)['fills'])==1


def test_quote_bid_touch_does_not_imply_mark_liquidation(env):
    e=env;e.opening('100');e.now[0]+=1000;m=e.mark('80');m['mark_price']='100'
    e.limits.match({m['symbol']:m})
    assert len(e.desk.export(e.account)['fills'])==1


def test_tier_includes_both_directions_and_pending_adds(env):
    e=env;m=e.mark();m['risk']['tiers']=[dict(maxSz='100',mmr='.004',maxLever='100'),dict(maxSz='1000',mmr='.02',maxLever='20')]
    q=e.desk.save_quote(m)
    e.desk.open(e.account,'long','1',q['id'],'','tier-first-open',{m['symbol']:m},100)
    with pytest.raises(ValueError,match='档位'):
        e.desk.open(e.account,'short','.1',q['id'],'','tier-second-open',{m['symbol']:m},100)
    e.desk.open(e.account,'short','.1',q['id'],'','tier-second-open',{m['symbol']:m},20)
    a=e.desk.snapshot({m['symbol']:m})['accounts'][0]
    assert D(a['maintenance'])==D('2.2')


def test_calendar_fee_day_partial_exits_and_account_isolation(env):
    e=env;p=e.opening('2')['position'];analytics.sample_equity(e.desk,{'BTC-USDT-SWAP':e.mark()})
    e.now[0]+=120000
    q=e.desk.save_quote(e.mark('110'));e.desk.close(p['id'],'1',q['id'],'next-day-close')
    analytics.sample_equity(e.desk,{})  # Missing observation remains a gap.
    data=analytics.history(e.desk,e.account,'2026-10')
    assert [d['date'] for d in data['days']]==['2026-10-15','2026-10-16']
    assert D(data['days'][0]['net'])==D('-.1')
    assert D(data['days'][1]['net'])==D('9.945')
    assert D(data['month_net'])==D('9.845')
    assert data['equity_curve'][1]['equity'] is None
    assert D(data['balance_curve'][-1]['balance'])==D(e.desk.export(e.account)['account']['cash'])
    other=e.desk.create_account('Other','500','other-account-qa')['id']
    assert analytics.history(e.desk,other,'2026-10')['days']==[]
    assert analytics.history(e.desk,e.account,'2026-09')['month_net']=='0'


def test_volume_ranking_compares_usdt_not_base_units(monkeypatch):
    at=book.clock_ms()
    tickers=[dict(instId=s,bidPx='1',askPx='2',ts=str(at),last=last,volCcy24h=volume)
             for s,last,volume in [('BTC-USDT-SWAP','80000','100'),('ETH-USDT-SWAP','3000','10000'),('DOGE-USDT-SWAP','.1','1000000')]]
    monkeypatch.setattr(PublicQuotes,'fetch',staticmethod(lambda:tickers))
    monkeypatch.setattr(PublicSymbols,'fetch',staticmethod(lambda:[dict(instId=r['instId'],instType='SWAP',state='live',settleCcy='USDT',ctType='linear') for r in tickers]))
    rows=PublicSymbols(PublicQuotes()).get()
    assert [r['base'] for r in rows]==['ETH','BTC','DOGE']
    assert rows[-1]['max_leverage']==20 and rows[0]['max_leverage']==100


def test_calendar_counts_all_fills_beyond_recent_ui_limit(env):
    e=env;m=e.mark();q=e.desk.save_quote(m)
    for i in range(510):
        e.desk.open(e.account,'long','.01',q['id'],'',f'history-open-{i:04}',{m['symbol']:m},100)
    assert len(e.desk.snapshot({m['symbol']:m})['fills'])==500
    history=analytics.history(e.desk,e.account,'2026-10')
    assert history['fill_count']==510
    assert D(history['statistics']['total_fees'])==D('.255')
    assert D(history['statistics']['turnover'])==510
    assert history['statistics']['closed_positions']==0
    assert D(history['days'][0]['net'])==D('-.255')
    assert D(history['balance_curve'][-1]['balance'])==D('999.745')


def test_legacy_fee_amounts_and_unallocated_entry_cost_survive_migration(env):
    e=env;p=e.opening('2')['position']
    with e.desk.connect() as db:
        # An old 0.1% entry and a partial close. Only newly added basis is missing.
        db.execute("UPDATE fills SET fee='.2',fee_rate=NULL,fee_role=NULL WHERE position_id=?",(p['id'],))
        db.execute("UPDATE positions SET remaining='1',fees='.2',entry_fees_remaining=NULL WHERE id=?",(p['id'],))
        db.execute("UPDATE accounts SET cash='999.8' WHERE id=?",(e.account,))
    migrated=book.PaperDesk(e.desk.path.parent).export(e.account)
    assert migrated['fills'][0]['fee']=='.2' and migrated['fills'][0]['fee_rate'] is None
    assert D(migrated['positions'][0]['entry_fees_remaining'])==D('.1')
    assert migrated['account']['cash']=='999.8'


def test_pre_add_observation_cannot_close_newly_merged_exposure(env):
    e=env;e.opening('1',stops={'take_profit':'110'})
    e.now[0]+=1000;old=e.mark('111')
    e.now[0]+=1000;e.opening('1',px='100',key='add-after-observation')
    e.limits.match({old['symbol']:old})
    assert len(e.desk.export(e.account)['fills'])==2
    e.now[0]+=1000;e.limits.match({old['symbol']:e.mark('111')})
    assert len(e.desk.export(e.account)['fills'])==3
    assert D(e.desk.export(e.account)['positions'][0]['remaining'])==0
