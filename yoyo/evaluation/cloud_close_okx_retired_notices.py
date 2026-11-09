"""Extract exact venue-reported USDT perpetual identities and delivery times from OKX notices."""
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from dateutil.parser import parse

from yoyo.evaluation import cloud_close_november_prices as binance
from yoyo.evaluation import cloud_close_okx_retired_prices as prices

EXP = prices.EXP.parent/'exp-cloud-close-okx-retired-notices-20261109-v1'
CACHE = prices.CACHE.parent/'cloud_close_okx_retired_notices'
BASE = 'https://www.okx.com'
MONTH = re.compile(r'January|February|March|April|May|June|July|August|September|October|November|December',re.I)


def get(url,path,allow_404=False):
    if path.is_file():
        return path.read_text()
    res = requests.get(url,timeout=(8,25))
    if allow_404 and res.status_code==404:
        return None
    res.raise_for_status()
    path.parent.mkdir(parents=True,exist_ok=True)
    text = res.content.decode('utf-8')
    path.write_text(text)
    return text


def plain(content):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>',' ',content)).split())


def time_utc(value):
    if (not MONTH.search(value) or not re.search(r'\bUTC\b',value,re.I)
            or len(re.findall(r'\d{1,2}:\d{2}',value))!=1 or re.search(r'[~–]|\s-\s',value)):
        return None
    try:
        stamp = parse(value,fuzzy=True)
        if stamp.tzinfo is None:
            return None
        return pd.Timestamp(stamp.astimezone(timezone.utc)).isoformat()
    except (ValueError,OverflowError):
        return None


def article(url):
    body = get(url,CACHE/'articles'/(url.rsplit('/',1)[-1]+'.html'))
    rows = []
    for table in re.findall(r'<table\b[^>]*>(.*?)</table>',body,re.S|re.I):
        trs = re.findall(r'<tr\b[^>]*>(.*?)</tr>',table,re.S|re.I)
        if not trs:
            continue
        head = plain(trs[0]).lower()
        if not (re.search(r'perpetual|swap|futures name|futures contract',head) and
                re.search(r'delist|delivery time|termination',head)):
            continue
        # Keep the original text if a multirow or unusual time is unclear.
        inherited_time, remaining = '',0
        for tr in trs[1:]:
            cells = re.findall(r'<t[dh]\b([^>]*)>(.*?)</t[dh]>',tr,re.S|re.I)
            if not cells:
                continue
            name = plain(cells[0][1])
            ids = list(dict.fromkeys(re.findall(r'(?<![A-Z0-9])([A-Z0-9]+)[/-]?USDT(?:-SWAP)?(?![A-Z0-9])',name.upper())))
            if not ids:
                continue
            if len(cells)>1:
                value = plain(cells[1][1])
                rowspan = re.search(r'rowspan=["\']?(\d+)',cells[1][0],re.I)
                inherited_time,remaining = value,int(rowspan.group(1))-1 if rowspan else 0
            elif remaining>0:
                value = inherited_time
                remaining -= 1
            else:
                value = ''
            for sym in ids:
                rows.append(dict(symbol=sym,notice=url,table_header=head,instrument_cell=name,time_cell=value,
                                 delivery_utc=time_utc(value)))
    return rows


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed historical OKX notices catalog')
    cfg = json.loads((EXP/'config.json').read_text())
    articles = []
    for page in range(1,101):
        text = get(cfg['directory']+str(page),CACHE/f'page_{page}.html',allow_404=True)
        if text is None:
            break
        listing = re.search(r'<ul\b[^>]*aria-label="Articles in [^"]+"[^>]*>(.*?)</ul>',text,re.S|re.I)
        if not listing:
            raise ValueError(f'Missing actual announcement list on page {page}')
        found = list(dict.fromkeys(urljoin(BASE,u) for u in re.findall(r'href="(/help/[^"]+)"',listing.group(1))))
        if not found:
            break
        if not set(found)-set(articles):
            raise ValueError(f'Global directory repeated an earlier page {page}')
        articles.extend(found)
    else:
        raise ValueError('Historical notice catalog exceeds bounded directory census')
    urls = list(dict.fromkeys(articles))
    with ThreadPoolExecutor(max_workers=5) as pool:
        outputs = list(pool.map(article,urls))
    items = pd.DataFrame([r for block in outputs for r in block])
    items.to_csv(EXP/'perpetual_notice_rows.csv.gz',index=False)
    native = pd.read_csv(prices.EXP/'coverage.csv.gz').merge(pd.read_csv(
        prices.audit.EXP/'historical_directory_checks.csv.gz')[['instrument','recorded_to']],on='instrument',validate='one_to_one')
    native['symbol'] = native.instrument.str.removesuffix('-USDT-SWAP')
    roster = binance._fixed_contract_periods()[1]['fixed_symbols']
    old_cfg = json.loads((EXP.parent/'exp-cloud-close-okx-retired-account-20261109-v1/config.json').read_text())
    earlier = {s for n in old_cfg['notices'] for s in n['symbols']}
    pool = items.merge(native[['symbol','recorded_to','hours','last_ms']],on='symbol')
    pool['prior_crypto_identity'] = (pool.symbol+'USDT').isin(roster)|pool.symbol.isin(earlier)
    pool['delivery'] = pd.to_datetime(pool.delivery_utc,utc=True)
    pool['recording_distance_days'] = (pool.delivery-pd.to_datetime(pool.recorded_to,utc=True)).dt.total_seconds().abs()/86400
    last_open = pd.to_datetime(pool.last_ms,unit='ms',utc=True)
    pool['selected'] = (pool.prior_crypto_identity & ~pool.symbol.isin(cfg['excluded_identities']) &
            pool.recording_distance_days.le(7) & pool.hours.gt(0) & (last_open<pool.delivery) &
            pool.delivery.lt(pd.Timestamp(cfg['date_cutoff'])))
    dup = pool[pool.selected].groupby('symbol').delivery.nunique()
    pool.loc[pool.symbol.isin(dup[dup>1].index),'selected'] = False
    pool.to_csv(EXP/'crosschecked_lifecycles.csv',index=False)
    picked = pool[pool.selected].drop_duplicates(['symbol','delivery'])
    additions = sorted(set(picked.symbol)-earlier)
    prices.safe_json(EXP/'summary.json',dict(directory_pages=page-1,articles=len(urls),usdt_perpetual_rows=len(items),
          matched_omitted_names=pool.symbol.nunique(),confirmed_names=len(picked),additional_to_first_17=additions,
          scope='Partial global English directory; membership and original identity crosschecks precede portfolio results. A candidate without a matched notice remains unknown.'))
    print('additional documented crypto contracts',len(additions),additions,flush=True)


if __name__=='__main__':
    main()
