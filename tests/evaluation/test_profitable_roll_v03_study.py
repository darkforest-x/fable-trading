"""Selection, unknown-result and venue-filter checks without market data."""
import pandas as pd

from yoyo.evaluation.profitable_roll_v03_study import complete,quantity_rules,select_examples


def test_selected_winners_cannot_be_random_censored_or_repeated_coin():
    rows=[]
    for key,symbol,value,scope,censored in [
        ('a1','AAA',4,'actual',False),('a2','AAA',3,'actual',False),
        ('b1','BBB',2,'actual',False),('bad1','CCC',100,'random',False),
        ('bad2','DDD',200,'actual',True),('wif','WIFUSDT',300,'actual',False)]:
        rows.append(dict(event_key=key,symbol=symbol,net_r=value,scope=scope,censored=censored,
                         period='earlier',entry_time='2025-01-01',entry_price=1,initial_stop=.9,
                         exit_time='2025-01-02',exit_price=2,net_usd=value))
    result=select_examples([pd.DataFrame(rows)],2)
    assert [r['event_key'] for r in result]==['a1','b1']


def test_unknown_or_censored_balance_is_not_a_completed_profit():
    table=pd.DataFrame({'status':['complete','ambiguous','complete','infeasible_initial','complete'],
        'censored':[False,False,True,False,False],'final_balance':[120,None,130,100,None]})
    assert complete(table).tolist()==[True,False,False,False,False]


def test_market_order_filter_and_zero_step_fallback():
    metadata={'symbols':[{'symbol':'ABC','filters':[
        {'filterType':'LOT_SIZE','stepSize':'.1','minQty':'.1','maxQty':'10000'},
        {'filterType':'MARKET_LOT_SIZE','stepSize':'0','minQty':'0','maxQty':'500'},
        {'filterType':'MIN_NOTIONAL','notional':'5'}]}]}
    result=quantity_rules('ABC',metadata)
    assert result=={'quantity_step':.1,'min_quantity':.1,'max_order_quantity':500.,'min_notional':5.}
