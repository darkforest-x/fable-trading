"""Manual dashboard reads only its runtime and retains positions on history failure."""
from dataclasses import asdict
import json
from yoyo.okx_execution.manual_dashboard import ManualDashboard, manual_snapshot, read_requests
from yoyo.okx_execution.config import Config, write_private
from test_manual_live import Exchange, h, preview
from test_manual_inventory import shared

class Account(Exchange):
    def get(self, path, **query):
        if path.endswith('/orders-history'):
            if getattr(self, 'history_failure', False): raise ValueError('PRIVATE_SENTINEL')
            return [dict(instId='ETH-USDT-SWAP',ordId='1',state='filled',ordType='market',side='buy',posSide='long',avgPx='100',sz='2',accFillSz='2',fee='-.1',feeCcy='USDT',pnl='0')]
        return super().get(path, **query)

def test_history_failure_keeps_positions():
    client=Account(lambda:9010000)
    client.positions=[dict(instId='FIL-USDT-SWAP',posSide='long',pos='2',mgnMode='cross')]
    client.history_failure=True
    d=manual_snapshot(client)
    assert d['positions'][0]['instId']=='FIL-USDT-SWAP'
    assert d['total_equity_usdt']=='1000'
    assert d['history']['status']=='unavailable'
    assert 'PRIVATE_SENTINEL' not in str(d) and not client.posts

def test_manual_configuration_is_three_percent_and_never_signal_capital(tmp_path):
    write_private(tmp_path/'config.json',asdict(Config()))
    write_private(tmp_path/'credentials.json',dict(api_key='PRIVATE_SENTINEL',secret='PRIVATE_SENTINEL',passphrase='PRIVATE_SENTINEL',demo=False))
    client=Account(lambda:9010000)
    def factory(config,credentials,*,writes):
        assert writes is False
        return client
    dashboard=ManualDashboard(tmp_path,factory)
    try:
        dashboard.snapshot();dashboard.future.result(3)
        d=dashboard.snapshot()
        assert d['account_scope']=='yolo_manual'
        assert d['config']['risk_fraction']=='0.03' and 'capital_usdt' not in d['config']
        assert d['account']['data']['history']['items'][0]['ordType']=='market'
        assert not client.posts and 'PRIVATE_SENTINEL' not in json.dumps(d)
        assert not (tmp_path/'manual.sqlite3').exists() and not (tmp_path/'execution.sqlite3').exists()
    finally: dashboard.close()

def test_idle_worker_does_no_remote_reads_but_new_confirmation_runs_gates(shared):
    def forbidden(*a,**kw):raise AssertionError('idle must not wait for exchange')
    get=shared['exchange'].get
    shared['exchange'].get=forbidden
    shared['engine'].tick()
    assert shared['store'].meta('heartbeat_ms')==shared['now'][0]
    shared['exchange'].get=get
    d=preview(shared);shared['service'].confirm(d['id'])
    shared['outside_orders'].append(dict(instId=d['symbol'],ordId='external-new'))
    shared['engine'].tick()
    assert shared['store'].trades()[0]['state']=='skipped'
    assert not shared['exchange'].posts

def test_expired_execution_retains_reviewed_size_and_target(h):
    d=preview(h);h['service'].confirm(d['id']);h['now'][0]+=61000
    h['engine'].tick()
    status=h['service'].status(d['id'])
    assert status['state']=='skipped' and status['size']==d['size']
    assert status['take_profit']==d['take_profit'] and not status.get('entry_price')
    from yoyo.okx_execution.manual_dashboard import executions
    row=executions(h['service'].runtime)['items'][0]
    assert row['reason_code']=='manual_expired' and row['take_profit']==d['take_profit']
    assert not h['exchange'].posts


def test_summary_preserves_freshness_and_gate_without_shipping_positions_or_ledger(tmp_path, monkeypatch):
    from yoyo.okx_execution.dashboard import Dashboard
    original = dict(account=dict(status='stale', updated_ms=123, age_ms=61000,
                                 data=dict(total_equity_usdt='100', positions=['PRIVATE_POSITION'], history=['PRIVATE_ORDER'])),
                    ledger=dict(items=['PRIVATE_LEDGER']))
    monkeypatch.setattr(Dashboard, 'snapshot', lambda self, force=False: dict(original))
    monkeypatch.setattr('yoyo.okx_execution.manual_dashboard.entry_status', lambda *a: dict(status='blocked', code='worker_stale'))
    monkeypatch.setattr('yoyo.okx_execution.manual_dashboard.worker_lock', lambda *a: False)
    monkeypatch.setattr('yoyo.okx_execution.manual_dashboard.executions', lambda *a: (_ for _ in ()).throw(AssertionError('summary must not read execution history')))
    dashboard=ManualDashboard(tmp_path)
    try:
        result=dashboard.snapshot(summary=True)
        assert result['account_scope']=='yolo_manual' and result['view']=='summary'
        assert result['entry_status']==dict(status='blocked',code='worker_stale')
        assert result['account']['status']=='stale' and result['account']['updated_ms']==123
        assert result['account']['data']==dict(total_equity_usdt='100')
        assert 'PRIVATE_' not in json.dumps(result)
        assert original['account']['data']['positions']==['PRIVATE_POSITION']
    finally: dashboard.close()


def test_workspace_keeps_every_position_and_intent_but_bounds_recent_history(tmp_path, monkeypatch):
    from yoyo.okx_execution.dashboard import Dashboard
    positions = [dict(instId=f'COIN{i}-USDT-SWAP') for i in range(60)]
    original = dict(account=dict(status='stale', updated_ms=123, data=dict(positions=positions,
                    total_equity_usdt='100', balance=dict(availEq='75'), history='PRIVATE_HISTORY')),
                    ledger=dict(items=['PRIVATE_LEDGER']))
    activity = dict(status='ready', items=[dict(id=str(i)) for i in range(50)],
                    active_items=[dict(id=str(i)) for i in range(60)], statistics=dict(filled=100))
    monkeypatch.setattr(Dashboard, 'snapshot', lambda self, force=False: dict(original))
    monkeypatch.setattr('yoyo.okx_execution.manual_dashboard.entry_status', lambda *a: dict(status='ready'))
    monkeypatch.setattr('yoyo.okx_execution.manual_dashboard.worker_lock', lambda *a: True)
    monkeypatch.setattr('yoyo.okx_execution.manual_dashboard.executions', lambda *a: activity)
    dashboard=ManualDashboard(tmp_path)
    try:
        result=dashboard.snapshot(workspace=True)
        assert result['view']=='workspace' and result['account_scope']=='yolo_manual'
        assert result['account']['status']=='stale' and len(result['account']['data']['positions'])==60
        assert len(result['executions']['active_items'])==60 and len(result['executions']['items'])==5
        assert result['executions']['statistics']['filled']==100
        assert 'PRIVATE_' not in json.dumps(result) and len(activity['items'])==50
    finally: dashboard.close()


def test_statistics_span_all_confirmed_trades_and_do_not_invent_external_pnl(h):
    from yoyo.okx_execution.manual_dashboard import executions
    # More than the visible history limit, with a loss, a win, and a close
    # whose economics cannot be assigned to the system lot.
    for i in range(65):
        h['store'].save(dict(id=str(i), symbol='ETH-USDT-SWAP', side='long', state='closed',
            event=dict(source='owner_manual_yolo'), entry_price='100',
            entry_order=dict(fee='-.1', feeCcy='USDT'),
            exit_order={} if i==64 else dict(pnl='1' if i%2 else '-1', fee='-.1', feeCcy='USDT')))
    stats=executions(h['service'].runtime)['statistics']
    assert stats==dict(scope='all_manual_trades',filled=65,closed=65,settled=64,
                       unattributed=1,realized_net_usdt='-12.8',wins=32)
    assert not h['exchange'].posts


def test_manual_snapshot_does_not_silently_drop_positions_after_fifty():
    client=Account(lambda:9010000)
    client.positions=[dict(instId=f'COIN{i}-USDT-SWAP',posSide='long',pos='2',mgnMode='cross') for i in range(65)]
    result=manual_snapshot(client)
    assert result['position_count']==len(result['positions'])==65
    assert not client.posts
