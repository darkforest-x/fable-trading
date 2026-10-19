"""Isolated Caddy HTTPS bridge for a phone on the same private LAN as this Mac.

The paper engine remains the sole worker on loopback. Caddy uses separate
storage, no admin listener, no HTTP redirects, and never installs a trust root.
Sources: caddyserver.com/docs/caddyfile/options and /directives/tls.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import plistlib
import shutil
import ssl
import subprocess
import uuid


def private_address(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise argparse.ArgumentTypeError('Use a private IPv4 address') from error
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if not any(address in ipaddress.IPv4Network(n) for n in networks):
        raise argparse.ArgumentTypeError('Use this Mac\'s specific RFC1918 LAN address')
    return str(address)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'run'])
    parser.add_argument('--host', required=True, type=private_address)
    parser.add_argument('--serve-certificate', action='store_true',
                        help='Expose only the public root certificate on HTTP port 8899')
    args = parser.parse_args()
    caddy = shutil.which('caddy')
    if not caddy:
        parser.error('Install Caddy from its official distribution first')
    os.umask(0o077)
    directory = Path.home() / 'Library/Application Support/SpikeDiscipline/https'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage = directory / 'storage'
    config = directory / 'Caddyfile'
    root = storage / 'pki/authorities/local/root.crt'
    # Fixed paths are quoted; host is a parsed private IPv4 address. No token is
    # stored in the bridge configuration or exposed in process arguments.
    text = f'''{{
    admin off
    persist_config off
    auto_https disable_redirects
    skip_install_trust
    storage file_system "{storage}"
    pki {{
        ca local {{
            name "Spike Discipline Local Test CA"
            root_cn "Spike Discipline Local Test CA"
        }}
    }}
}}

https://{args.host}:8898 {{
    bind {args.host}
    tls internal
    @api path /health /v1/*
    handle @api {{
        reverse_proxy 127.0.0.1:8897
    }}
    handle {{
        respond 404
    }}
}}
'''
    if args.serve_certificate:
        text += f'''
http://{args.host}:8899 {{
    bind {args.host}
    handle /spike-local-test.mobileconfig {{
        root * "{directory / 'public'}"
        header Content-Type application/x-apple-aspen-config
        file_server
    }}
    handle /spike-local-ca.crt {{
        root * "{root.parent}"
        rewrite * /root.crt
        header Content-Type application/x-x509-ca-cert
        file_server
    }}
    handle {{
        respond 404
    }}
}}
'''
    config.write_text(text)
    config.chmod(0o600)
    subprocess.run([caddy, 'validate', '--config', str(config), '--adapter', 'caddyfile'], check=True)
    public = directory / 'public'
    public.mkdir(exist_ok=True, mode=0o700)
    profile = public / 'spike-local-test.mobileconfig'
    # An unsigned, removable certificate-only profile, never MDM enrollment.
    # Stable identifiers avoid duplicate profiles on repeated local starts.
    namespace = uuid.UUID('2e858fc1-87f0-4f30-a7e7-658fce0e57d3')
    payload = dict(PayloadVersion=1, PayloadType='com.apple.security.root',
        PayloadIdentifier='com.spike.discipline.local-test.root',
        PayloadUUID=str(uuid.uuid5(namespace, 'root')),
        PayloadDisplayName='Spike Discipline Local Test CA',
        PayloadContent=ssl.PEM_cert_to_DER_cert(root.read_text()))
    profile.write_bytes(plistlib.dumps(dict(PayloadVersion=1,
        PayloadType='Configuration', PayloadIdentifier='com.spike.discipline.local-test',
        PayloadUUID=str(uuid.uuid5(namespace, 'profile')),
        PayloadDisplayName='Spike 本机测试证书',
        PayloadDescription='仅用于连接本机 Mac 上的 Spike 纪律模拟服务；测试结束后可移除。',
        PayloadRemovalDisallowed=False, PayloadContent=[payload])))
    profile.chmod(0o600)
    print(f'Phone API: https://{args.host}:8898', flush=True)
    print(f'Public test CA: {root}', flush=True)
    if args.serve_certificate:
        print(f'Certificate profile: http://{args.host}:8899/spike-local-test.mobileconfig', flush=True)
    if args.command == 'run':
        os.execv(caddy, [caddy, 'run', '--config', str(config), '--adapter', 'caddyfile'])


if __name__ == '__main__':
    main()
