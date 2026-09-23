"""Local crypto dataset inventory and bounded, offline OHLCV reuse.

SQLite contains metadata, never candle arrays. Indexing runs outside the monitor
pulse and publishes atomically. Dataset versions, exchange and bar identities
stay separate; ambiguous overlaps fail instead of silently choosing a winner.
Read-only SQLite URI: https://www.sqlite.org/uri.html. CSV reads use bounded
pandas chunks; only requested historical rows enter the returned frame.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = {"market": "行情 K 线", "vision": "YOLO 图像与标注", "features": "因子与特征", "archives": "历史归档", "research": "研究输入"}
EXCLUDED = {".git", "__pycache__", ".venv", "node_modules", ".DS_Store"}
TIME_COLUMNS = ("open_time", "ts", "timestamp", "datetime", "time", "date")
OHLC = {"open", "high", "low", "close"}


def now():
    return datetime.now(timezone.utc).isoformat()


def location(root=ROOT):
    return Path(root) / "data/crypto/_catalog"


def dataset_id(path):
    return re.sub(r"[^a-zA-Z0-9_-]", "-", str(path))[-100:] + "-" + hashlib.sha256(str(path).encode()).hexdigest()[:8]


def sources(root=ROOT):
    """Discover dataset roots only, excluding credentials and execution journals."""
    root = Path(root)
    result = []
    def add(path, category):
        if not path.exists() and not path.is_symlink():
            return
        rel = path.relative_to(root).as_posix()
        if any(x in rel.lower() for x in ("xau", "gold_multitimeframe", "owner_okx_history")):
            return
        resolved = path.resolve()
        storage = "external" if not resolved.is_relative_to(root) else "managed" if resolved.is_relative_to(root / "data/crypto") else "in_place"
        canonical = resolved.relative_to(root).as_posix() if storage != "external" else rel
        view = root / "data/crypto" / category / dataset_id(rel)
        if storage == "in_place" and view.is_symlink() and view.resolve() == resolved:
            canonical = view.relative_to(root).as_posix()
        display = path.parent.name + " / " + path.name if rel.startswith("experiments/") else path.name
        result.append(dict(id=dataset_id(rel), name=display, category=category,
                           category_label=CATEGORIES[category], path=canonical,
                           original_paths=[rel], storage=storage))
    data = root / "data"
    if data.is_dir():
        for p in sorted(data.iterdir()):
            if p.name.startswith("kline_"):
                add(p, "market")
            elif p.name in {"funding", "altdata"}:
                add(p, "features")
            elif p.is_dir() and p.name.startswith(("altcoin_", "imacd_", "genuine_flow", "ma206", "eth_micro")):
                add(p, "research")
            elif p.is_file() and p.name.startswith(("judgment_", "kronos_feats_", "owner_box_dataset")) and p.suffix == ".csv":
                add(p, "features")
    for p in sorted((root / "data/research").glob("*")):
        if p.is_dir() and not p.name.startswith("."):
            add(p, "market" if not p.name.startswith("open_factors") else "features")
    for p in sorted((root / "datasets").glob("*")):
        if p.is_dir() and not p.name.startswith("."):
            add(p, "research" if p.name in {"annotations", "manifests"} else "vision")
    for p in sorted((root / "archive/consolidated").glob("*/datasets/*")):
        if p.is_dir():
            add(p, "archives")
    for p in sorted((root / "archive/consolidated").glob("*/data/*")):
        if p.is_dir():
            add(p, "archives")
    # Only named inputs, not backtest outputs or trading account exports.
    for pattern in ("experiments/active/*/inputs", "experiments/active/*/inputs_*", "experiments/active/*/sources", "experiments/active/*/sources_*", "experiments/active/*/source_data", "experiments/active/*/data", "experiments/active/*/data_tradable", "analysis/output/*/kline_snapshot", "analysis/output/*/cache", "analysis/output/*/klines"):
        for p in sorted(root.glob(pattern)):
            if p.is_dir():
                add(p, "research")
    return list({x["id"]: x for x in result}.values())


def _timestamp(value):
    """Parse only explicit UTC/epoch market clocks, without local timezone drift."""
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    try:
        numeric = float(value)
        if numeric > 1e17: numeric /= 1e9
        elif numeric > 1e14: numeric /= 1e6
        elif numeric > 1e11: numeric /= 1e3
        return datetime.fromtimestamp(numeric, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc).isoformat() if parsed.tzinfo is None else parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return None


def market_metadata(path, identity_path):
    """Inspect header/first two rows/tail; coverage is observed, not completeness."""
    info = dict(symbol=None, exchange=None, timeframe=None, start=None, end=None,
                row_count=None, time_column=None, volume_column=None, market=None)
    if not (path.name.endswith(".csv") or path.name.endswith(".csv.gz")):
        return info
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            if not OHLC.issubset(columns): return info
            tc = next((c for c in TIME_COLUMNS if c in columns), None)
            vc = next((c for c in ("volume", "vol") if c in columns), None)
            if not tc or not vc: return info
            first, second = next(reader, {}), next(reader, {})
        first_time, second_time = _timestamp(first.get(tc)), _timestamp(second.get(tc))
        if not first_time: return info
        context = str(identity_path)
        match = re.search(r"(?:okx_|gate_|binance_)?([A-Z0-9]+(?:[_-]USDT(?:[_-]SWAP)?|USDT))", path.name)
        if not match: return info
        symbol = match[1]
        venues = {v for v in ("binance", "gate", "okx") if re.search(r"(?:^|[/_-])"+v+r"(?:[/_-]|$)", context.lower())}
        ex = next(iter(venues)) if len(venues) == 1 else None
        tf = None
        if second_time:
            minutes = (datetime.fromisoformat(second_time) - datetime.fromisoformat(first_time)).total_seconds() / 60
            tf = {1:"1m",2:"2m",3:"3m",5:"5m",15:"15m",30:"30m",60:"1H",120:"2H",240:"4H",1440:"1D"}.get(minutes)
        # Prefer explicit bar in the filename, so a gap cannot redefine frequency.
        matched = re.search(r"(?:_|-)(1m|2m|3m|5m|15m|30m|1H|4H|1h|4h|1D)(?:_|-|\.)", path.name)
        if matched: tf = matched[1].replace("h", "H")
        end = None
        if path.suffix != ".gz":
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 16384))
                tail = handle.read().decode("utf-8").splitlines()
            if tail:
                values = next(csv.reader([tail[-1]]))
                if len(values) == len(reader.fieldnames):
                    end = _timestamp(dict(zip(reader.fieldnames, values)).get(tc))
        market = "swap" if "SWAP" in symbol or re.search(r"binance[_/-]um(?:\d|[/_-])", context.lower()) else "spot" if re.search(r"(?:^|[/_-])spot(?:[/_-]|$)", context.lower()) else "unknown"
        info.update(symbol=symbol, exchange=ex, timeframe=tf, start=first_time, end=end,
                    time_column=tc, volume_column=vc,
                    market=market)
    except (OSError, UnicodeError, ValueError, csv.Error, EOFError):
        pass
    return info


def _files(path):
    if path.is_file():
        yield path
        return
    for current, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (Path(current)/d).is_symlink())
        for name in sorted(files):
            if name not in EXCLUDED and not name.startswith("."):
                yield Path(current) / name


def _receipts(directory):
    """Use explicit input provenance for generated streams with generic names."""
    manifest = directory / "manifest.json"
    if not manifest.is_file() or manifest.stat().st_size > 30_000_000:
        return {}
    try:
        streams = json.loads(manifest.read_text()).get("streams", [])
    except (ValueError, UnicodeError):
        return {}
    if not isinstance(streams, list): return {}
    result = {}
    for row in streams:
        if not isinstance(row, dict) or not row.get("path"): continue
        inputs = [str(x.get("path", "")).lower() for x in row.get("inputs", []) if isinstance(x, dict)]
        venues = {ex for ex in ("binance", "okx", "gate") if any(ex in p for p in inputs)}
        if len(venues) != 1: continue
        result[Path(row["path"]).name] = dict(exchange=next(iter(venues)),
            market="swap" if any("binance_um" in p or "swap" in p for p in inputs) else "unknown",
            end=_timestamp(row.get("last_ms")), row_count=row.get("rows"))
    return result


def build_index(root=ROOT):
    """Stream file metadata into a replacement SQLite index, never overwrite data."""
    root = Path(root).resolve()
    home = location(root); home.mkdir(parents=True, exist_ok=True)
    with (home / "index.lock").open("a+") as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        state = home / "status.json"
        def status(value):
            temp = state.with_suffix(".tmp")
            temp.write_text(json.dumps(value, ensure_ascii=False)); os.replace(temp, state)
        status(dict(status="running", started_at=now(), pid=os.getpid(), error=None))
        temp = home / ("index-" + uuid.uuid4().hex + ".sqlite3")
        try:
            with closing(sqlite3.connect(temp)) as db:
                db.executescript("""PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
                CREATE TABLE datasets(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE files(dataset_id TEXT, path TEXT, size_bytes INTEGER, mtime_ns INTEGER,
                    format TEXT, symbol TEXT, exchange TEXT, timeframe TEXT, start TEXT, end TEXT,
                    row_count INTEGER, time_column TEXT, volume_column TEXT, market TEXT,
                    PRIMARY KEY(dataset_id,path));
                CREATE TABLE info(key TEXT PRIMARY KEY,value TEXT);
                """)
                for item in sources(root):
                    receipts = _receipts(root / item["path"])
                    formats, exchanges, timeframes, symbols = set(), set(), set(), set()
                    count = size = missing = 0; starts=[]; ends=[]
                    if item["storage"] != "external":
                        for path in _files(root / item["path"]):
                            rel = path.relative_to(root).as_posix()
                            resolved = path.resolve()
                            if not resolved.is_relative_to(root):
                                missing += 1; continue
                            try: st=path.stat()
                            except OSError: missing += 1; continue
                            suffix = ".csv.gz" if path.name.endswith(".csv.gz") else path.suffix.lower() or "[none]"
                            relative = path.relative_to(root / item["path"]) if (root / item["path"]).is_dir() else Path(path.name)
                            info = market_metadata(path, item["original_paths"][0] + "/" + str(relative)) if item["category"] != "vision" else {}
                            if info.get("symbol") and path.name in receipts:
                                for key, value in receipts[path.name].items():
                                    if value is not None and not info.get(key) or key == "market" and info.get(key) == "unknown":
                                        info[key] = value
                            values = [info.get(k) for k in ("symbol","exchange","timeframe","start","end","row_count","time_column","volume_column","market")]
                            db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(item["id"],rel,st.st_size,st.st_mtime_ns,suffix,*values))
                            count+=1;size+=st.st_size; formats.add(suffix)
                            for key, collection in (("exchange",exchanges),("timeframe",timeframes),("symbol",symbols)):
                                if info.get(key): collection.add(info[key])
                            if info.get("start"): starts.append(info["start"])
                            if info.get("end"): ends.append(info["end"])
                            if count % 10000 == 0: db.commit()
                    item.update(file_count=count,total_bytes=size,formats=sorted(formats),exchanges=sorted(exchanges),
                                timeframes=sorted(timeframes),symbols=sorted(symbols),symbols_count=len(symbols),start=min(starts) if starts else None,
                                end=max(ends) if ends else None, missing_count=missing,
                                backtest_ready=bool(symbols and timeframes and exchanges),
                                notes=["文件逻辑大小；不等于内存或 APFS 共享后的实际物理占用。", "版本与原始时间切分保留，索引不授予训练或生产资格。"])
                    if item["storage"] == "external": item["notes"].append("外部只读旧缓存，仅登记入口，不移动、不索引外部文件。")
                    if starts: item["notes"].append("时间范围来自文件首尾或已有清单；具体回测读取时检查缺口和冲突。")
                    if missing: item["notes"].append(f"{missing} 个失效或越界链接未计入可用文件。")
                    db.execute("INSERT INTO datasets VALUES (?,?)",(item["id"],json.dumps(item,ensure_ascii=False)))
                    db.commit()
                    print(json.dumps({"dataset":item["name"],"files":count,"bytes":size},ensure_ascii=False),flush=True)
                db.execute("CREATE INDEX market_stream ON files(dataset_id,symbol,timeframe,exchange)")
                db.execute("INSERT INTO info VALUES ('generated_at',?)",(now(),)); db.commit()
            os.replace(temp, home / "index.sqlite3")
            status(dict(status="idle", finished_at=now(),error=None)); return True
        except Exception as error:
            status(dict(status="failed",finished_at=now(),error=str(error))); raise
        finally: temp.unlink(missing_ok=True)


class DatasetCatalog:
    def __init__(self, root=ROOT):
        self.root=Path(root).resolve(); self.home=location(self.root)

    def _connect(self):
        db=sqlite3.connect((self.home/"index.sqlite3").as_uri()+"?mode=ro",uri=True)
        db.row_factory=sqlite3.Row
        return db

    @staticmethod
    def _example(db, item):
        row=db.execute("SELECT symbol,timeframe,exchange,start,end FROM files WHERE dataset_id=? AND symbol IS NOT NULL AND timeframe IS NOT NULL AND exchange IS NOT NULL AND market IN ('spot','swap') AND start IS NOT NULL ORDER BY symbol,path LIMIT 1",(item["id"],)).fetchone()
        item["backtest_ready"] = row is not None
        if row is not None:
            step={"1m":1,"2m":2,"3m":3,"5m":5,"15m":15,"30m":30,"1H":60,"2H":120,"4H":240,"1D":1440}.get(row["timeframe"])
            if step:
                start=datetime.fromisoformat(row["start"])
                end=start+timedelta(minutes=step*2)
                if row["end"]: end=min(end,datetime.fromisoformat(row["end"])+timedelta(minutes=step))
                item["example"]=dict(symbol=row["symbol"],timeframe=row["timeframe"],exchange=row["exchange"],start=start.isoformat(),end=end.isoformat())
        return item

    def overview(self):
        state=dict(status="idle",error=None)
        if (self.home/"status.json").is_file():
            state=json.loads((self.home/"status.json").read_text())
            if state.get("status")=="running":
                try: os.kill(state["pid"],0)
                except (ProcessLookupError,KeyError): state=dict(status="failed",error="索引进程已退出，可重新建立索引。")
        items=[];generated=None
        if (self.home/"index.sqlite3").is_file():
            with closing(self._connect()) as db:
                items=[self._example(db,json.loads(x[0])) for x in db.execute("SELECT metadata FROM datasets ORDER BY id")]
                items.sort(key=lambda x:(list(CATEGORIES).index(x["category"]),x["storage"]!="managed",x["name"]))
                generated=db.execute("SELECT value FROM info WHERE key='generated_at'").fetchone()[0]
        counts=Counter(x["category"] for x in items)
        receipt=self.home/"maintenance.json"
        saved=json.loads(receipt.read_text()).get("deduplicated_bytes",0) if receipt.is_file() else 0
        return dict(indexed=bool(generated),generated_at=generated,items=items,maintenance=state,
                    categories=[dict(id=k,label=v,count=counts[k]) for k,v in CATEGORIES.items()],
                    summary=dict(dataset_count=len(items),file_count=sum(x["file_count"] for x in items),
                    total_bytes=sum(x["total_bytes"] for x in items),managed_bytes=sum(x["total_bytes"] for x in items if x["storage"]=="managed"),
                    duplicate_bytes=saved,missing_count=sum(x["missing_count"] for x in items)))

    def detail(self, key, limit=50, offset=0):
        if not (self.home/"index.sqlite3").is_file(): raise KeyError(key)
        with closing(self._connect()) as db:
            row=db.execute("SELECT metadata FROM datasets WHERE id=?",(key,)).fetchone()
            if row is None: raise KeyError(key)
            dataset=self._example(db,json.loads(row[0]))
            files=[dict(x) for x in db.execute("SELECT * FROM files WHERE dataset_id=? ORDER BY symbol IS NULL, path LIMIT ? OFFSET ?",(key,limit,offset))]
            return dict(dataset=dataset,files=files,total=dataset["file_count"],limit=limit,offset=offset)

    def file(self, key, path):
        if not (self.home/"index.sqlite3").is_file(): raise KeyError(key)
        with closing(self._connect()) as db:
            row=db.execute("SELECT size_bytes,mtime_ns FROM files WHERE dataset_id=? AND path=?",(key,path)).fetchone()
        target=(self.root/path).resolve()
        if row is None or not target.is_relative_to(self.root) or not target.is_file(): raise KeyError(path)
        st=target.stat()
        if (st.st_size,st.st_mtime_ns)!=tuple(row): raise ValueError("文件已变动，请重新索引后读取。")
        return target


def read_market_data(*, dataset_id, symbol, timeframe, start, end, exchange=None, root=ROOT, max_rows=2_000_000):
    """Read closed OHLCV in [start,end), with exact bar coverage or a clear error.

    Columns: one explicit time column plus open/high/low/close/volume (or vol),
    optional confirm. Reads only this dataset/symbol/bar, in 50k-row chunks.
    No downloading, interpolation, cross-venue merge or future-row feature use.
    """
    import pandas as pd
    cat=DatasetCatalog(root)
    a,b=pd.Timestamp(start),pd.Timestamp(end)
    a=a.tz_localize("UTC") if a.tzinfo is None else a.tz_convert("UTC")
    b=b.tz_localize("UTC") if b.tzinfo is None else b.tz_convert("UTC")
    minutes={"1m":1,"2m":2,"3m":3,"5m":5,"15m":15,"30m":30,"1H":60,"2H":120,"4H":240,"1D":1440}.get(timeframe)
    if not minutes or a>=b: raise ValueError("明确指定有效周期和起止时间。")
    delta=pd.Timedelta(minutes=minutes)
    if a.value%delta.value or b.value%delta.value: raise ValueError("时间范围必须对齐所选 K 线周期。")
    if (b-a)//delta > max_rows: raise ValueError("请求过大，请缩短区间或逐币回测。")
    with closing(cat._connect()) as db:
        rows=[dict(x) for x in db.execute("SELECT * FROM files WHERE dataset_id=? AND symbol=? AND timeframe=?",(dataset_id,symbol,timeframe))]
    if exchange: rows=[x for x in rows if x["exchange"]==exchange]
    if not rows or len({(x["exchange"],x["market"]) for x in rows})!=1 or not rows[0]["exchange"] or rows[0]["market"] not in {"spot","swap"}:
        raise ValueError("本地数据源缺失或交易所/市场身份不唯一；请选择明确数据集。")
    frames=[]
    for row in rows:
        if row["end"] and pd.Timestamp(row["end"])<a or row["start"] and pd.Timestamp(row["start"])>=b: continue
        path=cat.file(dataset_id,row["path"])
        tc,vc=row["time_column"],row["volume_column"]
        for chunk in pd.read_csv(path,chunksize=50000,usecols=lambda c:c in {tc,vc,"open","high","low","close","confirm"}):
            if pd.api.types.is_numeric_dtype(chunk[tc]):
                number=abs(float(chunk[tc].dropna().iloc[0]))
                unit="ns" if number>1e17 else "us" if number>1e14 else "ms" if number>1e11 else "s"
                times=pd.to_datetime(chunk[tc],unit=unit,utc=True,errors="raise")
            else: times=pd.to_datetime(chunk[tc],utc=True,errors="raise")
            selected=chunk.loc[(times>=a)&(times<b)].copy()
            if "confirm" in selected: selected=selected.loc[pd.to_numeric(selected.confirm).eq(1)]
            selected["open_time"]=times.loc[selected.index]
            selected=selected.rename(columns={vc:"volume"})[["open_time","open","high","low","close","volume"]]
            for col in ("open","high","low","close","volume"): selected[col]=pd.to_numeric(selected[col],errors="raise")
            if not selected.empty: frames.append(selected)
            if sum(len(x) for x in frames)>max_rows*3: raise ValueError("重叠副本过多，请选择更窄的数据集。")
    if not frames: raise ValueError("所选本地区间没有数据；未自动下载。")
    result=pd.concat(frames,ignore_index=True).drop_duplicates()
    if result.open_time.duplicated().any(): raise ValueError("同一根 K 线存在冲突副本，未自动覆盖。")
    result=result.sort_values("open_time").reset_index(drop=True)
    expected=pd.date_range(a,b,freq=delta,inclusive="left")
    if not result.open_time.equals(pd.Series(expected,name="open_time")):
        raise ValueError("本地数据有缺口或未收盘 K 线；请补齐明确缺失区间后回测。")
    if result.isna().any().any(): raise ValueError("OHLCV 存在空值。")
    return result


def launch_index(root=ROOT):
    home=location(root);home.mkdir(parents=True,exist_ok=True)
    with (home/"index.log").open("ab") as log:
        process=subprocess.Popen([sys.executable,"-m","yoyo.data.dataset_catalog","index","--root",str(root)],cwd=root,
                                 stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    return dict(status="running",pid=process.pid)


if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("command",choices=["index"]);parser.add_argument("--root",type=Path,default=ROOT)
    args=parser.parse_args();build_index(args.root)
