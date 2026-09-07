"""Prepare and score an Owner-blinded Grade-A candidate calibration pack.

Sources: the frozen 2026-09-03 daily-mover proposal ledger, the close Grade-A
training manifest, and its exact renderer. This is development label evidence,
not an independent evaluation or a training-set mutation. Candidate sampling
uses proposal stratum, direction and half-year only, never confidence or return.
Cross-venue interval exclusion covers both classes and both positive/negative
training members, with the existing 150-bar split buffer. Unknown historical
exposure remains unknown even when membership exclusion passes.

The public pack contains only unannotated input pixels and opaque identifiers.
The original representative window is retained (including its visible post-core
context); its clock is the last visible bar close, never the earlier first-hit
clock. No candles beyond that window are copied to the public pack.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yoyo.contracts.holdout import HOLDOUT_START, assert_pre_holdout

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-15m-grade-a-owner-calibration-20260907-v1"
EXPERIMENT = ROOT / "experiments/active" / EXPERIMENT_ID
MINING = ROOT / "experiments/active/exp-15m-ma-launch-grade-a-daily-movers-5000-v1"
DATASET = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
PACK = ROOT / "datasets/grade_a_owner_calibration_20260907_v1"
LABELS = {"LONG", "SHORT", "NO_SIGNAL", "UNCERTAIN"}
SEMANTICS = {"CORE_WICKS_MA", "MA_BUNDLE", "OTHER", "UNCERTAIN"}
REASONS = {"non_dense", "far_from_ma", "already_launched", "not_launch",
           "multiple_cores", "insufficient_context", "other"}
BAR = timedelta(minutes=15)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))


def instant(value: str) -> datetime:
    out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if out.tzinfo is None:
        raise ValueError("timezone is required")
    return out.astimezone(timezone.utc)


def symbol(value: str) -> str:
    """Join literal OKX perpetual and Binance names; do not guess asset aliases."""
    out = value.upper()
    if out.endswith("_USDT_SWAP"):
        return out[:-10] + "USDT"
    return out.replace("_", "")


def rank(seed: int, *parts: object) -> str:
    return hashlib.sha256("|".join(map(str, (seed, *parts))).encode()).hexdigest()


def assert_digest(path: Path, expected: str) -> None:
    if sha(path) != expected:
        raise ValueError(f"SHA drift: {path}")


def assert_source_first(paths: list[Path]) -> str:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        committed = subprocess.check_output(["git", "show", f"{head}:{rel}"], cwd=ROOT)
        if hashlib.sha256(committed).hexdigest() != sha(path):
            raise ValueError(f"commit the builder before generating artifacts: {rel}")
    return head


def membership_intervals(manifest: Path, purge_bars: int) -> dict[str, list[tuple[datetime, datetime]]]:
    """Union complete known sample dependencies across venues, directions and splits."""
    grouped = defaultdict(list)
    with manifest.open() as handle:
        for line in handle:
            row = json.loads(line)
            start = instant(row["window_start_time"])
            end = max(instant(row["window_end_time"]), instant(row.get("dependency_end_time", row["window_end_time"])))
            if row["sample_kind"] == "positive":
                end = max(end, instant(row["core_end_time"]) + 5 * BAR)
            assert_pre_holdout(end, what="existing Grade-A dependency")
            grouped[symbol(row["exchange_symbol"])].append((start - purge_bars * BAR, end + purge_bars * BAR))
    merged = {}
    for key, spans in grouped.items():
        union = []
        for start, end in sorted(spans):
            if union and start <= union[-1][1]:
                union[-1] = (union[-1][0], max(end, union[-1][1]))
            else:
                union.append((start, end))
        merged[key] = union
    return merged


def overlaps(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    index = bisect.bisect_right([span[0] for span in intervals], end)
    return index > 0 and intervals[index - 1][1] >= start


def audit_population(rows: list[dict], intervals: dict, max_visible: datetime) -> tuple[list[dict], list[dict]]:
    seen = set()
    eligible, ledger = [], []
    for row in rows:
        event = row["event_id"]
        if event in seen:
            raise ValueError("duplicate source event_id")
        seen.add(event)
        start, end = instant(row["window_start_time"]), instant(row["window_end_time"])
        assert_pre_holdout(end, what="candidate metadata")
        if end > max_visible or start > end:
            raise ValueError("candidate exceeds frozen development time bound")
        if row["timeframe"] != "15m" or int(row["window_len"]) != 18 or (end-start) != 17 * BAR:
            raise ValueError("candidate window contract drift")
        if int(row["window_end_i"]) - int(row["window_start_i"]) != 17:
            raise ValueError("candidate indices disagree with times")
        near = overlaps(intervals.get(symbol(row["exchange_symbol"]), []), start, end)
        reasons = []
        if row["novelty_status"] != "new_event_review":
            reasons.append("original_training_overlap")
        if near:
            reasons.append("cross_venue_any_class_member_dependency_plus150")
        ledger.append({"event_id": event, "source_novelty": row["novelty_status"],
                       "canonical_symbol": symbol(row["exchange_symbol"]), "excluded_by": reasons,
                       "selection_eligible": not reasons, "training_eligible": False,
                       "independence_status": "not_established"})
        if not reasons:
            if row["review_bucket"] not in {"candidate_positive", "candidate_hard_negative"}:
                raise ValueError(f"unrecognized proposal bucket: {row['review_bucket']}")
            if row["model_direction"] not in {"LONG", "SHORT"}:
                raise ValueError("unrecognized direction")
            eligible.append(row)
    return eligible, ledger


def halfyear(row: dict) -> str:
    date = instant(row["window_end_time"])
    return f"{date.year}H{1 if date.month <= 6 else 2}"


def allocate(groups: dict[str, list[dict]], target: int) -> dict[str, int]:
    total = sum(map(len, groups.values()))
    if target > total or target < len(groups):
        raise ValueError("insufficient stratum capacity")
    allocation = {key: 1 for key in groups}
    for _ in range(target - len(groups)):
        available = [key for key in groups if allocation[key] < len(groups[key])]
        chosen = min(available, key=lambda key: (allocation[key] / len(groups[key]), key))
        allocation[chosen] += 1
    return allocation


def select(rows: list[dict], seed: int, per_bucket_direction: int = 60) -> tuple[list[dict], list[dict]]:
    """Balanced challenge sampling, with fixed-time strata and no overlapping windows."""
    selected, strata = [], []
    selected_spans = defaultdict(list)
    for bucket in ("candidate_positive", "candidate_hard_negative"):
        for direction in ("LONG", "SHORT"):
            groups = defaultdict(list)
            for row in rows:
                if row["review_bucket"] == bucket and row["model_direction"] == direction:
                    groups[halfyear(row)].append(row)
            if not groups:
                raise ValueError("missing proposal/direction stratum")
            quotas = allocate(groups, per_bucket_direction)
            for period in sorted(groups):
                take = []
                for row in sorted(groups[period], key=lambda x: rank(seed, "sample", x["event_id"])):
                    key = symbol(row["exchange_symbol"])
                    start, end = instant(row["window_start_time"]), instant(row["window_end_time"])
                    if any(start <= b and a <= end for a, b in selected_spans[key]):
                        continue
                    take.append(row)
                    selected_spans[key].append((start, end))
                    if len(take) == quotas[period]:
                        break
                if len(take) != quotas[period]:
                    raise ValueError("non-overlap constraint prevents quota; do not relax after selection")
                selected.extend(take)
                strata.append({"proposal_bucket": bucket, "direction": direction, "halfyear": period,
                               "population": len(groups[period]), "selected": len(take)})
    return selected, strata


def blind_order(rows: list[dict], seed: int, repeats_per_group: int = 9) -> list[dict]:
    repeats = []
    for bucket in ("candidate_positive", "candidate_hard_negative"):
        for direction in ("LONG", "SHORT"):
            pool = [r for r in rows if r["review_bucket"] == bucket and r["model_direction"] == direction]
            repeats.extend(sorted(pool, key=lambda x: rank(seed, "repeat", x["event_id"]))[:repeats_per_group])
    repeat_ids = {row["event_id"] for row in repeats}
    others = sorted([row for row in rows if row["event_id"] not in repeat_ids], key=lambda x: rank(seed, "primary", x["event_id"]))
    first = sorted(repeats + others[:120-len(repeats)], key=lambda x: rank(seed, "first", x["event_id"]))
    rest = others[120-len(repeats):]
    def entry(row: dict, copy: int) -> dict:
        return {"review_id": rank(seed, "blind", row["event_id"], copy)[:24], "event_id": row["event_id"],
                "is_primary": copy == 0, "repeat_of_review_id": None if copy == 0 else rank(seed, "blind", row["event_id"], 0)[:24]}
    out = [entry(row, 0) for row in first + rest[:60]]
    tail = [entry(row, 0) for row in rest[60:]] + [entry(row, 1) for row in repeats]
    out.extend(sorted(tail, key=lambda x: rank(seed, "tail", x["review_id"])))
    return out


def render_selected(rows: list[dict], prereg: dict, image_dir: Path) -> tuple[dict, list[dict]]:
    """Replay selected W18 pixels only; recorded archives are checked before loading."""
    import cv2
    from scripts import mine_15m_ma_launch_grade_a_daily_movers_5000 as mine

    parent = json.loads((MINING / "preregistration.json").read_text())
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["source_month"]].append(row)
    outputs, source_receipts = {}, []
    for month in sorted(grouped):
        # Reject a potentially overlapping month before opening any archive.
        if month > "2025-10":
            raise ValueError("archive month exceeds frozen mining period")
        targets = grouped[month]
        names = {row["exchange_symbol"] for row in targets}
        source_manifest = MINING / f"results/shards/{month}/source_manifest.json"
        expected = json.loads(source_manifest.read_text())["archives"]
        wanted = [r for r in expected if r["symbol"] in names and "selected_symbol_context" in r["role"]]
        allowed = {str(r["path"]): r for r in wanted}
        for record in wanted:
            assert_pre_holdout(record["last_bar_open"], what="archive bound before opening")
            assert_digest(ROOT / record["path"], record["sha256"])
        # Check all paths the legacy loader may open, including unexpected files.
        archive_root = ROOT / parent["data"]["archive_root"]
        for name in names:
            for adjacent in mine.adjacent_months(month):
                path = mine.prior.month_archive_path(archive_root, name, adjacent)
                if path.exists() and path.relative_to(ROOT).as_posix() not in allowed:
                    raise ValueError("unrecorded source archive would be opened")
        frames, receipts = mine.load_selected_frames(parent, month=month, archive_root=archive_root, symbols=sorted(names))
        for receipt in receipts:
            if receipt["sha256"] != allowed[receipt["path"]]["sha256"]:
                raise ValueError("source changed during loading")
        for row in targets:
            frame = frames[row["exchange_symbol"]]
            window = frame.iloc[int(row["window_start_i"]):int(row["window_end_i"])+1]
            if len(window) != 18 or mine.utc(window.iloc[0]["open_time"]).isoformat() != row["window_start_time"] or mine.utc(window.iloc[-1]["open_time"]).isoformat() != row["window_end_time"]:
                raise ValueError("render time/index mismatch")
            image, transform = mine.render_chart(window, out_path=None)
            pixel_sha = mine.pixel_sha256(image)
            if pixel_sha != row["input_pixel_sha256"]:
                raise ValueError(f"exact unannotated input replay failed: {row['event_id']}")
            path = image_dir / (rank(prereg["seed"], "blind", row["event_id"], 0)[:24] + ".png")
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
                raise ValueError("PNG write failed")
            decoded = cv2.imread(str(path))
            if mine.pixel_sha256(decoded) != pixel_sha:
                raise ValueError("PNG roundtrip drift")
            outputs[row["event_id"]] = {"image": f"images/{path.name}", "image_sha256": sha(path),
                "input_pixel_sha256": pixel_sha, "n_bars": len(window),
                "bar_centers_norm": [transform.x_at(i) / transform.width for i in range(len(window))]}
        for record in wanted:
            assert_digest(ROOT / record["path"], record["sha256"])
        source_receipts.extend(receipts)
        print(f"replayed {month}: {len(targets)} inputs", flush=True)
    if len({r["input_pixel_sha256"] for r in outputs.values()}) != len(rows):
        raise ValueError("distinct selected events have duplicate pixels")
    return outputs, source_receipts


def build(prereg_path: Path = EXPERIMENT / "preregistration.json", pack: Path = PACK) -> dict:
    prereg = json.loads(prereg_path.read_text())
    if pack.exists():
        raise ValueError("pack already exists; refusing to overwrite review evidence")
    paths = [Path(__file__).resolve(), ROOT / "yoyo/datasets/templates/grade_a_calibration.html", prereg_path]
    head = assert_source_first(paths)
    for relative, digest in prereg["inputs"].items():
        assert_digest(ROOT / relative, digest)
    queue = MINING / "results/review_queue.csv"
    with queue.open() as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 5083:
        raise ValueError("source population changed")
    intervals = membership_intervals(DATASET / "manifest.jsonl", prereg["purge_bars"])
    eligible, exclusion = audit_population(rows, intervals, instant(prereg["max_visible_bar_open"]))
    selected, strata = select(eligible, prereg["seed"])
    order = blind_order(selected, prereg["seed"])
    pack.mkdir(parents=True)
    public = pack / "public"
    image_dir = public / "images"
    image_dir.mkdir(parents=True)
    private = pack / "admin"
    # Freeze identities before raw archive or pixel reads. Keep this receipt on failure.
    jsonl(private / "selected_source_rows.jsonl", selected)
    jsonl(private / "exclusion_ledger.jsonl", exclusion)
    dump(private / "selection_receipt.json", {"builder_commit": head, "strata": strata,
        "selected_event_ids": [r["event_id"] for r in selected], "source_count": len(rows), "eligible_count": len(eligible)})
    images, sources = render_selected(selected, prereg, image_dir)
    by_event = {row["event_id"]: row for row in selected}
    items, truth = [], []
    primary_positions = {}
    gaps = []
    for position, item in enumerate(order):
        image = images[item["event_id"]]
        target_name = f"images/{item['review_id']}.png"
        if item["is_primary"]:
            primary_positions[item["review_id"]] = position
        else:
            shutil.copyfile(public / image["image"], public / target_name)
            gaps.append(position - primary_positions[item["repeat_of_review_id"]])
        items.append({"review_id": item["review_id"], "image": target_name,
                      "n_bars": image["n_bars"], "bar_centers_norm": image["bar_centers_norm"]})
        row = by_event[item["event_id"]]
        truth.append({**item, "image_sha256": image["image_sha256"], "input_pixel_sha256": image["input_pixel_sha256"],
            "source_proposal_bucket": row["review_bucket"], "source_direction": row["model_direction"],
            "source_core_start": int(row["core_start_local"])+1, "source_core_end": int(row["core_end_local"])+1,
            "visible_end_bar_open": row["window_end_time"],
            "available_at": (instant(row["window_end_time"]) + BAR).isoformat(),
            "earlier_episode_first_available_at": row["first_available_at"],
            "training_eligible": False, "production_eligible": False})
    if len(items) != 276 or min(gaps) < 60:
        raise ValueError("repeat schedule drift")
    manifest = {"schema_version": 1, "pack_id": EXPERIMENT_ID, "title": "六均线密集启动 · 形态校准", "items": items}
    dump(public / "manifest.json", manifest)
    manifest_sha = sha(public / "manifest.json")
    payload = {**manifest, "manifest_sha256": manifest_sha}
    safe = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (ROOT / "yoyo/datasets/templates/grade_a_calibration.html").read_text()
    if template.count("__PACK_JSON__") != 1:
        raise ValueError("review template placeholder drift")
    (public / "index.html").write_text(template.replace("__PACK_JSON__", safe))
    jsonl(private / "truth.jsonl", truth)
    dump(private / "source_receipts.json", sources)
    for relative, digest in prereg["inputs"].items():
        assert_digest(ROOT / relative, digest)
    counts = Counter(reason for row in exclusion for reason in row["excluded_by"])
    summary = {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "builder_commit": head,
        "created_at": datetime.now(timezone.utc).isoformat(), "source_events": len(rows),
        "eligible_after_cross_venue_exclusion": len(eligible), "exclusion_reason_counts_overlapping": dict(counts),
        "unique_review_events": len(selected), "blind_repeats": len(gaps), "review_items": len(items),
        "minimum_repeat_position_gap": min(gaps), "unique_primary_pixel_hashes": len(images),
        "selected_symbols": len({symbol(row["exchange_symbol"]) for row in selected}),
        "earliest_visible_bar": min(row["window_start_time"] for row in selected),
        "latest_visible_bar": max(row["window_end_time"] for row in selected),
        "strata": strata, "source_archives_checked": len(sources), "raw_input_pixel_replay_passed": len(images),
        "manifest_sha256": manifest_sha, "public_html_sha256": sha(public / "index.html"),
        "truth_sha256": sha(private / "truth.jsonl"), "answers_received": 0,
        "cohen_kappa": None, "owner_confirmed_positive_rate": None,
        "independence_status": "not_established", "training_eligible": False, "production_eligible": False,
        "new_model_inference": False, "holdout_read": False,
        "null_control": "All selected public images must replay the frozen unannotated model-input pixel hashes; source overlays and headers are never copied.",
        "limitations": ["Model-selected, post-hoc daily-mover calibration; cannot estimate population recall or precision.",
            "Cross-venue literal-symbol membership exclusion does not establish unseen history or eliminate correlated market regimes.",
            "Representative-window pixels are retained, not reconstructed as earlier episode first-hit pixels.",
            "Owner annotations and repeat consistency are pending, with no invented numerical acceptance threshold."]}
    dump(EXPERIMENT / "results/build_receipt.json", summary)
    return summary


def validate_answer(answer: dict, n_bars: int) -> bool:
    """Return False for explicit drafts; fail closed on malformed saved answers."""
    if not answer.get("answered_at"):
        return False
    instant(answer["answered_at"])
    if answer.get("label") not in LABELS:
        raise ValueError("invalid saved label")
    if not isinstance(answer.get("reasons", []), list) or any(reason not in REASONS for reason in answer.get("reasons", [])):
        raise ValueError("invalid reason")
    if not isinstance(answer.get("note", ""), str):
        raise ValueError("invalid note")
    if answer["label"] in {"LONG", "SHORT"}:
        start, end = answer.get("core_start"), answer.get("core_end")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= n_bars:
            raise ValueError("invalid core geometry")
        top, bottom = answer.get("box_top_norm"), answer.get("box_bottom_norm")
        if type(top) not in (int, float) or type(bottom) not in (int, float) or not math.isfinite(top) or not math.isfinite(bottom) or not 0 <= top < bottom <= 1:
            raise ValueError("invalid vertical geometry")
        if answer.get("box_semantics") not in SEMANTICS:
            raise ValueError("missing box semantics")
    elif any(answer.get(key) is not None for key in ("core_start", "core_end", "box_top_norm", "box_bottom_norm", "box_semantics")):
        raise ValueError("non-signal retains stale positive geometry")
    return True


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[k] * right[k] for k in LABELS) / n ** 2
    observed = sum(a == b for a, b in pairs) / n
    return (observed - expected) / (1 - expected) if expected < 1 else None


def score(export: dict, pack: Path = PACK) -> dict:
    manifest_path = pack / "public/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if export.get("schema_version") != 1 or export.get("pack_id") != manifest["pack_id"] or export.get("manifest_sha256") != sha(manifest_path):
        raise ValueError("answer export belongs to a different pack or manifest")
    truth_path = pack / "admin/truth.jsonl"
    receipt = json.loads((EXPERIMENT / "results/build_receipt.json").read_text()) if pack == PACK else None
    if receipt:
        assert_digest(manifest_path, receipt["manifest_sha256"])
        assert_digest(truth_path, receipt["truth_sha256"])
    truth = [json.loads(line) for line in truth_path.read_text().splitlines()]
    public = {row["review_id"]: row for row in manifest["items"]}
    if {row["review_id"] for row in truth} != set(public) or len(truth) != len(public):
        raise ValueError("public/private identity drift")
    answers, drafts = {}, []
    seen = set()
    if not isinstance(export.get("answers"), list):
        raise ValueError("answers must be a list")
    for answer in export["answers"]:
        rid = answer.get("review_id")
        if rid not in public or rid in seen:
            raise ValueError("duplicate or foreign review_id")
        seen.add(rid)
        if validate_answer(answer, public[rid]["n_bars"]):
            answers[rid] = answer
        else:
            drafts.append(rid)
    joined = [{**row, "owner_answer": answers[row["review_id"]]} for row in truth if row["review_id"] in answers]
    primary = [row for row in joined if row["is_primary"]]
    pairs, geometry = [], []
    for row in joined:
        old = row["repeat_of_review_id"]
        if old and old in answers:
            a, b = answers[old], row["owner_answer"]
            pairs.append((a["label"], b["label"]))
            if a["label"] == b["label"] and a["label"] in {"LONG", "SHORT"}:
                geometry.append({"core_equal": (a["core_start"], a["core_end"]) == (b["core_start"], b["core_end"]),
                    "start_abs_error_bars": abs(a["core_start"]-b["core_start"]),
                    "end_abs_error_bars": abs(a["core_end"]-b["core_end"]),
                    "vertical_max_abs_error_norm": max(abs(a["box_top_norm"]-b["box_top_norm"]),abs(a["box_bottom_norm"]-b["box_bottom_norm"])),
                    "box_semantics_equal": a["box_semantics"] == b["box_semantics"]})
    complete = len(answers) == len(public)
    return {"schema_version": 1, "pack_id": manifest["pack_id"], "complete": complete,
        "saved_answers": len(answers), "draft_answers": len(drafts), "missing_or_draft": len(public)-len(answers),
        "primary_saved": len(primary), "primary_label_counts": dict(Counter(row["owner_answer"]["label"] for row in primary)),
        "repeat_pairs_complete": len(pairs), "repeat_agreement": sum(a == b for a,b in pairs)/len(pairs) if pairs else None,
        "cohen_kappa": kappa(pairs), "same_direction_positive_repeat_geometry": geometry,
        "joined_answers": joined, "training_eligible": False, "production_eligible": False,
        "label_mutation_performed": False, "independence_status": "not_established",
        "next_gate": "Owner interpretation of repeat/semantic results and matched-negative feasibility; no automatic Gold or training launch" if complete else "await_remaining_owner_answers"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build")
    p = commands.add_parser("score")
    p.add_argument("--answers", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "build":
        result = build()
        print(json.dumps({k:v for k,v in result.items() if k != "strata"}, ensure_ascii=False, indent=2))
    else:
        if args.output.exists():
            raise ValueError("refusing to overwrite scored Owner evidence")
        result = score(json.loads(args.answers.read_text()))
        result["answers_source_sha256"] = sha(args.answers)
        dump(args.output, result)
        print(json.dumps({k:v for k,v in result.items() if k != "joined_answers"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
