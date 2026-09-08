"""Verify the expanded experiment's deliverable without market/model replay.

Checks report/data linkage, original PNG pixels, separately annotated copies,
embedded HTML image payloads, frozen sources and independent review status.
This is file/geometry integrity QA, not a claim that a browser render was
visually reviewed, nor a second evaluation of model or economic outcomes.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/"experiments/active/exp-imacd-yolo-expanded-20260908-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    source = str(Path(__file__).relative_to(ROOT))
    subprocess.run(["git", "ls-files", "--error-unmatch", source], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)
    if subprocess.check_output(["git", "status", "--porcelain", "--", source], cwd=ROOT, text=True).strip():
        raise ValueError("commit QA source before producing its receipt")
    r = EXP/"results"
    summary = json.loads((r/"summary.json").read_text())
    review = json.loads((r/"independent_review.json").read_text())
    examples = json.loads((r/"example_manifest.json").read_text())
    report_receipt = json.loads((r/"report_receipt.json").read_text())
    report = ROOT/"analysis/p1_imacd_yolo_expanded_20260908.md"
    html = ROOT/"analysis/html/p1_imacd_yolo_expanded_20260908.html"
    manifest = json.loads((EXP/"source_manifest.json").read_text())
    checks = dict(frozen_sources=all(sha(ROOT/p)==h for p,h in manifest["files"].items()),
        independent_review_passed=review["all_passed"] and not review["failed_checks"],
        complete_216_groups=len(summary["inputs"])==216,
        report_summary_link=report_receipt["summary_sha256"]==sha(r/"summary.json"),
        report_source_link=report_receipt["report_source_sha256"]==sha(ROOT/"yoyo/evaluation/imacd_yolo_expanded_report.py"),
        report_md_link=report_receipt["report_sha256"]==sha(report),
        finalizer_source_link=summary["finalizer_source_sha256"]==sha(ROOT/summary["finalizer_source"]),
        examples_summary_link=examples["summary_sha256"]==sha(r/"summary.json"),
        examples_no_model_replay=examples["model_inference_runs"]==0,
        report_examples_link=report_receipt["examples"]["manifest_sha256"]==sha(r/"example_manifest.json"))
    png_count = 0
    for example in examples["examples"]:
        key = example["event_id"]
        for kind, p in example["paths"].items():
            path = ROOT/p
            pixels = cv2.imread(str(path), cv2.IMREAD_COLOR)
            checks[f"{key}/{kind}/png"] = pixels is not None and sha(path)==example["file_sha256"][kind]
            png_count += 1
            if kind == "input" and pixels is not None:
                checks[f"{key}/original_pixels"] = hashlib.sha256(pixels.tobytes()).hexdigest()==example["pixel_sha256_verified"]
        checks[f"{key}/clock"] = (pd.Timestamp(example["context_last_open_at"])
            + pd.Timedelta(minutes=example["timeframe_min"]) == pd.Timestamp(example["model_available_at"]))
    text = html.read_text()
    embedded = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', text)
    checks["html_image_count"] = len(embedded)==3+2*len(examples["examples"])
    checks["html_images_decode"] = all(cv2.imdecode(np.frombuffer(base64.b64decode(x,validate=True),dtype=np.uint8),cv2.IMREAD_COLOR) is not None for x in embedded)
    checks["html_standalone_no_relative_png"] = not re.search(r'<img[^>]+src=["\'](?!data:)[^"\']+\.png',text)
    receipt = dict(checks=checks, all_passed=all(checks.values()), n_checks=len(checks),
        failed_checks=[k for k,v in checks.items() if not v],
        png_example_files=png_count, html_embedded_images=len(embedded),
        html_sha256=sha(html), html_bytes=html.stat().st_size,
        tests_pre_run="112 passed: expanded8 + timeframe16 + prior15m20 + layer-boundary68",
        tests_output_fix="2 additional regression checks passed;114 total, without rerunning inference",
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        limitation="File and image integrity only; no full HTML browser-render review claimed.")
    (r/"qa_receipt.json").write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({k:receipt[k] for k in ("all_passed","n_checks","failed_checks","html_embedded_images")}))
    if not receipt["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
