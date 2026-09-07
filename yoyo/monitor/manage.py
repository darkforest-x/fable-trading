"""Install/status/stop the owner-authorized Mac LaunchAgent, without secrets.

Uses launchd KeepAlive and caffeinate -is. This prevents idle system sleep
while plugged in; it cannot make a closed/offline/shut-down Mac scan markets.
No other launch job, power preference, executor or production setting changes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys

LABEL = "com.fable.impulse-monitor"
ROOT = Path(__file__).resolve().parents[2]
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def status(target):
    """Return only service-owned paths and validated top-level launchd fields.

    launchctl print includes inherited environment variables, potentially
    containing unrelated credentials. Capture both streams in memory and
    never return or echo either stream, even when launchctl fails. Parsing
    accepts only the shallowest field indentation; nested environment keys
    cannot impersonate service state. Values have fixed types/allowlists.
    """
    logs = Path.home() / "Library/Logs/Fable/ImpulseMonitor"
    summary = dict(label=LABEL, state="unavailable", pid=None, runs=None,
                   config_path=str(PLIST), stdout_log=str(logs / "service.log"),
                   stderr_log=str(logs / "service-error.log"))
    try:
        result = subprocess.run(["launchctl", "print", target], capture_output=True,
                                text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return summary
    if result.returncode:
        return summary
    summary["state"] = "unknown"
    fields = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"([ \t]+)([a-z][a-z ]*) = (.*)", line)
        if match:
            fields.append((len(match[1].expandtabs(8)), match[2], match[3]))
    if not fields:
        return summary
    top_indent = min(indent for indent, _, _ in fields)
    states = {"running", "not running", "spawn scheduled", "waiting", "exited",
              "starting", "stopping", "suspended"}
    for indent, name, value in fields:
        if indent != top_indent:
            continue
        if name == "state" and value in states:
            summary["state"] = value
        elif name in ("pid", "runs") and re.fullmatch(r"[0-9]{1,10}", value):
            summary[name] = int(value)
    return summary


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
        summary = status(target)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["state"] == "unavailable":
            raise SystemExit(1)
    elif args.action == "stop":
        subprocess.run(["launchctl", "bootout", target], check=True)
        print("Monitor stopped; run install to start it again.")
    else:
        subprocess.run(["launchctl", "kickstart", "-k", target], check=True)


if __name__ == "__main__":
    main()
