"""Synthetic execution checks for the identified single-case replay; no market reads."""
import copy

import pytest

from yoyo.evaluation.wif_screenshot_case import replay,stamp


def fixture():
    bars=[(100,101,99,100),(100,106,100,105),(105,106,103,104),
          (104,111,104,110),(111,112,110,111),(111,112,109,110),
          (111,118,111,117),(118,119,117,118),(118,119,116,117),
          (117,125,117,124),(125,126,123,125),(115,116,114,115)]
    rows=[[i*3600000,*b,1] for i,b in enumerate(bars)]
    marks={r[0]:r[:5] for r in rows}
    tiers=[{"maxSz":"10000000","mmr":"0.01","maxLever":"50"}]
    return rows,marks,tiers


def run(cap=None,rates=None,**kwargs):
    rows,marks,tiers=fixture()
    return replay(rows,marks,rates or {},tiers,quantity=100,leverage=40,
                  cap=cap,capital=10000,stop0=98,tick=.01,**kwargs)


def test_two_and_continuous_share_first_two_and_exit():
    two,a=run(2);many,b=run(None)
    assert two["adds_count"]==2 and many["adds_count"]==3
    extract=lambda events:[(e["time_utc"],e["quantity"],e["price"]) for e in events if e["kind"]=="add"]
    assert extract(a)==extract(b)[:2]
    assert two["exit_price"]==many["exit_price"]==115
    assert two["exit_bar"]==many["exit_bar"]
    for e in b:
        if e["kind"]=="add":
            assert e["net_at_stop"]>=e["retained_target"]-1e-7
            assert e["quantity"]==int(e["quantity"])


def test_ledger_conserves_entry_exit_fees_and_historical_funding():
    result,events=run(rates={4*3600000:.0005})
    legs=[e for e in events if e["kind"] in {"entry","add"}]
    fund=next(e for e in events if e["kind"]=="funding")
    assert fund["payment"]==pytest.approx(100*111*.0005)
    assert fund["total_quantity"]==100  # Funding precedes the next-open add.
    q=sum(e["quantity"] for e in legs)
    cost=sum(e["quantity"]*e["price"] for e in legs)
    expected=10000+q*result["exit_price"]-cost-.001*cost-.001*q*result["exit_price"]-fund["payment"]
    assert result["final_balance"]==pytest.approx(expected)


def test_original_full_margin_open_cannot_pay_fees():
    rows=[[0,.1402,.141,.139,.1405,1]];marks={0:rows[0][:5]}
    tiers=[{"maxSz":"58000","mmr":".01","maxLever":"50"}]
    invalid,_=replay(rows,marks,{},tiers,quantity=25000,leverage=35.05,cap=2)
    assert invalid["status"]=="infeasible_initial"
    valid,_=replay(rows,marks,{},tiers,quantity=25000,leverage=40,cap=2)
    assert valid["status"]=="complete"


def test_current_mark_maintenance_screen_does_not_invent_liquidation_balance():
    rows=[[0,100,101,97,100,1]];marks={0:[0,100,101,80,100]}
    tiers=[{"maxSz":"10000","mmr":".01","maxLever":"50"}]
    result,_=replay(rows,marks,{},tiers,quantity=100,leverage=40,cap=2,capital=1000,stop0=98)
    assert result["status"]=="ambiguous"
    assert result["exit_reason"]=="stop_and_maintenance_same_hour"
    assert result["final_balance"] is None


def test_new_stop_is_known_at_close_and_cannot_stop_earlier_in_same_bar():
    rows,marks,tiers=fixture()
    result,events=replay(rows[:4],marks,{},tiers,quantity=100,leverage=40,cap=2,capital=10000,stop0=98,tick=.01)
    assert result["censored"] and result["exit_reason"]=="boundary_mark"
    update=next(e for e in events if e["kind"]=="stop_update")
    assert update["time_utc"]==stamp(4*3600000)
    assert not any(e["kind"]=="add" for e in events)


def test_future_prices_do_not_change_earlier_actions():
    rows,marks,tiers=fixture();other=copy.deepcopy(rows);other[-1][1:5]=[130,200,129,190]
    kw=dict(quantity=100,leverage=40,cap=None,capital=10000,stop0=98,tick=.01)
    _,a=replay(rows,marks,{},tiers,**kw);_,b=replay(other,marks,{},tiers,**kw)
    cutoff=stamp(10*3600000)
    assert [e for e in a if e["time_utc"]<cutoff]==[e for e in b if e["time_utc"]<cutoff]


def test_tier_leverage_jump_limits_new_quantity():
    rows,marks,_=fixture()
    tiers=[{"maxSz":"100","mmr":".01","maxLever":"50"},
           {"maxSz":"1000000","mmr":".5","maxLever":"1"}]
    _,events=replay(rows[:5],marks,{},tiers,quantity=100,leverage=40,cap=None,capital=1000,stop0=98,tick=.01)
    assert not any(e["kind"]=="add" for e in events)
    assert any(e["kind"]=="reject" for e in events)
