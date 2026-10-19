"""Manage the discipline app's separate named Cloudflare connector on macOS.

The owner chose discipline.spikeok.com for the independent paper app. Only
health and authenticated v1 routes reach the existing loopback paper engine.
Cloudflare account/DNS changes are explicit CLI operations, not side effects
of starting this service. Existing workspace tunnels are never touched.
Source: developers.cloudflare.com/cloudflare-one/networks/connectors/
cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
import uuid

LABEL = 'com.spike.discipline.cloudflared'
HOST = 'discipline.spikeok.com'
RUNTIME = Path.home() / 'Library/Application Support/SpikeDiscipline/public'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
CONFIG = RUNTIME / 'cloudflared.json'
CREDENTIALS = RUNTIME / 'tunnel-credentials.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install', 'start', 'stop', 'status'])
    parser.add_argument('--tunnel-id', type=uuid.UUID)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This helper manages a macOS LaunchAgent only')
    domain = f'gui/{os.getuid()}'
    target = f'{domain}/{LABEL}'
    result = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
    loaded = result.returncode == 0
    if args.command == 'status':
        print(f'{LABEL}: https://{HOST}')
        # Never print the inherited launchd environment, which can hold secrets.
        if not loaded:
            print('Service is not loaded')
        for line in result.stdout.splitlines():
            if line.strip().startswith(('state = ', 'pid = ', 'last exit code = ')):
                print(line.strip())
        return result.returncode
    if args.command == 'stop':
        if loaded:
            subprocess.run(['launchctl', 'bootout', target], check=True)
            # bootout can return before launchd has removed a stopping job.
            # Do not let a following start mistake that job for a running one.
            deadline = time.monotonic() + 10
            while subprocess.run(['launchctl', 'print', target], capture_output=True).returncode == 0:
                if time.monotonic() >= deadline:
                    parser.error('Connector is still stopping; retry start after status reports unloaded')
                time.sleep(0.2)
        return 0
    if args.command == 'install':
        binary = shutil.which('cloudflared')
        if not binary or not args.tunnel_id or not CREDENTIALS.is_file():
            parser.error('Install cloudflared, create dedicated credentials, and provide --tunnel-id first')
        identity = json.loads(CREDENTIALS.read_text())
        if identity.get('TunnelID') != str(args.tunnel_id):
            parser.error('Dedicated credentials do not match the requested tunnel')
        os.umask(0o077)
        RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
        CREDENTIALS.chmod(0o600)
        config = {'tunnel': str(args.tunnel_id), 'credentials-file': str(CREDENTIALS),
                  'metrics': '127.0.0.1:0', 'protocol': 'http2', 'edge-ip-version': '4',
                  'loglevel': 'info',
                  'ingress': [
                      {'hostname': HOST, 'path': '^/(health|v1/.*)$',
                       'service': 'http://127.0.0.1:8897'},
                      {'service': 'http_status:404'}]}
        if CONFIG.exists() and json.loads(CONFIG.read_text()) != config:
            parser.error('Existing connector config differs; inspect it before replacing')
        CONFIG.write_text(json.dumps(config, indent=2) + '\n')
        CONFIG.chmod(0o600)
        subprocess.run([binary, 'tunnel', '--config', str(CONFIG), 'ingress', 'validate'], check=True)
        log = RUNTIME / 'connector.log'
        log.touch(mode=0o600, exist_ok=True)
        log.chmod(0o600)
        document = dict(Label=LABEL,
            ProgramArguments=[binary, 'tunnel', '--config', str(CONFIG), '--no-autoupdate',
                              'run', str(args.tunnel_id)],
            WorkingDirectory=str(RUNTIME), RunAtLoad=True, KeepAlive=True,
            ThrottleInterval=15, ProcessType='Background',
            StandardOutPath=str(log), StandardErrorPath=str(log))
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        if PLIST.exists() and plistlib.loads(PLIST.read_bytes()) != document:
            parser.error('Existing LaunchAgent differs; inspect it before replacing')
        PLIST.write_bytes(plistlib.dumps(document))
        PLIST.chmod(0o600)
    if not PLIST.exists() or not CONFIG.exists() or not CREDENTIALS.exists():
        parser.error('Run install first')
    if not loaded:
        subprocess.run(['launchctl', 'bootstrap', domain, str(PLIST)], check=True)
    print(f'{LABEL} loaded; verify https://{HOST}/health separately')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
