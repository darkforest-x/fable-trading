"""Reconcile Binance USD-M listings with current and archived instrument classifications."""
from pathlib import Path
import json
import sys
import pandas as pd
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from yoyo.data.binance_um_vision_listing import symbols,_list
DEST=ROOT/'data/research/alt-isolated-12h-wicks-20261111/binance'
# Stablecoin, bullion, major coin and exchange-compiled index underlyings.
EXCLUDE=set('BTC ETH FOOTBALL DOTECO BTCDOM DEFI BLUEBIRD BTCDOMINANCE BUSD USDC FDUSD TUSD USDP USDT USD1 USDE USDD DAI UST USTC USTCUSDT PAXG XAUT XAU XAG'.split())


def get_roster():
    DEST.mkdir(exist_ok=True,parents=True)
    cached=DEST/'instrument_snapshot.json'
    if not cached.exists():
        current=requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo',timeout=30);current.raise_for_status()
        cached.write_text(json.dumps(current.json(),ensure_ascii=False))
    instruments=json.loads(cached.read_text())['symbols']
    now={r['symbol']:r for r in instruments}
    p=ROOT/'experiments/active/exp-cloud-close-archive-complete-20261109-v1'
    old=pd.read_csv(p/'original_contract_classification.csv').fillna('')
    meta={r.symbol:(r.archive_underlyingType,r.archive_contractType,r.archive_baseAsset) for r in old.itertuples()}
    extra=pd.read_csv(p/'supplemental_contract_evidence.csv').fillna('')
    meta.update({r.symbol:(r.underlyingType,r.contractType,r.baseAsset) for r in extra.itertuples()})
    archive=DEST/'archive_symbols.json'
    if not archive.exists():
        monthly=set(symbols())
        base='data/futures/um/daily/klines/'
        daily={v[len(base):].strip('/') for v in _list(base) if v.startswith(base)}
        archive.write_text(json.dumps(sorted(monthly|daily),ensure_ascii=False))
    listed=set(json.loads(archive.read_text()))
    rows=[]
    for symbol in sorted(listed|set(now)|set(meta)):
        if not symbol.endswith('USDT') or '_' in symbol:continue
        z=now.get(symbol,{})
        underlying,contract,base=meta.get(symbol,('','',symbol[:-4]))
        if z:
            underlying,contract,base=z['underlyingType'],z['contractType'],z['baseAsset']
        why=('major/stablecoin/bullion/index' if base in EXCLUDE else
             'non-crypto' if underlying and underlying not in ('COIN','PREMARKET') else
             'non-perpetual' if contract and contract not in ('PERPETUAL','TRADIFI_PERPETUAL') else
             'verified_crypto' if underlying else 'archive_only_unclassified')
        rows.append(dict(symbol=symbol,eligible=why in ('verified_crypto','archive_only_unclassified'),
                         basis=why,underlying=underlying,contract_type=contract,base=base,
                         current=bool(z),archive=symbol in listed,status=z.get('status',''),
                         listed_ms=z.get('onboardDate',''),end_ms=z.get('deliveryDate','')))
    a=pd.DataFrame(rows)
    a.to_csv(DEST/'universe_audit.csv',index=False)
    a[a.eligible].to_csv(DEST/'universe_eligible.csv',index=False)
    print(a.groupby('basis').size().to_string(),flush=True)
    print('unknown:',a[a.basis=='archive_only_unclassified'].symbol.tolist(),flush=True)


if __name__=='__main__':
    get_roster()
