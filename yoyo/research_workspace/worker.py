"""Serial, process-isolated offline jobs with immutable output directories.

The only replay adapter invokes the existing frozen V12.8 engine with unchanged
entries, exits, split and costs. No arbitrary commands, network data fetches,
training, promotion, notifications or execution hooks are accepted.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .catalog import Catalog
from .registrations import register_result
from .store import WorkspaceStore, now


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def summarize_replay(folder):
    """Authenticate receipts, then reuse established ex-post metrics and tests.

Only labels/outcomes use future bars. No feature or entry policy is changed.
Partial-universe manifests remain partial; task success never changes that flag.
"""
    import numpy as np
    import pandas as pd
    from yoyo.evaluation import spike_v128_recent_report as stats

    run = Path(folder) / "replay"
    manifest = json.loads((run / "manifest.json").read_text())
    identity = json.loads((run / "identity.json").read_text())
    run_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    expected = {f"{s}_{tf}m" for s in identity["symbols"] for tf in identity["timeframes"]}
    if (manifest.get("errors") or manifest["run_identity"] != run_hash
            or set(manifest["stream_keys"]) != expected or set(manifest["receipts"]) != expected):
        raise ValueError("重放清单身份或范围不一致")
    tables = {"trades": [], "controls": []}
    for key in sorted(expected):
        directory = run / "streams" / key
        receipt_path = directory / "receipt.json"
        if digest(receipt_path) != manifest["receipts"][key]:
            raise ValueError("重放 receipt 哈希不一致")
        receipt = json.loads(receipt_path.read_text())
        if receipt["status"] != "complete" or receipt["run_identity"] != run_hash:
            raise ValueError("重放流未完成")
        for name, sha in receipt["files"].items():
            if Path(name).name != name or digest(directory / name) != sha:
                raise ValueError("重放逐笔文件哈希不一致")
        for name in tables:
            tables[name].append(stats.read_csv(directory / f"{name}.csv.gz"))
    merged = {k: pd.concat([t for t in v if not t.empty], ignore_index=True)
              if any(not t.empty for t in v) else pd.DataFrame() for k, v in tables.items()}
    cfg = identity["config"]
    trades = stats.enrich(merged["trades"], pd.Timestamp(cfg["split"]))
    closed = trades.loc[~trades.censored.astype(bool)].copy() if not trades.empty else trades
    results = []
    views = {"summary": ["timeframe_min", "arm"], "periods": ["timeframe_min", "arm", "period"]}
    controls = merged["controls"]
    matched = pd.DataFrame()
    if not closed.empty and not controls.empty:
        matched = controls.loc[controls.matched.astype(bool)].merge(closed, on="trade_key", suffixes=("_ctrl", ""), validate="one_to_one")
        matched["excess_bp"] = (matched.net_return - matched.control_net_return) * 1e4
    for view, keys in views.items():
        table = stats.grouped_metrics(closed, keys) if not closed.empty else pd.DataFrame()
        comparisons = []
        if not matched.empty:
            for key, group in matched.groupby(keys):
                if view == "periods" and key[-1] == "earlier":
                    group = group.loc[pd.to_datetime(group.control_exit_time, utc=True) < pd.Timestamp(cfg["split"])]
                if group.empty:
                    continue
                comparisons.append(dict(zip(keys, key), matched=len(group), target_net_bp=float(group.net_bp.mean()),
                                        control_net_bp=float(group.control_net_return.mean() * 1e4))
                                   | stats.block_inference(group.excess_bp, group.week, cfg["stat_seed"], cfg["bootstrap"]))
        comparison = pd.DataFrame(comparisons)
        if not comparison.empty:
            comparison["p_holm"] = np.nan
            primary = comparison.dropna(subset=["p_one_sided"]).sort_values("p_one_sided")
            running = 0.
            for rank, (idx, row) in enumerate(primary.iterrows()):
                running = max(running, min(1., row.p_one_sided * (len(primary) - rank)))
                comparison.loc[idx, "p_holm"] = running
            table = table.merge(comparison, on=keys, how="left", validate="one_to_one")
        table.to_csv(Path(folder) / f"{view}.csv", index=False)
        results.append({"name": view, "columns": list(table.columns), "rows": json.loads(table.to_json(orient="records")),
                        "total_rows": len(table), "truncated": False})
    return {"tables": results, "config": cfg, "stream_count": len(expected), "symbols": identity["symbols"],
            "source_manifest_sha256": digest(run / "manifest.json"), "subset": identity["subset"],
            "notes": ["任务完成只表示所选流已重放；未作策略通过判定。", "子集不代表全市场，累计 R 不代表账户复利。",
                      "使用原逐笔匹配随机对照与周块置换；前后段分开显示。", "本任务为规则重放，AUC 和排序 top-decile 不适用。"],
            "training_eligible": False, "production_eligible": False}


def execute(store, root, job, lock_fd=None):
    folder = Path(job["output"])
    folder.mkdir(parents=True, exist_ok=False)
    save(folder / "request.json", dict(job["spec"], job_id=job["id"], requested_at=job["created_at"]))
    if job["recipe"] == "verify-evidence":
        value = Catalog(root).evidence(job["experiment_id"])
        value["notes"] = value.get("notes", []) + ["这是已有证据的读取快照；没有重算交易，也未验证统计结论。"]
        save(folder / "result.json", value)
        return
    if job["recipe"] != "spike-v128-frozen":
        raise ValueError("未知回测适配器")
    # Preserve the repository's builder-before-artifacts rule for this wrapper.
    for source in ("yoyo/research_workspace/worker.py", "tests/research_workspace/test_worker.py",
                   "yoyo/research_workspace/registrations.py", "tests/research_workspace/test_registrations.py"):
        check = subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", source], cwd=root, capture_output=True)
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", source], cwd=root, capture_output=True)
        if check.returncode or tracked.returncode:
            raise ValueError("回测适配器与测试须先提交，当前构建代码未冻结。")
    command = [sys.executable, "-m", "yoyo.evaluation.spike_v128_recent", "--output", str(folder / "replay"),
               "--workers", "1", "--symbols", *job["spec"]["symbols"]]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    save(folder / "provenance.json", {"command": command, "commit": commit, "adapter_sha256": digest(__file__), "started_at": now()})
    with (folder / "run.log").open("wb") as log:
        # Keep serialization if the wrapper is killed while the engine remains
        # alive: the engine inherits the lock until its own process exits.
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, pass_fds=() if lock_fd is None else (lock_fd,))
        try:
            while process.poll() is None:
                if store.job(job["id"])["cancel_requested"]:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    return
                time.sleep(.5)
            if process.returncode:
                raise RuntimeError("原回测引擎退出失败；请查看任务日志，原始输出已保留。")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
    if not store.job(job["id"])["cancel_requested"]:
        save(folder / "result.json", summarize_replay(folder))


def run(root, runtime):
    store = WorkspaceStore(runtime)
    with (store.runtime / "worker.lock").open("a+") as lock:
        # Blocking here closes the submit/worker-exit race. Waiting launchers
        # exit immediately once the active worker drains the queue.
        fcntl.flock(lock, fcntl.LOCK_EX)
        store.recover()
        while True:
            job = store.claim()
            if job is None:
                break
            try:
                execute(store, Path(root), job, lock.fileno())
                cancelled = store.job(job["id"])["cancel_requested"]
                if not cancelled:
                    register_result(Path(root), job)
                store.finish(job["id"], "cancelled" if cancelled else "completed")
            except Exception as error:
                store.finish(job["id"], "failed", str(error))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.runtime)
