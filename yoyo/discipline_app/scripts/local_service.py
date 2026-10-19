"""Manage only the independent, loopback-only macOS paper LaunchAgent.

Does not import or restart the research/production monitor or execution layer.
The access token is stored in the private runtime directory, never in the plist.
"""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = 'com.spike.discipline.paper'
ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path.home() / 'Library/Application Support/SpikeDiscipline'
PYTHON = Path.home() / 'Library/Caches/SpikeDiscipline/service-venv/bin/python'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install', 'start', 'stop', 'status'])
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This helper only manages the local macOS service')
    domain = f'gui/{os.getuid()}'
    target = f'{domain}/{LABEL}'
    if args.command == 'status':
        result = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
        # launchctl also prints inherited environment secrets; expose only
        # process health fields, never its full diagnostic document.
        print(LABEL)
        if result.returncode:
            print('Service is not loaded')
        else:
            for line in result.stdout.splitlines():
                if line.strip().startswith(('state = ', 'pid = ', 'last exit code = ')):
                    print(line.strip())
        return result.returncode
    if args.command == 'stop':
        return subprocess.call(['launchctl', 'bootout', target])
    if args.command == 'install':
        if not PYTHON.exists():
            parser.error(f'Install the isolated service environment first: {PYTHON}')
        os.umask(0o077)
        RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run([str(PYTHON), '-m', 'yoyo.discipline_app', 'init'], cwd=ROOT, check=True)
        document = dict(Label=LABEL,
            ProgramArguments=[str(PYTHON), '-m', 'yoyo.discipline_app', 'serve'],
            WorkingDirectory=str(ROOT), RunAtLoad=True, KeepAlive=True,
            ThrottleInterval=15, ProcessType='Background',
            StandardOutPath=str(RUNTIME / 'service.stdout.log'),
            StandardErrorPath=str(RUNTIME / 'service.stderr.log'),
            EnvironmentVariables={'PYTHONUNBUFFERED': '1'})
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        if PLIST.exists() and plistlib.loads(PLIST.read_bytes()) != document:
            parser.error('An existing different plist uses this label; inspect it before replacing')
        PLIST.write_bytes(plistlib.dumps(document))
        PLIST.chmod(0o600)
    if not PLIST.exists():
        parser.error('Run install first')
    loaded = subprocess.run(['launchctl', 'print', target], capture_output=True).returncode == 0
    if not loaded:
        subprocess.run(['launchctl', 'bootstrap', domain, str(PLIST)], check=True)
    print(f'{LABEL} loaded; loopback http://127.0.0.1:8897; paper only')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
