"""Audit known Binance delivery boundaries in the completed 61-day study.

Only reads authenticated frozen manifests, candidate/control events and all
cashbooks. No strategy, matching, return evaluation, or network request runs.
Writes only diagnostics/previous_study_delivery_audit.json beside this script.
"""
from pathlib import Path
import hashlib,json
import pandas as pd

ROOT=Path('/Users/zhangzc/fable-trading')
RESULTS=ROOT/'experiments/active/exp-spike-burst-validation-20260910-v1/results'
OUTPUT=ROOT/'experiments/active/exp-spike-burst-three-year-20260910-v1/diagnostics/previous_study_delivery_audit.json'
END=pd.Timestamp('2026-09-09T00:00:00Z')
sources=[]
def checked(path,expected=None,role='input'):
    path=Path(path)
    raw=path.read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if expected is not None:
        assert digest==expected,str(path)
    sources.append(dict(path=str(path),sha256=digest,size_bytes=len(raw),role=role))
    return raw

dm=json.loads(checked(RESULTS/'dataset_manifest.json',role='dataset_manifest'))
vm=json.loads(checked(RESULTS/'validation_manifest.json',role='validation_manifest'))
auth={str(Path(a['path']).resolve()):a['sha256'] for a in dm['artifacts']+vm['artifacts']}
catalog=next(a for a in dm['catalog_sources'] if a['path'].endswith('/binance.json'))
markets=json.loads(checked(catalog['path'],catalog['sha256'],'frozen_Binance_catalog'))['markets']
known={}
for market in markets:
    value=market['raw'].get('deliveryDate')
    if value is not None and 0<int(value)<END.value//1000000:
        known[market['symbol']]=pd.to_datetime(int(value),unit='ms',utc=True)

def read(path):
    path=Path(path)
    checked(path,auth[str(path.resolve())],'frozen_result')
    return pd.read_csv(path,low_memory=False)

def audit_rows(frame):
    b=frame.loc[frame.venue.eq('binance')].copy()
    z=b.loc[b.symbol.isin(known)].copy()
    z['known_delivery_utc']=z.symbol.map(known)
    for c in ['decision_time','entry_time','exit_time_lower','exit_time_upper']:
        z[c]=pd.to_datetime(z[c],utc=True)
    z['decision_at_or_after_delivery']=z.decision_time.ge(z.known_delivery_utc)
    z['entry_at_or_after_delivery']=z.entry_time.ge(z.known_delivery_utc)
    z['holding_extends_beyond_delivery']=z.exit_time_upper.gt(z.known_delivery_utc)
    unsafe=z.decision_at_or_after_delivery|z.entry_at_or_after_delivery|z.holding_extends_beyond_delivery
    cols=['event_id','matched_event_id','control_number','symbol','asset','minutes','arm','valid',
          'decision_time','entry_time','exit_time_lower','exit_time_upper','known_delivery_utc',
          'decision_at_or_after_delivery','entry_at_or_after_delivery','holding_extends_beyond_delivery',
          'exit_reason','net_return','signal_quote_volume','prior24h_quote_volume']
    cols.extend(c for c in ['portfolio_selected','portfolio_rejection','notional','realized_net_pnl'] if c in z)
    def records(x):
        return json.loads(x[cols].to_json(orient='records',date_format='iso'))
    return dict(total_rows=len(frame),Binance_rows=len(b),rows_with_known_delivery_before_study_end=len(z),
        decision_at_or_after_delivery=int(z.decision_at_or_after_delivery.sum()),
        entry_at_or_after_delivery=int(z.entry_at_or_after_delivery.sum()),
        holding_extends_beyond_delivery=int(z.holding_extends_beyond_delivery.sum()),
        affected_rows=int(unsafe.sum()),affected=records(z.loc[unsafe]))

events=audit_rows(read(RESULTS/'events.csv.gz'))
controls=audit_rows(read(RESULTS/'controls.csv.gz'))
accounts=[]
allocated_total=allocated_Binance=allocated_affected=0
for path in sorted((RESULTS/'accounts').glob('*_ledger.csv.gz')):
    frame=read(path)
    outcome=audit_rows(frame)
    selected=frame.portfolio_selected.eq(True)
    allocated_total+=int(selected.sum())
    allocated_Binance+=int((selected&frame.venue.eq('binance')).sum())
    found=sum(bool(x['portfolio_selected']) for x in outcome['affected'])
    allocated_affected+=found
    accounts.append(dict(path=str(path),selected_count=int(selected.sum()),
        selected_Binance_count=int((selected&frame.venue.eq('binance')).sum()),
        allocated_boundary_violations=found,**outcome))

assert len(accounts)==60 and allocated_total==7775 and allocated_affected==0
assert events['affected_rows']==1 and controls['affected_rows']==3
assert all(a['arm']=='focus_trail' and a['minutes']==60 and a['symbol']=='ACXUSDT'
           for a in events['affected']+controls['affected'])
result=dict(schema='previous-spike-study-delivery-audit-v1',
    created_at=str(pd.Timestamp.now(tz='UTC')),sources=sources,
    new_backtest=False,parameter_changes=False,network_requests=False,
    definition=dict(study='UTC [2026-07-10,2026-09-09)',cut_source='authenticated Binance catalog raw.deliveryDate',
        entry_invalid='entry_time >= known deliveryDate',
        holding_invalid='exit_time_upper > known deliveryDate; exits exactly at delivery are not labelled extending beyond',
        allocated_denominator='All 60 cashbooks, including overlapping full/paired/without-USeless accounts; not independent or unique trades',
        coverage='Known Binance delivery dates only; no assertion of complete historical delisting coverage on all exchanges'),
    known_delivery_before_end_count=len(known),
    known_delivery_before_end={k:t.isoformat() for k,t in known.items()},
    events=events,controls=controls,accounts=accounts,
    totals=dict(cashbooks=len(accounts),selected_rows_including_overlapping_accounts=allocated_total,
        selected_Binance_rows_including_overlapping_accounts=allocated_Binance,
        selected_known_delivery_violations=allocated_affected,
        affected_candidate_copies_across_cashbooks=sum(a['affected_rows'] for a in accounts),
        unique_affected_actual_events=events['affected_rows'],unique_affected_controls=controls['affected_rows']),
    interpretation=[
        'No selected position in these frozen cashbooks enters or remains held beyond a known Binance delivery boundary.',
        'This does not mean the event dataset is clean: one focus1H actual and three controls lie after ACX delivery and were scored as -0.2% boundary marks.',
        'Affected cashbook rows all have zero notional and rejection ten_asset_limit. Signal quote turnover is also zero, but that is not the recorded earlier rejection reason.',
        'SPIKE burst events/controls and 4H events/controls have zero detected known-delivery violations.',
        'The old focus event denominators and its matched event statistics retain these contaminated rows.',
        'Removing expired bars and drawing controls again could change matched schedules or account results; no corrected re-evaluation was done here.',
        '130 catalog symbols with a delivery date before the study end is not the same denominator as the adapter audit of 128 symbols with observed post-delivery placeholder rows.'
    ],
    reproduce_command='.venv/bin/python experiments/active/exp-spike-burst-three-year-20260910-v1/diagnostics/reproduce_previous_study_delivery_audit.py')
OUTPUT.parent.mkdir(parents=True,exist_ok=True)
OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(json.dumps(result['totals'],indent=2))
print('output',OUTPUT,'sha256',hashlib.sha256(OUTPUT.read_bytes()).hexdigest())
