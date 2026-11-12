"""Prepare five fixed ruler-alignment add-on pairs without touching cohort v1.

This runner reuses prepare.py's card, H1, matching, archive-fallback, and 5m
tape validators. Owner dates were fixed from the latest same-rail Binance H1
touch before the chart ruler boundary. Controls remain outcome-blind and are
unique against every owner symbol, all 19 original control symbols, and this
five-pair add-on batch.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import sys

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import prepare as shared  # noqa: E402


SOURCE_COHORT = HERE / "cohort.csv"
OUT = HERE / "ruler" / "cohort.csv"
TAPE_DIR = shared.TAPE_DIR

FIXED_OWNER_EVENTS = [
    # Fixed chart-ruler add-on order: XAG, DRAM, SOXL, ZK, NEAR.
    dict(case_id="photo_21", pair_id="photo_21", source_photo_id="photo_07",
         symbol="XAGUSDT", month="2026-05", side="short", touch_beijing="2026-05-14 01:00"),
    dict(case_id="photo_22", pair_id="photo_22", source_photo_id="photo_12",
         symbol="DRAMUSDT", month="2026-06", side="short", touch_beijing="2026-06-25 06:00"),
    dict(case_id="photo_23", pair_id="photo_23", source_photo_id="photo_14",
         symbol="SOXLUSDT", month="2026-09", side="long", touch_beijing="2026-09-15 13:00"),
    dict(case_id="photo_24", pair_id="photo_24", source_photo_id="photo_04",
         symbol="ZKUSDT", month="2024-10", side="short", touch_beijing="2024-10-23 01:00"),
    dict(case_id="photo_25", pair_id="photo_25", source_photo_id="photo_03",
         symbol="NEARUSDT", month="2025-07", side="short", touch_beijing="2025-07-23 00:00"),
]


def _case_event(cards: pd.DataFrame, spec: dict) -> dict:
    touch_ms = shared._utc_ms(spec["touch_beijing"])
    found = cards.loc[
        cards.symbol.eq(spec["symbol"]) & cards.month.eq(spec["month"])
        & cards.side.eq(spec["side"]) & cards.touch_ms.eq(touch_ms)
    ]
    if len(found) != 1:
        raise ValueError(f"{spec['case_id']}: expected one frozen card at {spec['touch_beijing']}; found {len(found)}")
    card = found.iloc[0].to_dict()
    prepared_spec = dict(spec, role="owner", candidate_type="ruler_prior_touch_fixed_additional",
                         touch_ms=touch_ms, touch_beijing=spec["touch_beijing"],
                         asset_class="", rail=None, source_ref="all_replay_v1/cards.csv.gz")
    event, _, _, _ = shared._prepare_event(prepared_spec, card)
    event["pair_id"] = event["case_id"]
    event["role"] = "owner"
    event["source_photo_id"] = spec["source_photo_id"]
    event["candidate_type"] = "ruler_prior_touch_fixed_additional"
    return event


def _main() -> None:
    if not SOURCE_COHORT.is_file():
        raise FileNotFoundError(f"original cohort missing: {SOURCE_COHORT}")
    if OUT.exists():
        raise FileExistsError(f"refusing to replace add-on cohort: {OUT}")
    proposed_ids = [x["case_id"] for x in FIXED_OWNER_EVENTS]
    proposed_tape_ids = proposed_ids + [x.replace("photo_", "control_", 1) for x in proposed_ids]
    existing_tapes = [TAPE_DIR / f"{case_id}.npy" for case_id in proposed_tape_ids if (TAPE_DIR / f"{case_id}.npy").exists()]
    if existing_tapes:
        raise FileExistsError(f"refusing to overwrite add-on tape(s): {existing_tapes}")

    original = pd.read_csv(SOURCE_COHORT, keep_default_na=False)
    if len(original) != 39 or original.role.value_counts().to_dict() != {"owner": 20, "control": 19}:
        raise ValueError("original cohort no longer matches the frozen 20-owner/19-control ledger")
    banned_symbols = set(original.loc[original.symbol.ne(""), "symbol"].astype(str))
    if original.loc[original.role.eq("control") & original.coverage.eq("complete"), "symbol"].nunique() != 19:
        raise ValueError("expected 19 unique original control symbols")

    cards = shared._read_cards()
    owners = [_case_event(cards, spec) for spec in FIXED_OWNER_EVENTS]
    added_owner_symbols = {str(event["symbol"]) for event in owners}
    banned_symbols |= added_owner_symbols

    temp_context = tempfile.TemporaryDirectory(prefix="month-firstweek-ruler-addendum-")
    temp_root = Path(temp_context.name)
    shared.local_bars._MONTH_ZIP_ROOTS = tuple(shared.local_bars._MONTH_ZIP_ROOTS) + (temp_root,)
    archive_attempts: dict[tuple[str, str], str] = {}
    owner_tapes: dict[str, object] = {}

    for event in owners:
        event["direction_close"] = float(event["direction_close"])
        tape, coverage, reason = shared._load_tape(event, temp_root, archive_attempts)
        if coverage != "complete":
            raise ValueError(f"{event['case_id']} is unavailable: {coverage}: {reason}")
        owner_tapes[event["case_id"]] = tape

    selected_controls: list[tuple[dict, object, int, str]] = []
    used_control_symbols: set[str] = set()
    for owner in owners:
        candidates = shared._candidate_pool(cards, owner, banned_symbols)
        candidates.sort(key=lambda candidate: shared._match_key(owner, candidate))
        failures: list[str] = []
        chosen = None
        chosen_tape = None
        chosen_rank = 0
        for rank, candidate in enumerate(candidates, start=1):
            symbol = str(candidate["symbol"])
            if symbol in banned_symbols or symbol in used_control_symbols:
                continue
            tape, coverage, reason = shared._load_tape(candidate, temp_root, archive_attempts)
            if coverage != "complete":
                failures.append(f"{candidate['card_id']}:{coverage}")
                continue
            chosen, chosen_tape, chosen_rank = candidate, tape, rank
            break
        if chosen is None:
            raise RuntimeError(
                f"{owner['case_id']}: no complete unique different-symbol control after outcome-blind matching; "
                + ";".join(failures[:30])
            )
        used_control_symbols.add(str(chosen["symbol"]))
        control_id = owner["case_id"].replace("photo_", "control_", 1)
        selected_controls.append((dict(chosen, case_id=control_id, pair_id=owner["pair_id"],
                                      role="control", source_photo_id=owner["source_photo_id"],
                                      candidate_type="ordinary_daily_first_touch", coverage="complete", reason=""),
                                  chosen_tape, chosen_rank, ";".join(failures[:30])))

    # Persist every tape before publishing the atomic cohort ledger.
    records: list[dict] = []
    control_by_pair = {record[0]["pair_id"]: record for record in selected_controls}
    for owner in owners:
        owner_path = shared._write_tape(owner["case_id"], owner_tapes[owner["case_id"]])
        records.append(shared._record(owner, tape_path=owner_path, coverage="complete"))
        control, tape, rank, rejections = control_by_pair[owner["pair_id"]]
        control_path = shared._write_tape(control["case_id"], tape)
        match_rule = ("same_month_side;score=(asset_class,month_first_or_retouch,wick_reclaim,close_reclaim,"
                      "month_week,day,hour,absolute_time); unique_symbol; excluded_original_owners_and_controls")
        records.append(shared._record(control, tape_path=control_path, coverage="complete",
                                      match_rank=rank, match_rule=match_rule,
                                      match_rejections=rejections))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp_csv = OUT.with_name(f".{OUT.name}.tmp")
    pd.DataFrame(records, columns=shared.CSV_COLUMNS).to_csv(temp_csv, index=False, encoding="utf-8")
    temp_csv.replace(OUT)
    temp_context.cleanup()

    print(f"ruler_cohort={OUT} rows={len(records)} pairs={len(selected_controls)} "
          f"complete_tapes={len(proposed_tape_ids)} controls={sorted(used_control_symbols)}")
    print(f"excluded_original_owner_symbols={len(banned_symbols)} "
          f"excluded_original_control_symbols={original.loc[original.role.eq('control'), 'symbol'].nunique()}")
    for key, status in sorted(archive_attempts.items()):
        if status != "registered_local_source":
            print(f"archive {key[0]} {key[1]} {status}")


if __name__ == "__main__":
    _main()
