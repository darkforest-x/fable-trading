"""Read-only onboarding for this app's separate OKX account.

Source: https://www.okx.com/docs-v5/en/ (authentication, demo trading, account
configuration). DemoProbe always uses simulation; LiveReadOnlyProbe is an
explicit separate selection. Neither can write exchange or paper orders.
"""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from urllib.parse import urlencode
import httpx


HOST = 'https://www.okx.com'
READ_PATHS = frozenset(('/api/v5/account/config', '/api/v5/account/balance',
    '/api/v5/account/positions', '/api/v5/trade/orders-pending',
    '/api/v5/trade/orders-algo-pending'))


class DemoError(ValueError):
    """Sanitized error; never expose response bodies or signed request headers."""


@dataclass(frozen=True)
class Credentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)

    def __post_init__(self):
        for value in (self.key, self.secret, self.passphrase):
            if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise DemoError('凭据为空或格式无效，请在本机重新输入。')


class DemoProbe:
    simulated_header = '1'
    environment = 'okx_demo'

    def __init__(self, credentials, transport=None):
        self.credentials = credentials
        self.client = httpx.Client(base_url=HOST, timeout=12, follow_redirects=False,
                                   transport=transport)
        self.offset = 0.0

    def close(self):
        self.client.close()

    @staticmethod
    def _data(response):
        try:
            result = response.json()
            code = str(result['code'])
            data = result.get('data')
        except (ValueError, KeyError, TypeError):
            raise DemoError('OKX 返回格式异常，未完成核验。') from None
        if response.status_code != 200 or code != '0':
            messages = {
                '50101': 'API 与所选交易环境不匹配，请核对 API 的实盘或模拟归属。',
                '50111': 'API Key 无效，请检查模拟 API 和所属站点。',
                '50113': '签名校验失败，请核对 Secret。',
                '50105': 'API Passphrase 不正确。',
                '50102': '请求时间超出范围，请校准 Mac 系统时间。',
                '50110': '当前 Mac 出口 IP 不在此 API 的 IP 白名单内。',
            }
            safe_code = code if code.isdigit() and len(code) < 10 else 'unknown'
            raise DemoError(messages.get(code, f'OKX 核验未通过（代码 {safe_code}）；请检查 API 权限和所属站点。'))
        if not isinstance(data, list):
            raise DemoError('OKX 数据格式异常，未完成核验。')
        return data

    def sync_time(self):
        start = time.time()
        try:
            rows = self._data(self.client.get('/api/v5/public/time', headers={'x-simulated-trading': self.simulated_header}))
            end = time.time()
            if end - start > 5:
                raise DemoError('OKX 连接延迟过高，请稍后重新核验。')
            self.offset = int(rows[0]['ts']) / 1000 - (start + end) / 2
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, DemoError):
                raise
            raise DemoError('无法读取 OKX 服务时间。') from None

    def get(self, path, params=None):
        if path not in READ_PATHS:
            raise DemoError('此接入核验仅允许读取账户状态。')
        request_path = path + ('?' + urlencode(params) if params else '')
        timestamp = datetime.fromtimestamp(time.time() + self.offset, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        signature = base64.b64encode(hmac.new(self.credentials.secret.encode(),
            (timestamp + 'GET' + request_path).encode(), hashlib.sha256).digest()).decode()
        headers = {'OK-ACCESS-KEY': self.credentials.key,
                   'OK-ACCESS-SIGN': signature,
                   'OK-ACCESS-TIMESTAMP': timestamp,
                   'OK-ACCESS-PASSPHRASE': self.credentials.passphrase,
                   'x-simulated-trading': self.simulated_header}
        return self._data(self.client.get(request_path, headers=headers))

    def inspect(self):
        try:
            self.sync_time()
            config = self.get('/api/v5/account/config')[0]
            permissions = set(config.get('perm', '').split(','))
            if 'trade' not in permissions:
                raise DemoError('此模拟 API 没有交易权限；请在 OKX 自行检查权限。')
            if 'withdraw' in permissions:
                raise DemoError('请为此 App 使用不含提币权限的独立模拟 API。')
            balance = self.get('/api/v5/account/balance', {'ccy': 'USDT'})
            positions = self.get('/api/v5/account/positions', {'instType': 'SWAP'})
            orders = self.get('/api/v5/trade/orders-pending', {'instType': 'SWAP'})
            algos = self.get('/api/v5/trade/orders-algo-pending', {'ordType': 'conditional', 'instType': 'SWAP'})
            if not balance:
                raise DemoError('未读取到模拟账户资产，请先在 OKX 模拟交易初始化账户。')
            return dict(connected=True, environment=self.environment, execution_enabled=False,
                verified_at=time.time(), account_mode=str(config.get('acctLv', '')),
                position_mode=str(config.get('posMode', '')),
                account_reference=hashlib.sha256(str(config.get('uid', '')).encode()).hexdigest()[:16],
                trade_permission=True, swap_positions=len(positions), pending_orders=len(orders),
                conditional_orders=len(algos),
                note='连接核验成功；尚未启用 OKX 下单和交易所保护单。')
        except httpx.HTTPError:
            raise DemoError('无法连接 OKX 模拟 API，请检查网络和所属站点。') from None
        except (KeyError, IndexError, TypeError):
            raise DemoError('OKX 账户返回不完整，未完成核验。') from None


class LiveReadOnlyProbe(DemoProbe):
    """Explicit live-account READ access; no environment fallback or write method.

    Owner changed requested integration to real account on 2026-10-18 after
    the supplied key returned 50101 in demo. Reads do not authorize orders.
    """
    simulated_header = '0'
    environment = 'okx_live'


def save_private(path: Path, value):
    """Atomic, owner-only storage outside the repository; no secret in argv."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise DemoError('拒绝将接入信息写入符号链接。')
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix='.demo-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_status(runtime: Path, environment='okx_live'):
    if environment not in ('okx_live', 'okx_demo'):
        raise DemoError('未知账户环境。')
    path = runtime / ('okx-live-status.json' if environment == 'okx_live' else 'okx-demo-status.json')
    if not path.exists():
        return dict(connected=False, environment=environment, execution_enabled=False)
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise DemoError('模拟接入状态文件权限异常。')
    raw = json.loads(path.read_text())
    result = {k: raw.get(k) for k in ('connected', 'verified_at', 'account_mode',
        'position_mode', 'trade_permission')}
    # Never expose saved credentials, IDs, account amounts or provider payloads.
    return dict(result, environment=environment, execution_enabled=False)
