"""Bundle public OKX coin artwork for offline native loading; no private API.

Run explicitly when updating the app. Existing verified image files are reused.
Only the fixed public OKX catalog/CDN hosts are accessed with bounded workers.
Unavailable artwork remains a readable initial in the UI, never a broken image.
"""
import concurrent.futures
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'mobile/src/assets/coins'
CDN = 'https://static.okx.com/cdn/oksupport/asset/currency/icon/'


def image_format(data):
    # Some .png URLs return JPEG/WebP. Detect bytes; do not transcode artwork.
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    return None


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    response = subprocess.run(['curl', '-fsS', '--max-time', '30',
        'https://www.okx.com/api/v5/public/instruments?instType=SWAP'],
        capture_output=True, check=True)
    catalog = json.loads(response.stdout)
    if catalog.get('code') != '0':
        raise ValueError('Public catalog unavailable')
    coins = sorted({r['instId'].removesuffix('-USDT-SWAP').lower()
        for r in catalog['data'] if r.get('state') == 'live' and r.get('ctType') == 'linear'
        and r.get('settleCcy') == 'USDT' and r['instId'].endswith('-USDT-SWAP')})

    def download(coin):
        if not re.fullmatch(r'[a-z0-9]{1,40}', coin):
            return coin, False
        for target in DEST.glob(coin + '.*'):
            if image_format(target.read_bytes()):
                return coin, True
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'image.png'
            result = subprocess.run(['curl', '-fsS', '--max-time', '12', '--retry', '1',
                '--max-filesize', '524288', CDN + coin + '.png', '-o', str(output)],
                capture_output=True)
            if result.returncode == 0 and output.exists():
                data = output.read_bytes()
                extension = image_format(data)
                if extension:
                    (DEST / (coin + '.' + extension)).write_bytes(data)
                    return coin, True
        return coin, False

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(download, coins):
            results.append(result)
            if len(results) % 50 == 0:
                print(f'Artwork checked {len(results)}/{len(coins)}', flush=True)
    manifest = dict(source=CDN, generated_at=time.time(), catalog_count=len(coins),
        bundled=[c for c, ok in results if ok], fallback=[c for c, ok in results if not ok])
    (DEST.parent / 'coin-logos.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(catalog=len(coins), bundled=len(manifest['bundled']),
        fallback=len(manifest['fallback']), bytes=sum(p.stat().st_size for p in DEST.iterdir()))))


if __name__ == '__main__':
    main()
