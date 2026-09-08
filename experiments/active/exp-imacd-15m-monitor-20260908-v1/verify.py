"""Verify derived before/after monitor receipts; no prices, network or secrets.

Use the committed yoyo.monitor.acceptance collector before and after deployment.
This compares activation boundaries, immutable historical delivery receipts,
market-period coverage and current marker/outbox audits, not economic outcomes.
"""
import hashlib
import json
from pathlib import Path


def verify(directory):
    directory = Path(directory)
    before = json.loads((directory / 'before.json').read_text())
    after = json.loads((directory / 'after.json').read_text())
    runtime = after['status']['runtime']
    count = after['status']['universe']['count']
    checks = {}
    checks['three_periods'] = runtime['timeframes'] == ['15m', '1H', '4H']
    checks['all_contract_periods'] = after['markets']['by_timeframe'] == dict.fromkeys(runtime['timeframes'], count)
    scan = after['status']['scan']
    checks['full_scan'] = scan['completed'] == scan['total'] == count * 3 and scan['errors'] == 0
    checks['healthy_current_markets'] = after['health']['ok'] and after['markets']['stale'] == 0
    checks['no_duplicates'] = after['journal']['duplicate_identity_groups'] == 0
    for channel in ('telegram', 'bark'):
        old = {r['event_id']: r for r in before['journal'][channel + '_receipts']}
        new = {r['event_id']: r for r in after['journal'][channel + '_receipts']}
        checks[channel + '_old_receipts_unchanged'] = all(new.get(k) == v for k, v in old.items())
        key = 'notification_since_ms' if channel == 'telegram' else 'bark_notification_since_ms'
        checks[channel + '_cutover_unchanged'] = runtime[key] == before['status']['runtime'][key]
        checks[channel + '_healthy'] = all(after['status'][channel].get(k, 0) == 0 for k in ('failed', 'unknown', 'pending'))
        checks[channel + '_no_pre_15m_activation'] = not after['timeframe_audit']['invalid_' + channel + '_ids']
    activation = runtime['timeframe_notification_since_ms']
    checks['new_15m_cutover'] = (activation['15m'] > before['status']['now_ms']
                               and activation['1H'] == activation['4H'] == 0)
    checks['marker_contract'] = not after['signal_contract_audit']['invalid_signal_ids']
    checks['outbox_contract'] = all(not after[key][field] for key in ('signal_contract_audit', 'bark_audit')
                                   for field in ('invalid_outbox_ids', 'pre_activation_outbox_ids'))
    checks['running_source_matches_disk'] = all(
        after['source_sha256']['yoyo/monitor/' + name] == digest
        for name, digest in runtime['startup_source_sha256'].items())
    result = {'checks': checks, 'passed': all(checks.values()),
              'source_commit': runtime['source_commit'],
              'receipt_sha256': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                                  for name in ('before.json', 'after.json')},
              'scan': scan, 'coverage': after['markets']['by_timeframe'],
              'timeframe_activation': activation}
    (directory / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit('acceptance failed')


if __name__ == '__main__':
    verify(Path(__file__).parent / 'results')
