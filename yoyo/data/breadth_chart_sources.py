"""Read bounded windows from the registered Binance breadth research sources."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

OHLC = ("open", "high", "low", "close")


def source_entries(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    raw = receipt.get("local_sources", [])
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    by_identity: dict[tuple[int, int] | str, dict[str, Any]] = {}
    seen_candidate_sets: set[tuple[str, ...]] = set()
    for item in sorted((x for x in raw if isinstance(x, dict)),
                       key=lambda x: (int(x.get("priority") or 999), str(x.get("kind", "")))):
        if not (item.get("resolved_path") or item.get("path") or item.get("aliases")):
            continue
        candidate_values = [item.get("resolved_path"), item.get("path"), *(item.get("aliases") or [])]
        candidate_set = tuple(sorted({str(value) for value in candidate_values if value}))
        if candidate_set and candidate_set in seen_candidate_sets:
            continue
        if candidate_set:
            seen_candidate_sets.add(candidate_set)
        candidates: list[Path] = []
        candidate_names: set[str] = set()
        identity: tuple[int, int] | str | None = None
        for candidate in candidate_values:
            if not candidate:
                continue
            path = Path(str(candidate)).expanduser()
            if path.is_file():
                try:
                    resolved = path.resolve(strict=True)
                    stat = resolved.stat()
                    key: tuple[int, int] | str = (stat.st_dev, stat.st_ino) if stat.st_ino else str(resolved)
                except OSError:
                    resolved = path
                    key = str(path)
                if str(path) not in candidate_names:
                    candidates.append(path)
                    candidate_names.add(str(path))
                if identity is None:
                    identity = key
        if identity is None:
            identity = str(item.get("resolved_path") or item.get("path") or "")
        if identity in by_identity:
            existing = by_identity[identity]
            for candidate in candidates:
                if str(candidate) not in {str(path) for path in existing["candidates"]}:
                    existing["candidates"].append(candidate)
            existing["kinds"] = sorted(set(existing["kinds"] + [str(item.get("kind", "unknown"))]))
            continue
        chosen = str(candidates[0]) if candidates else None
        resolved = candidates[0].resolve(strict=False) if candidates else None
        entry = {"meta": item, "resolved": resolved, "chosen": chosen,
                 "candidates": candidates, "kinds": [str(item.get("kind", "unknown"))]}
        entries.append(entry)
        by_identity[identity] = entry
    return entries


def _row_ts(line: bytes, path: Path) -> int:
    try:
        return int(line.split(b",", 1)[0])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"invalid sorted Binance CSV timestamp in {path.name}") from exc


def _line_start_at_or_before(handle: Any, offset: int, header_end: int) -> int:
    cursor = offset
    while cursor > header_end:
        block_start = max(header_end, cursor - 4096)
        handle.seek(block_start)
        block = handle.read(cursor - block_start)
        newline = block.rfind(b"\n")
        if newline >= 0:
            return block_start + newline + 1
        cursor = block_start
    return header_end


def _lower_bound_csv_ts(handle: Any, target: int, header_end: int, file_size: int, path: Path) -> int:
    """Find the first row at/after target in a sorted plain CSV without scanning older rows."""
    low, high = header_end, file_size
    while low < high:
        middle = (low + high) // 2
        position = _line_start_at_or_before(handle, middle, header_end)
        handle.seek(position)
        line = handle.readline()
        if not line:
            high = position
            break
        if _row_ts(line, path) < target:
            low = handle.tell()
        else:
            high = position
    return low


def read_plain_csv_windows(path: Path, windows: list[tuple[int, int]], columns=OHLC) -> Any:
    """Use binary timestamp search for the project's sorted, uncompressed OHLC series."""
    import pandas as pd

    file_size = path.stat().st_size
    lines: list[bytes] = []
    with path.open("rb") as handle:
        header = handle.readline()
        header_columns = header.decode("utf-8-sig").rstrip("\r\n").split(",")
        if not header_columns or header_columns[0] != "ts":
            # Older or alternate layouts remain correct through the streaming reader.
            return read_streaming_csv_windows(path, windows, columns)
        header_end = handle.tell()
        for start, end in windows:
            position = _lower_bound_csv_ts(handle, start, header_end, file_size, path)
            handle.seek(position)
            while True:
                line = handle.readline()
                if not line:
                    break
                stamp = _row_ts(line, path)
                if stamp >= end:
                    break
                if stamp >= start:
                    lines.append(line)
    if not lines:
        return pd.DataFrame(columns=["ts", *columns])
    payload = header + b"".join(lines)
    return pd.read_csv(io.BytesIO(payload), usecols=lambda name: name in {"ts", *columns},
                       float_precision="round_trip")


def read_streaming_csv_windows(path: Path, windows: list[tuple[int, int]], columns=OHLC) -> Any:
    import numpy as np
    import pandas as pd

    frames = []
    for chunk in pd.read_csv(path, usecols=lambda name: name in {"ts", *columns}, chunksize=100_000,
                             float_precision="round_trip"):
        if "ts" not in chunk or any(name not in chunk for name in OHLC):
            raise ValueError(f"OHLC 5m source lacks required columns: {path.name}")
        ts = pd.to_numeric(chunk["ts"], errors="coerce").to_numpy(dtype="float64")
        finite = np.isfinite(ts)
        wanted = np.zeros(len(chunk), dtype=bool)
        for start, end in windows:
            wanted |= finite & (ts >= start) & (ts < end)
        if wanted.any():
            selected = chunk.loc[wanted].copy()
            selected["ts"] = pd.to_numeric(selected["ts"], errors="raise").astype("int64")
            for column in selected.columns.difference(["ts"]):
                selected[column] = pd.to_numeric(selected[column], errors="raise")
            frames.append(selected)
    if not frames:
        return pd.DataFrame(columns=["ts", *columns])
    return pd.concat(frames, ignore_index=True)


def read_source_windows(path: Path, windows: list[tuple[int, int]], columns=OHLC) -> Any:
    if path.suffix.lower() == ".gz":
        return read_streaming_csv_windows(path, windows, columns)
    return read_plain_csv_windows(path, windows, columns)
