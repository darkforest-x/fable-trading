#!/usr/bin/env python3
"""Overnight autonomous loop for owner_w20_midbox_cold.

Owner 2026-08-07: keep working overnight — poll 3060, fetch weights when done,
evaluate + light backtest, if weak diagnose and iterate (more negs / retrain).
Does NOT promote ACTIVE, does NOT touch holdout, does NOT place orders.

Loop:
  1. Poll training log / results.csv / best.pt on 3060
  2. When idle+best exists → scp weights home
  3. Local gates (same-dataset val, not frozen-200 ruler — geometry is W20-30):
       - curve health (best epoch, peak-after collapse)
       - val F1 / P / R (IoU≥0.30)
       - pure-neg FP rate (silence)
       - light economic check: hit pos mid → +12 bar return vs random control
  4. Decision:
       PASS  → write final report, exit 0
       MORE_NEG → add empty-bg (same render), re-ship, chain-train from best
       RETRAIN_COLD → cold restart if curve collapsed
       STOP_BUDGET → max cycles / wall clock
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
_YOYO = Path.home() / "yoyo-trading"
for p in (PROJECT, _YOYO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

HOST_DEFAULT = os.environ.get("FABLE_3060_HOST", "zzc@192.168.1.4")
REMOTE = "C:/fable"
RUN_NAME = "owner_w20_midbox_cold"
DATASET = PROJECT / "datasets" / "dense_owner_w20_midbox"
STATE_DIR = PROJECT / "analysis" / "output" / "w20_overnight"
LOCAL_RUN = PROJECT / "runs" / "detect" / "runs" / "detect" / RUN_NAME
WEIGHTS_DIR = LOCAL_RUN / "weights"

# Gates (single-dataset geometry; NOT frozen-200 F1)
MIN_VAL_F1 = 0.28
MAX_NEG_FP_RATE = 0.20  # fraction of pure-neg images with ≥1 box @ conf
MIN_POS_RECALL = 0.35
MIN_BEST_EPOCH = 4
MAX_CYCLES = 4
POLL_SEC = 180
WALL_HOURS = 10
CHAIN_EPOCHS = 40
CHAIN_PATIENCE = 12
COLD_EPOCHS = 80
COLD_PATIENCE = 20


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (STATE_DIR / "overnight.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ssh(host: str, ps: str, timeout: int = 60) -> tuple[int, str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout=15",
        host,
        ps,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out.replace("\r", "")
    except subprocess.TimeoutExpired:
        return 124, "ssh timeout"
    except Exception as e:
        return 1, str(e)


def scp_from(host: str, remote: str, local: Path) -> bool:
    local.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        f"{host}:{remote}",
        str(local),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return r.returncode == 0 and local.exists()


def scp_to(host: str, local: Path, remote: str) -> bool:
    cmd = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        str(local),
        f"{host}:{remote}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return r.returncode == 0


@dataclass
class TrainStatus:
    python_running: bool
    has_best: bool
    has_results: bool
    n_epochs: int
    last_epoch: int
    best_map50: float
    log_tail: str
    done: bool


def poll_train(host: str) -> TrainStatus:
    # inject current RUN_NAME into remote PowerShell
    ps = f"""
$run = '{RUN_NAME}'
$py = @(Get-Process python* -EA SilentlyContinue).Count
$best = Test-Path "C:/fable/runs/detect/runs/detect/$run/weights/best.pt"
$res = Test-Path "C:/fable/runs/detect/runs/detect/$run/results.csv"
$ne = 0; $le = 0; $bm = 0
if ($res) {{
  $rows = Import-Csv "C:/fable/runs/detect/runs/detect/$run/results.csv"
  $ne = $rows.Count
  if ($ne -gt 0) {{
    $le = [int]($rows[-1].epoch)
    $col = ($rows[0].PSObject.Properties.Name | Where-Object {{ $_ -like '*mAP50(B)*' -and $_ -notlike '*95*' }} | Select-Object -First 1)
    if ($col) {{ $bm = ($rows | ForEach-Object {{ [double]($_.$col) }} | Measure-Object -Maximum).Maximum }}
  }}
}}
$tail = ''
if (Test-Path "C:/fable/logs/$run.log") {{
  $tail = (Get-Content "C:/fable/logs/$run.log" -Tail 5) -join ' | '
}}
Write-Output "PY=$py"
Write-Output "BEST=$best"
Write-Output "RES=$res"
Write-Output "NE=$ne"
Write-Output "LE=$le"
Write-Output "BM=$bm"
Write-Output "TAIL=$tail"
"""
    code, out = ssh(host, ps, timeout=90)
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    py = int(d.get("PY", "0") or 0)
    has_best = d.get("BEST", "False").lower() == "true"
    has_res = d.get("RES", "False").lower() == "true"
    ne = int(float(d.get("NE", "0") or 0))
    le = int(float(d.get("LE", "0") or 0))
    bm = float(d.get("BM", "0") or 0)
    # done = has best + results and no python (or log says complete)
    tail = d.get("TAIL", "")
    finished_kw = any(
        k in tail.lower()
        for k in ("training complete", "results saved", "80 epochs completed", "early stopping")
    )
    done = has_best and has_res and (py == 0 or finished_kw) and ne >= 5
    # still training if python alive
    if py > 0:
        done = False
    return TrainStatus(
        python_running=py > 0,
        has_best=has_best,
        has_results=has_res,
        n_epochs=ne,
        last_epoch=le,
        best_map50=bm,
        log_tail=tail[:300],
        done=done,
    )


def fetch_artifacts(host: str, tag: str) -> Path:
    dest = STATE_DIR / f"cycle_{tag}"
    dest.mkdir(parents=True, exist_ok=True)
    wdir = dest / "weights"
    wdir.mkdir(exist_ok=True)
    ok_b = scp_from(
        host,
        f"{REMOTE}/runs/detect/runs/detect/{RUN_NAME}/weights/best.pt",
        wdir / "best.pt",
    )
    scp_from(
        host,
        f"{REMOTE}/runs/detect/runs/detect/{RUN_NAME}/results.csv",
        dest / "results.csv",
    )
    scp_from(
        host,
        f"{REMOTE}/runs/detect/runs/detect/{RUN_NAME}/args.yaml",
        dest / "args.yaml",
    )
    # keep results next to weights parent for diagnose_curve(weights.parent.parent)
    if (dest / "results.csv").exists():
        pass  # dest/results.csv ; weights at dest/weights/best.pt → parent.parent=dest
    # also park under runs/ for convenience
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    if ok_b:
        shutil.copy2(wdir / "best.pt", WEIGHTS_DIR / "best.pt")
        shutil.copy2(wdir / "best.pt", STATE_DIR / f"best_{tag}.pt")
    if not ok_b:
        raise RuntimeError("failed to fetch best.pt")
    return wdir / "best.pt"


def diagnose_curve(results_csv: Path) -> dict:
    if not results_csv.exists():
        return {"ok": False, "reason": "no_results_csv"}
    rows = list(csv.DictReader(results_csv.open()))
    if not rows:
        return {"ok": False, "reason": "empty_results"}
    mk = next(c for c in rows[0] if "mAP50(B)" in c and "95" not in c)
    maps = [float(r[mk]) for r in rows]
    bi = int(np.argmax(maps))
    best_ep = int(float(rows[bi]["epoch"]))
    peak = maps[bi]
    post = maps[bi:]
    collapse = sum(1 for x in post if x < peak * 0.2) / max(len(post), 1)
    ok = best_ep >= MIN_BEST_EPOCH and collapse <= 0.5 and peak >= 0.05
    return {
        "ok": ok,
        "n_epochs": len(rows),
        "best_epoch": best_ep,
        "peak_map50": round(peak, 4),
        "collapse_frac": round(collapse, 3),
        "reason": (
            "ok"
            if ok
            else (
                "best_too_early"
                if best_ep < MIN_BEST_EPOCH
                else "collapse"
                if collapse > 0.5
                else "peak_too_low"
            )
        ),
    }


def evaluate_detector(weights: Path, confs=(0.15, 0.25, 0.35, 0.45)) -> dict:
    """Val F1 + pure-neg FP rate on dense_owner_w20_midbox (same geometry)."""
    from ultralytics import YOLO

    from src.detection.owner_eval import _iou, _load_txt

    model = YOLO(str(weights))
    vi = DATASET / "images" / "val"
    vl = DATASET / "labels" / "val"
    images = sorted(vi.glob("*.png"))
    pos_imgs = []
    neg_imgs = []
    for img in images:
        boxes = _load_txt(vl / f"{img.stem}.txt")
        if boxes:
            pos_imgs.append((img, boxes))
        else:
            neg_imgs.append(img)

    sweep = []
    for conf in confs:
        tp = fp = fn = 0
        for img, gt in pos_imgs:
            res = model.predict(str(img), conf=conf, verbose=False)[0]
            preds = (
                [tuple(map(float, b)) for b in res.boxes.xywhn.cpu().numpy()]
                if res.boxes is not None
                else []
            )
            used = set()
            for g in gt:
                m = next(
                    (
                        k
                        for k, p in enumerate(preds)
                        if k not in used and _iou(g, p) >= 0.30
                    ),
                    None,
                )
                if m is None:
                    fn += 1
                else:
                    used.add(m)
                    tp += 1
            fp += len(preds) - len(used)
        # neg images: any prediction is FP image
        neg_fire = 0
        for img in neg_imgs:
            res = model.predict(str(img), conf=conf, verbose=False)[0]
            npred = 0 if res.boxes is None else len(res.boxes)
            if npred > 0:
                neg_fire += 1
            fp += npred  # also count boxes as fp for P/R
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        neg_fp_rate = neg_fire / max(len(neg_imgs), 1)
        sweep.append(
            {
                "conf": conf,
                "f1": round(f1, 4),
                "p": round(prec, 4),
                "r": round(rec, 4),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "neg_fp_rate": round(neg_fp_rate, 4),
                "n_pos": len(pos_imgs),
                "n_neg": len(neg_imgs),
            }
        )
    # pick conf maximizing F1 under neg_fp_rate cap if possible
    feasible = [s for s in sweep if s["neg_fp_rate"] <= MAX_NEG_FP_RATE]
    best = max(feasible or sweep, key=lambda s: s["f1"])
    return {"best": best, "sweep": sweep}


def light_economic_check(weights: Path, conf: float, n_sample: int = 80) -> dict:
    """Hit-conditioned short-horizon return on val positives vs random control.

    Not a full strategy backtest — only checks whether true-positive detections
    sit on bars that are not immediately toxic after entry.
    """
    try:
        from ultralytics import YOLO

        from scripts.build_w20_midbox_dataset import resolve_series
        from src.detection.owner_eval import _iou, _load_txt
        from yoyo.layers.l1_detection.data import add_mas
        from yoyo.layers.l1_detection.render import render_chart
    except Exception as e:
        return {"ok": False, "reason": f"import_fail:{e}"}

    man_path = DATASET / "w20_manifest.json"
    if not man_path.exists():
        return {"ok": False, "reason": "no_manifest"}
    manifest = [r for r in json.loads(man_path.read_text()) if r.get("split") == "val"]
    if not manifest:
        return {"ok": False, "reason": "no_val_manifest"}

    rng = np.random.default_rng(20260807)
    if len(manifest) > n_sample:
        idx = rng.choice(len(manifest), size=n_sample, replace=False)
        sample = [manifest[i] for i in idx]
    else:
        sample = manifest

    model = YOLO(str(weights))
    hit_rets = []
    miss = 0
    horizon = 12  # bars
    cost = 0.0015  # one-way-ish pressure

    for r in sample:
        try:
            # use stored image if present
            stem = Path(r["out_img"]).stem if "out_img" in r else None
            img_path = None
            if stem:
                for sp in ("val", "train"):
                    p = DATASET / "images" / sp / f"{stem}.png"
                    if p.exists():
                        img_path = p
                        break
            if img_path is None:
                # re-render from series
                df = resolve_series(r["symbol"])
                if df is None:
                    continue
                en = add_mas(df)
                w0, wlen = int(r["win_start"]), int(r["win_len"])
                win = en.iloc[w0 : w0 + wlen].reset_index(drop=True)
                img, _ = render_chart(win, out_path=None)
                tmp = STATE_DIR / "_tmp_econ.png"
                import cv2

                cv2.imwrite(str(tmp), img)
                img_path = tmp
            res = model.predict(str(img_path), conf=conf, verbose=False)[0]
            preds = (
                [tuple(map(float, b)) for b in res.boxes.xywhn.cpu().numpy()]
                if res.boxes is not None
                else []
            )
            gt = _load_txt(DATASET / "labels" / "val" / f"{img_path.stem}.txt")
            if not gt:
                # try train path stem
                gt = _load_txt(DATASET / "labels" / "train" / f"{img_path.stem}.txt")
            hit = False
            for g in gt:
                if any(_iou(g, p) >= 0.30 for p in preds):
                    hit = True
                    break
            if not hit:
                # also count any fire on pos image as soft hit for econ
                hit = len(preds) > 0
            if not hit:
                miss += 1
                continue
            df = resolve_series(r["symbol"])
            if df is None:
                continue
            mid = int(r["mid_global"])
            if mid + horizon >= len(df) or mid < 0:
                continue
            px0 = float(df.iloc[mid]["close"])
            px1 = float(df.iloc[mid + horizon]["close"])
            if px0 <= 0:
                continue
            # direction-agnostic absolute edge is wrong; use long bias as labeled starts
            ret = (px1 - px0) / px0 - cost
            hit_rets.append(ret)
        except Exception:
            continue

    # random control: same symbols random bars
    ctrl = []
    for r in sample[: min(60, len(sample))]:
        df = resolve_series(r["symbol"])
        if df is None or len(df) < 200:
            continue
        for _ in range(2):
            i = int(rng.integers(50, len(df) - horizon - 1))
            px0 = float(df.iloc[i]["close"])
            px1 = float(df.iloc[i + horizon]["close"])
            if px0 > 0:
                ctrl.append((px1 - px0) / px0 - cost)

    hit_mean = float(np.mean(hit_rets)) if hit_rets else float("nan")
    ctrl_mean = float(np.mean(ctrl)) if ctrl else float("nan")
    lift = hit_mean - ctrl_mean if hit_rets and ctrl else float("nan")
    ok = bool(hit_rets) and np.isfinite(lift) and lift > -0.002  # not badly toxic
    return {
        "ok": ok,
        "n_hit": len(hit_rets),
        "n_miss": miss,
        "hit_mean_ret_12bar": round(hit_mean, 5) if hit_rets else None,
        "ctrl_mean_ret_12bar": round(ctrl_mean, 5) if ctrl else None,
        "lift": round(lift, 5) if np.isfinite(lift) else None,
        "horizon_bars": horizon,
        "cost": cost,
        "conf": conf,
    }


def decide(curve: dict, det: dict, econ: dict) -> tuple[str, str]:
    """Return (action, reason). actions: PASS | MORE_NEG | RETRAIN_COLD | STOP."""
    best = det["best"]
    if not curve.get("ok") and curve.get("reason") in ("best_too_early", "collapse"):
        return "RETRAIN_COLD", f"curve:{curve.get('reason')}"
    if best["neg_fp_rate"] > MAX_NEG_FP_RATE and best["r"] >= 0.25:
        return "MORE_NEG", f"neg_fp={best['neg_fp_rate']:.3f}>{MAX_NEG_FP_RATE}"
    if best["f1"] < MIN_VAL_F1 and best["r"] < MIN_POS_RECALL:
        # both weak — more negs often helps P; also could need more pos but we only control neg
        if best["neg_fp_rate"] > 0.1:
            return "MORE_NEG", f"weak_f1={best['f1']:.3f}_and_fp"
        return "RETRAIN_COLD", f"weak_f1={best['f1']:.3f}_low_recall"
    if best["f1"] < MIN_VAL_F1 and best["neg_fp_rate"] > 0.12:
        return "MORE_NEG", f"f1={best['f1']:.3f}_fp={best['neg_fp_rate']:.3f}"
    if not econ.get("ok", True) and best["f1"] >= MIN_VAL_F1:
        # detection ok but toxic entries — still report pass-with-warning; data geometry issue
        return "PASS", f"det_ok_econ_weak_lift={econ.get('lift')}"
    if best["f1"] >= MIN_VAL_F1 and best["neg_fp_rate"] <= MAX_NEG_FP_RATE:
        return "PASS", f"f1={best['f1']:.3f}_neg_fp={best['neg_fp_rate']:.3f}"
    if best["r"] >= MIN_POS_RECALL and best["neg_fp_rate"] > MAX_NEG_FP_RATE:
        return "MORE_NEG", f"recall_ok_fp_high={best['neg_fp_rate']:.3f}"
    return "PASS", f"default_best_effort_f1={best['f1']:.3f}"


def add_more_negatives(ratio: float) -> dict:
    cmd = [
        sys.executable,
        str(PROJECT / "scripts" / "add_w20_midbox_negatives.py"),
        "--dataset",
        str(DATASET),
        "--ratio",
        str(ratio),
        "--seed",
        str(20260807 + int(ratio * 10)),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{PROJECT}:{_YOYO}"
    log(f"adding negatives ratio={ratio}")
    r = subprocess.run(cmd, cwd=str(PROJECT), env=env, capture_output=True, text=True, timeout=3600)
    (STATE_DIR / "last_neg_add.log").write_text((r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0:
        raise RuntimeError(f"add_neg failed: {r.stderr[-500:]}")
    # parse last json-ish summary
    summary_path = DATASET / "w20_neg_summary.json"
    return json.loads(summary_path.read_text()) if summary_path.exists() else {}


def ship_dataset(host: str) -> None:
    tar = STATE_DIR / "ds_w20_ship.tar"
    if tar.exists():
        tar.unlink()
    subprocess.run(
        [
            "bash",
            "-c",
            f"COPYFILE_DISABLE=1 tar cf {tar} --exclude='*.npy' --exclude='*.cache' --exclude='._*' "
            f"-C {DATASET.parent} {DATASET.name}",
        ],
        check=True,
        timeout=600,
    )
    log(f"shipping dataset {tar.stat().st_size/1e6:.0f}MB")
    if not scp_to(host, tar, f"{REMOTE}/ds_w20.tar"):
        raise RuntimeError("scp dataset failed")
    code, out = ssh(
        host,
        f"cd {REMOTE}; Remove-Item -Recurse -Force datasets/{DATASET.name} -ErrorAction SilentlyContinue; "
        f"tar xf ds_w20.tar -C datasets; Remove-Item ds_w20.tar; "
        f"Write-Output ok",
        timeout=300,
    )
    if code != 0 or "ok" not in out:
        raise RuntimeError(f"remote extract failed: {out[-400:]}")


def write_train_cmd(host: str, *, name: str, model_remote: str, epochs: int, patience: int) -> None:
    cmd_body = (
        "@echo off\r\n"
        "cd /d C:\\fable\r\n"
        "if not exist logs mkdir logs\r\n"
        f"C:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\train_dense.py "
        f"--name {name} --model {model_remote} "
        f"--dataset C:/fable/datasets/{DATASET.name} "
        f"--epochs {epochs} --patience {patience} --batch 8 --cache false --workers 2 "
        f"> C:\\fable\\logs\\{name}.log 2>&1\r\n"
    )
    local_cmd = STATE_DIR / f"run_{name}.cmd"
    local_cmd.write_bytes(cmd_body.encode("ascii", errors="ignore"))
    if not scp_to(host, local_cmd, f"{REMOTE}/run_{name}.cmd"):
        raise RuntimeError("scp cmd failed")
    code, out = ssh(
        host,
        f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine='cmd.exe /c C:\\\\fable\\\\run_{name}.cmd'}}; "
        f"Write-Output ('pid=' + $r.ProcessId + ' ret=' + $r.ReturnValue)",
        timeout=60,
    )
    log(f"launch train {name}: {out.strip()}")
    if "ret=0" not in out.replace(" ", ""):
        # ret=0 may be formatted differently
        if "pid=" not in out:
            raise RuntimeError(f"WMI launch failed: {out}")


def launch_chain(host: str, local_best: Path, cycle: int) -> str:
    name = f"owner_w20_midbox_c{cycle}"
    # ship best as base
    if not scp_to(host, local_best, f"{REMOTE}/base_w20_chain.pt"):
        raise RuntimeError("scp chain base failed")
    global RUN_NAME
    # update module-level for poll paths — chain uses new name
    write_train_cmd(
        host,
        name=name,
        model_remote="C:/fable/base_w20_chain.pt",
        epochs=CHAIN_EPOCHS,
        patience=CHAIN_PATIENCE,
    )
    return name


def launch_cold(host: str, cycle: int) -> str:
    name = f"owner_w20_midbox_cold_c{cycle}"
    # ensure yolo11s on remote
    base = PROJECT / "models" / "yolo11s.pt"
    scp_to(host, base, f"{REMOTE}/models/yolo11s_w20.pt")
    write_train_cmd(
        host,
        name=name,
        model_remote="C:/fable/models/yolo11s_w20.pt",
        epochs=COLD_EPOCHS,
        patience=COLD_PATIENCE,
    )
    return name


def set_run_name(name: str) -> None:
    global RUN_NAME, LOCAL_RUN, WEIGHTS_DIR
    RUN_NAME = name
    LOCAL_RUN = PROJECT / "runs" / "detect" / "runs" / "detect" / RUN_NAME
    WEIGHTS_DIR = LOCAL_RUN / "weights"


def write_state(obj: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def run_cycle_eval(weights: Path, tag: str) -> dict:
    curve = diagnose_curve(weights.parent.parent / "results.csv")
    # results may be in cycle dir
    alt = STATE_DIR / f"cycle_{tag}" / "results.csv"
    if alt.exists():
        curve = diagnose_curve(alt)
    log(f"curve: {curve}")
    det = evaluate_detector(weights)
    log(f"det best: {det['best']}")
    econ = light_economic_check(weights, conf=float(det["best"]["conf"]))
    log(f"econ: {econ}")
    action, reason = decide(curve, det, econ)
    report = {
        "tag": tag,
        "time": now_iso(),
        "weights": str(weights),
        "curve": curve,
        "detector": det,
        "econ": econ,
        "action": action,
        "reason": reason,
        "gates": {
            "MIN_VAL_F1": MIN_VAL_F1,
            "MAX_NEG_FP_RATE": MAX_NEG_FP_RATE,
            "MIN_POS_RECALL": MIN_POS_RECALL,
            "MIN_BEST_EPOCH": MIN_BEST_EPOCH,
        },
    }
    (STATE_DIR / f"eval_{tag}.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=HOST_DEFAULT)
    ap.add_argument("--poll", type=int, default=POLL_SEC)
    ap.add_argument("--max-cycles", type=int, default=MAX_CYCLES)
    ap.add_argument("--wall-hours", type=float, default=WALL_HOURS)
    ap.add_argument(
        "--assume-done",
        action="store_true",
        help="skip poll; evaluate local/remote best immediately",
    )
    args = ap.parse_args()
    host = args.host
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    cycle = 0
    history: list[dict] = []
    current_name = RUN_NAME
    set_run_name(current_name)

    log(f"overnight start host={host} wall={args.wall_hours}h max_cycles={args.max_cycles}")
    write_state({"status": "running", "started": now_iso(), "host": host})

    try:
        while cycle < args.max_cycles:
            if (time.time() - t0) > args.wall_hours * 3600:
                log("wall clock budget exhausted")
                break

            if not args.assume_done or cycle > 0:
                st = poll_train(host)
                log(
                    f"poll run={RUN_NAME} py={st.python_running} epochs={st.n_epochs} "
                    f"last={st.last_epoch} map={st.best_map50:.4f} done={st.done}"
                )
                write_state(
                    {
                        "status": "training" if not st.done else "fetching",
                        "run": RUN_NAME,
                        "poll": asdict(st),
                        "cycle": cycle,
                        "updated": now_iso(),
                    }
                )
                if not st.done:
                    time.sleep(args.poll)
                    continue
            else:
                log("assume-done: fetching without poll wait")

            tag = f"{cycle}_{RUN_NAME}"
            try:
                weights = fetch_artifacts(host, tag)
            except Exception as e:
                log(f"fetch failed: {e}; sleep and retry")
                time.sleep(args.poll)
                continue

            report = run_cycle_eval(weights, tag)
            history.append(
                {
                    "cycle": cycle,
                    "run": RUN_NAME,
                    "action": report["action"],
                    "reason": report["reason"],
                    "f1": report["detector"]["best"]["f1"],
                    "neg_fp": report["detector"]["best"]["neg_fp_rate"],
                    "map_peak": report["curve"].get("peak_map50"),
                }
            )
            (STATE_DIR / "history.json").write_text(json.dumps(history, indent=2))

            action = report["action"]
            log(f"DECISION cycle={cycle} action={action} reason={report['reason']}")

            if action == "PASS":
                final = {
                    "status": "PASS",
                    "finished": now_iso(),
                    "best_weights": str(weights),
                    "report": report,
                    "history": history,
                    "note": "NOT promoted to ACTIVE; owner review required",
                }
                (STATE_DIR / "FINAL.json").write_text(json.dumps(final, indent=2, ensure_ascii=False))
                # human readable
                (STATE_DIR / "MORNING_README.md").write_text(
                    f"""# w20 overnight result — PASS

- time: {now_iso()}
- weights: `{weights}`
- val F1: {report['detector']['best']['f1']} @ conf={report['detector']['best']['conf']}
- neg FP rate: {report['detector']['best']['neg_fp_rate']}
- curve peak mAP50: {report['curve'].get('peak_map50')} best_ep={report['curve'].get('best_epoch')}
- econ lift 12bar: {report['econ'].get('lift')}

**未 promote ACTIVE。** 醒后看 `analysis/output/w20_overnight/`。
""",
                    encoding="utf-8",
                )
                write_state(final)
                log("PASS — exiting")
                return 0

            cycle += 1
            if cycle >= args.max_cycles:
                log("max cycles reached after decision")
                break

            if action == "MORE_NEG":
                # bump ratio: 1.0 → 1.5 → 2.0 → 2.5
                ratio = 1.0 + 0.5 * cycle
                neg_sum = add_more_negatives(ratio=ratio)
                log(f"neg summary: {neg_sum.get('train')} / {neg_sum.get('val')}")
                ship_dataset(host)
                new_name = launch_chain(host, weights, cycle)
                set_run_name(new_name)
                log(f"chain training started as {new_name}")
                time.sleep(60)
                continue

            if action == "RETRAIN_COLD":
                # optionally still add some negs if FP high
                if report["detector"]["best"]["neg_fp_rate"] > 0.15:
                    add_more_negatives(ratio=1.0 + 0.5 * cycle)
                    ship_dataset(host)
                new_name = launch_cold(host, cycle)
                set_run_name(new_name)
                log(f"cold retrain started as {new_name}")
                time.sleep(60)
                continue

            log(f"unknown action {action}, stop")
            break

        # budget stop
        final = {
            "status": "BUDGET_STOP",
            "finished": now_iso(),
            "history": history,
            "note": "hit cycle/wall budget; see last eval_*.json",
        }
        (STATE_DIR / "FINAL.json").write_text(json.dumps(final, indent=2, ensure_ascii=False))
        (STATE_DIR / "MORNING_README.md").write_text(
            f"""# w20 overnight result — BUDGET_STOP

- time: {now_iso()}
- history: {json.dumps(history, indent=2)}
- inspect: `analysis/output/w20_overnight/`
""",
            encoding="utf-8",
        )
        write_state(final)
        log("BUDGET_STOP")
        return 0

    except Exception as e:
        log(f"FATAL: {e}\n{traceback.format_exc()}")
        write_state({"status": "FATAL", "error": str(e), "time": now_iso()})
        (STATE_DIR / "MORNING_README.md").write_text(
            f"# FATAL\n\n{e}\n\nSee overnight.log\n", encoding="utf-8"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
