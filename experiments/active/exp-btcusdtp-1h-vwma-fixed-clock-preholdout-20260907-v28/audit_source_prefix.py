"""Post-run bracketed archive/prefix integrity check, not an atomicity claim.

V28 reused a reader that hashed before OPEN materialization but did not rehash
after it. Preserve that runner unchanged. Independently check CURRENT archive
against its original SHA, materialize only pre2025 OPEN again, compare every
saved OPEN/timestamp, then rehash both archive and saved prefix. This proves
post-run consistency, not that the original read was an atomic snapshot.
No labels/inference/refitting, no HLCV or2025+ price materialization.
"""
import csv
from datetime import datetime,timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3];E=Path(__file__).resolve().parent


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):h.update(block)
    return h.hexdigest()


def clock(value):
    t=datetime.fromisoformat(value.replace("Z","+00:00"))
    if t.utcoffset().total_seconds()!=0:raise ValueError("Explicit UTC required")
    return t


def main():
    own=Path(__file__).resolve();commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    if subprocess.check_output(["git","show",commit+":"+str(own.relative_to(ROOT))],cwd=ROOT)!=own.read_bytes():raise ValueError("Commit source checker first")
    config=json.loads((E/"config.json").read_text());source=config["source"];path=ROOT/source["path"]
    # Read only timestamp column logically before price materialization. csv
    # line splitting necessarily handles raw bytes; no non-time field is used.
    phase=datetime(2025,1,1,tzinfo=timezone.utc);last=None;prefix=0;physical=0
    with path.open() as stream:
        rows=csv.reader(stream);header=next(rows);offset=header.index("open_time")
        for row in rows:
            t=clock(row[offset])
            if last is not None and t<=last:raise ValueError("Unsorted/duplicate source clock")
            if t>=clock(source["end_exclusive"]):raise ValueError("Source boundary breached")
            last=t;physical+=1;prefix+=t<phase
    if physical!=341567 or prefix!=219551 or sha(path)!=source["sha256"]:raise ValueError("Current source disagrees with pinned archive")
    saved=E/"results/open_prefix.csv.gz";saved_sha=sha(saved)
    summary=json.loads((E/"results/summary.json").read_text());summary_sha=sha(E/"results/summary.json")
    if saved_sha!=summary["output_hashes"]["open_prefix.csv.gz"]:raise ValueError("Saved prefix changed")
    with path.open() as stream,gzip.open(saved,"rt") as prior:
        raw=csv.DictReader(stream);expected=csv.DictReader(prior)
        for i in range(prefix):
            a,b=next(raw),next(expected)
            if clock(a["open_time"])>=phase or clock(a["open_time"])!=clock(b["open_time"]) or Decimal(a["open"])!=Decimal(b["open"]):
                raise ValueError("Saved OPEN differs from current pinned archive at row "+str(i))
        if next(expected,None) is not None:raise ValueError("Extra saved OPEN row")
    if sha(path)!=source["sha256"] or sha(saved)!=saved_sha or sha(E/"results/summary.json")!=summary_sha:raise ValueError("Source/prefix changed during independent check")
    result=dict(status="passed",at=datetime.now(timezone.utc).isoformat(),checker_commit=commit,checker_sha256=sha(own),
                summary_sha256=summary_sha,archive_sha256=source["sha256"],saved_prefix_sha256=saved_sha,
                physical_clock_rows=physical,compared_open_rows=prefix,post2024_prices_materialized=0,
                entry_features_or_labels_changed=False,original_run_atomic_snapshot_proved=False,
                purpose="Post-run consistency with bracketed hashes; no silent rewrite of original reader provenance.")
    with (E/"source_prefix_audit.json").open("x") as stream:json.dump(result,stream,indent=2)
    print(json.dumps(result))


if __name__=="__main__":main()
