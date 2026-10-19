"""Independent local service entrypoint; paper-only and loopback-only by default."""
import argparse
import json
import os
import secrets
from pathlib import Path
from .book import Book
from .policy import Policy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'serve', 'status', 'policy'])
    parser.add_argument('--runtime', type=Path, default=Path.home() / 'Library/Application Support/SpikeDiscipline')
    parser.add_argument('--port', type=int, default=8897)
    parser.add_argument('--policy-file', type=Path)
    parser.add_argument('--apply-now', action='store_true', help='Explicit owner-authorized flat-ledger policy activation')
    parser.add_argument('--mode', choices=['paper', 'okx_live'], default='paper')
    parser.add_argument('--enable-live-execution', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    args.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = args.runtime / 'access-token'
    if args.enable_live_execution and args.mode != 'okx_live':
        parser.error('Live execution requires explicit --mode okx_live')
    if args.mode == 'okx_live':
        if args.command != 'serve':
            parser.error('Live mode is available only for the serve command')
        import fcntl
        import stat
        from .live_book import LiveBook
        from .okx_demo import Credentials
        from .okx_execution import OkxExecutionClient
        path = args.runtime / 'okx-live-credentials.json'
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            parser.error('Missing or unsafe private live credential file')
        raw = json.loads(path.read_text())
        if raw.get('environment') != 'okx_live' or raw.get('host') != 'https://www.okx.com':
            parser.error('Live credential environment mismatch')
        lease = (args.runtime / 'live.service.lock').open('a')
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Another live service owns this runtime')
        client = OkxExecutionClient(Credentials(raw['key'],raw['secret'],raw['passphrase']),
            environment='okx_live',write_enabled=args.enable_live_execution)
        del raw
        from .accounts import Accounts
        book = Accounts(LiveBook(args.runtime / 'live.sqlite3', client), args.runtime)
    else:
        book = Book(args.runtime / 'paper.sqlite3')
    if args.command == 'init':
        if not token_file.exists():
            with token_file.open('x') as output:
                output.write(secrets.token_urlsafe(36) + '\n')
            token_file.chmod(0o600)
        if book.snapshot()['policy'] is None:
            book.configure(Policy())
        print(json.dumps({'mode': 'paper', 'live_enabled': False, 'token_file': str(token_file), 'runtime': str(args.runtime)}))
    elif args.command == 'status':
        state = book.snapshot()
        print(json.dumps({k: state[k] for k in ('mode', 'policy_revision', 'blockers', 'heartbeat')}, ensure_ascii=False))
    elif args.command == 'policy':
        if not args.policy_file:
            parser.error('--policy-file required')
        policy = Policy.parse(json.loads(args.policy_file.read_text()))
        (book.activate_policy_now if args.apply_now else book.configure)(policy)
        print('Policy applied or queued; existing locks and counters retained.')
    else:
        if not token_file.exists():
            parser.error('Run init first')
        import uvicorn
        from .api import create_app
        app = create_app(book, token_file.read_text().strip(), origins=(
            'capacitor://localhost', 'https://localhost',
            'http://127.0.0.1:5199', 'http://localhost:5199'))
        uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
