"""Hourly macOS LaunchAgent for this iPhone's installed development release.

Enroll each shipped version with --app, --device and the exact --commit used
to build it. The private source snapshot is independent of the working tree.
Run 'status' for the last attempt, or 'check' to check the schedule now.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

BUNDLE = 'com.spike.discipline.native'
LABEL = 'com.spike.discipline.ios-renewal'
ROOT = Path.home() / 'Library/Application Support/SpikeDiscipline/ios-renewal'
CACHE = Path.home() / 'Library/Caches/SpikeDiscipline/ios-renewal'
AGENT = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
CONFIG = ROOT / 'release.json'
STATUS = ROOT / 'status.json'
NATIVE = 'yoyo/discipline_app/native'
DAY = 86400
BUILD_TIMEOUT = 2400


class DeviceUnavailable(RuntimeError):
    pass


def write_file(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        output.write(data)
    os.replace(output.name, path)


def save_json(path, value):
    write_file(path, json.dumps(value, indent=2).encode())


def sanitize_environment():
    # This task never needs the GUI session's unrelated API credential. Do not
    # pass it to Xcode's build phases or the device-management subprocesses.
    os.environ.pop('XAI_API_KEY', None)


def signed_release(app, udid, team=None):
    """Verify the sealed app, widget, team, device and earliest profile expiry."""
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', '--deep', str(app)], check=True)
    packages = [app, app / 'PlugIns/SpikeWidgets.appex']
    expires = []
    profiles = []
    for package, bundle_id in zip(packages, (BUNDLE, BUNDLE + '.widgets')):
        info = plistlib.loads((package / 'Info.plist').read_bytes())
        profile = plistlib.loads(subprocess.check_output([
            '/usr/bin/security', 'cms', '-D', '-i', str(package / 'embedded.mobileprovision')]))
        profile_team = profile['TeamIdentifier'][0]
        if team is None:
            team = profile_team
        if (profile_team != team or info['CFBundleIdentifier'] != bundle_id
                or profile['Entitlements']['application-identifier'] != team + '.' + bundle_id
                or udid.lower() not in [d.lower() for d in profile['ProvisionedDevices']]):
            raise ValueError('签名的账户、设备或应用不匹配')
        expires.append(profile['ExpirationDate'].replace(tzinfo=timezone.utc).timestamp())
        profiles.append(profile['UUID'])
    if not (app / 'service-token').is_file() or (app / 'service-token').stat().st_size < 32:
        raise ValueError('安装包缺少服务授权')
    info = plistlib.loads((app / 'Info.plist').read_bytes())
    return dict(version=info['CFBundleShortVersionString'], build=info['CFBundleVersion'],
                team=team, expires=min(expires), profiles=profiles, path=str(app))


def run_cycle(services, config, now):
    if config['expires'] - now > 2 * DAY:
        return {'status': 'scheduled'}
    candidate = services.prepare(config, now)
    version = (config['version'], config['build'])
    if (not candidate or candidate['expires'] <= max(config['expires'], now)
            or (candidate['version'], candidate['build']) != version):
        return {'status': 'waiting_profile'}
    try:
        installed = services.installed(config)
        if installed != version:
            return {'status': 'version_changed'}
        services.install(config, candidate)
        if services.installed(config) != version:
            return {'status': 'verification_failed'}
    except DeviceUnavailable:
        return {'status': 'waiting_device'}
    services.save(dict(config, expires=candidate['expires'], installed_at=now))
    return {'status': 'renewed'}


def offline_install_evidence(pkg, prior_version, prior_build, now):
    if (prior_version, prior_build) != (pkg['version'], pkg['build']):
        raise ValueError('离线登记要求上一轮核实的版本/构建号与签名包完全一致')
    return dict(source='prior_device_inspection', version=pkg['version'], build=pkg['build'],
                recorded_at=now, profile_check='pending_until_device_available')


def device_call(config, args, timeout):
    with tempfile.TemporaryDirectory() as temp:
        result = Path(temp) / 'device.json'
        cmd = ['/usr/bin/xcrun', 'devicectl', *args, '--device', config['udid'],
               '--timeout', str(timeout), '--json-output', str(result)]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 25)
        if proc.returncode:
            detail = (proc.stderr + proc.stdout).decode(errors='replace').strip().splitlines()
            raise DeviceUnavailable(detail[0][:240] if detail else 'iPhone 暂不可用')
        return json.loads(result.read_text())['result']


class Services:
    def installed(self, config):
        rows = device_call(config, ['device', 'info', 'apps', '--bundle-id', BUNDLE], 18)['apps']
        return next(((r['version'], r['bundleVersion']) for r in rows
                     if r['bundleIdentifier'] == BUNDLE), None)

    def install(self, config, candidate):
        device_call(config, ['device', 'install', 'app', candidate['path']], 120)

    def save(self, config):
        save_json(CONFIG, config)

    def prepare(self, config, now, force_build=False):
        dd = CACHE / config['commit']
        app = dd / 'Build/Products/Release-iphoneos/SpikeDiscipline.app'
        if app.is_dir() and not force_build:
            try:
                cached = signed_release(app, config['udid'], config['team'])
                if (cached['version'], cached['build']) == (config['version'], config['build']) and cached['expires'] > max(config['expires'], now + DAY):
                    return cached
            except (ValueError, KeyError, OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
                pass  # An interrupted build can be rebuilt from the pinned source.
        project = ROOT / config['commit'] / NATIVE / 'SpikeDiscipline.xcodeproj'
        with (ROOT / 'xcodebuild.log').open('w') as log:
            proc = subprocess.run(['/usr/bin/xcodebuild', '-project', str(project), '-scheme', 'SpikeDiscipline',
                                   '-configuration', 'Release', '-destination', 'generic/platform=iOS',
                                   '-derivedDataPath', str(dd), '-allowProvisioningUpdates', '-jobs', '3', 'build'],
                                  stdout=log, stderr=subprocess.STDOUT, timeout=BUILD_TIMEOUT)
        if proc.returncode:
            raise RuntimeError('Xcode 续签失败，请查看 xcodebuild.log 或 Xcode 的开发者账户状态')
        return signed_release(app, config['udid'], config['team'])


def check():
    config = json.loads(CONFIG.read_text())
    now = time.time()
    services = Services()
    try:
        if config.get('preflight'):
            # Prove automatic signing works under launchd, with the user's login keychain.
            pkg = services.prepare(config, now, force_build=True)
            if (pkg['version'], pkg['build']) != (config['version'], config['build']) or pkg['expires'] <= now:
                raise ValueError('后台签名验证失败：版本不符或描述文件已过期')
            config.pop('preflight')
            config['signing_verified_at'] = now
            services.save(config)
        result = run_cycle(services, config, now)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        result = {'status': 'error', 'detail': str(error)[:300]}
    config = json.loads(CONFIG.read_text())
    save_json(STATUS, dict(result, checked_at=now, expires=config['expires'],
                           signing_verified_at=config.get('signing_verified_at')))
    if config['expires'] - now <= DAY and result['status'] != 'renewed':
        last = config.get('last_warning', 0)
        if now - last >= DAY:
            message = ('请让本机连上已配对的 iPhone，必要时解锁手机。' if result['status']=='waiting_device'
                       else '请查看本机的自动续签状态。')
            subprocess.run(['/usr/bin/osascript','-e',
                            'display notification "' + message + '" with title "天才交易员签名即将或已经到期"'],
                           capture_output=True, timeout=15)
            services.save(dict(config, last_warning=now))
    return result


def enroll(args):
    services = Services()
    app = args.app.resolve()
    pkg = signed_release(app, args.device)
    if pkg['expires'] <= time.time():
        raise ValueError('请先安装仍在有效期内的版本')
    config = {k: pkg[k] for k in ('version','build','team','expires')}
    commit = subprocess.check_output(['git', 'rev-parse','--verify','--end-of-options',
                                      args.commit + '^{commit}'], text=True).strip()
    if not re.fullmatch('[0-9a-f]{40,64}', commit):
        raise ValueError('无效的 Git 提交')
    config.update(udid=args.device, commit=commit, enrolled_at=time.time(), preflight=True)
    if bool(args.prior_version) != bool(args.prior_build):
        raise ValueError('离线登记须同时提供上一轮核实的版本和构建号')
    if args.prior_version and (args.prior_version, args.prior_build) != (pkg['version'], pkg['build']):
        raise ValueError('上一轮核实的版本/构建号与签名包不一致')
    try:
        installed = services.installed(config)
    except DeviceUnavailable:
        if not args.prior_version:
            raise ValueError('iPhone 不可用；离线登记要求上一轮核实的版本/构建号与签名包完全一致')
        # This is an explicit fallback for a previously inspected device. It can
        # seed only that exact signed version/build; each later install still
        # re-reads the phone and requires the same pair before replacing it.
        config['installed_evidence'] = offline_install_evidence(
            pkg, args.prior_version, args.prior_build, time.time())
    else:
        if installed != (pkg['version'], pkg['build']):
            raise ValueError('请先把指定版本安装到这部 iPhone')
        # When connected, verify this exact set of profiles before seeding expiry.
        provision = shutil.which('ideviceprovision')
        if not provision:
            raise RuntimeError('登记时需要 ideviceprovision 读取手机上的描述文件')
        listed = subprocess.check_output([provision, '-u', args.device, 'list'], text=True).lower()
        if not all(profile.lower() in listed for profile in pkg['profiles']):
            raise ValueError('手机与签名包的描述文件不匹配')
        config['installed_evidence'] = dict(
            source='live_device_inspection', version=pkg['version'], build=pkg['build'],
            recorded_at=time.time(), profile_check='verified', profiles=pkg['profiles'])
    snapshot = ROOT / commit
    if not snapshot.exists():
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            archive = Path(temp) / 'source.tar'
            subprocess.run(['git','archive','-o',str(archive),commit,NATIVE,
                            'yoyo/discipline_app/mobile/src/assets/coins'],check=True)
            stage = Path(temp) / 'source'
            stage.mkdir()
            with tarfile.open(archive) as tar:
                if any(not (m.isdir() or m.isfile()) or Path(m.name).is_absolute()
                       or '..' in Path(m.name).parts for m in tar.getmembers()):
                    raise ValueError('签名源目录含不支持的文件')
                tar.extractall(stage)
            stage.rename(snapshot)
    write_file(ROOT / 'renew_ios.py', Path(__file__).read_bytes())
    services.save(config)
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(Label=LABEL, ProgramArguments=['/usr/bin/python3',str(ROOT/'renew_ios.py'),'check'],
               RunAtLoad=True, StartInterval=3600, ProcessType='Background', Umask=0o077,
               StandardErrorPath=str(ROOT/'agent.stderr.log'))
    write_file(AGENT, plistlib.dumps(doc))
    domain = 'gui/' + str(os.getuid())
    target = domain + '/' + LABEL
    if subprocess.run(['/bin/launchctl','print',target],capture_output=True).returncode == 0:
        subprocess.run(['/bin/launchctl','bootout',target],check=True)
    subprocess.run(['/bin/launchctl','enable',target],check=True)
    subprocess.run(['/bin/launchctl','bootstrap',domain,str(AGENT)],check=True)


def main():
    os.umask(0o077)
    sanitize_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    registration = subs.add_parser('enroll')
    registration.add_argument('--app', required=True, type=Path)
    registration.add_argument('--device', required=True)
    registration.add_argument('--commit', required=True)
    registration.add_argument('--prior-version', help='仅在手机当前不可用时，上一轮已核实的安装版本')
    registration.add_argument('--prior-build', help='仅在手机当前不可用时，上一轮已核实的安装构建号')
    subs.add_parser('check')
    subs.add_parser('status')
    args = parser.parse_args()
    if args.command == 'status':
        print(STATUS.read_text() if STATUS.exists() else '等待首次检查')
        return
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT / 'lock').open('a') as lock:
        if args.command == 'enroll':
            fcntl.flock(lock, fcntl.LOCK_EX)
            enroll(args)
        else:
            # bootstrap during enrollment can start the agent before enrollment unlocks.
            fcntl.flock(lock, fcntl.LOCK_EX)
            check()


if __name__ == '__main__':
    main()
