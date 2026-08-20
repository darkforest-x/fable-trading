"""Estimate the fixed W10 gold set's label error rate. Inference only.

Protocol 17.6 wants a DIRECT spot-check error rate before the gold set may be
called training-eligible. The migration produced DIRECT=0, so the number has
never existed, and it is the only gate the set has not passed.

This estimates it with confident learning instead of guessing it from a sample:
run the already-trained classifier over the held-out splits, hand cleanlab the
(label, out-of-sample probability) pairs, and let it estimate the joint
distribution of noisy and true labels.

Two things this deliberately does NOT do:

  train        the weights are the frozen 2026-08-13 checkpoint retrieved from
               the 3060 before its reinstall; its sha256 is asserted against the
               value backtest_fixed_w10_cls_holdout3d.py already pins
  promote      it prints a number. Whether that number clears protocol 17.6 is
               an owner decision, and nothing here writes training_eligible.

val and test are reported SEPARATELY and never pooled. The run early-stopped on
val, so the model saw those labels through model selection; only test is
untouched. Pooling them would inflate the sample and quietly contaminate the
estimate with the split the model was tuned against.

Preprocessing is WhiteLetterbox(960) + ToTensor, copied from the training script
because the checkpoint pickles a reference to that class. Getting it wrong would
measure a preprocessing mismatch and report it as label noise.

Two stages, in two virtualenvs, because ultralytics needs numpy 2.x and cleanlab
declares numpy<2. They cannot share an environment, so the probabilities are
written to disk between them. That is the better shape anyway: the audit can be
re-run, or run with a different tool, without re-running inference, and the
probabilities become a citable artifact rather than an intermediate value.

    # stage 1 -- torch + ultralytics, numpy 2.x
    /tmp/fable_infer_venv/bin/python tools/datasets/score_gold_label_quality.py \
        --stage predict \
        --dataset-root ~/fable-trading/datasets/fixed_w10_core4_confirm1_v1/classification \
        --weights ~/fable-trading/analysis/output/fixed_w10_cls_holdout3d_20260813/best.pt \
        --out experiments/active/exp-p1-gold-label-quality-cleanlab-v1

    # stage 2 -- cleanlab, numpy 1.x
    /tmp/fable_eval_venv/bin/python tools/datasets/score_gold_label_quality.py \
        --stage audit \
        --out experiments/active/exp-p1-gold-label-quality-cleanlab-v1
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from PIL import Image


# The 3060 trainer pickled this into best.pt, so it has to exist on __main__.
# Byte-for-byte the class from scripts/train_fixed_w10_cls.py -- a different
# resize or fill colour would change every probability below.
class WhiteLetterbox:
    """Pad with renderer white, keep aspect, no crop."""

    def __init__(self, size: int, fill: int = 255):
        self.size = int(size)
        self.fill = fill

    def __call__(self, img):
        w, h = img.size
        scale = self.size / max(w, h)
        nw = max(1, round(w * scale))
        nh = max(1, round(h * scale))
        img = img.resize((nw, nh), Image.BILINEAR)
        canvas = Image.new("RGB", (self.size, self.size), (self.fill, self.fill, self.fill))
        if img.mode != "RGB":
            img = img.convert("RGB")
        canvas.paste(img, ((self.size - nw) // 2, (self.size - nh) // 2))
        return canvas


EXPECTED_WEIGHTS_SHA256 = "18bcb5988e6dd36bdf2fc8a1a22d3ad66ab78b777a1d02c88080c937e98d0541"
IMGSZ = 960
CLASSES = ("NO_SIGNAL", "SIGNAL")

#: val early-stopped this run, so its labels reached the model through model
#: selection. Only test is untouched. Never pooled.
SPLIT_STATUS = {
    "val": "used_for_early_stopping",
    "test": "never_evaluated_until_now",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect(split_dir: Path) -> List[Dict[str, object]]:
    rows = []
    for index, name in enumerate(CLASSES):
        class_dir = split_dir / name
        if not class_dir.is_dir():
            raise SystemExit(f"missing class directory: {class_dir}")
        for image in sorted(class_dir.glob("*.png")):
            rows.append({"path": image, "label_name": name, "label": index})
    if not rows:
        raise SystemExit(f"no images under {split_dir}")
    return rows


def predict(rows, weights: Path, device: str, batch: int):
    import numpy as np
    import torch
    import torchvision.transforms as transforms
    from ultralytics import YOLO

    model = YOLO(str(weights), task="classify")
    net = model.model.to(device).eval()
    names = getattr(model.model, "names", None) or getattr(model, "names", {})
    # The checkpoint's own class order decides which column is SIGNAL. Assuming
    # it matches CLASSES would silently invert the audit if the trainer differed.
    order = [str(names[i]) for i in sorted(names)] if isinstance(names, dict) else [str(n) for n in names]
    if order != list(CLASSES):
        raise SystemExit(
            f"checkpoint class order is {order}, expected {list(CLASSES)}; the "
            "probability columns would be mislabelled"
        )

    transform = transforms.Compose([WhiteLetterbox(IMGSZ), transforms.ToTensor()])
    out = np.zeros((len(rows), len(CLASSES)), dtype=np.float64)
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        tensors = [transform(Image.open(r["path"]).convert("RGB")) for r in chunk]
        stacked = torch.stack(tensors).to(device)
        with torch.no_grad():
            logits = net(stacked)
            if isinstance(logits, (list, tuple)):
                logits = logits[0]
            probs = torch.softmax(logits.float(), dim=1).detach().cpu().numpy()
        out[start : start + len(chunk)] = probs
        print(f"  scored {min(start + batch, len(rows))}/{len(rows)}", flush=True)
    return out


def audit(labels, probs) -> Dict[str, object]:
    import numpy as np
    from cleanlab.filter import find_label_issues
    from cleanlab.rank import get_label_quality_scores

    issues = find_label_issues(
        labels=labels, pred_probs=probs, return_indices_ranked_by="self_confidence"
    )
    scores = get_label_quality_scores(labels=labels, pred_probs=probs)
    predicted = probs.argmax(axis=1)
    flagged = np.isin(np.arange(len(labels)), issues)
    return {
        "n": int(len(labels)),
        "n_flagged": int(len(issues)),
        "estimated_label_error_rate": round(float(len(issues) / len(labels)), 6),
        "model_accuracy_on_given_labels": round(float((predicted == labels).mean()), 6),
        "label_quality_score_mean": round(float(scores.mean()), 6),
        "label_quality_score_p05": round(float(np.percentile(scores, 5)), 6),
        "flagged_indices": [int(i) for i in issues],
        "per_class_flagged": {
            CLASSES[c]: int(((labels == c) & flagged).sum()) for c in range(len(CLASSES))
        },
        "per_class_n": {CLASSES[c]: int((labels == c).sum()) for c in range(len(CLASSES))},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=["predict", "audit", "null-control"], required=True)
    ap.add_argument("--dataset-root")
    ap.add_argument("--weights")
    ap.add_argument("--out", required=True)
    ap.add_argument("--splits", nargs="+", default=["val", "test"])
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.stage == "audit":
        return run_audit(out_dir, args.splits)
    if args.stage == "null-control":
        return run_null_control(out_dir, args.splits)

    import numpy as np
    import torch

    if not (args.dataset_root and args.weights):
        raise SystemExit("--stage predict needs --dataset-root and --weights")
    weights = Path(args.weights).expanduser()
    actual = sha256_file(weights)
    if actual != EXPECTED_WEIGHTS_SHA256:
        raise SystemExit(
            f"weights sha256 {actual} != expected {EXPECTED_WEIGHTS_SHA256}. This is "
            "not the frozen 2026-08-13 checkpoint; every number below would describe "
            "a different model."
        )

    device = args.device or (
        "cuda:0"
        if torch.cuda.is_available()
        else ("mps" if torch.backends.mps.is_available() else "cpu")
    )
    root = Path(args.dataset_root).expanduser()
    for split in args.splits:
        print(f"[{split}] {SPLIT_STATUS.get(split, 'unknown status')}")
        rows = collect(root / split)
        probs = predict(rows, weights, device, args.batch)
        target = out_dir / f"probabilities_{split}.jsonl"
        with target.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "_meta": {
                            "split": split,
                            "split_status": SPLIT_STATUS.get(split, "unknown"),
                            "out_of_sample": split == "test",
                            "weights_sha256": actual,
                            "device": device,
                            "imgsz": IMGSZ,
                            "classes": list(CLASSES),
                            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "holdout_read": False,
                            "training_performed": False,
                        }
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            for i, row in enumerate(rows):
                handle.write(
                    json.dumps(
                        {
                            "image": str(Path(row["path"]).relative_to(root)),
                            "given_label": row["label_name"],
                            "label": int(row["label"]),
                            "probs": [round(float(x), 8) for x in probs[i]],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(f"[{split}] wrote {target} ({len(rows)} rows)")
    print("\nstage 1 done. Now run --stage audit in the cleanlab venv.")
    return 0


def run_null_control(out_dir: Path, splits) -> int:
    """What would this flag if the labels carried no information at all?

    Without it, "6.2% flagged" has no scale. Shuffling the labels against the
    same probabilities gives the ceiling the method produces on pure noise, and
    the real number is only meaningful as a fraction of that.
    """
    import numpy as np
    from cleanlab.filter import find_label_issues

    out = {}
    for split in splits:
        source = out_dir / f"probabilities_{split}.jsonl"
        if not source.is_file():
            raise SystemExit(f"{source} missing -- run --stage predict first")
        rows = [json.loads(l) for l in source.read_text(encoding="utf-8").splitlines() if l.strip()][1:]
        labels = np.array([r["label"] for r in rows], dtype=int)
        probs = np.array([r["probs"] for r in rows], dtype=np.float64)

        # n_jobs=1: cleanlab's multiprocessing cannot re-import a __main__ that
        # was piped in, and the failure is an unrelated-looking FileNotFoundError.
        real = len(
            find_label_issues(
                labels=labels, pred_probs=probs,
                return_indices_ranked_by="self_confidence", n_jobs=1,
            )
        )
        rng = np.random.default_rng(20260820)
        shuffled = []
        for _ in range(10):
            noise = labels.copy()
            rng.shuffle(noise)
            shuffled.append(
                len(
                    find_label_issues(
                        labels=noise, pred_probs=probs,
                        return_indices_ranked_by="self_confidence", n_jobs=1,
                    )
                )
            )
        shuffled = np.array(shuffled)
        disagreements = int((probs.argmax(1) != labels).sum())
        out[split] = {
            "n": int(len(labels)),
            "model_disagreements": disagreements,
            "flagged_real_labels": real,
            "flagged_shuffled_mean": float(shuffled.mean()),
            "flagged_shuffled_min": int(shuffled.min()),
            "flagged_shuffled_max": int(shuffled.max()),
            "ratio_real_over_shuffled": round(float(real / shuffled.mean()), 4),
            "n_shuffles": 10,
        }
        print(
            f"[{split}] real={real} ({real/len(labels):.2%})  "
            f"shuffled={shuffled.mean():.1f} ({shuffled.mean()/len(labels):.1%})  "
            f"ratio={real/shuffled.mean():.3f}  model_disagreements={disagreements}"
        )
    (out_dir / "null_control.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {out_dir / 'null_control.json'}")
    return 0


def run_audit(out_dir: Path, splits) -> int:
    """Stage 2: read the probabilities and estimate the label error rate."""
    import numpy as np

    results = {}
    meta_all = {}
    for split in splits:
        source = out_dir / f"probabilities_{split}.jsonl"
        if not source.is_file():
            raise SystemExit(f"{source} missing -- run --stage predict first")
        lines = [json.loads(l) for l in source.read_text(encoding="utf-8").splitlines() if l.strip()]
        meta = lines[0]["_meta"]
        rows = lines[1:]
        labels = np.array([r["label"] for r in rows], dtype=int)
        probs = np.array([r["probs"] for r in rows], dtype=np.float64)

        summary = audit(labels, probs)
        summary["split_status"] = meta["split_status"]
        summary["out_of_sample"] = meta["out_of_sample"]
        results[split] = summary
        meta_all[split] = meta

        flagged = set(summary["flagged_indices"])
        with (out_dir / f"per_image_{split}.jsonl").open("w", encoding="utf-8") as handle:
            for i, row in enumerate(rows):
                handle.write(
                    json.dumps(
                        {
                            "image": row["image"],
                            "given_label": row["given_label"],
                            "p_signal": round(row["probs"][CLASSES.index("SIGNAL")], 6),
                            "flagged_by_cleanlab": i in flagged,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(
            f"[{split}] n={summary['n']} flagged={summary['n_flagged']} "
            f"error_rate={summary['estimated_label_error_rate']:.4f} "
            f"acc={summary['model_accuracy_on_given_labels']:.4f} "
            f"({summary['split_status']})"
        )

    payload = {
        "schema_version": 1,
        "experiment_id": "exp-p1-gold-label-quality-cleanlab-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/datasets/score_gold_label_quality.py --stage audit",
        "inference_meta": meta_all,
        "holdout_read": False,
        "training_performed": False,
        "training_eligible_changed": False,
        "note": (
            "val and test are never pooled: the run early-stopped on val, so only "
            "test is genuinely out of sample. The headline number is test's."
        ),
        "splits": results,
    }
    (out_dir / "label_quality.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {out_dir / 'label_quality.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
