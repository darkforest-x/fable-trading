"""Compare committed-builder acceptance receipts; never contact any service."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    before, after = (json.loads(path.read_text()) for path in (args.before, args.after))
    checks = []

    def check(name, passed):
        checks.append(dict(name=name, passed=bool(passed)))

    old, new = before['status'], after['status']
    check('running_version_1_5_0', after['health']['version'] == '1.5.0')
    check('service_and_market_healthy', after['health']['ok'])
    scan = new['scan']
    check('complete_error_free_scan', scan['status'] == 'idle' and scan['errors'] == 0
          and scan['completed'] == scan['total'] == after['markets']['total'] > 0)
    check('all_three_periods', set(after['markets']['by_timeframe']) == {'15m', '1H', '4H'})
    for key in ('notification_since_ms', 'bark_notification_since_ms',
                'timeframe_notification_since_ms', 'tv_profile', 'signal_kind', 'fresh_minutes'):
        check('unchanged_' + key, old['runtime'][key] == new['runtime'][key])
    check('unchanged_protocol', before['health']['protocol'] == after['health']['protocol'])
    for key in ('signals.py', 'policy.py', 'bark.py'):
        check('unchanged_source_' + key, old['runtime']['startup_source_sha256'][key]
              == new['runtime']['startup_source_sha256'][key])
    for key, value in new['runtime']['startup_source_sha256'].items():
        check('running_bytes_' + key, value == after['source_sha256']['yoyo/monitor/' + key])
    check('compact_photo_delivery_enabled', new['telegram']['delivery_format'] == 'chart_with_compact_caption')
    for channel in ('telegram', 'bark'):
        receipts = channel + '_receipts'
        indexed = {row['event_id']: row for row in after['journal'][receipts]}
        check(channel + '_old_receipts_preserved', all(indexed.get(row['event_id']) == row
              for row in before['journal'][receipts]))
        check(channel + '_no_pending_or_new_failures', new[channel]['pending'] == 0
              and all(new[channel][key] <= old[channel][key] for key in ('failed', 'unknown')))
    old_ids = {r['event_id'] for r in before['journal']['telegram_receipts']}
    media = after['journal']['telegram_media']
    check('no_media_backfill_for_old_notifications', all(r['event_id'] not in old_ids for r in media))
    check('all_new_media_intact', all(r['bytes'] > 0 and r['hash_valid'] and not r['error'] for r in media))
    check('no_duplicate_signal_identity', after['journal']['duplicate_identity_groups'] == 0)
    check('no_invalid_or_pre_activation_signals', all(not value for section in
          ('signal_contract_audit', 'bark_audit', 'timeframe_audit')
          for key, value in after[section].items() if key.endswith('_ids')))
    result = dict(generated_at=datetime.now(timezone.utc).isoformat(),
                  source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  inputs={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.before, args.after)},
                  passed=all(r['passed'] for r in checks), checks=checks,
                  generated_snapshots=len(media),
                  note='Snapshot generation/Telegram receipts are not phone-display or read receipts.')
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(passed=result['passed'], checks=len(checks),
                         failures=[r['name'] for r in checks if not r['passed']], snapshots=len(media))))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
