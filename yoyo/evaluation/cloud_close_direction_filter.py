"""Readmit long-only or short-only from the complete frozen V1.7 opportunity pool."""
import json

from yoyo.evaluation import cloud_close_own_daily_trend as parent

EXP = parent.EXP.parent/'exp-cloud-close-direction-filter-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed direction filter comparison')
    rows,quotes,sources = parent.source_book()
    output = {}
    for side,label in [(1,'long_only'),(-1,'short_only')]:
        output[label] = parent.run_wallet(rows[rows.side.eq(side)],quotes,sources,EXP/label)
    reference = json.loads((parent.repair.EXP/'summary.json').read_text())['cases']['fixed']['metrics']
    parent.archive.prices.safe_json(EXP/'summary.json',dict(reference=reference,arms=output))


if __name__=='__main__':
    main()
