"""Owner's BTC/ETH-independent entry limits; synthetic state, never live trades."""
import copy
from decimal import Decimal as D
import pytest
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.policy import Policy, Rejected
from test_discipline_service import Clock, market, ticket
from test_live_book import live, ticket as live_ticket


@pytest.fixture
def book(tmp_path):
    b = Book(tmp_path/'independent.sqlite3', Clock(), initial_equity='1000')
    b.configure(Policy())
    market(b, symbols=('BTC', 'ETH', 'SOL', 'DOGE'))
    return b


@pytest.mark.parametrize('held,other', [('BTC', 'ETH'), ('ETH', 'BTC')])
@pytest.mark.parametrize('pending', [False, True])
def test_full_major_pool_leaves_other_major_and_alt_available(book, held, other, pending):
    request = ticket(other,stop='100',leverage=100,fraction='.1',sizing_mode='margin')
    empty_plan = book.preview(request)['plan']
    symbol = held+'-USDT-SWAP'
    with book.transaction() as (_, s):
        if pending:
            s['pending'] = [dict(id='order',symbol=symbol,side='long',
                plan=dict(quantity='100',entry='100',margin='100'))]
        else:
            s['positions'] = [dict(id='position',symbol=symbol,side='long',direction=1,
                entry='100',quantity='100',remaining='100',margin='100',leverage=100)]
        assert book.margin_quantity_limit(s,symbol,D(100),100) == 0
        assert book.exposure_quantity_limit(s,symbol,D(100),'long') == 0
        assert book.margin_quantity_limit(s,other+'-USDT-SWAP',D(100),100) == 100
        assert book.exposure_quantity_limit(s,other+'-USDT-SWAP',D(100),'long') > 99
        assert book.margin_quantity_limit(s,'SOL-USDT-SWAP',D(100),20) == 10
    result = book.preview(request)['plan']
    assert result['notional'] == empty_plan['notional']
    assert D(result['notional']) > 9800
    assert D(result['margin']) <= 100 and D(result['risk']) <= 50


def test_same_symbol_fills_and_remaining_order_reserve_once(book):
    with book.transaction() as (_,s):
        s['positions']=[dict(symbol='BTC-USDT-SWAP',entry='100',remaining='30',leverage=100,direction=1)]
        s['pending']=[dict(id='order',symbol='BTC-USDT-SWAP',side='long',
            plan=dict(entry='100',quantity='70',margin='70'))]
        assert book.margin_quantity_limit(s,'BTC-USDT-SWAP',D(100),100)==0
        assert book.margin_quantity_limit(s,'BTC-USDT-SWAP',D(100),100,exclude_pending='order')==70
        assert book.exposure_quantity_limit(s,'BTC-USDT-SWAP',D(100),'long')==0
        assert book.margin_quantity_limit(s,'ETH-USDT-SWAP',D(100),100)==100


def test_alts_still_share_margin_and_exposure(book):
    with book.transaction() as (_,s):
        s['pending']=[dict(id='alt',symbol='SOL-USDT-SWAP',side='long',
            plan=dict(entry='100',quantity='50',margin='50'))]
        assert book.margin_quantity_limit(s,'DOGE-USDT-SWAP',D(100),20)==0
        assert book.exposure_quantity_limit(s,'DOGE-USDT-SWAP',D(100),'long')==0
        assert book.margin_quantity_limit(s,'BTC-USDT-SWAP',D(100),100)==100


def test_legacy_scope_stays_shared_until_explicit_idempotent_migration(book):
    with book.transaction() as (_,s):
        del s['policy']['major_allowance_scope']
        s['positions']=[dict(id='old',policy=copy.deepcopy(s['policy']),stop='99',risk='20')]
        s['pending']=[dict(id='resting',plan={'quantity':'2'})]
        s['intents']={'resting':{'policy':copy.deepcopy(s['policy'])}}
        s['daily']={'key':'fixture','entries':2};s['loss_streak']=2;s['cooldown_until']=12345
    with book.read_transaction() as (_,before):
        assert book.policy(before).major_allowance_scope=='shared'
    book.separate_major_allowances()
    with book.read_transaction() as (_,after):
        assert after['policy_revision']==before['policy_revision']+1
        assert after['policy']['major_allowance_scope']=='independent'
        for field in ('positions','pending','intents','daily','loss_streak','cooldown_until','closed'):
            assert before[field]==after[field]
    book.separate_major_allowances()
    with book.read_transaction() as (_,again):
        assert again['policy_revision']==after['policy_revision']
    assert len([e for e in book.events() if e['kind']=='major_allowances_owner_separated'])==1


def test_scope_migration_does_not_overwrite_a_proposal(book):
    with book.transaction() as (_,s):
        s['policy']['major_allowance_scope']='shared'
        s['policy_proposal']={'policy':Policy().document(),'effective_after':9999999999}
    with pytest.raises(Rejected,match='待生效'):
        book.separate_major_allowances()
    assert Policy.parse({'major_allowance_scope':'shared'}).allowance_pool('BTC-USDT-SWAP',exposure=True)=='portfolio'
    with pytest.raises(Rejected):
        Policy(major_allowance_scope='unknown').validate()


def test_owner_migration_invalidates_old_live_preview_without_exchange_write(live):
    import uuid
    b, ex, _ = live
    with b.transaction() as (_,s):
        s['policy']['major_allowance_scope']='shared'
    request = live_ticket()
    preview = b.preview(request)
    writes = copy.deepcopy(ex.writes)
    b.separate_major_allowances()
    with pytest.raises(Rejected):
        b.enter(uuid.uuid4().hex,request,preview['policy_revision'],preview_id=preview['preview_id'])
    assert ex.writes == writes
