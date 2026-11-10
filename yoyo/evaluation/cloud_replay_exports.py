"""Package snapshots exported by scripts/export_cloud_replay.cjs at their original URLs."""
import argparse
import base64
import io
import json
import tempfile
from pathlib import Path

from PIL import Image
from yoyo.evaluation.cloud_inverse_booklet_overview import HISTORY, publish_zip


def publish(images):
    manifest = json.loads((HISTORY / 'manifest.json').read_text())
    records = manifest['records']
    expected = {row['id'] for row in records}
    if {path.stem for path in images.glob('*.png')} != expected:
        raise ValueError('Export requires one unique PNG per frozen trade')
    with tempfile.TemporaryDirectory(dir=HISTORY) as tmp:
        staged = Path(tmp)
        for r in records:
            with Image.open(images / (r['id'] + '.png')) as image:
                if image.size != (1280, 620):
                    raise ValueError('Incomplete shared chart capture: ' + r['id'])
                rgb = image.convert('RGB')
                # A blank chart can still have a valid summary. Check the plot as well.
                if max(len(ch.getcolors(256) or range(257)) for ch in rgb.crop((0, 130, 1280, 595)).split()) < 18:
                    raise ValueError('Empty chart: ' + r['id'])
                blob = io.BytesIO()
                rgb.save(blob, format='WEBP', lossless=True, method=3)
            data = base64.b64encode(blob.getvalue()).decode('ascii')
            title = f"{r['symbol']} / USDT · 12H · " + ('做多' if r['side']==1 else '做空')
            # An SVG envelope preserves existing chart links and offline image viewers.
            # The enclosed image is the actual common chart rendered in Chromium.
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                   f'width="1280" height="620" viewBox="0 0 1280 620" role="img"><title>{title}</title>'
                   f'<image width="1280" height="620" xlink:href="data:image/webp;base64,{data}"/></svg>')
            (staged / (r['id']+'.svg')).write_text(svg)
        for r in records:
            (staged / (r['id']+'.svg')).replace(HISTORY / r['chartUrl'])
    publish_zip()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path, help='Directory of PNGs from the shared replay export')
    publish(parser.parse_args().images)
