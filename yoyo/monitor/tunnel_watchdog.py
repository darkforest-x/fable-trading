"""Recover the existing named tunnel from confirmed, sustained Cloudflare 1033.

Two 2026-10-26 incidents left cloudflared alive and locally ready while Cloudflare
reported no active connector. launchd KeepAlive cannot recover a living process.
Check actual public reachability independently of the monitor/market scan loop.
Only three consecutive 1033 responses with healthy local origin and gateway may
restart the exact existing connector job. DNS/timeout/403/502 failures never do.
Source: https://developers.cloudflare.com/support/troubleshooting/http-status-codes/
cloudflare-1xxx-errors/error-1033/
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time
import urllib.error
import urllib.request

from . import public_tunnel as gateway

LABEL = "com.spike.public-tunnel-health"
INTERVAL = 60
COOLDOWN = 600
SERVICE = "spike Impulse Monitor"


def probe(url, headers=None):
    """Read a bounded response without system HTTP proxies or redirect following."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    request = urllib.request.Request(url, headers={
        "User-Agent": "SPIKE-Public-Health/1.0", "Cache-Control": "no-cache",
        **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        try:
            response = opener.open(request, timeout=8)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(65536).decode("utf-8", "replace")
            code = response.code
            cloudflare = (response.headers.get("Server", "").lower() == "cloudflare"
                          and bool(response.headers.get("CF-Ray")))
        if code == 200:
            try:
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("service") == SERVICE:
                    return "healthy"
            except ValueError:
                pass
        # Both curl's plain-text page and Cloudflare's browser error template.
        if code == 530 and cloudflare and re.search(
            r"error\s+code\s*:\s*1033\b|\bError\s+1033\b|error-code[^>]*>\s*1033\b",
            body, re.I,
        ):
            return "cloudflare_1033"
        return "http_" + str(code)
    except (OSError, ValueError):
        return "unreachable"


def decide(previous, now, identity, local, edge, public, installed):
    """Require consecutive real failures and bound all restart attempts."""
    same = previous.get("identity") == identity
    # Preserve future timestamps after a backwards clock adjustment: uncertainty
    # must delay another restart, not erase the cooldown/rate limit.
    attempts = [t for t in previous.get("attempts", [])
                if isinstance(t, (int, float)) and now - t < 3600]
    consecutive = (same and 0 < now - previous.get("checked_at", 0) <= INTERVAL * 1.5
                   and previous.get("public") == "cloudflare_1033")
    candidate = installed and local == edge == "healthy" and public == "cloudflare_1033"
    failures = (previous.get("failures", 0) + 1 if consecutive else 1) if candidate else 0
    action = "healthy" if public == "healthy" else "observe"
    if not installed:
        action = "connector_disabled"
    elif candidate and failures >= 3:
        if attempts and now - attempts[-1] < COOLDOWN:
            action = "cooldown"
        elif len(attempts) >= 3:
            action = "hourly_limit"
        else:
            action = "restart_connector"
            attempts.append(now)
            failures = 0
    return dict(identity=identity, checked_at=now, local=local, gateway=edge,
                public=public, failures=failures, attempts=attempts, action=action)


def restart_connector():
    # Never bootstrap a deliberately stopped job or touch the application worker.
    if not gateway.installed(gateway.NAMED_LABEL):
        return "connector_disabled"
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", gateway.target(gateway.NAMED_LABEL)],
            capture_output=True, timeout=25, check=False)
        return "requested" if result.returncode == 0 else "failed"
    except (OSError, subprocess.SubprocessError):
        return "failed"


def check():
    from .named_tunnel import saved

    runtime = gateway.RUNTIME
    if not (runtime / "named-endpoint.json").exists():
        return {"action": "not_configured"}
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(runtime / "tunnel-health.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"action": "already_checking"}
        endpoint = saved()
        state_path = runtime / "tunnel-health.json"
        previous = json.loads(state_path.read_text()) if state_path.exists() else {}
        installed = gateway.installed(gateway.NAMED_LABEL)
        local = edge = public = "not_checked"
        if installed:
            local = probe("http://127.0.0.1:8766/api/status")
            edge = probe(f"http://127.0.0.1:{gateway.PORT}/api/status", {
                "Host": gateway.fixed_host(endpoint["url"]), "X-Forwarded-Proto": "https"})
            public = probe(endpoint["url"] + "/api/status")
        state = decide(previous, time.time(), endpoint["url"] + "/" + endpoint["tunnel_id"],
                       local, edge, public, installed)
        # Record the attempt BEFORE the side effect; a timeout cannot cause a loop.
        gateway.private_write(state_path, json.dumps(state) + "\n")
        if state["action"] == "restart_connector":
            state["restart_result"] = restart_connector()
            gateway.private_write(state_path, json.dumps(state) + "\n")
        if state["action"] != previous.get("action") or state["action"] == "restart_connector":
            print(json.dumps(state), flush=True)
        return state


def install():
    """Install a separate one-shot check; no changes to the connector or monitor."""
    from .named_tunnel import saved

    saved()  # Require a valid existing named endpoint before installing anything.
    path = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    config = dict(Label=LABEL,
                  ProgramArguments=[str(gateway.ROOT / ".venv/bin/python"), "-m",
                                    "yoyo.monitor.tunnel_watchdog", "check"],
                  WorkingDirectory=str(gateway.ROOT), StartInterval=INTERVAL,
                  RunAtLoad=True, ProcessType="Background", ExitTimeOut=10,
                  StandardOutPath=str(gateway.RUNTIME / (LABEL + ".log")),
                  StandardErrorPath=str(gateway.RUNTIME / (LABEL + ".log")))
    if path.exists() and plistlib.loads(path.read_bytes()) != config:
        raise RuntimeError("Existing tunnel health job differs; inspect before replacing")
    path.parent.mkdir(parents=True, exist_ok=True)
    gateway.private_write(path, plistlib.dumps(config))
    if not gateway.installed(LABEL):
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                       check=True, capture_output=True, timeout=15)
    print("Tunnel health check installed: 60s interval, 3 confirmed failures, 10min cooldown")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "install", "stop", "status"])
    action = parser.parse_args().action
    if action == "install":
        install()
    elif action == "stop":
        gateway.remove_job(LABEL)
    elif action == "status":
        path = gateway.RUNTIME / "tunnel-health.json"
        print(json.dumps(dict(installed=gateway.installed(LABEL),
                              last_check=json.loads(path.read_text()) if path.exists() else None)))
    else:
        check()


if __name__ == "__main__":
    main()
