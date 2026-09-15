"""One-time authorized recent-window replay of frozen ChartArt BBRSi rules.

This runner has no parameter-search path.  It checks the owner's authorization,
the committed frozen builders, and the one-use consumption claim before it
opens a CSV.  A failed run intentionally leaves its started record and claim
in place rather than silently consuming the same final-review window again.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from yoyo.data.release_eth_prefix import validate_ohlcv
from yoyo.evaluation.chartart_bbrsi import features, replay
from yoyo.evaluation.chartart_bbrsi_study import matched_controls, statistics
from yoyo.evaluation.chartart_martingale_account import simulate_account


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-chartart-bbrsi-martingale-20260915-v1"
ENGINE_FILES = (
    "yoyo/evaluation/chartart_bbrsi.py",
    "yoyo/evaluation/chartart_bbrsi_study.py",
    "yoyo/evaluation/chartart_martingale_account.py",
    "src/data/fetch_okx.py",
)
RUNNER_FILES = (
    "yoyo/evaluation/chartart_bbrsi_recent.py",
    "tests/evaluation/test_chartart_bbrsi_recent.py",
)
# This validator affects source admissibility but is outside the owner's
# four engine-hash fields, so it is frozen against HEAD separately.
AUXILIARY_FILES = ("yoyo/data/release_eth_prefix.py",)
EXPECTED_STARTS = {
    1: "2026-09-06T16:00:00Z",
    3: "2026-08-23T16:00:00Z",
    15: "2026-05-31T16:00:00Z",
}
EXPECTED_END = "2026-09-15T14:30:00Z"


def sha256(path: Path) -> str:
    """Return the byte-level identity of one frozen input or output."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def _head_bytes(root: Path, relative: str) -> bytes:
    """Read a tracked HEAD blob so an uncommitted builder cannot score data."""
    try:
        return subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=root)
    except subprocess.CalledProcessError as exc:
        raise ValueError("frozen dependency absent from HEAD: " + relative) from exc


def _require_config(cfg: Dict[str, Any]) -> None:
    """Keep this final runner fixed to the authorized strategy and windows."""
    if cfg.get("timeframes") != [1, 3, 15] or cfg.get("end") != EXPECTED_END:
        raise ValueError("recent config does not match the frozen final windows")
    if cfg.get("multipliers") != [1, 2] or cfg.get("entry_margin_leverages") != [1, 10]:
        raise ValueError("recent config does not match frozen account arms")
    if (cfg.get("initial_cash"), cfg.get("base_notional"), cfg.get("cost"), cfg.get("seed"), cfg.get("control_draws")) != (1000, 100, .002, 915611, 20):
        raise ValueError("recent config does not match frozen account settings")
    if cfg.get("warmup_bars") != 1500 or cfg.get("minimum_warmup_bars") != 250:
        raise ValueError("recent config has an invalid warmup contract")
    windows = cfg.get("windows", {})
    for minutes, start in EXPECTED_STARTS.items():
        records = windows.get(str(minutes))
        if records != [{"name": "screenshot", "start": start}]:
            raise ValueError("recent config does not match the authorized screenshot window")


def validate_authorization(*, root: Path = ROOT, experiment: Path = EXP,
                           head_reader: Optional[Callable[[Path, str], bytes]] = None
                           ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    """Validate authorization and all frozen code before any source path is used."""
    root, experiment = Path(root), Path(experiment)
    config_path, auth_path = experiment / "recent_config.json", experiment / "authorization_recent.json"
    cfg_bytes, auth_bytes = config_path.read_bytes(), auth_path.read_bytes()
    cfg, auth = json.loads(cfg_bytes), json.loads(auth_bytes)
    _require_config(cfg)
    if auth.get("approved") is not True:
        raise ValueError("recent holdout authorization is not approved")
    if auth.get("config_sha256") != hashlib.sha256(cfg_bytes).hexdigest():
        raise ValueError("recent authorization config drift")
    if auth.get("holdout_consumption_number") != 1 or not isinstance(auth.get("authorization_id"), str):
        raise ValueError("recent authorization has no valid first-consumption identity")
    expected = auth.get("engine_sha256")
    if not isinstance(expected, dict) or set(expected) != set(ENGINE_FILES):
        raise ValueError("recent authorization engine hash set drift")
    reader = head_reader or _head_bytes
    receipt_path = experiment / "source_receipt.json"
    dependencies = (*ENGINE_FILES, *RUNNER_FILES, *AUXILIARY_FILES,
                    str(config_path.relative_to(root)), str(auth_path.relative_to(root)),
                    str(receipt_path.relative_to(root)))
    hashes: Dict[str, str] = {}
    for relative in dependencies:
        path = root / relative
        if reader(root, relative) != path.read_bytes():
            raise ValueError("uncommitted frozen dependency: " + relative)
        hashes[relative] = sha256(path)
    for relative in ENGINE_FILES:
        if expected[relative] != hashes[relative]:
            raise ValueError("recent authorization engine drift: " + relative)
    return cfg, auth, hashes


def begin_run(*, root: Path = ROOT, experiment: Path = EXP,
              head_reader: Optional[Callable[[Path, str], bytes]] = None,
              commit_reader: Optional[Callable[[Path], str]] = None
              ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str], Path]:
    """Reserve the authorized exposure once, before parsing a source CSV."""
    cfg, auth, hashes = validate_authorization(root=root, experiment=experiment, head_reader=head_reader)
    experiment = Path(experiment)
    out = experiment / "recent_results"
    claim = experiment / "holdout_consumptions" / (auth["authorization_id"] + ".json")
    if claim.exists():
        raise ValueError("recent authorization consumption claim already exists")
    if out.exists():
        raise ValueError("recent results already exist; no automatic resume is permitted")
    out.mkdir()
    commit = (commit_reader or (lambda value: subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=value, text=True).strip()))(Path(root))
    _save(out / "started.json", {
        "status": "started", "authorization_id": auth["authorization_id"],
        "holdout_consumption_number": 1, "config_sha256": sha256(experiment / "recent_config.json"),
        "config": cfg, "builders": hashes, "source_commit": commit,
    })
    claim.parent.mkdir(exist_ok=True)
    with claim.open("x") as stream:
        json.dump({
            "authorization_id": auth["authorization_id"], "holdout_consumption_number": 1,
            "config_sha256": sha256(experiment / "recent_config.json"), "status": "claimed_before_source_read",
        }, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return cfg, auth, hashes, out


def _read_recent(path: Path, minutes: int, start: str, end: str, warmup_bars: int,
                 minimum_warmup_bars: int) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Read only the requested warmup-to-end prefix; never fill a missing bar."""
    end_stamp, start_stamp = pd.Timestamp(end), pd.Timestamp(start)
    warmup_start = start_stamp - pd.Timedelta(minutes=minutes * warmup_bars)
    before = path.stat()
    digest, rows = hashlib.sha256(), []
    with path.open("rb") as stream:
        header = stream.readline()
        names = next(csv.reader([header.decode("utf-8-sig").strip()]))
        if names[0] != "ts" or not {"open", "high", "low", "close", "volume"} <= set(names):
            raise ValueError("unexpected recent OKX source header")
        digest.update(header)
        while True:
            token = bytearray()
            while True:
                char = stream.read(1)
                if char in (b"", b","):
                    break
                if char in (b"\r", b"\n") or len(token) >= 24:
                    raise ValueError("invalid recent source timestamp")
                token.extend(char)
            if not token and char == b"":
                break
            if char != b",":
                raise ValueError("truncated recent source timestamp")
            stamp = pd.Timestamp(int(token), unit="ms", tz="UTC")
            if stamp >= end_stamp:
                break
            suffix = stream.readline()
            if stamp < warmup_start:
                continue
            raw = bytes(token) + b"," + suffix
            fields = next(csv.reader([raw.decode().strip()]))
            if len(fields) != len(names):
                raise ValueError("recent source row width changed")
            value = dict(zip(names, fields))
            rows.append((stamp, *[float(value[key]) for key in ("open", "high", "low", "close", "volume")]))
            digest.update(raw)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("recent source changed during read")
    raw_frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    if raw_frame.empty:
        raise ValueError("recent source has no approved rows")
    validate_ohlcv(raw_frame, minutes)
    if raw_frame.open_time.iloc[-1] + pd.Timedelta(minutes=minutes) != end_stamp:
        raise ValueError("recent source does not end on a complete authorized bar")
    warmup = int((raw_frame.open_time < start_stamp).sum())
    if warmup < minimum_warmup_bars:
        raise ValueError("recent source has insufficient complete warmup bars")
    return raw_frame.set_index("open_time"), {
        "path": str(path), "consumed_source_sha256": digest.hexdigest(), "rows": len(raw_frame),
        "first_open": str(raw_frame.open_time.iloc[0]),
        "last_close": str(raw_frame.open_time.iloc[-1] + pd.Timedelta(minutes=minutes)),
        "warmup_bars": warmup, "gaps": 0, "duplicates": 0,
    }


def _source_path(experiment: Path, cfg: Dict[str, Any], minutes: int) -> Path:
    """Require exactly one official native-timeframe file in its isolated folder."""
    configured = Path(cfg["source_directories"][str(minutes)])
    folder = configured if configured.is_absolute() else experiment.parents[2] / configured
    expected = experiment / "recent_source" / ("%dm" % minutes)
    if folder.resolve() != expected.resolve():
        raise ValueError("recent config source directory drift")
    candidates = sorted(folder.glob("okx_ETH_USDT_SWAP_%dm_*.csv" % minutes))
    if len(candidates) != 1:
        raise ValueError("expected exactly one recent %dm OKX source" % minutes)
    return candidates[0]


def _holm(records: List[Dict[str, Any]]) -> None:
    """Attach the single three-window Holm family correction to each result."""
    valid = [(i, item["metrics"]["p_value"]) for i, item in enumerate(records)
             if item["metrics"].get("p_value") is not None]
    ceiling = 0.0
    for rank, (index, value) in enumerate(sorted(valid, key=lambda item: item[1])):
        ceiling = max(ceiling, min(1.0, value * (len(valid) - rank)))
        records[index]["metrics"]["p_holm"] = ceiling


def run(*, root: Path = ROOT, experiment: Path = EXP) -> Dict[str, Any]:
    """Execute the one authorized review; a partial result must not be rerun automatically."""
    cfg, auth, hashes, out = begin_run(root=root, experiment=experiment)
    experiment = Path(experiment)
    summaries: List[Dict[str, Any]] = []
    inputs: List[Dict[str, Any]] = []
    for minutes in cfg["timeframes"]:
        window = cfg["windows"][str(minutes)][0]
        source = _source_path(experiment, cfg, minutes)
        frame, receipt = _read_recent(source, minutes, window["start"], cfg["end"],
                                      cfg["warmup_bars"], cfg["minimum_warmup_bars"])
        frame = features(frame)
        inputs.append({"minutes": minutes, **receipt})
        folder = out / ("%dm_%s" % (minutes, window["name"]))
        folder.mkdir()
        trades = replay(frame, window["start"], cfg["end"], minutes)
        trades.to_csv(folder / "unit_trades.csv", index=False)
        controls = matched_controls(frame, trades, window["start"], cfg["end"],
                                    cfg["seed"] + minutes, cfg["control_draws"])
        controls.to_csv(folder / "matched_controls.csv.gz", index=False)
        accounts = []
        for multiplier in cfg["multipliers"]:
            for leverage in cfg["entry_margin_leverages"]:
                account, sized, curve = simulate_account(
                    frame, trades, multiplier=multiplier, initial_cash=cfg["initial_cash"],
                    base_notional=cfg["base_notional"], max_leverage=leverage, cost=cfg["cost"],
                )
                arm = "m%g_l%g" % (multiplier, leverage)
                sized.to_csv(folder / (arm + "_trades.csv"), index=False)
                curve.to_csv(folder / (arm + "_curve.csv.gz"), index=False)
                accounts.append({"multiplier": multiplier, "margin_leverage": leverage, **account})
        record = {
            "minutes": minutes, "window": window["name"], "start": window["start"], "end": cfg["end"],
            "evaluated_bars": int((frame.index >= pd.Timestamp(window["start"])).sum()),
            "long_signals": int(frame.loc[frame.index >= pd.Timestamp(window["start"]), "long_signal"].sum()),
            "short_signals": int(frame.loc[frame.index >= pd.Timestamp(window["start"]), "short_signal"].sum()),
            "metrics": statistics(trades, controls, cfg["seed"] + minutes), "accounts": accounts,
            "holdout_consumption_number": 1,
        }
        _save(folder / "summary.json", record)
        summaries.append(record)
    _holm(summaries)
    _save(out / "inputs.json", inputs)
    _save(out / "summary.json", summaries)
    files = {str(path.relative_to(out)): sha256(path) for path in out.rglob("*")
             if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}}
    manifest = {"authorization_id": auth["authorization_id"], "holdout_consumed": True,
                "holdout_consumption_number": 1, "config_sha256": sha256(experiment / "recent_config.json"),
                "builders": hashes, "files": files, "windows": len(summaries)}
    _save(out / "manifest.json", manifest)
    (out / "manifest.sha256").write_text(sha256(out / "manifest.json") + "\n")
    return manifest


if __name__ == "__main__":
    run()
