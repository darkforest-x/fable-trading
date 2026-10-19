"""Persist an owner-authorized Cloudflare hostname without retiring a working fallback early.

Uses a locally managed tunnel and exact-host ingress to the existing Caddy gate.
Registration, DNS delegation and cloudflared login are external prerequisites;
this module never creates accounts, bypasses domain holds, or prints credentials.
Source: https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from uuid import UUID

from . import public_tunnel as gateway


def saved():
    endpoint = json.loads((gateway.RUNTIME / "named-endpoint.json").read_text())
    gateway.fixed_host(endpoint["url"])
    UUID(endpoint["tunnel_id"])
    return endpoint


def config(url, tunnel_id, credentials_file):
    """Build YAML with exact-host routing; JSON quoting prevents YAML interpolation."""
    host = gateway.fixed_host(url)
    tunnel_id = str(UUID(tunnel_id))
    return (f"tunnel: {json.dumps(tunnel_id)}\n"
            f"credentials-file: {json.dumps(str(credentials_file))}\n"
            "ingress:\n"
            f"  - hostname: {json.dumps(host)}\n"
            f"    service: http://127.0.0.1:{gateway.PORT}\n"
            "  - service: http_status:404\n")


def start(url, tunnel_id, credentials_file):
    gateway.fixed_host(url)
    tunnel_id = str(UUID(tunnel_id))
    source = Path(credentials_file).expanduser().resolve()
    if source.is_relative_to(gateway.ROOT):
        raise ValueError("Tunnel credentials must be outside the repository")
    try:
        credentials = json.loads(source.read_text())
        valid = (str(UUID(credentials.get("TunnelID", ""))) == tunnel_id
                 and all(isinstance(credentials.get(k), str) and credentials[k]
                         for k in ("AccountTag", "TunnelSecret")))
    except (OSError, ValueError, AttributeError, TypeError):
        raise ValueError("Invalid or unreadable tunnel credentials") from None
    if not valid:
        raise ValueError("Tunnel credentials do not match the requested tunnel")
    previous = saved() if (gateway.RUNTIME / "named-endpoint.json").exists() else {}
    if previous and (previous["url"], previous["tunnel_id"]) != (url, tunnel_id):
        raise ValueError("A different named tunnel is already configured; inspect it before replacing")

    cloudflared = gateway.binary("cloudflared")
    gateway.RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    gateway.RUNTIME.chmod(0o700)
    private_credentials = gateway.RUNTIME / f"cloudflare-{tunnel_id}.json"
    gateway.private_write(private_credentials, json.dumps(credentials) + "\n")
    tunnel_config = gateway.RUNTIME / "cloudflare-named.yml"
    candidate = gateway.RUNTIME / "cloudflare-named.candidate.yml"
    gateway.private_write(candidate, config(url, tunnel_id, private_credentials))
    result = subprocess.run([cloudflared, "tunnel", "--config", str(candidate), "ingress", "validate"],
                            capture_output=True, timeout=15)
    if result.returncode:
        candidate.unlink(missing_ok=True)
        raise RuntimeError("Cloudflare ingress validation failed; existing services retained")
    candidate.replace(tunnel_config)
    old_url = gateway.saved_fixed_url()
    gateway.start_gateway(public_url=url, additional_public_urls=(old_url,) if old_url else ())
    gateway.start_job(gateway.NAMED_LABEL, [cloudflared, "tunnel", "--config", str(tunnel_config),
                     "--no-autoupdate", "--protocol", "http2", "--metrics", "127.0.0.1:0",
                     "run", tunnel_id])
    endpoint = {**previous, "provider": "cloudflare_named", "url": url,
                "tunnel_id": tunnel_id, "credentials_file": str(private_credentials)}
    gateway.private_write(gateway.RUNTIME / "named-endpoint.json", json.dumps(endpoint) + "\n")
    reachable = gateway.public_ready(url)
    if reachable:
        endpoint.setdefault("activated_at_ms", int(time.time() * 1000))
        gateway.private_write(gateway.RUNTIME / "named-endpoint.json", json.dumps(endpoint) + "\n")
        gateway.private_write(gateway.RUNTIME / "fixed-endpoint.json",
                              json.dumps({"provider": "cloudflare_named", "url": url}) + "\n")
        # Do not retire either fallback until the real public hostname serves the app.
        for label in (gateway.LABELS[1], gateway.FIXED_LABEL):
            gateway.remove_job(label)
        gateway.private_write(gateway.RUNTIME / "access.txt",
            f"SPIKE 固定公网入口（免登录）\n地址：{url}/#market?tab=overview\n"
            "Cloudflare 正式隧道；重连后地址不变。Mac 登录后自动启动；Mac 和网络必须在线。\n"
            "持有链接即可访问原工作台；原网关访问规则保留。\n"
            "查看/恢复：.venv/bin/python -m yoyo.monitor.public_tunnel status 或 start\n")
    return status(reachable=reachable)


def start_saved():
    endpoint = saved()
    return start(endpoint["url"], endpoint["tunnel_id"], endpoint["credentials_file"])


def status(services=None, reachable=None):
    endpoint = saved()
    if services is None:
        services = {label: gateway.installed(label)
                    for label in (*gateway.LABELS, gateway.FIXED_LABEL, gateway.NAMED_LABEL)}
    if reachable is None:
        reachable = gateway.public_ready(endpoint["url"]) if services[gateway.NAMED_LABEL] else False
    return dict(services=services, gateway_ready=gateway.gateway_ready() if services[gateway.LABELS[0]] else False,
                access_mode="no_login", provider="cloudflare_named", url=endpoint["url"],
                public_ready=reachable, pending_activation=not bool(endpoint.get("activated_at_ms")),
                access_file=str(gateway.RUNTIME / "access.txt"),
                note="Persistent hostname; public_ready probes this Mac's network path. Mac and network must remain online")
