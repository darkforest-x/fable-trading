"""Install and inspect the isolated, low-priority V7/V8 forward shadow job."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess


LABEL = "com.spike.v78-shadow"
ROOT = Path(__file__).resolve().parents[2]
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def configuration() -> dict[str, object]:
    logs = Path.home() / "Library/Logs/Spike/V78Shadow"
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/nice", "-n", "10", str(ROOT / ".venv/bin/python"),
                             "-m", "yoyo.monitor.v78_shadow"],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "ExitTimeOut": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "service.log"),
        "StandardErrorPath": str(logs / "service-error.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    }


def status(target: str) -> dict[str, object]:
    logs = Path.home() / "Library/Logs/Spike/V78Shadow"
    summary: dict[str, object] = {
        "label": LABEL, "state": "unavailable", "pid": None, "runs": None,
        "config_path": str(PLIST), "stdout_log": str(logs / "service.log"),
        "stderr_log": str(logs / "service-error.log"),
    }
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
    top = min(item[0] for item in fields)
    states = {"running", "not running", "spawn scheduled", "waiting", "exited",
              "starting", "stopping", "suspended"}
    for indent, name, value in fields:
        if indent != top:
            continue
        if name == "state" and value in states:
            summary["state"] = value
        elif name in ("pid", "runs") and re.fullmatch(r"[0-9]{1,10}", value):
            summary[name] = int(value)
    return summary


def install() -> None:
    config = configuration()
    logs = Path(config["StandardOutPath"]).parent
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if PLIST.exists():
        if plistlib.loads(PLIST.read_bytes()) != config:
            raise RuntimeError("Existing V7/V8 shadow configuration differs; inspect before replacing.")
    else:
        PLIST.write_bytes(plistlib.dumps(config))
        PLIST.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    if subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"],
                      capture_output=True, check=False).returncode:
        subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    print("V7/V8 forward shadow installed; it cannot notify or execute trades.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "status", "stop", "restart"))
    args = parser.parse_args()
    target = f"gui/{os.getuid()}/{LABEL}"
    if args.action == "install":
        install()
    elif args.action == "status":
        result = status(target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["state"] == "unavailable":
            raise SystemExit(1)
    elif args.action == "stop":
        subprocess.run(["launchctl", "bootout", target], check=True)
    else:
        subprocess.run(["launchctl", "kickstart", "-k", target], check=True)


if __name__ == "__main__":
    main()
