"""Book snapshot is the last one at least 30s before the touch bar opens."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
import pandas as pd
from yoyo.evaluation.monday_1618_book import snapshot, features


def write(root, day, rows):
    path = Path(root)/'bookDepth'/'BTCUSDT'/f'BTCUSDT-bookDepth-{day}.zip'
    path.parent.mkdir(parents=True, exist_ok=True)
    csv = 'timestamp,percentage,depth,notional\n'+''.join(f'{t},{p},1,{n}\n' for t, p, n in rows)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(path.stem+'.csv', csv)


class BookTests(unittest.TestCase):
    def test_uses_last_snapshot_before_cut_and_side(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, '2024-01-02', [('2024-01-02 10:00:00', 1, 200), ('2024-01-02 10:00:00', -1, 100),
                                    ('2024-01-02 10:14:20', 1, 50), ('2024-01-02 10:14:20', -1, 500),
                                    ('2024-01-02 10:14:40', 1, 7e8), ('2024-01-02 10:14:40', -1, 3),
                                    ('2024-01-02 10:15:00', 1, 9e9), ('2024-01-02 10:15:00', -1, 1)])
            snap = snapshot(Path(d), 'BTC', pd.Timestamp('2024-01-02 10:15:00', tz='UTC'))
            self.assertEqual(snap[1], 50.)
            rows = pd.DataFrame([dict(symbol='BTC', touch_time='2024-01-02 10:15:00+00:00', side=-1),
                                 dict(symbol='BTC', touch_time='2024-01-02 10:15:00+00:00', side=1)])
            f = features(rows, Path(d))
            self.assertAlmostEqual(f.book_1pct.iloc[0], -2.302585, places=5)
            self.assertAlmostEqual(f.book_1pct.iloc[1], 2.302585, places=5)

    def test_stale_snapshot_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, '2024-01-02', [('2024-01-02 09:00:00', 1, 200), ('2024-01-02 09:00:00', -1, 100)])
            self.assertIsNone(snapshot(Path(d), 'BTC', pd.Timestamp('2024-01-02 10:15:00', tz='UTC')))


if __name__ == '__main__':unittest.main()
