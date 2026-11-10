"""Export customer-facing excerpts from the checked-in historical research books, never live accounts."""
import base64
import json
from pathlib import Path
from xml.etree import ElementTree as ET

SITE = Path(__file__).resolve().parents[1]
BOOKS = SITE.parents[1] / 'monitor/static/strategy-booklet'
ASSETS = SITE / 'dist/assets'
# Projected fields are explicitly allowed. No service/API clients or account records are inputs.
KEYS = ('5','60','allin3','v3','v3add','v3add20','v3lock','spike','live-allin3','live-breadth',
        'live-wick3x3','live-wick3x3far','live-wick6x3','live-wick6x3hi','live-wick6x3mid','live-cloud-v17')

def main():
    books = {}
    for key in KEYS:
        src = json.loads((BOOKS / f'{key}-history.json').read_text())
        rows = [[coin, int(open_ms), int(close_ms), side]
                for coin, open_ms, close_ms, side, *_ in src['recent'][:3]]
        if not all(side in ('多','空') and end >= start > 0 for _, start,end,side in rows):
            raise ValueError(key)
        books[key] = {'count': src['summary']['trades'], 'rows':rows}
    archive = json.loads((BOOKS / 'cloud-v17-history/manifest.json').read_text())
    if archive['tradeCount'] != len(archive['records']): raise ValueError('archive count')
    charts = []
    for number, row in enumerate(archive['records'][:3], start=1):
        name = f'cloud-v17-{number:06d}'
        if row['id'] != name: raise ValueError('unexpected archive order')
        root = ET.parse(BOOKS / f'cloud-v17-history/charts/{name}.svg').getroot()
        img, = root.findall('{http://www.w3.org/2000/svg}image')
        href = img.get('href') or img.get('{http://www.w3.org/1999/xlink}href', '')
        meta, content = href.split(',',1)
        if meta != 'data:image/webp;base64': raise ValueError('expected embedded original')
        data = base64.b64decode(content, validate=True)
        if not (data[:4] == b'RIFF' and data[8:12] == b'WEBP'): raise ValueError('image type')
        file = f'replay-{number:02d}.webp'; (ASSETS / file).write_bytes(data)
        charts.append({'id':number, 'symbol':row['symbol'],'side':'做空' if row['side']==-1 else '做多',
                       'entry':row['entryMs'],'exit':row['exitMs'],'file':file})
    (SITE/'dist/book-excerpts.js').write_text('window.SpikeExcerpts = '+json.dumps(
        {'books':books, 'charts':charts,'archiveCount':archive['tradeCount']},
        ensure_ascii=False,separators=(',',':'))+';\n')

if __name__=='__main__': main()
