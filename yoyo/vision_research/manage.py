"""Manage only the loopback vision workbench as a macOS user LaunchAgent.

The server must outlive the command/agent session that opens its frontend.
Uses local launchctl bootstrap/bootout and launchd.plist RunAtLoad/KeepAlive;
the existing monitor's scanner, notification and power settings are untouched.
No credentials are copied into the plist or printed from launchctl output.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import sys
import time

import httpx

LABEL = "com.fable.spike-vision-research"
ROOT = Path(__file__).resolve().parents[2]
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
LOGS = Path.home() / "Library/Logs/Fable/SpikeVisionResearch"
URL = "http://127.0.0.1:8771"


def launchctl(*arguments):
    # launchctl print includes inherited environment: capture, never echo it.
    return subprocess.run(["/bin/launchctl", *arguments], capture_output=True,
                          timeout=15, check=False)


def target():
    return f"gui/{os.getuid()}/{LABEL}"


def is_loaded():
    return launchctl("print", target()).returncode == 0


def healthy():
    try:
        with httpx.Client(trust_env=False, timeout=12) as client:
            response = client.get(URL + "/api/status")
            return response.status_code == 200 and response.json().get("app_name") == "SPIKE Vision Lab"
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def start():
    if not is_loaded():
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", 8771)) == 0:
                raise RuntimeError("Port 8771 is already occupied by an unmanaged process; inspect it before starting.")
        python = ROOT / ".venv/bin/python"
        if not python.is_file():
            raise RuntimeError("The repository .venv/bin/python is missing.")
        LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = {
            "Label": LABEL,
            "ProgramArguments": [str(python), "-m", "yoyo.vision_research.server", "--port", "8771"],
            "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 10, "ExitTimeOut": 10,
            "StandardOutPath": str(LOGS / "service.log"),
            "StandardErrorPath": str(LOGS / "service-error.log"),
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        }
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        if PLIST.exists():
            if plistlib.loads(PLIST.read_bytes()) != config:
                raise RuntimeError("Existing workbench service config differs; inspect before replacing.")
        else:
            with PLIST.open("xb") as stream:
                plistlib.dump(config, stream)
            PLIST.chmod(0o600)
        if launchctl("bootstrap", f"gui/{os.getuid()}", str(PLIST)).returncode:
            raise RuntimeError("Workbench service could not be loaded; inspect the service logs.")
    for _ in range(20):
        if healthy():
            return
        time.sleep(0.25)
    raise RuntimeError("Workbench service loaded but HTTP is not ready; inspect " + str(LOGS))


def main():
    parser = argparse.ArgumentParser(description="Manage the local SPIKE Vision Lab service")
    parser.add_argument("action", choices=["start", "status", "stop", "restart"])
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This service manager requires macOS; run vision_research.server directly elsewhere.")
    if args.action == "stop":
        if is_loaded() and launchctl("bootout", target()).returncode:
            raise RuntimeError("Workbench service could not be stopped.")
        print("Workbench stopped for this login session; use start to open it again.")
        return
    if args.action == "restart" and is_loaded():
        if launchctl("kickstart", "-k", target()).returncode:
            raise RuntimeError("Workbench service could not be restarted.")
    if args.action in {"start", "restart"}:
        start()
    state = {"label": LABEL, "loaded": is_loaded(), "http_ready": healthy(),
             "url": URL, "config": str(PLIST), "logs": str(LOGS)}
    print(json.dumps(state, ensure_ascii=False, indent=2))
    if not state["loaded"] or not state["http_ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
