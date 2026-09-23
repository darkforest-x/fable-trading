"""Journaled local dataset moves and exact-byte copy-on-write deduplication.

Moves retain legacy aliases so frozen manifests/configs keep their paths.
Only explicitly selected, untracked, non-symlink directories may move. File
counts, bytes, inode identity and all internal symlink targets are verified.
Deduplication uses APFS clonefile, not hardlinks: subsequent writes are private.
Source: https://github.com/apple-oss-distributions/xnu/blob/main/bsd/man/man2/clonefile.2
No data downloads, model changes, schema conversion or row-level merging.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid

from .dataset_catalog import ROOT, location, now, sources


def digest(path):
    value=hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda:handle.read(1<<20),b""):value.update(chunk)
    return value.hexdigest()


def _save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n");os.replace(tmp,path)


def move_dataset(relative, *, root=ROOT):
    """Move one explicit offline directory; do not follow external cache links."""
    root=Path(root).resolve(); old=root/relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Relative dataset path required")
    research_input = re.fullmatch(r"experiments/active/[^/]+/(inputs|data|data_tradable)", relative)
    if not (relative.startswith("data/kline_") or relative.startswith("data/research/") or research_input):
        raise ValueError("Only named local market datasets may move")
    if old.is_symlink():
        if old.resolve().is_relative_to(root/"data/crypto/market"):return dict(status="already_managed",source=relative)
        raise ValueError("External and pre-existing symlinks cannot move")
    tracked=subprocess.check_output(["git","ls-files","-z","--",relative],cwd=root)
    if tracked:raise ValueError("Tracked dataset content must retain its Git location")
    name=("research-" if relative.startswith("data/research/") else "")+old.name
    if research_input: name=old.parent.name+"-"+old.name
    new=root/"data/crypto/market"/name
    journal=location(root)/"moves"/(hashlib.sha256(relative.encode()).hexdigest()[:16]+".json")
    if not old.exists() and new.is_dir() and journal.is_file():
        previous=json.loads(journal.read_text())
        if previous.get("status")=="prepared" and previous.get("source")==relative and previous.get("destination")==new.relative_to(root).as_posix():
            for rel,inode,size,mtime in previous["identities"]:
                st=(new/rel).stat()
                if (st.st_ino,st.st_size,st.st_mtime_ns)!=(inode,size,mtime):raise ValueError("Interrupted move identity mismatch")
            old.symlink_to(os.path.relpath(new,old.parent),target_is_directory=True)
            previous.update(status="complete",completed_at=now(),recovered=True)
            _save(journal,previous);return previous
    if not old.is_dir():raise ValueError("Source directory missing")
    if new.exists() or new.is_symlink():raise ValueError("Destination already exists")
    identities=[];links=[];total=0
    for current,dirs,files in os.walk(old,followlinks=False):
        for name in dirs+files:
            p=Path(current)/name
            if p.is_symlink():
                raise ValueError("Dataset contains symlinks; preserve its original version directory")
            elif p.is_file():
                st=p.stat();identities.append((p.relative_to(old),st.st_ino,st.st_size,st.st_mtime_ns));total+=st.st_size
    record=dict(status="prepared",source=relative,destination=new.relative_to(root).as_posix(),files=len(identities),bytes=total,started_at=now())
    record["identities"]=[(str(rel),inode,size,mtime) for rel,inode,size,mtime in identities]
    _save(journal,record)
    new.parent.mkdir(parents=True,exist_ok=True)
    os.rename(old,new)
    # An interrupted move is explicitly recorded; recreate the alias before
    # rewriting internal links so every historical path is again resolvable.
    old.symlink_to(os.path.relpath(new,old.parent),target_is_directory=True)
    for rel,target in links:
        p=new/rel
        p.unlink();p.symlink_to(os.path.relpath(target,p.parent),target_is_directory=target.is_dir())
    for rel,inode,size,mtime in identities:
        actual=(new/rel).stat()
        if (actual.st_ino,actual.st_size,actual.st_mtime_ns)!=(inode,size,mtime):
            raise ValueError("File changed during relocation; inspect journal")
        if (old/rel).stat().st_ino!=inode:raise ValueError("Legacy alias parity failed")
    for rel,target in links:
        if (old/rel).resolve()!=target.resolve():raise ValueError("Symlink parity failed")
    record.update(status="complete",completed_at=now(),symlinks=len(links));_save(journal,record)
    return record


def link_views(root=ROOT):
    """Provide a single folder entry without moving versioned training manifests."""
    root=Path(root).resolve()
    for item in sources(root):
        if item["category"] not in {"vision","features","archives","research"}:continue
        target=root/item["original_paths"][0]
        if not target.resolve().is_relative_to(root):continue
        entry=root/"data/crypto"/item["category"]/item["id"]
        entry.parent.mkdir(parents=True,exist_ok=True)
        if entry.is_symlink() and entry.resolve()==target.resolve():continue
        if entry.exists() or entry.is_symlink():raise ValueError("Dataset view collision")
        entry.symlink_to(os.path.relpath(target,entry.parent),target_is_directory=target.is_dir())


def clone_duplicate(source, target):
    """Atomically remove independent duplicate blocks, keeping private COW files."""
    source,target=Path(source),Path(target)
    if sys.platform!="darwin":raise ValueError("COW cleanup is available only on macOS/APFS")
    if source.is_symlink() or target.is_symlink() or not source.is_file() or not target.is_file():raise ValueError("Regular files required")
    a,b=source.stat(),target.stat()
    if a.st_ino==b.st_ino or a.st_size!=b.st_size:return None
    sha=digest(source)
    if digest(target)!=sha:return None
    temp=target.with_name(".dedupe-"+uuid.uuid4().hex)
    libc=ctypes.CDLL(None,use_errno=True)
    clone=libc.clonefile;clone.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int];clone.restype=ctypes.c_int
    try:
        if clone(os.fsencode(source),os.fsencode(temp),0):raise OSError(ctypes.get_errno(),"clonefile refused; original retained")
        shutil.copystat(target,temp)
        if digest(temp)!=sha:raise ValueError("Clone hash mismatch")
        identity=lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
        if identity(source.stat())!=identity(a) or identity(target.stat())!=identity(b):raise ValueError("File changed during deduplication")
        os.replace(temp,target)
        return dict(source=str(source),target=str(target),sha256=sha,bytes=b.st_size,method="apfs-copy-on-write")
    finally:temp.unlink(missing_ok=True)


def deduplicate(*, root=ROOT, minimum_bytes=65536):
    """Limit cleanup to indexed local datasets, same basename/size and full SHA."""
    root=Path(root).resolve();home=location(root)
    receipt_path=home/"maintenance.json"
    receipt=json.loads(receipt_path.read_text()) if receipt_path.is_file() else dict(deduplicated_bytes=0,files=0)
    completed=set()
    ledger=home/"deduplication.jsonl"
    if ledger.exists():
        for line in ledger.open():
            row=json.loads(line);completed.add((row["target"],row["sha256"]))
    db=sqlite3.connect((home/"index.sqlite3").as_uri()+"?mode=ro",uri=True)
    groups={}
    # Cheap size/name candidates avoid hashing unique archives or tiny labels.
    for rel,size in db.execute("SELECT path,size_bytes FROM files WHERE size_bytes>=? AND format IN ('.csv','.csv.gz','.png','.jpg','.zip')",(minimum_bytes,)):
        path=root/rel
        if path.is_symlink() or not path.resolve().is_relative_to(root):continue
        groups.setdefault((path.name,size),[]).append(path)
    db.close()
    with ledger.open("a") as log:
        for paths in groups.values():
            if len(paths)<2:continue
            by_hash={}
            for path in paths:
                sha=digest(path)
                if sha not in by_hash:by_hash[sha]=path;continue
                if (str(path),sha) in completed:continue
                row=clone_duplicate(by_hash[sha],path)
                if row:
                    row["completed_at"]=now();log.write(json.dumps(row)+"\n");log.flush();os.fsync(log.fileno())
                    receipt["deduplicated_bytes"]+=row["bytes"];receipt["files"]+=1
                    _save(receipt_path,receipt)
    receipt["updated_at"]=now();_save(receipt_path,receipt)
    return receipt


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("command",choices=["move","deduplicate","link-views"])
    parser.add_argument("paths",nargs="*");parser.add_argument("--root",type=Path,default=ROOT)
    args=parser.parse_args()
    if args.command=="move":
        for path in args.paths:print(json.dumps(move_dataset(path,root=args.root),ensure_ascii=False),flush=True)
    elif args.command=="link-views":link_views(args.root)
    else:print(json.dumps(deduplicate(root=args.root),ensure_ascii=False))
