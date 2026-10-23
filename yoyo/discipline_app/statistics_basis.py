"""Owner-confirmed fixed capital for App-managed PnL percentages only.

This is a display denominator, not equity, available margin or a risk setting.
The 2026-10-20 owner decision uses a fixed pre-trade capital observation; future
cash flows and external trades never change it. No exchange client is imported.
"""
import json
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCOPE = 'app_managed_fixed_capital'


def confirmed_amount(state):
    """Read only a positive, finite basis confirmed for this exact account."""
    basis = state.get('statistics_return_basis')
    if not isinstance(basis, dict):
        return None
    if (basis.get('scope') != SCOPE or basis.get('currency') != 'USDT'
            or basis.get('confirmed') is not True or not state.get('account_reference')
            or basis.get('account_reference') != state['account_reference']):
        return None
    try:
        amount = Decimal(basis['amount'])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def set_fixed_basis(path, amount, account_reference, source, now=None):
    """Explicit local owner action; preserve an existing basis and all risk state.

    Opens an existing live ledger, checks account identity, and writes only the
    display basis, revision and audit event in one transaction. Repeating the
    same amount is idempotent; replacing a confirmed basis requires a separately
    reviewed migration and is intentionally unsupported.
    """
    value = Decimal(amount)
    if not value.is_finite() or value <= 0:
        raise ValueError('Statistics capital must be positive and finite')
    if not account_reference or not source.strip():
        raise ValueError('Account identity and basis provenance are required')
    stamp = time.time() if now is None else now
    basis = dict(amount=str(value), currency='USDT', scope=SCOPE, confirmed=True,
                 account_reference=account_reference, confirmed_at=stamp, source=source)
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=rw', uri=True, timeout=15)
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM state WHERE id=1').fetchone()
        if row is None:
            raise ValueError('Missing live ledger')
        state = json.loads(row[0])
        if state.get('mode') != 'okx_live' or state.get('account_reference') != account_reference:
            raise ValueError('Statistics basis account mismatch')
        if state.get('statistics_return_basis') is not None:
            if confirmed_amount(state) == value:
                return state['statistics_return_basis']
            raise ValueError('Existing statistics basis must not be replaced')
        state['statistics_return_basis'] = basis
        state['version'] += 1
        db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
        db.execute('INSERT INTO journal(time,kind,body) VALUES(?,?,?)',
                   (stamp, 'statistics_basis_owner_confirmed',
                    json.dumps({k: v for k, v in basis.items() if k != 'account_reference'}, ensure_ascii=False)))
        db.commit()
        return basis
    finally:
        db.close()
