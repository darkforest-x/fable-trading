"""Freeze the reference data the dense-launch similarity layers need, for Pine.

The Grade-A scanner ranks a candidate against Owner-accepted examples twice:

* stage 1 (``ma_launch_owner_autofill_review.profile_distance``) takes the
  nearest of fifty accepted v7 profiles over fourteen scaled features and a
  4x10 core+release sequence, and requires distance <= 0.5;
* stage 2 (``ma_launch_owner_perfect_filter._score_all``) scores six axes, one
  of which blends segmented lock-step/DTW/DDTW distances to the two Owner
  anchors (#44 perfect, #42 good), six semantic rejects and the accepted
  family, and keeps candidates whose quality score reaches the frozen
  perfect threshold.

Both layers need data, not just thresholds, so this module recomputes those
profiles from the hash-pinned preregistrations and writes a pack that the Pine
port embeds verbatim. It reads frozen research inputs only: no market fetch,
no labels, no training, no defaults touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from yoyo.datasets import ma_launch_owner_perfect_filter as pf
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features, read_preholdout_prefix
from yoyo.datasets.ma_launch_owner_autofill10000 import load_reference_profiles
from yoyo.datasets.ma_launch_owner_autofill_review import FEATURE_NAMES

AUTOFILL_PREREG = Path("experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json")
DECIMALS = 4


def _digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _stage2_profiles(prereg: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, list[str]]]:
    """Rebuild the 7x22 reference sequences from their pinned source geometry."""
    _, references, family, _ = pf._load_pinned_rows(prereg)
    frames: dict[str, Any] = {}
    sequences: dict[str, np.ndarray] = {}
    for row in (*references, *family):
        key = pf._profile_key(row)
        if key in sequences:
            continue
        source = str(row["source_path"])
        if source not in frames:
            raw, _ = read_preholdout_prefix(pf._repo_path(source), end_exclusive=pf.HOLDOUT_START, bar_minutes=15)
            frames[source] = add_candidate_features(raw)
        sequences[key] = pf.extract_profile(frames[source], row, bar_minutes=15).sequence
    roles: dict[str, list[str]] = {}
    for row in references:
        roles.setdefault(str(row["reference_role"]), []).append(pf._profile_key(row))
    return sequences, {"anchors": roles["perfect"] + roles["good"], "bad": roles["semantic_reject"],
                       "family": [pf._profile_key(row) for row in family]}


def nearest_combined(sequence: np.ndarray, pool: Sequence[np.ndarray], contract: Mapping[str, Any]) -> float:
    """Frozen nearest-reference distance: lock-step prefilter, then segmented DTW blend."""
    k = int(contract["nearest_prefilter_lockstep_k"])
    slices, weights = contract["segment_slices"], contract["segment_weights"]
    order = list(range(len(pool)))
    if len(pool) > k:
        order = sorted(order, key=lambda i: (pf.segmented_lockstep_distance(
            sequence, pool[i], segment_slices=slices, segment_weights=weights), i))[:k]
    return min(pf.segmented_sequence_distance(
        sequence, pool[i], radius=int(contract["sakoe_chiba_radius_bars"]),
        component_weights=contract["weights"], segment_slices=slices, segment_weights=weights,
    )["combined_distance"] for i in order)


def build_pack(decimals: int = DECIMALS) -> dict[str, Any]:
    """Return every constant and reference array the Pine port embeds."""
    prereg = pf.read_json(pf.DEFAULT_PREREG)
    autofill = pf.read_json(AUTOFILL_PREREG)
    contract = prereg["sequence_distance"]
    sequences, roles = _stage2_profiles(prereg)
    anchors = [sequences[k] for k in roles["anchors"]]
    bad = [sequences[k] for k in roles["bad"]]
    family = [sequences[k] for k in roles["family"]]
    scale = float(np.median([nearest_combined(seq, anchors, contract) for seq in bad]))
    axis_weights = prereg["ranking"]["axis_weights"]
    worst = float(prereg["ranking"]["worst_axis_weight"])
    anchor_scores = []
    for index, key in enumerate(roles["anchors"]):
        others = [seq for position, seq in enumerate(anchors) if position != index]
        good = nearest_combined(sequences[key], others, contract)
        anchor_scores.append(pf._axis_scores(
            _metrics_for(prereg, key), good_distance=good, bad_distance=nearest_combined(sequences[key], bad, contract),
            family_distance=nearest_combined(sequences[key], family, contract), distance_scale=scale,
            axis_weights=axis_weights, worst_axis_weight=worst)["quality_score"])
    stage1_profiles, _ = load_reference_profiles(autofill)
    similarity = autofill["reference_family"]
    return {
        "generated_from": {
            "perfect_filter_preregistration": str(pf.DEFAULT_PREREG), "perfect_filter_sha256": _digest(pf.DEFAULT_PREREG),
            "autofill_preregistration": str(AUTOFILL_PREREG), "autofill_sha256": _digest(AUTOFILL_PREREG),
        },
        "decimals": decimals,
        "stage1": {
            "feature_names": list(FEATURE_NAMES),
            "feature_scales": [float(v) for v in similarity["feature_scales"]],
            "feature_weight": float(similarity["feature_weight"]),
            "sequence_weight": float(similarity["sequence_weight"]),
            "max_distance": float(similarity["max_distance"]),
            "features": [[float(v) for v in p.features] for p in stage1_profiles],
            "sequences": [[[float(v) for v in channel] for channel in p.sequence] for p in stage1_profiles],
        },
        "stage2": {
            "radius": int(contract["sakoe_chiba_radius_bars"]),
            "component_weights": {k: float(v) for k, v in contract["weights"].items()},
            "segment_slices": {k: [int(v) for v in bounds] for k, bounds in contract["segment_slices"].items()},
            "segment_weights": {k: float(v) for k, v in contract["segment_weights"].items()},
            "prefilter_k": int(contract["nearest_prefilter_lockstep_k"]),
            "axis_weights": {k: float(v) for k, v in axis_weights.items()},
            "worst_axis_weight": worst,
            "distance_scale": scale,
            "perfect_threshold": float(min(anchor_scores)),
            "anchor_scores": [float(v) for v in anchor_scores],
            "anchors": [seq.tolist() for seq in anchors],
            "bad": [seq.tolist() for seq in bad],
            "family": [seq.tolist() for seq in family],
        },
    }


def _metrics_for(prereg: Mapping[str, Any], key: str) -> Mapping[str, float]:
    """Metrics of one pinned reference, rebuilt exactly as ``_score_all`` does."""
    _, references, family, _ = pf._load_pinned_rows(prereg)
    for row in (*references, *family):
        if pf._profile_key(row) == key:
            raw, _ = read_preholdout_prefix(pf._repo_path(str(row["source_path"])),
                                            end_exclusive=pf.HOLDOUT_START, bar_minutes=15)
            return pf.extract_profile(add_candidate_features(raw), row, bar_minutes=15).metrics
    raise KeyError(key)


def pine_rows(values: Sequence[Sequence[float]], decimals: int) -> str:
    """One Pine string literal per reference, comma separated, fixed precision."""
    return "\n".join('    "' + ",".join(f"{v:.{decimals}f}" for v in np.asarray(row).ravel()) + '",'
                     for row in values)



TEMPLATE = Path("yoyo/evaluation/pine/ma_dense_launch_v2.template.pine")
GENERATED = Path("yoyo/evaluation/pine/ma_dense_launch_v2.pine")


def render_pine(pack: Mapping[str, Any], template: str) -> str:
    """Fill the template's constants and reference-data block from one pack."""
    stage1, stage2 = pack["stage1"], pack["stage2"]
    decimals = int(pack["decimals"])
    constants = {
        "__S1_FEATURE_WEIGHT__": stage1["feature_weight"], "__S1_SEQUENCE_WEIGHT__": stage1["sequence_weight"],
        "__S1_MAX_DISTANCE__": stage1["max_distance"], "__DISTANCE_SCALE__": stage2["distance_scale"],
        "__PERFECT_THRESHOLD__": stage2["perfect_threshold"], "__W_LOCKSTEP__": stage2["component_weights"]["lockstep"],
        "__W_DTW__": stage2["component_weights"]["dtw"], "__W_DDTW__": stage2["component_weights"]["ddtw"],
        "__SEG_PRELUDE__": stage2["segment_weights"]["prelude"], "__SEG_CORE__": stage2["segment_weights"]["core"],
        "__SEG_RELEASE__": stage2["segment_weights"]["release"],
        "__AX_DENSITY__": stage2["axis_weights"]["density_topology"],
        "__AX_QUIET__": stage2["axis_weights"]["prelude_quietness"],
        "__AX_CONTACT__": stage2["axis_weights"]["price_bundle_contact"],
        "__AX_RELEASE__": stage2["axis_weights"]["release_cleanliness"],
        "__AX_CLEAN__": stage2["axis_weights"]["wick_reverse_cleanliness"],
        "__AX_SIMILAR__": stage2["axis_weights"]["reference_similarity"],
        "__WORST_AXIS__": stage2["worst_axis_weight"], "__PREFILTER_K__": stage2["prefilter_k"],
        "__RADIUS__": stage2["radius"],
    }
    out = template
    for key, value in constants.items():
        out = out.replace(key, repr(int(value)) if isinstance(value, int) else f"{float(value):.10f}")
    blocks = [_pool("S1SCALE", [stage1["feature_scales"]], decimals),
              _pool("S1REF", [list(f) + [v for ch in s for v in ch]
                              for f, s in zip(stage1["features"], stage1["sequences"])], decimals)]
    for name, key in (("S2ANCHOR", "anchors"), ("S2BAD", "bad"), ("S2FAMILY", "family")):
        blocks.append(_pool(name, [[v for channel in sequence for v in channel] for sequence in stage2[key]], decimals))
    return out.replace("__DATA__", "\n".join(blocks))


def _pool(target: str, rows: Sequence[Sequence[float]], decimals: int) -> str:
    """Reference rows live inside their own function; the main body keeps one line each.

    TradingView limits both a single scope's length (CE10205) and the main
    body's (CE10295), and continuation lines may not be indented by a multiple
    of four, hence the six-space rows.
    """
    literals = ['"' + ",".join(f"{float(v):.{decimals}f}" for v in row) + '"' for row in rows]
    if len(literals) == 1:
        return f"var array<float> {target} = f_parse(array.from({literals[0]}))"
    body = ",\n      ".join(literals)
    return (f"f_pool_{target.lower()}() =>\n    f_parse(array.from(\n      {body}))\n\n"
            f"var array<float> {target} = f_pool_{target.lower()}()")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decimals", type=int, default=DECIMALS)
    parser.add_argument("--emit-pine", type=Path, default=None)
    args = parser.parse_args()
    pack = build_pack(args.decimals)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pack, ensure_ascii=False, indent=1) + "\n")
    if args.emit_pine is not None:
        args.emit_pine.write_text(render_pine(pack, TEMPLATE.read_text()))
    print(json.dumps({"stage1_references": len(pack["stage1"]["features"]),
                      "anchors": len(pack["stage2"]["anchors"]), "bad": len(pack["stage2"]["bad"]),
                      "family": len(pack["stage2"]["family"]), "distance_scale": pack["stage2"]["distance_scale"],
                      "perfect_threshold": pack["stage2"]["perfect_threshold"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
