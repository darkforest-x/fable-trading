"""Install/status/stop the owner-authorized Mac LaunchAgent, without secrets.

Uses launchd KeepAlive and caffeinate -is. This prevents idle system sleep
while plugged in; it cannot make a closed/offline/shut-down Mac scan markets.
No other launch job, power preference, executor or production setting changes.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = "com.fable.impulse-monitor"
ROOT = Path(__file__).resolve().parents[2]
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def install():
    logs = Path.home() / "Library/Logs/Fable/ImpulseMonitor"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/caffeinate", "-is", str(ROOT / ".venv/bin/python"), "-m", "yoyo.monitor.server", "--port", "8766"],
        "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 30, "ExitTimeOut": 10,
        "StandardOutPath": str(logs / "service.log"), "StandardErrorPath": str(logs / "service-error.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if PLIST.exists():
        existing = plistlib.loads(PLIST.read_bytes())
        if existing != config:
            raise RuntimeError("Existing service configuration differs; inspect before replacing.")
    else:
        PLIST.write_bytes(plistlib.dumps(config))
        PLIST.chmod(0o600)
    target = f"gui/{os.getuid()}"
    known = subprocess.run(["launchctl", "print", target + "/" + LABEL], capture_output=True)
    if known.returncode:
        subprocess.run(["launchctl", "bootstrap", target, str(PLIST)], check=True)
    print("Service installed: http://127.0.0.1:8766")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "status", "stop", "restart"])
    args = parser.parse_args()
    target = f"gui/{os.getuid()}/{LABEL}"
    if args.action == "install":
        install()
    elif args.action == "status":
        subprocess.run(["launchctl", "print", target], check=True)
    elif args.action == "stop":
        subprocess.run(["launchctl", "bootout", target], check=True)
        print("Monitor stopped; run install to start it again.")
    else:
        subprocess.run(["launchctl", "kickstart", "-k", target], check=True)


if __name__ == "__main__":
    main()
