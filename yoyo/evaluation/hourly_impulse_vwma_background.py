"""V27 saved-frame adapter: VWMA40 entry background, unchanged SMA5 evidence.

Pure transformation, no I/O, allocation, execution, labels or outcome fields.
Exact native UTC hour joins only; the old frame may have a longer RIGHT tail,
but its left edge and every internal source hour must match the full V26 range.
First prove every old SMA feature and every common OHLCV/ATR/shape feature.
ATR-fraction terciles remain prior720/min168/shift1/linear per hourly segment;
their original values are retained only after independent recomputation parity.

VWMA changes 1h MA, side, three-hour slope, prior24 flips, hourly colour/slope
validity and current/prior strict body-cross exclusions. Old hourly validity is
also retained as a conservative inherited availability gate, never relaxed.
Original 5m SMA colour/availability/source IDs remain byte-value unchanged:
these are inherited management-source evidence, not extra exact matching keys.
Only month / UTC6h / ATR bucket are matched later by the frozen V23 graph.
Do not use the old K2 assign_controls: it still imposes six exact keys.

Only real NEW mother decision times are excluded, not unioned with the old
251 list. No future cross is excluded. Raw and hourly segment IDs are different
domains; only hourly IDs are checked against hour gaps. Current next-open risk
remains a graph responsibility and cannot remove an original mother here.

pandas2.3.3 rolling quantile defaults to linear interpolation:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.core.window.rolling.Rolling.quantile.html
No data-collection/DOE dependencies are added to the project Python3.9 runtime.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, ENTRY_COLUMNS, make_entries
from yoyo.evaluation.hourly_impulse_background_support import FOLDS, FRAME_FIELDS, _validate
from yoyo.evaluation.hourly_impulse_vwma import FEATURE_COLUMNS, REFERENCE_COLUMNS, _hour
from yoyo.evaluation.hourly_impulse_vwma_support import PARAMS

REFERENCE_DEPENDENT = ("ma", "ma_side", "ma_slope_atr", "cross_count24")
COMMON = tuple(BAR_COLUMNS)+("segment_id",)+tuple(c for c in FEATURE_COLUMNS if c not in REFERENCE_DEPENDENT)
INHERITED = ("entry_open", "source_segment_id", "entry_source_segment_id", "known_entry_open",
             "entry_source_continuous", "known_5m_available", "known_5m_colour", "known_5m_valid",
             "management_source_segment_id")
BUCKET_COLUMNS = ("atr_fraction", "atr_tercile_low", "atr_tercile_high", "vol_bucket")
OLD_HOURLY = REFERENCE_DEPENDENT+("known_hourly_colour", "unsigned_hourly_slope_sign", "known_hourly_valid")
PREFIXES = ("inherited_sma_", "exit_sma5_", "entry_reference_")


def _frame(frame, required, name):
    if not isinstance(frame,pd.DataFrame) or not frame.columns.is_unique or not set(required).issubset(frame):
        raise ValueError(name+" requires unique complete schema")
    if any(any(word in str(c).lower() for word in ("outcome","net_return","gross_return","mfe","mae","exit_price")) for c in frame):
        raise ValueError("Outcome columns forbidden: "+name)
    if any(str(c).startswith(PREFIXES) for c in frame):
        raise ValueError("Preexisting adapter diagnostics: "+name)
    out=frame.copy(deep=True).reset_index(drop=True)
    for key in ("open_time","signal_time","decision_time"):
        if key in out:
            out[key]=pd.array([_hour(v) for v in out[key]],dtype="datetime64[ns, UTC]")
    if "open_time" in out and (not out.open_time.is_unique or not out.open_time.is_monotonic_increasing):
        raise ValueError(name+" requires unique sorted hours")
    return out


def _same(a,b,columns,name):
    try:
        pd.testing.assert_frame_equal(a[list(columns)].reset_index(drop=True),b[list(columns)].reset_index(drop=True),
                                      check_dtype=False,rtol=1e-12,atol=1e-12)
    except AssertionError as error:
        raise ValueError(name+" parity mismatch") from error


def _boolean(values,name):
    if values.isna().any() or not values.map(lambda v:isinstance(v,(bool,np.bool_))).all():
        raise ValueError(name+" must be explicit booleans")


def _featured(frame,kind):
    out=_frame(frame,COMMON+REFERENCE_DEPENDENT+REFERENCE_COLUMNS,kind)
    if out.empty:
        raise ValueError("Full source feature range cannot be empty")
    for key,value in (("ma_kind",kind),("ma_length",40),("bar_minutes",60)):
        # CSV drops attrs; recorded numeric/feature parity remains mandatory.
        if key in frame.attrs and frame.attrs[key]!=value:
            raise ValueError(kind+" feature attrs conflict: "+key)
    segment=out.open_time.diff().ne(pd.Timedelta(hours=1)).cumsum().sub(1)
    if not out.segment_id.eq(segment).all():
        raise ValueError("Feature segments must be timestamp-derived hourly IDs")
    _boolean(out.reference_known,kind+" reference_known")
    _boolean(out.reference_volume_valid,kind+" reference_volume_valid")
    if not out.reference_known.eq(np.isfinite(out.ma)).all():
        raise ValueError(kind+" reference knownness disagrees with MA")
    side=np.where(out.ma.isna(),0,np.where(out.hl2>=out.ma,1,-1))
    if not out.ma_side.eq(side).all():
        raise ValueError(kind+" side mismatch")
    available=pd.array([_hour(v) for v in out.reference_available_at],dtype="datetime64[ns, UTC]")
    if not (available==out.open_time+pd.Timedelta(hours=1)).all():
        raise ValueError(kind+" reference availability mismatch")
    return out


def rebuild_matching(mothers,old_matching,sma_hourly,vwma_hourly):
    """Return new matching frame and source-only diagnostics; preserve all mothers.

    `mothers` is the unmodified V26 ENTRY_COLUMNS + fold contract. The caller
    enforces its pinned 288 membership/fourfold counts and graph coverage260.
    New matching rows cover every observed source hour in the V26 saved range;
    a shared physical gap remains a gap (not filled). Missing rows in just one
    source are a lineage error, not permission to inner-join them away.
    """
    m=_frame(mothers,ENTRY_COLUMNS+["fold"],"mothers")
    a,b=_featured(sma_hourly,"SMA"),_featured(vwma_hourly,"VWMA")
    h=_frame(old_matching,tuple(FRAME_FIELDS)+tuple(FEATURE_COLUMNS)+tuple(BAR_COLUMNS)+
             ("segment_id",)+BUCKET_COLUMNS+INHERITED,"old matching")
    _same(a,b,COMMON,"Two-arm common features")
    if h.empty or h.open_time.iloc[0]!=a.open_time.iloc[0] or h.open_time.iloc[-1]<a.open_time.iloc[-1]:
        raise ValueError("Old matching must cover the identical left edge and full V26 range")
    old_rows=len(h)
    h=h.loc[h.open_time.le(a.open_time.iloc[-1])].reset_index(drop=True)
    _same(h,a,tuple(BAR_COLUMNS)+("segment_id",)+tuple(FEATURE_COLUMNS),"Frozen old SMA features")
    if not h.signal_time.eq(h.open_time).all() or not h.decision_time.eq(h.open_time+pd.Timedelta(hours=1)).all():
        raise ValueError("Old matching own clocks disagree")
    # Validate old support/exclusion flags BEFORE changing their reference.
    _validate(m,h)
    limits={fold:(pd.Timestamp(start,tz="UTC"),pd.Timestamp(end,tz="UTC")-pd.Timedelta(hours=72))
            for fold,start,end in FOLDS}
    if any(not limits[r.fold][0]<=r.decision_time<limits[r.fold][1] for r in m.itertuples()):
        raise ValueError("Mother fold/72h embargo clock mismatch")
    if not h.signal_atr.eq(h.atr).all():
        if not np.allclose(h.signal_atr,h.atr,rtol=1e-12,atol=1e-12,equal_nan=True):
            raise ValueError("Old signal ATR mismatch")
    fraction=a.atr/a.close
    expected=pd.DataFrame({"atr_fraction":fraction})
    for key,q in (("atr_tercile_low",1/3),("atr_tercile_high",2/3)):
        expected[key]=fraction.groupby(a.segment_id).transform(lambda s:s.shift(1).rolling(720,min_periods=168).quantile(q))
    good=expected.notna().all(axis=1)
    expected["vol_bucket"]=(fraction.gt(expected.atr_tercile_low).astype(int)+fraction.gt(expected.atr_tercile_high).astype(int)).where(good).astype("Int64")
    _same(h,expected,BUCKET_COLUMNS,"Causal ATR bucket")
    # Prove each new original request belongs to its own VWMA hour; never
    # transfer old SMA or another mother's values into a new request.
    selected=make_entries(b,PARAMS).set_index("event_id")
    if not set(m.event_id).issubset(selected.index):
        raise ValueError("Mother is not a frozen VWMA entry")
    actual=m[ENTRY_COLUMNS].sort_values("event_id").reset_index(drop=True)
    want=selected.loc[m.event_id].reset_index().sort_values("event_id").reset_index(drop=True)
    _same(actual,want,ENTRY_COLUMNS,"Own VWMA mother")
    out=h.copy(deep=True)
    for key in OLD_HOURLY:
        out["inherited_sma_"+key]=h[key]
    for key in ("known_5m_colour","known_5m_available","known_5m_valid","management_source_segment_id"):
        out["exit_sma5_"+key]=h[key]
    for key in REFERENCE_DEPENDENT+REFERENCE_COLUMNS:
        out[key]=b[key]
    out["known_hourly_colour"]=b.ma_side
    out["unsigned_hourly_slope_sign"]=np.sign(b.ma_slope_atr)
    out["known_hourly_valid"]=(h.known_hourly_valid & b.reference_known & b.ma_side.isin([-1,1]) &
                                 np.isfinite(b.ma_slope_atr) & np.isfinite(b.ma))
    out["entry_reference_kind"]="VWMA"
    out["entry_reference_length"]=40
    cross=(out.open.lt(out.ma)&out.close.gt(out.ma))|(out.open.gt(out.ma)&out.close.lt(out.ma))
    times=set(out.loc[cross,"decision_time"])
    out["raw_strict_body_cross"]=cross
    out["current_or_prior_cross_excluded"]=out.decision_time.isin(times|{t+pd.Timedelta(hours=1) for t in times})
    out["actual_mother_decision_excluded"]=out.decision_time.isin(set(m.decision_time))
    out["matching_support"]=(out.vol_bucket.notna() & out.signal_atr.gt(0) & np.isfinite(out.signal_atr) &
                              out.known_entry_open & out.entry_source_continuous & out.known_5m_valid & out.known_hourly_valid)
    out["candidate_eligible"]=out.matching_support & ~out.current_or_prior_cross_excluded & ~out.actual_mother_decision_excluded
    _same(out,h,INHERITED+BUCKET_COLUMNS,"Inherited source/exit evidence")
    _validate(m,out)
    out.attrs.update(old_matching.attrs)
    out.attrs.update(entry_reference="VWMA40_HL2",management_reference="SMA5_HL2",raw_source_rebuilt=False)
    diagnostics={"mothers":len(m),"source_rows":len(out),"old_right_tail_rows_excluded":old_rows-len(out),
                 "old_sma_all_feature_parity":True,"new_mother_all_entry_field_parity":True,
                 "causal_atr_bucket_parity":True,"inherited_source_and_sma5_unchanged":True,
                 "known_hourly_rule":"inherited_known_and_VWMA_side_and_finite_slope",
                 "matching_keys":["month","utc_6h_bucket","vol_bucket"],
                 "current_prior_cross_rebuilt":True,"actual_mother_exclusion_rebuilt":True,
                 "old_candidate_count":int(h.candidate_eligible.sum()),"new_candidate_count":int(out.candidate_eligible.sum()),
                 "old_actual_times":int(h.actual_mother_decision_excluded.sum()),"new_actual_times":int(out.actual_mother_decision_excluded.sum()),
                 "allocation_performed":False,"outcomes_used":False,"raw_source_rebuilt":False}
    return out,diagnostics
