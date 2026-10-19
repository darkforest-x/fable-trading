"""Explicit, verified external dataset locations for the local catalog.

Owner-authorized storage migration (2026-10-16) preserves logical dataset paths.
Unregistered external caches remain excluded. A registered location must match
its mount identity and declared verification manifest. The owner explicitly
waived content hashing for task 52, selecting size/time/access checks; legacy
SHA-256 mode remains available when explicitly registered or selected by default.
"""
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import plistlib
import subprocess


def registrations(root):
    root = Path(root).resolve()
    config = root / "data/crypto/_catalog/external_locations.json"
    if not config.exists():
        return []
    payload = json.loads(config.read_text())
    if payload.get("version") != 1:
        raise ValueError("不支持的外接存储登记版本。")
    records = []
    for source, value in payload["datasets"].items():
        relative = Path(source)
        target, mount = Path(value["target"]), Path(value["mount"])
        manifest = (root / value["manifest"]).resolve()
        permitted = ((relative.parts[:2] == ("data", "crypto") and len(relative.parts) == 4
                      and relative.parts[2] in ("market", "research", "archives", "vision", "features"))
                     or (relative.parts[:2] == ("data", "research") and len(relative.parts) == 3)
                     or (relative.parts[:1] == ("data",) and len(relative.parts) == 2
                         and relative.name.startswith(("altcoin_", "imacd_", "genuine_flow", "ma206", "eth_micro")))
                     or (relative.parts[:1] == ("datasets",) and 2 <= len(relative.parts) <= 7)
                     or (relative.parts[:2] == ("archive", "consolidated")
                         and len(relative.parts) == 5 and relative.parts[3] in ("data", "datasets"))
                     or (relative.parts[:2] == ("experiments", "active") and len(relative.parts) == 4
                         and (relative.name in ("inputs", "sources", "source_data", "data", "data_tradable")
                              or relative.name.startswith(("inputs_", "sources_"))))
                     or (relative.parts[:2] == ("analysis", "output") and len(relative.parts) == 4
                         and relative.name in ("kline_snapshot", "cache", "klines")))
        if (not permitted
                or any(part.startswith(".") for part in relative.parts)
                or not target.is_absolute() or not mount.is_absolute()
                or target == mount or not target.is_relative_to(mount)
                or target.resolve() != target or mount.resolve() != mount
                or not manifest.is_relative_to(root / "data/_storage_migrations")
                or not isinstance(value.get("partition_uuid"), str)):
            raise ValueError("无效的外接存储登记路径。")
        records.append(dict(value, source=source, target=target, mount=mount, manifest=manifest))
    return records


def descendant_locations(path, root, records=None):
    """Find explicitly registered children of a dataset that stays partly local."""
    root = Path(root).resolve()
    parent = Path(path).resolve()
    records = registrations(root) if records is None else records
    # Compare lexical entry paths: resolving the child would leave the dataset
    # and erase the relationship between a local root and its migrated images.
    return [item for item in records
            if root / item['source'] != parent and (root / item['source']).is_relative_to(parent)]


def registered_location(path, root, records=None):
    root = Path(root).resolve()
    target = Path(path).resolve()
    for item in registrations(root) if records is None else records:
        if (target.is_relative_to(item["target"])
                and (root / item["source"]).resolve() == item["target"]):
            return item
    return None


def validate_volume(item):
    # Native metadata queries can fail transiently during large NTFS copies.
    # Retry the query once, but never reuse a cached identity or retry a mismatch.
    for attempt in range(2):
        if not os.path.ismount(item["mount"]):
            raise ValueError("外接数据盘未连接，请连接已登记的硬盘后重试。")
        try:
            info = plistlib.loads(subprocess.check_output(
                ["diskutil", "info", "-plist", str(item["mount"])], timeout=10,
                stderr=subprocess.DEVNULL))
            break
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            if attempt:
                raise ValueError("无法核实外接数据盘身份。") from error
            logging.getLogger(__name__).warning(
                "External volume identity query failed (%s); retrying once",
                type(error).__name__)
    if info.get("DiskUUID") != item["partition_uuid"]:
        raise ValueError("外接数据盘身份与登记不一致。")


@lru_cache(maxsize=8)
def _manifest_entries(path, size, mtime_ns):
    result = {}
    with Path(path).open() as stream:
        for line in stream:
            row = json.loads(line)
            relative = Path(row["path"])
            if relative.is_absolute() or ".." in relative.parts or str(relative) in result:
                raise ValueError("无效的外接数据校验清单。")
            result[str(relative)] = row
    return result


def expected_entry(item, path):
    target = Path(path).resolve()
    if not target.is_relative_to(item["target"]):
        return None
    info = item["manifest"].stat()
    return _manifest_entries(str(item["manifest"]), info.st_size, info.st_mtime_ns).get(
        str(target.relative_to(item["target"])))


def verify_file(item, path):
    validate_volume(item)
    if not Path(path).is_file():
        raise KeyError(str(path))
    expected = expected_entry(item, path)
    if not expected:
        raise KeyError(str(path))
    try:
        if item.get("verification", "sha256") == "metadata":
            # Owner explicitly opted out of hashing for task 52. Keep volume,
            # allowlist, size and second-precision mtime checks, plus real access.
            info = Path(path).stat()
            if (info.st_size != expected["bytes"] or
                    info.st_mtime_ns // 1_000_000_000 != expected["mtime_ns"] // 1_000_000_000):
                raise ValueError("外接文件大小或修改时间已变动，请核对迁移记录。")
            with Path(path).open("rb") as stream:
                stream.read(1)
            return
        if item.get("verification", "sha256") != "sha256":
            raise ValueError("不支持的外接数据验收方式。")
        value = hashlib.sha256()
        with Path(path).open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                value.update(chunk)
    except PermissionError as error:
        raise ValueError("macOS 尚未授权后台服务读取移动硬盘，请在系统设置中授权该 Python 的磁盘访问权限。") from error
    if value.hexdigest() != expected["sha256"]:
        raise ValueError("外接文件已变动，与迁移校验清单不符。")


def verify_access(item):
    """Fail before replacing an index if this process lacks volume access."""
    info = item["manifest"].stat()
    entries = _manifest_entries(str(item["manifest"]), info.st_size, info.st_mtime_ns)
    if not entries:
        raise ValueError("外接数据校验清单为空。")
    verify_file(item, item["target"] / next(iter(entries)))
