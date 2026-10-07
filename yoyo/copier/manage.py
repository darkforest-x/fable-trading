"""Install, migrate and operate the Discord copier as a macOS user LaunchAgent.

Standard library only, so it runs from either venv. The service itself runs from
``.venv-copier`` (requirements-copier.txt): discord.py, telethon, python-okx and
openai stay out of the research venv, whose torch/numpy/pandas pins are a
cross-machine contract (CLAUDE.md, 铁律 6).

Why the old service died (2026-09-14): its venv linked ``python3`` to
/Applications/Xcode.app, Xcode was removed, and launchd restarted a missing
interpreter thousands of times (exit 127) while the admin UI and tunnel kept
running, so the dashboard looked alive. ``install`` refuses an interpreter that
does not resolve, and ``status`` reports the real launchd state.

Safety:
* ``migrate`` copies the old runtime state and sets ``dry_run=true`` in the copy.
  Going live again is the owner's action (CLAUDE.md, 实盘纪律 11).
* ``install`` refuses to start while the legacy cloudflared agent is loaded --
  it published port 8080 to the internet with no authentication.
* ``.env`` is copied byte-for-byte with mode 0600 and never read or printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import socket
import sqlite3
import subprocess

LABEL = "com.fable.discord-copier"
LEGACY_LABELS = ("codex.discord-okx-api", "codex.discord-okx-admin", "codex.discord-okx-cloudflared")
ROOT = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("COPIER_HOME") or
            Path.home() / "Library" / "Application Support" / "Fable" / "DiscordCopier")
LOGS = Path.home() / "Library" / "Logs" / "Fable" / "DiscordCopier"
AGENTS = Path.home() / "Library" / "LaunchAgents"
PLIST = AGENTS / (LABEL + ".plist")
PYTHON = ROOT / ".venv-copier" / "bin" / "python"
PORT = 8080
DB_NAME = "copier.db"
HISTORY_SUFFIXES = (".json", ".csv")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def launchctl(*arguments: str) -> subprocess.CompletedProcess:
    # launchctl print includes inherited environment variables: capture, never echo.
    return subprocess.run(["/bin/launchctl", *arguments], capture_output=True, text=True,
                          timeout=15, check=False)


def target(label: str = LABEL) -> str:
    return f"gui/{os.getuid()}/{label}"


def loaded(label: str = LABEL) -> bool:
    return launchctl("print", target(label)).returncode == 0


def port_open(port: int = PORT) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def backup_database(source: Path, destination: Path) -> None:
    """Consistent copy even while a writer holds the WAL (sqlite3 backup API)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    destination.chmod(0o600)


def force_dry_run(database: Path, reason: str) -> None:
    stamp = now_iso()
    with sqlite3.connect(database) as conn:
        conn.execute("""INSERT INTO settings(key,value,updated_at) VALUES('dry_run','true',?)
                        ON CONFLICT(key) DO UPDATE SET value='true', updated_at=excluded.updated_at""", (stamp,))
        conn.execute("INSERT INTO audit_logs(event, detail, payload_json, created_at) VALUES(?,?,?,?)",
                     ("dry_run", "on", json.dumps({"reason": reason}, ensure_ascii=False), stamp))


def migrate(source: Path) -> dict:
    source = source.expanduser().resolve()
    database = HOME / "data" / DB_NAME
    if database.exists():
        raise RuntimeError(f"{database} already exists; refusing to overwrite migrated state.")
    if not (source / "data" / DB_NAME).is_file():
        raise RuntimeError(f"No {DB_NAME} under {source}/data.")
    private_dir(HOME)
    private_dir(HOME / "data")
    private_dir(HOME / "config")
    backup_database(source / "data" / DB_NAME, database)
    copied = [str(database)]
    if (source / "config.yaml").is_file():
        shutil.copy2(source / "config.yaml", HOME / "config.yaml")
        copied.append(str(HOME / "config.yaml"))
    for path in sorted((source / "config").glob("*.yaml")):
        shutil.copy2(path, HOME / "config" / path.name)
        copied.append(str(HOME / "config" / path.name))
    for path in sorted((source / "data").iterdir()):
        if path.is_file() and path.suffix in HISTORY_SUFFIXES:
            shutil.copy2(path, HOME / "data" / path.name)
            copied.append(str(HOME / "data" / path.name))
    env_copied = False
    if (source / ".env").is_file():
        shutil.copyfile(source / ".env", HOME / ".env")
        (HOME / ".env").chmod(0o600)
        env_copied = True
    force_dry_run(database, "migrated into fable-trading; owner re-enables live trading")
    receipt = {"migrated_at": now_iso(), "source": str(source), "home": str(HOME),
               "dry_run_forced": True, "env_copied": env_copied, "files": copied}
    (HOME / "MIGRATED_FROM.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def retire_legacy() -> dict:
    """Unload the old agents; keep their plists, renamed, so the change is reversible."""
    suffix = ".retired-" + dt.date.today().strftime("%Y%m%d")
    result = {}
    for label in LEGACY_LABELS:
        was_loaded = loaded(label)
        if was_loaded and launchctl("bootout", target(label)).returncode:
            raise RuntimeError(f"Could not unload {label}; inspect it with launchctl.")
        plist = AGENTS / (label + ".plist")
        moved = None
        if plist.exists():
            moved = plist.with_name(plist.name + suffix)
            plist.rename(moved)
        result[label] = {"was_loaded": was_loaded, "plist_moved_to": str(moved) if moved else None}
    return result


def install() -> str:
    if not PYTHON.exists() or not PYTHON.resolve().is_file():
        raise RuntimeError(f"{PYTHON} does not resolve to an interpreter; create .venv-copier first.")
    if not (HOME / "data" / DB_NAME).is_file():
        raise RuntimeError(f"No database under {HOME}; run `migrate` first.")
    legacy = [label for label in LEGACY_LABELS if loaded(label)]
    if legacy:
        raise RuntimeError("Legacy agents still loaded (" + ", ".join(legacy) + "); run `retire-legacy` first. "
                           "The old cloudflared agent publishes port 8080 without authentication.")
    if not loaded() and port_open():
        raise RuntimeError(f"Port {PORT} is held by an unmanaged process; inspect it before starting.")
    private_dir(LOGS)
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(PYTHON), "-m", "yoyo.copier.main"],
        "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 30, "ExitTimeOut": 15,
        "StandardOutPath": str(LOGS / "service.log"), "StandardErrorPath": str(LOGS / "service-error.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                                 "COPIER_HOME": str(HOME)},
    }
    AGENTS.mkdir(parents=True, exist_ok=True)
    if PLIST.exists():
        if plistlib.loads(PLIST.read_bytes()) != config:
            raise RuntimeError("Existing copier service configuration differs; inspect before replacing.")
    else:
        PLIST.write_bytes(plistlib.dumps(config))
        PLIST.chmod(0o600)
    if not loaded() and launchctl("bootstrap", f"gui/{os.getuid()}", str(PLIST)).returncode:
        raise RuntimeError("Copier service could not be loaded; inspect the service logs.")
    return f"Copier installed: API http://127.0.0.1:{PORT}, UI http://127.0.0.1:8766/#copier"


def status() -> dict:
    summary = {"label": LABEL, "state": "unavailable", "pid": None, "runs": None, "last_exit_code": None,
               "home": str(HOME), "logs": str(LOGS), "port_open": port_open(),
               "legacy_loaded": [label for label in LEGACY_LABELS if loaded(label)]}
    result = launchctl("print", target())
    if result.returncode:
        return summary
    summary["state"] = "unknown"
    fields = [(len(m[1].expandtabs(8)), m[2], m[3]) for m in
              (re.fullmatch(r"([ \t]+)([a-z][a-z ]*) = (.*)", line) for line in result.stdout.splitlines()) if m]
    if not fields:
        return summary
    top = min(indent for indent, _, _ in fields)
    for indent, name, value in fields:
        if indent != top:
            continue
        if name == "state" and re.fullmatch(r"[a-z ]{3,20}", value):
            summary["state"] = value
        elif name in ("pid", "runs") and re.fullmatch(r"[0-9]{1,10}", value):
            summary[name] = int(value)
        elif name == "last exit code" and re.fullmatch(r"-?[0-9]{1,5}|\(never exited\)", value):
            summary["last_exit_code"] = value
    return summary


def backup() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = private_dir(HOME / "backups") / f"copier-{stamp}.db"
    backup_database(HOME / "data" / DB_NAME, destination)
    return str(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the Discord copier LaunchAgent.")
    parser.add_argument("action", choices=["migrate", "retire-legacy", "install", "status", "restart", "stop", "backup"])
    parser.add_argument("--source", type=Path, default=Path.home() / "discord-okx-copier",
                        help="old project directory for `migrate`")
    args = parser.parse_args()
    if args.action == "migrate":
        print(json.dumps(migrate(args.source), ensure_ascii=False, indent=2))
    elif args.action == "retire-legacy":
        print(json.dumps(retire_legacy(), ensure_ascii=False, indent=2))
    elif args.action == "install":
        print(install())
    elif args.action == "status":
        summary = status()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["state"] == "unavailable":
            raise SystemExit(1)
    elif args.action == "restart":
        if launchctl("kickstart", "-k", target()).returncode:
            raise SystemExit("Copier service is not loaded; run install.")
    elif args.action == "stop":
        if launchctl("bootout", target()).returncode:
            raise SystemExit("Copier service was not loaded.")
        print("Copier stopped; run install to start it again.")
    else:
        print(backup())


if __name__ == "__main__":
    main()
