"""The frozen holdout boundary. One definition, because there were ten.

CLAUDE.md rule 1 makes the holdout the most expensive object in the project:
reading it costs an owner authorisation and a permanent ledger entry, and a
configuration that reads it by accident has spent something that cannot be
refunded. A constant with that property should exist once.

At consolidation it existed ten times, under six names, in five repositories'
worth of code that now shares one tree:

    yoyo/layers/l2_judgment/train.py        HOLDOUT_START
    yoyo/datasets/gold_render.py            HOLD_DEFAULT
    src/judgment/p1_dataset.py              HOLDOUT_CUTOFF
    src/judgment/p2_protocol.py             HOLDOUT_CUTOFF
    src/detection/eth3m_v2_validation.py    HOLDOUT_START
    src/detection/eth3m_v2_quality_audit.py HOLDOUT_START
    src/detection/eth3m_v2_evidence.py      HOLDOUT_START
    src/backtest/run.py                     ACCEPT_START
    configs/local_signal_v2_p1.yaml         holdout_start_exclusive
    configs/labelstudio/gold_annotation_v1.json  holdout_start
    configs/numeric_baseline/mvp.yaml       data_end_boundary

All eleven agree on 2026-05-04T00:00:00Z today. Nothing made them agree, and
nothing would have said so if one had been edited: they are in different files,
under different names, and no two of them are read by the same test. The
failure mode is quiet and expensive -- a config that is one day early trains on
holdout bars and reports a clean number.

This module is the single definition. The others are not rewritten here --
rewriting a constant inside the live judgment path is a change to running code,
and this task's first rule is to change nothing that runs. Instead
`tests/causality/test_holdout_boundary_is_single_valued.py` reads every one of
them and fails if any disagrees, so drift is caught at the moment it is typed.
Wiring them to import from here is a separate, provable step.

The boundary is EXCLUSIVE: a bar belongs to the holdout when its time is at or
after it. `is_holdout` and `is_pre_holdout` are the two ways to ask, so that
nobody has to remember which side `>=` falls on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: Frozen 2026-05-04T00:00:00Z. An owner decision; changing it is not a refactor.
HOLDOUT_START = datetime(2026, 5, 4, 0, 0, 0, tzinfo=timezone.utc)

#: The same instant as an ISO-8601 string, for configs and manifests.
HOLDOUT_START_ISO = "2026-05-04T00:00:00+00:00"


class HoldoutBoundaryError(ValueError):
    """A time that cannot be placed relative to the boundary. Never guessed."""


def _coerce(value: Any) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HoldoutBoundaryError(f"not an ISO-8601 instant: {value!r}") from exc
    elif hasattr(value, "to_pydatetime"):  # pandas.Timestamp, without importing pandas
        moment = value.to_pydatetime()
    else:
        raise HoldoutBoundaryError(
            f"cannot place {value!r} ({type(value).__name__}) relative to the holdout boundary"
        )
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise HoldoutBoundaryError(
            f"{value!r} is naive. A bar time with no zone is 8 hours from being on the "
            "wrong side of the boundary, and CST is the zone this project reports in."
        )
    return moment


def is_holdout(when: Any) -> bool:
    """True when `when` is at or after the boundary, i.e. inside the holdout."""
    return _coerce(when) >= HOLDOUT_START


def is_pre_holdout(when: Any) -> bool:
    """True when `when` is strictly before the boundary."""
    return not is_holdout(when)


def assert_pre_holdout(when: Any, *, what: str = "this record") -> None:
    """Fail closed on a holdout read that nobody authorised.

    Use at the point of reading, not at the point of reporting. A pipeline that
    discovers the read afterwards has already spent the window.
    """
    moment = _coerce(when)
    if moment >= HOLDOUT_START:
        raise HoldoutBoundaryError(
            f"{what} is at {moment.isoformat()}, at or after the frozen holdout boundary "
            f"{HOLDOUT_START.isoformat()}. Reading it requires an explicit owner "
            "authorisation recorded in the report as consumption number N for this "
            "configuration (CLAUDE.md rule 1)."
        )
