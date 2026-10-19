"""Owner-authorized, login-free public tunnels for the workspace.

The monitor and VLM stay on loopback. On 2026-09-24 the owner explicitly removed
the public login. Mutation origins are still checked against the public host
BEFORE adapting headers for the existing loopback-only services. Quick Tunnel
URLs are temporary; named Cloudflare tunnels retain an owner-controlled hostname.
The legacy ngrok dev domain is also fixed, but remains subject to account quotas.

Sources:
https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers
https://caddyserver.com/docs/caddyfile/matchers#expression
https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
https://ngrok.com/docs/gateway/domains#dev-domains
https://ngrok.com/docs/pricing-limits/free-plan-limits
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path.home() / "Library/Application Support/Fable/PublicTunnel"
LABELS = ("com.spike.public-gateway", "com.spike.public-tunnel")
FIXED_LABEL = "com.spike.public-ngrok"
NAMED_LABEL = "com.spike.public-cloudflare"
PORT = 8780


def fixed_host(url):
    # An exact HTTPS hostname only: never interpolate Caddy syntax or credentials.
    if not isinstance(url, str) or not re.fullmatch(
        r"https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", url
    ):
        raise ValueError("Use an HTTPS origin without a path, port, or credentials")
    return url.removeprefix("https://")


def saved_fixed_url():
    path = RUNTIME / "fixed-endpoint.json"
    if not path.exists():
        return None
    url = json.loads(path.read_text())["url"]
    fixed_host(url)
    return url


def caddy_config(port=PORT, upstream_port=8766, public_url=None, additional_public_urls=()) -> str:
    if not all(isinstance(value, int) and 1024 <= value <= 65535 for value in (port, upstream_port)):
        raise ValueError("Invalid loopback port")
    hosts = "*.trycloudflare.com"
    if public_url:
        hosts += " " + fixed_host(public_url)
    for url in additional_public_urls:
        host = fixed_host(url)
        if host not in hosts.split():
            hosts += " " + host
    return r'''{
    admin off
    auto_https off
    persist_config off
}
http://:GATEWAY_PORT {
    bind 127.0.0.1
    @invalid_host not host PUBLIC_HOSTS
    @insecure not header X-Forwarded-Proto https
    @bad_origin {
        not method GET HEAD OPTIONS
        expression `{http.request.header.Origin} != "https://" + {http.request.host} || ({http.request.header.Sec-Fetch-Site} != "" && {http.request.header.Sec-Fetch-Site} != "same-origin")`
    }
    @desktop path /api/tradingview/open /api/tradingview/open/*
    route {
        header {
            X-Robots-Tag "noindex, nofollow, noarchive"
            Referrer-Policy "no-referrer"
            X-Content-Type-Options "nosniff"
        }
        respond @invalid_host "Invalid tunnel host" 421
        respond @insecure "Use the HTTPS tunnel address" 400
        respond @bad_origin "Open this action from the same SPIKE HTTPS workspace." 403
        respond @desktop "Desktop opening is local-only. Use the chart link on this device." 403
        reverse_proxy 127.0.0.1:UPSTREAM_PORT {
            header_up Host 127.0.0.1:UPSTREAM_PORT
            header_up Origin http://127.0.0.1:UPSTREAM_PORT
            header_up Sec-Fetch-Site same-origin
            header_up X-Fable-Gateway public
            header_up -Authorization
            header_up -Proxy-Authorization
            header_up -Cookie
            header_up -Forwarded
            header_up -X-Forwarded-*
            header_down -Server
            header_down Location "^http://127[.]0[.]0[.]1:UPSTREAM_PORT(/.*)$" "https://{http.request.host}$1"
        }
    }
}
'''.replace("GATEWAY_PORT", str(port)).replace("UPSTREAM_PORT", str(upstream_port)).replace("PUBLIC_HOSTS", hosts)


def binary(name):
    candidate = shutil.which(name) or f"/opt/homebrew/opt/{name}/bin/{name}"
    if not Path(candidate).is_file():
        raise RuntimeError(f"Install {name} from Homebrew first")
    # Keep Homebrew's stable symlink so a package upgrade does not strand launchd.
    return str(Path(candidate).absolute())


def private_write(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as output:
        output.write(content.encode() if isinstance(content, str) else content)
    temporary.chmod(0o600)
    os.replace(temporary, path)


def job_config(label, arguments):
    return dict(Label=label, ProgramArguments=arguments, WorkingDirectory=str(ROOT),
                RunAtLoad=True, KeepAlive=True, ThrottleInterval=15, ExitTimeOut=10,
                StandardOutPath=str(RUNTIME / (label + ".log")),
                StandardErrorPath=str(RUNTIME / (label + ".log")))


def target(label):
    return f"gui/{os.getuid()}/{label}"


def installed(label):
    # launchctl's full output can contain inherited credentials; never display it.
    return subprocess.run(["launchctl", "print", target(label)], capture_output=True, timeout=10).returncode == 0


def start_job(label, arguments):
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    expected = job_config(label, arguments)
    if path.exists() and plistlib.loads(path.read_bytes()) != expected:
        raise RuntimeError(f"Existing {label} differs; refusing to replace it")
    private_write(path, plistlib.dumps(expected))
    if not installed(label):
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True, timeout=15)


def gateway_ready():
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/status", headers={
        "Host": "probe.trycloudflare.com", "X-Forwarded-Proto": "https"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
            return response.status == 200 and not response.headers.get("WWW-Authenticate")
    except OSError:
        return False


def start_gateway(public_url=None, additional_public_urls=()):
    caddy = binary("caddy")
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    RUNTIME.chmod(0o700)
    config = RUNTIME / "Caddyfile"
    expected = caddy_config(public_url=public_url, additional_public_urls=additional_public_urls)
    changed = not config.exists() or config.read_text() != expected
    # Validate before replacing the active file. Restart only the gateway so
    # cloudflared keeps its existing public URL during the login removal.
    candidate = RUNTIME / "Caddyfile.candidate"
    private_write(candidate, expected)
    validated = subprocess.run([caddy, "validate", "--config", str(candidate), "--adapter", "caddyfile"],
                               capture_output=True, timeout=15)
    if validated.returncode:
        candidate.unlink(missing_ok=True)
        raise RuntimeError("Caddy configuration validation failed; tunnel not started")
    if changed:
        os.replace(candidate, config)
    else:
        candidate.unlink()
    gateway_running = installed(LABELS[0])
    start_job(LABELS[0], [caddy, "run", "--config", str(config), "--adapter", "caddyfile"])
    if changed and gateway_running:
        subprocess.run(["launchctl", "kickstart", "-k", target(LABELS[0])], check=True, timeout=15)
    for _ in range(40):
        if gateway_ready():
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("Login-free gateway did not become ready; tunnel not started")


def start():
    if (RUNTIME / "named-endpoint.json").exists():
        from . import named_tunnel
        return named_tunnel.start_saved()
    if url := saved_fixed_url():
        return start_fixed(url)
    cloudflared = binary("cloudflared")
    start_gateway()
    if not installed(LABELS[1]):
        private_write(RUNTIME / (LABELS[1] + ".log"), "")
    start_job(LABELS[1], [cloudflared, "tunnel", "--config", "/dev/null", "--no-autoupdate",
                         "--protocol", "http2", "--metrics", "127.0.0.1:0",
                         "--url", f"http://127.0.0.1:{PORT}"])
    result = status()
    for _ in range(20):
        if result["url"]:
            break
        time.sleep(0.5)
        result = status()
    private_write(RUNTIME / "access.txt", "SPIKE 公网入口（免登录）\n地址：" + (result["url"] or "正在建立，请稍后查看状态")
                  + "\n无需账号或密码；持有链接即可访问完整工作台。"
                  + "\n\n地址请运行：.venv/bin/python -m yoyo.monitor.public_tunnel status\n"
                  "停止公网入口：.venv/bin/python -m yoyo.monitor.public_tunnel stop\n"
                  "免费试用地址可能变化；Mac 必须保持在线。\n")
    return result


def public_ready(url):
    request = urllib.request.Request(url + "/api/status", headers={
        "User-Agent": "SPIKE-Public-Health/1.0", "ngrok-skip-browser-warning": "1"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200 and json.load(response).get("service") == "spike Impulse Monitor"
    except (OSError, ValueError, AttributeError):
        return False


def remove_job(label):
    if installed(label):
        subprocess.run(["launchctl", "bootout", target(label)], check=True, timeout=15)
    (Path.home() / "Library/LaunchAgents" / (label + ".plist")).unlink(missing_ok=True)


def start_fixed(url):
    fixed_host(url)
    ngrok = binary("ngrok")
    start_gateway(public_url=url)
    # ngrok reads the owner's existing private credential file. No secret is
    # copied into repository files, LaunchAgent arguments, or the access note.
    log = RUNTIME / (FIXED_LABEL + ".log")
    if not log.exists():
        private_write(log, "")
    start_job(FIXED_LABEL, [ngrok, "http", f"http://127.0.0.1:{PORT}", "--url", url,
                           "--inspect=false", "--log=stdout", "--log-format=json"])
    for _ in range(6):
        if public_ready(url):
            break
        time.sleep(1)
    else:
        raise RuntimeError("Fixed public endpoint did not become reachable; previous tunnel retained")
    private_write(RUNTIME / "fixed-endpoint.json", json.dumps({"provider": "ngrok", "url": url}) + "\n")
    # Retire the temporary endpoint only after the new HTTPS route reaches the app.
    remove_job(LABELS[1])
    private_write(RUNTIME / "access.txt", "SPIKE 固定公网入口（免登录）\n地址：" + url + "/#paper-desk\n"
                  "无需账号或密码；持有链接即可访问完整工作台。\n"
                  "固定域名绑定现有 ngrok 账号；重启后仍使用同一地址。\n"
                  "Mac 登录后自动启动，Mac 和工作台必须保持在线。\n"
                  "免费版首次访问需点 Visit Site，有月度流量与请求配额。\n\n"
                  "查看状态：.venv/bin/python -m yoyo.monitor.public_tunnel status\n"
                  "启动入口：.venv/bin/python -m yoyo.monitor.public_tunnel start\n"
                  "停止入口：.venv/bin/python -m yoyo.monitor.public_tunnel stop\n")
    return status()


def status():
    services = {label: installed(label) for label in (*LABELS, FIXED_LABEL, NAMED_LABEL)}
    if (RUNTIME / "named-endpoint.json").exists():
        from . import named_tunnel
        return named_tunnel.status(services)
    if url := saved_fixed_url():
        return dict(services=services, gateway_ready=gateway_ready() if services[LABELS[0]] else False,
                    access_mode="no_login", provider="ngrok", url=url,
                    public_ready=public_ready(url) if services[FIXED_LABEL] else False,
                    access_file=str(RUNTIME / "access.txt"),
                    note="Fixed account dev domain; provider quotas apply; Mac must remain online")
    log = RUNTIME / (LABELS[1] + ".log")
    urls = re.findall(r"https://[a-z0-9]+(?:-[a-z0-9]+)*[.]trycloudflare[.]com", log.read_text(errors="replace") if log.exists() else "")
    return dict(services=services, gateway_ready=gateway_ready() if services[LABELS[0]] else False,
                access_mode="no_login",
                url=urls[-1] if urls and services[LABELS[1]] else None,
                access_file=str(RUNTIME / "access.txt"),
                note="Temporary URL; no provider uptime guarantee; Mac must remain online")


def stop():
    for label in reversed((*LABELS, FIXED_LABEL, NAMED_LABEL)):
        remove_job(label)
    return dict(stopped=True, local_workspace="http://127.0.0.1:8766")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "start-fixed", "start-cloudflare", "status", "stop"])
    parser.add_argument("--url", help="Account-owned HTTPS origin for start-fixed or start-cloudflare")
    parser.add_argument("--tunnel-id", help="Existing locally managed Cloudflare tunnel UUID")
    parser.add_argument("--credentials-file", help="Private cloudflared tunnel JSON outside the repository")
    args = parser.parse_args()
    if args.action == "start-fixed" and not args.url:
        parser.error("start-fixed requires --url")
    if args.action == "start-cloudflare":
        if not all((args.url, args.tunnel_id, args.credentials_file)):
            parser.error("start-cloudflare requires --url, --tunnel-id and --credentials-file")
        from . import named_tunnel
        print(json.dumps(named_tunnel.start(args.url, args.tunnel_id, args.credentials_file),
                         ensure_ascii=False, indent=2))
        return
    print(json.dumps({"start": start, "start-fixed": lambda: start_fixed(args.url),
                      "status": status, "stop": stop}[args.action](), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
