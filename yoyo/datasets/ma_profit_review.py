"""Build a six-event, non-training visual review of retained local-15m outcomes.

The causal A/B views are byte-for-byte the output of ``event_assets`` and end
at the five-bar confirmation.  Separate future-review charts may show the
label horizon solely to verify the recorded TP/SL/TIMEOUT outcome.  No YOLO
labels or training inputs are produced here.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix, sha256_file
from yoyo.datasets.ma_profit_dataset import ROOT, add_hl2_mas, event_assets


class ProfitReviewError(RuntimeError):
    """Raised when frozen outcome review controls cannot be reproduced."""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProfitReviewError(f"JSON object required: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ProfitReviewError(f"timezone required: {value!r}")
    return stamp.tz_convert("UTC")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ProfitReviewError(f"path outside repository: {path}") from exc


def _committed(paths: list[Path]) -> str:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ProfitReviewError("review builder must run on main")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *[_relative(path) for path in paths]], cwd=ROOT, text=True).strip()
    if dirty:
        raise ProfitReviewError("commit builder and frozen review selection before rendering:\n" + dirty)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def freeze_selection(outcomes_path: Path, selection_path: Path) -> dict[str, Any]:
    """Freeze up to three SHA-ordered train winners per direction without outcome ranking."""

    outcomes_path, selection_path = outcomes_path.resolve(), selection_path.resolve()
    if selection_path.exists():
        raise FileExistsError(f"refusing to replace frozen review selection: {selection_path}")
    rows = _jsonl(outcomes_path)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        profit = row.get("profit", {})
        if row.get("split") == "train" and bool(profit.get("retained", False)) and row.get("direction") in {"LONG", "SHORT"}:
            grouped[str(row["direction"])].append(row)
    chosen: list[dict[str, Any]] = []
    for direction in ("LONG", "SHORT"):
        chosen.extend(sorted(grouped[direction], key=lambda row: hashlib.sha256(str(row["event_id"]).encode()).hexdigest())[:3])
    chosen.sort(key=lambda row: (str(row["direction"]), hashlib.sha256(str(row["event_id"]).encode()).hexdigest()))
    control = {
        "schema_version": 1,
        "outcomes_path": _relative(outcomes_path),
        "outcomes_sha256": sha256_file(outcomes_path),
        "selection_rule": "train && profit.retained; stable SHA256(event_id) order; max 3 per direction; no outcome-magnitude ranking",
        "requested_max_per_direction": 3,
        "selected_count": len(chosen),
        "direction_counts": {direction: sum(row["direction"] == direction for row in chosen) for direction in ("LONG", "SHORT")},
        "training_eligible": False,
        "production_eligible": False,
        "events": [{"sample_order": index, "event_id": row["event_id"], "direction": row["direction"]} for index, row in enumerate(chosen, 1)],
    }
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(selection_path, control)
    return control


def _selected_rows(selection: Mapping[str, Any], outcomes_path: Path) -> list[dict[str, Any]]:
    if selection.get("outcomes_path") != _relative(outcomes_path) or selection.get("outcomes_sha256") != sha256_file(outcomes_path):
        raise ProfitReviewError("outcomes selection SHA/path drift")
    by_id = {str(row.get("event_id")): row for row in _jsonl(outcomes_path)}
    selected = selection.get("events")
    if not isinstance(selected, list) or not selected or len(selected) > 6:
        raise ProfitReviewError("selection must contain one to six events")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        event_id = str(item.get("event_id", ""))
        row = by_id.get(event_id)
        if event_id in seen or row is None or row.get("split") != "train" or not row.get("profit", {}).get("retained"):
            raise ProfitReviewError(f"invalid frozen review event: {event_id}")
        if item.get("direction") != row.get("direction"):
            raise ProfitReviewError(f"selection direction drift: {event_id}")
        seen.add(event_id); result.append(row)
    return result


def _source_path(row: Mapping[str, Any]) -> Path:
    path = Path(str(row["source_path"]))
    return path if path.is_absolute() else ROOT / path


def _load_until(row: Mapping[str, Any], end_exclusive: pd.Timestamp) -> pd.DataFrame:
    source = _source_path(row)
    expected_sha = str(row.get("source_sha256", ""))
    if expected_sha and sha256_file(source) != expected_sha:
        raise ProfitReviewError(f"source SHA drift: {row['event_id']}")
    frame, _ = read_preholdout_prefix(source, end_exclusive=end_exclusive, bar_minutes=int(row["bar_minutes"]))
    return frame


def _future_chart(frame: pd.DataFrame, row: Mapping[str, Any], output: Path) -> None:
    """Render retrospective label evidence; this PNG is physically outside causal inputs."""

    profit = dict(row["profit"])
    times = pd.to_datetime(frame["open_time"], utc=True)
    end_time = _utc(profit["label_window_end_utc"])
    core_end = _utc(row["core_end_time"])
    bar_delta = pd.Timedelta(minutes=int(row["bar_minutes"]))
    end_i = int(np.flatnonzero(times == end_time - pd.Timedelta(minutes=int(row["bar_minutes"])))[0])
    core_end_i = int(np.flatnonzero(times == core_end)[0])
    decision_i = core_end_i + 5
    future = frame.iloc[decision_i + 1:end_i + 1]
    if len(future) != 48 or times.iloc[end_i] + bar_delta != end_time or not (pd.to_datetime(future["open_time"], utc=True).diff().dropna() == bar_delta).all():
        raise ProfitReviewError(f"incomplete or gapped 12-hour future review: {row['event_id']}")
    start_i = max(0, core_end_i - 32)
    visible = add_hl2_mas(frame.iloc[:end_i + 1]).iloc[start_i:end_i + 1].reset_index(drop=True)
    xs = np.arange(len(visible))
    fig, axis = plt.subplots(figsize=(16, 8), dpi=120)
    for x, candle in visible.iterrows():
        color = "#2979ff" if candle.close >= candle.open else "#7b2cbf"
        axis.vlines(x, candle.low, candle.high, color="#555555", linewidth=.7)
        axis.bar(x, max(abs(candle.close - candle.open), 1e-12), bottom=min(candle.open, candle.close), width=.62, color=color)
    for column, color in (("sma20", "#666666"), ("ema20", "#4f81bd"), ("sma60", "#777777"), ("ema60", "#6a5acd"), ("sma120", "#999999"), ("ema120", "#9b59b6")):
        axis.plot(xs, visible[column], color=color, linewidth=.9, label=column.upper())
    core_local = core_end_i - start_i
    confirm_local = core_local + 5
    entry_time = _utc(profit["entry_open_time_utc"])
    entry_local = int(np.flatnonzero(pd.to_datetime(visible["open_time"], utc=True) == entry_time)[0])
    axis.axvspan(core_local - int(row["core_bars"]) + 1 - .5, core_local + .5, color="#f6c453", alpha=.24, label="core")
    axis.axvline(confirm_local + .5, color="#1565c0", linestyle="--", label="confirmation close")
    axis.axvline(entry_local, color="#2e7d32", linestyle=":", label="next-bar entry")
    for value, label, color in ((profit["entry_price"], "entry", "#1565c0"), (profit["stop_price"], "SL", "#c62828"), (profit["target_price"], "3R TP", "#2e7d32"), (profit["exit_price"], f"exit {profit['outcome']}", "#6d4c41")):
        axis.axhline(float(value), color=color, linewidth=1, linestyle="--", label=label)
    exit_time = _utc(profit["exit_time_utc"])
    close_times = pd.to_datetime(visible["open_time"], utc=True) + pd.Timedelta(minutes=int(row["bar_minutes"]))
    exit_clock = pd.to_datetime(visible["open_time"], utc=True) if "_gap_" in str(profit.get("reason", "")) else close_times
    exit_matches = np.flatnonzero(exit_clock == exit_time)
    if len(exit_matches):
        axis.scatter([int(exit_matches[0])], [float(profit["exit_price"])], color="#6d4c41", marker="x", s=54, zorder=6, label="actual exit")
    entry_local = entry_time.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M UTC+8")
    axis.set_title(f"Historical label review · {row['symbol']} · {entry_local} · {row['direction']} · not a training input")
    axis.set_xlabel("15m bars; future shown only for recorded-label verification")
    axis.set_ylabel("price")
    axis.legend(ncol=4, fontsize=8, loc="upper left")
    fig.tight_layout(); fig.savefig(output, dpi=120); plt.close(fig)


def _contact_sheet(paths: list[Path], output: Path) -> None:
    thumbs = []
    for path in paths:
        image = Image.open(path).convert("RGB"); image.thumbnail((470, 273)); thumbs.append((f"{path.parent.name.split('_', 1)[0]} · {path.stem}", image.copy()))
    canvas = Image.new("RGB", (1440, max(1, ((len(thumbs) + 2) // 3) * 310)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (label, image) in enumerate(thumbs):
        x, y = (index % 3) * 480, (index // 3) * 310
        canvas.paste(image, (x, y + 22)); draw.text((x + 4, y + 4), label, fill="black")
    canvas.save(output)


def build(selection_path: Path, out: Path) -> dict[str, Any]:
    """Materialize causal crops and separate label-horizon review figures after commit."""

    selection_path, out = selection_path.resolve(), out.resolve()
    selection = _json(selection_path)
    outcomes_path = ROOT / str(selection["outcomes_path"])
    if out.exists():
        raise FileExistsError(f"refusing to overwrite review output: {out}")
    commit = _committed([Path(__file__), selection_path])
    rows = _selected_rows(selection, outcomes_path)
    out.mkdir(parents=True); causal_dir, future_dir = out / "causal_inputs", out / "future_review"
    causal_dir.mkdir(); future_dir.mkdir()
    manifest: list[dict[str, Any]] = []; sheet_paths: list[Path] = []
    for order, row in enumerate(rows, 1):
        profit = dict(row["profit"]); source = _source_path(row); source_sha = sha256_file(source)
        decision = _utc(profit["decision_close_time_utc"])
        causal_frame = _load_until(row, decision)
        assets = event_assets(causal_frame, row)
        if [asset["variant"] for asset in assets] != ["A", "B1", "B2"]:
            raise ProfitReviewError(f"causal view contract drift: {row['event_id']}")
        event_dir = causal_dir / f"{order:02d}_{row['event_id']}"; event_dir.mkdir()
        for asset in assets:
            target = event_dir / f"{asset['variant']}.png"; target.write_bytes(asset["png"]); sheet_paths.append(target)
            manifest.append({"kind": "causal_input", "event_id": row["event_id"], "sample_order": order, "variant": asset["variant"], "path": target.relative_to(out).as_posix(), "sha256": sha256_file(target), "source_sha256": source_sha, "visible_end_close_time_utc": asset["visible"]["visible_end_close_time_utc"], "decision_at_utc": profit["decision_close_time_utc"], "training_eligible": False})
        future_frame = _load_until(row, _utc(profit["label_window_end_utc"]))
        target = future_dir / f"{order:02d}_{row['event_id']}.png"; _future_chart(future_frame, row, target)
        manifest.append({"kind": "future_label_review", "event_id": row["event_id"], "sample_order": order, "path": target.relative_to(out).as_posix(), "sha256": sha256_file(target), "source_sha256": source_sha, "label_window_end_utc": profit["label_window_end_utc"], "training_eligible": False})
    _write_jsonl(out / "preview_manifest.jsonl", manifest)
    _contact_sheet(sheet_paths, out / "contact_sheet.png")
    cards = "".join(f'<li>{html.escape(item["event_id"])} · <a href="{html.escape(item["path"])}">{html.escape(item["kind"])}</a></li>' for item in manifest)
    (out / "index.html").write_text(f"<meta charset=utf-8><title>MA profit review</title><h1>Historical review only</h1><p>Not a dataset, not training, not a win-rate claim.</p><ul>{cards}</ul>", encoding="utf-8")
    summary = {"builder_commit": commit, "selection_sha256": sha256_file(selection_path), "outcomes_sha256": selection["outcomes_sha256"], "selected_event_ids": [row["event_id"] for row in rows], "images": len(manifest), "training_eligible": False, "production_eligible": False}
    _write_json(out / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze-selection"); freeze.add_argument("--outcomes", type=Path, required=True); freeze.add_argument("--selection", type=Path, required=True)
    render = commands.add_parser("build"); render.add_argument("--selection", type=Path, required=True); render.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = freeze_selection(args.outcomes, args.selection) if args.command == "freeze-selection" else build(args.selection, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
