#!/usr/bin/env python3
"""Render immutable diagnostics for the L2 reference-augmentation experiment.

Inputs are the completed experiment receipts and scored pre-holdout validation
ledger.  The charts never participate in fitting, threshold selection, or
evaluation.  Candlestick examples show only bars available at the frozen L1
decision time; the outcome text is added outside the market plot after scoring.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any, Mapping

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_global_context import (
    normalize_ohlcv,
    render_global_chart,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-reference-augmentation-v1"
RESULTS_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_reference_augmentation_v1"
SNAPSHOT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_global_context_v1" / "snapshot"
GROUPS = (
    ("kept_winner", True, 1),
    ("kept_loser", True, 0),
    ("dropped_winner", False, 1),
)


class DiagnosticError(RuntimeError):
    """Fail closed when a declared experiment artifact has drifted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repo_path(value: object) -> Path:
    path = (ROOT / str(value)).resolve()
    path.relative_to(ROOT.resolve())
    return path


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def require_hash(path: Path, expected: str, label: str) -> None:
    observed = sha256_file(path)
    if observed != expected:
        raise DiagnosticError(f"{label} hash drifted: {observed} != {expected}")


def bool_series(series: pd.Series) -> pd.Series:
    mapped = series.astype(str).str.lower().map({"true": True, "false": False})
    if mapped.isna().any():
        raise DiagnosticError("boolean column contains unexpected values")
    return mapped.astype(bool)


def economic_rates(reference: pd.DataFrame) -> pd.DataFrame:
    return (
        reference.groupby(["side", "reference_morphology_kind"], as_index=False)
        .agg(n=("episode_id", "size"), tp_rate=("label", "mean"), net_mean=("net_ret", "mean"))
        .sort_values(["side", "reference_morphology_kind"])
    )


def standardized_feature_shift(representatives: pd.DataFrame) -> pd.DataFrame:
    features = (
        "ma_spread_pct",
        "full_spread",
        "dense_frac48",
        "atr_pct",
        "pre_range168",
        "ret_48",
    )
    rows: list[dict[str, Any]] = []
    for side in ("long", "short"):
        subset = representatives[representatives["side"] == side]
        for feature in features:
            values = pd.to_numeric(subset[feature], errors="raise")
            scale = float(values.quantile(0.75) - values.quantile(0.25))
            if scale <= 0:
                raise DiagnosticError(f"non-positive pooled IQR: {side}/{feature}")
            medians = subset.groupby("event_source")[feature].median()
            rows.append(
                {
                    "side": side,
                    "feature": feature,
                    "standardized_median_shift": float(
                        (medians["reference_window"] - medians["real_l1"]) / scale
                    ),
                }
            )
    return pd.DataFrame(rows)


def plot_diagnostics(
    dataset: Mapping[str, Any],
    training: Mapping[str, Any],
    reference: pd.DataFrame,
    representatives: pd.DataFrame,
    output: Path,
) -> None:
    rates = economic_rates(reference)
    shifts = standardized_feature_shift(representatives)
    base = training["baseline_metrics"]
    aug = training["augmented_metrics"]
    one = training["augmented_single_feature_metrics"]

    fig, axes = plt.subplots(2, 2, figsize=(18, 11), constrained_layout=True)
    ax = axes[0, 0]
    funnel_labels = ("Manifest images", "Eligible windows", "Economic rows", "Independent train blocks")
    funnel_values = (
        dataset["reference"]["manifest_rows"],
        dataset["reference"]["eligible_manifest_rows"],
        dataset["reference"]["economic_rows"],
        dataset["augmented_train_representatives"],
    )
    bars = ax.bar(funnel_labels, funnel_values, color=("#64748b", "#3b82f6", "#0ea5e9", "#14b8a6"))
    ax.bar_label(bars, labels=[f"{int(value):,}" for value in funnel_values], padding=3)
    ax.set_title("Image/window lineage to independent L2 rows")
    ax.tick_params(axis="x", rotation=12)
    ax.set_ylabel("Count")

    ax = axes[0, 1]
    x = np.arange(2)
    width = 0.34
    for offset, kind, color in ((-width / 2, "positive", "#10b981"), (width / 2, "negative", "#f97316")):
        values = [
            float(rates[(rates["side"] == side) & (rates["reference_morphology_kind"] == kind)]["tp_rate"].iloc[0])
            * 100
            for side in ("long", "short")
        ]
        bars = ax.bar(x + offset, values, width, label=f"morphology {kind}", color=color)
        ax.bar_label(bars, labels=[f"{value:.1f}%" for value in values], padding=3)
    ax.set_xticks(x, ("LONG", "SHORT"))
    ax.set_ylabel("Economic TP rate")
    ax.set_title("Morphology label is not the economic target")
    ax.legend(frameon=False)
    ax.set_ylim(0, max(35, ax.get_ylim()[1]))

    ax = axes[1, 0]
    features = shifts["feature"].drop_duplicates().tolist()
    x = np.arange(len(features))
    for offset, side, color in ((-width / 2, "long", "#2563eb"), (width / 2, "short", "#dc2626")):
        values = shifts[shifts["side"] == side].set_index("feature").loc[features]["standardized_median_shift"]
        ax.bar(x + offset, values, width, label=side.upper(), color=color)
    ax.axhline(0, color="#111827", linewidth=0.8)
    ax.set_xticks(x, features, rotation=25, ha="right")
    ax.set_ylabel("(reference median - real-L1 median) / pooled IQR")
    ax.set_title("Reference vs real-L1 feature-domain shift")
    ax.legend(frameon=False)

    ax = axes[1, 1]
    configs = ("Original", "Augmented 28f", "Augmented 1f")
    metrics = (base, aug, one)
    top_net = [float(item["final_validation"]["top_decile"]["net_mean"]) * 100 for item in metrics]
    q90_net = [float(item["frozen_q90"]["net_mean"]) * 100 for item in metrics]
    x = np.arange(len(configs))
    bars1 = ax.bar(x - width / 2, top_net, width, label="top-decile net", color="#8b5cf6")
    bars2 = ax.bar(x + width / 2, q90_net, width, label="tune-q90 net", color="#06b6d4")
    ax.axhline(0, color="#111827", linewidth=0.8)
    ax.set_xticks(x, configs)
    ax.set_ylabel("Mean return after 0.2% cost")
    ax.set_title("Frozen final-validation comparison")
    ax.legend(frameon=False)
    for bars in (bars1, bars2):
        ax.bar_label(bars, labels=[f"{value:+.2f}%" for value in bars.datavalues], padding=3)
    for index, item in enumerate(metrics):
        ax.text(
            index,
            min(top_net[index], q90_net[index]) - 0.18,
            f"AUC {item['final_validation']['roc_auc']:.3f}\nq90 n={item['frozen_q90']['n']}",
            ha="center",
            va="top",
            fontsize=9,
            color="#334155",
        )
    fig.suptitle(
        "15m L2 reference augmentation: quantity increased, target-domain performance did not",
        fontsize=18,
        fontweight="bold",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, facecolor="white")
    plt.close(fig)


def choose_examples(scored: pd.DataFrame) -> pd.DataFrame:
    selected: list[pd.DataFrame] = []
    for group, keep, label in GROUPS:
        subset = scored[(scored["augmented_keep"] == keep) & (scored["label"] == label)].copy()
        for side in ("long", "short"):
            chosen = subset[subset["side"] == side].sort_values(
                ["augmented_score", "l1_confidence", "episode_id"],
                ascending=[False, False, True],
            ).head(4).copy()
            chosen["diagnostic_group"] = group
            selected.append(chosen)
    result = pd.concat(selected, ignore_index=True)
    if result["episode_id"].duplicated().any():
        raise DiagnosticError("stratified diagnostic examples overlap")
    if len(result) < 24:
        remaining = scored[~scored["episode_id"].isin(result["episode_id"])].sort_values(
            ["augmented_score", "l1_confidence", "episode_id"],
            ascending=[False, False, True],
        ).head(24 - len(result)).copy()
        remaining["diagnostic_group"] = "additional_high_score"
        result = pd.concat([result, remaining], ignore_index=True)
    if len(result) != 24 or result["episode_id"].duplicated().any():
        raise DiagnosticError(f"expected 24 distinct examples, observed {len(result)}")
    return result


def render_examples(scored: pd.DataFrame, charts_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}
    charts_dir.mkdir(parents=True, exist_ok=True)
    for order, row in enumerate(choose_examples(scored).to_dict("records"), 1):
        symbol = str(row["symbol"])
        if symbol not in frames:
            source = SNAPSHOT_DIR / f"{symbol}.csv"
            if not source.is_file():
                raise DiagnosticError(f"missing frozen snapshot: {source}")
            frames[symbol] = normalize_ohlcv(source)
        render_row = dict(row)
        render_row["l2_score"] = float(row["augmented_score"])
        render_row["l2_threshold"] = float(row["augmented_threshold"])
        render_row["l2_keep"] = bool(row["augmented_keep"])
        image = render_global_chart(render_row, frames[symbol])
        status = "TP" if int(row["label"]) == 1 else "NON_TP"
        detail = (
            f"AUDIT {row['diagnostic_group'].upper()} | economic={status} | "
            f"net={float(row['net_ret']) * 100:+.2f}%"
        )
        cv2.putText(image, detail, (1010, 94), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (80, 30, 130), 2, cv2.LINE_AA)
        filename = (
            f"{order:02d}_{row['diagnostic_group']}_{symbol}_{str(row['side']).upper()}_"
            f"{str(row['feature_bar_time'])[:10].replace('-', '')}.png"
        )
        path = charts_dir / filename
        if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise DiagnosticError(f"failed to write {path}")
        rows.append(
            {
                "order": order,
                "diagnostic_group": row["diagnostic_group"],
                "episode_id": row["episode_id"],
                "symbol": symbol,
                "side": row["side"],
                "label": int(row["label"]),
                "outcome": row["outcome"],
                "net_ret": float(row["net_ret"]),
                "augmented_score": float(row["augmented_score"]),
                "augmented_keep": bool(row["augmented_keep"]),
                "chart_path": repo_relative(path),
                "chart_sha256": sha256_file(path),
                "chart_pixel_sha256": pixel_sha256(image),
            }
        )
    return pd.DataFrame(rows)


def build_overview(manifest: pd.DataFrame, output: Path) -> None:
    thumbs: list[np.ndarray] = []
    for row in manifest.to_dict("records"):
        image = cv2.imread(str(repo_path(row["chart_path"])), cv2.IMREAD_COLOR)
        if image is None:
            raise DiagnosticError(f"could not decode {row['chart_path']}")
        thumbs.append(cv2.resize(image, (480, 312), interpolation=cv2.INTER_AREA))
    cols = 4
    rows = (len(thumbs) + cols - 1) // cols
    canvas = np.full((rows * 312, cols * 480, 3), 245, dtype=np.uint8)
    for index, thumb in enumerate(thumbs):
        y, x = divmod(index, cols)
        canvas[y * 312 : (y + 1) * 312, x * 480 : (x + 1) * 480] = thumb
    if not cv2.imwrite(str(output), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise DiagnosticError(f"failed to write {output}")


def build_gallery(manifest: pd.DataFrame, output: Path) -> None:
    cards: list[str] = []
    for row in manifest.to_dict("records"):
        relative = (Path("..") / Path(row["chart_path"]).relative_to("analysis")).as_posix()
        cards.append(
            "<article>"
            f"<h2>{int(row['order']):02d} · {html.escape(str(row['diagnostic_group']))} · "
            f"{html.escape(str(row['symbol']))} · {html.escape(str(row['side']).upper())}</h2>"
            f"<p>outcome={html.escape(str(row['outcome']))} · net={float(row['net_ret']) * 100:+.2f}% · "
            f"score={float(row['augmented_score']):.6f}</p>"
            f"<a href='{html.escape(relative)}'><img loading='lazy' src='{html.escape(relative)}'></a>"
            "</article>"
        )
    document = """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>L2 reference augmentation diagnostics</title>
<style>body{margin:0;background:#0f172a;color:#e2e8f0;font:15px/1.5 system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:24px}article{margin:0 0 24px;background:#1e293b;border:1px solid #334155;border-radius:12px;overflow:hidden}h1,h2,p{margin:14px 18px}img{display:block;width:100%;height:auto;background:#fff}a{display:block}</style></head><body><main>
<h1>15m L2 reference augmentation · 24 actual final-validation model inputs</h1>
<p>Each chart contains 168 closed bars only. Outcome text is audit metadata and was not visible to either L1 or L2.</p>
""" + "".join(cards) + "</main></body></html>\n"
    output.write_text(document, encoding="utf-8")


def main() -> int:
    dataset_receipt_path = RESULTS_DIR / "dataset_receipt.json"
    training_receipt_path = RESULTS_DIR / "training_receipt.json"
    dataset = read_json(dataset_receipt_path)
    training = read_json(training_receipt_path)
    if dataset.get("experiment_id") != EXPERIMENT_ID or training.get("experiment_id") != EXPERIMENT_ID:
        raise DiagnosticError("experiment identity drifted")
    require_hash(dataset_receipt_path, training["dataset_receipt_sha256"], "dataset receipt")
    reference_path = repo_path(dataset["reference"]["reference_path"])
    representatives_path = repo_path(dataset["representative_path"])
    scored_path = repo_path(training["scored_validation_path"])
    require_hash(reference_path, dataset["reference"]["reference_sha256"], "reference rows")
    require_hash(representatives_path, dataset["representative_sha256"], "representatives")
    require_hash(scored_path, training["scored_validation_sha256"], "scored validation")

    reference = pd.read_csv(reference_path, low_memory=False)
    representatives = pd.read_csv(representatives_path, low_memory=False)
    scored = pd.read_csv(scored_path, low_memory=False)
    scored["dependency_representative"] = bool_series(scored["dependency_representative"])
    scored["augmented_keep"] = bool_series(scored["augmented_keep"])
    scored = scored[scored["dependency_representative"]].copy()

    diagnostic_path = OUTPUT_DIR / "reference_augmentation_diagnostics.png"
    plot_diagnostics(dataset, training, reference, representatives, diagnostic_path)
    charts_dir = OUTPUT_DIR / "diagnostic_charts"
    manifest = render_examples(scored, charts_dir)
    manifest_path = OUTPUT_DIR / "diagnostic_chart_manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    overview_path = OUTPUT_DIR / "diagnostic_chart_overview.png"
    build_overview(manifest, overview_path)
    gallery_path = ROOT / "analysis" / "html" / "p3_15m_ma_launch_l2_reference_augmentation_diagnostic_gallery_20260902.html"
    gallery_path.parent.mkdir(parents=True, exist_ok=True)
    build_gallery(manifest, gallery_path)

    rates_path = OUTPUT_DIR / "reference_economic_rates.csv"
    economic_rates(reference).to_csv(rates_path, index=False)
    shifts_path = OUTPUT_DIR / "reference_domain_shift.csv"
    standardized_feature_shift(representatives).to_csv(shifts_path, index=False)
    receipt = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "training_receipt_sha256": sha256_file(training_receipt_path),
        "diagnostic_png": {"path": repo_relative(diagnostic_path), "sha256": sha256_file(diagnostic_path)},
        "overview_png": {"path": repo_relative(overview_path), "sha256": sha256_file(overview_path)},
        "gallery": {"path": repo_relative(gallery_path), "sha256": sha256_file(gallery_path)},
        "chart_manifest": {"path": repo_relative(manifest_path), "sha256": sha256_file(manifest_path), "rows": len(manifest)},
        "economic_rates": {"path": repo_relative(rates_path), "sha256": sha256_file(rates_path)},
        "domain_shift": {"path": repo_relative(shifts_path), "sha256": sha256_file(shifts_path)},
        "future_candles_in_chart": 0,
        "manual_selection": False,
        "used_for_training_or_threshold_selection": False,
        "holdout_consumed": False,
        "production_eligible": False,
    }
    receipt_path = RESULTS_DIR / "diagnostic_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
