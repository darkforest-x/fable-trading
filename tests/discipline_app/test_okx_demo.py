"""Offline protocol checks; these do not establish an authenticated OKX connection."""
import base64
import hashlib
import hmac
import json
import stat
import time
import httpx
import pytest
from yoyo.discipline_app.okx_demo import Credentials, DemoProbe, LiveReadOnlyProbe, DemoError, save_private, read_status


@pytest.mark.parametrize('probe_type,header', [(DemoProbe, '1'), (LiveReadOnlyProbe, '0')])
def test_probe_signs_exact_get_path_and_never_writes(probe_type, header):
    requests = []
    creds = Credentials('fixture-key', 'fixture-secret', 'fixture-passphrase')
    def exchange(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.headers['x-simulated-trading'] == header
        assert request.url.host == 'www.okx.com'
        path = request.url.raw_path.decode()
        if request.url.path == '/api/v5/public/time':
            data = [{'ts': str(int(time.time()*1000))}]
        else:
            signed = request.headers['OK-ACCESS-TIMESTAMP'] + 'GET' + path
            expected = base64.b64encode(hmac.new(creds.secret.encode(), signed.encode(), hashlib.sha256).digest()).decode()
            assert request.headers['OK-ACCESS-SIGN'] == expected
            assert request.headers['OK-ACCESS-KEY'] == creds.key
            if request.url.path.endswith('/config'):
                data = [dict(perm='read_only,trade', uid='fake-account', acctLv='2', posMode='net_mode')]
            elif request.url.path.endswith('/balance'):
                data = [dict(details=[dict(ccy='USDT', eq='5000')])]
            else:
                data = []
        return httpx.Response(200, json=dict(code='0', data=data))
    probe = probe_type(creds, httpx.MockTransport(exchange))
    try:
        status = probe.inspect()
        assert status['connected'] and not status['execution_enabled']
        assert len(requests) == 6
        assert '5000' not in json.dumps(status)
        with pytest.raises(DemoError):
            probe.get('/api/v5/trade/order')
        assert len(requests) == 6
    finally:
        probe.close()


def test_demo_errors_never_echo_provider_body_or_credentials():
    for code in ('50101', '50113', 'secret-echo'):
        response = httpx.Response(400, json=dict(code=code, msg='fixture-secret', data=[]))
        with pytest.raises(DemoError) as error:
            DemoProbe._data(response)
        assert 'fixture-secret' not in str(error.value)
        assert 'secret-echo' not in str(error.value)
    assert 'fixture-secret' not in repr(Credentials('key', 'fixture-secret', 'pass'))


def test_private_credential_write_is_atomic_and_rejects_symlink(tmp_path):
    directory = tmp_path/'private'
    target = directory/'credentials.json'
    save_private(target, {'key': 'fixture'})
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {'key': 'fixture'}
    target.unlink()
    target.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(DemoError):
        save_private(target, {'key': 'fixture'})
    assert not (tmp_path/'elsewhere').exists()


def test_status_cannot_disclose_saved_secrets_or_enable_execution(tmp_path):
    save_private(tmp_path/'okx-live-status.json', dict(connected=True, key='private-key',
        secret='private-secret', execution_enabled=True, trade_permission=True, verified_at=1))
    status = read_status(tmp_path)
    assert status['connected']
    assert not status['execution_enabled']
    assert 'private' not in json.dumps(status)
    assert 'key' not in status and 'secret' not in status
