"""Maintain an owner-authorized SSH display tunnel to the 3060.

The Mac scanner stays bound to loopback. Only the authenticated Windows host
gets a loopback forwarding port; its separate desktop client handles clicks.
OpenSSH verifies the already-known host key, exits on initial forwarding
failure and detects disconnects; launchd restarts this dedicated connection.
No firewall, SSH server, monitoring job, model or notification changes.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess

LABEL = "com.spike.3060-tunnel"


def tunnel_config(remote: str, log_dir: Path) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@(?:[A-Za-z0-9][A-Za-z0-9.-]{0,252})", remote):
        raise ValueError("Use user@verified-host, not SSH options or a URL")
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/ssh", "-N", "-T", "-o", "BatchMode=yes",
                             "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes",
                             "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
                             "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR",
                             "-R", "127.0.0.1:8767:127.0.0.1:8766", remote],
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
        "ExitTimeOut": 10,
        "StandardOutPath": str(log_dir / "tunnel.log"),
        "StandardErrorPath": str(log_dir / "tunnel-error.log"),
    }


def install(remote: str):
    logs = Path.home() / "Library/Logs/Spike/WindowsClient"
    config = tunnel_config(remote, logs)
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    plist = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    target = domain + "/" + LABEL
    if plist.exists() and plistlib.loads(plist.read_bytes()) != config:
        raise RuntimeError("Existing tunnel differs; inspect its destination before changing it.")
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(config))
    plist.chmod(0o600)
    known = subprocess.run(["launchctl", "print", target], capture_output=True, timeout=10)
    if known.returncode:
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True, timeout=10)
    print("Spike Windows display tunnel installed; scanner unchanged.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("remote", help="Previously verified SSH user@Windows host")
    args = parser.parse_args()
    install(args.remote)


if __name__ == "__main__":
    main()
