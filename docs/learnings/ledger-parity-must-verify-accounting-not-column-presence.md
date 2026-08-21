# Ledger parity must verify accounting, not column presence

## Problem

A TradingView export can match entry/exit times and prices while carrying wrong
commission or net-profit values. Merely requiring fee and net columns to exist
lets zero-filled or differently defined accounting pass a nominal ledger check.

## Dead end

Treat price/time identity as full parity, retain `commission_total` and
`net_profit` only for a later manual review, then subtract the project's 20 bp
comparison cost from TradingView net again. This can both approve an incorrect
ledger and double count commission.

## Effective path

Recompute frozen Pine gross P&L, entry-plus-exit commission and net P&L for every
canonical trade. Require the TradingView money fields to match within an
explicit account-currency display-rounding tolerance, require unique entry
identities, and record that TradingView net already includes commission.
Funding and venue slippage stay separate explicit gates rather than being
silently assumed zero.

## General rule

Schema presence proves transport shape, not semantic parity. A financial ledger
is reconciled only when identities, quantities or derived cash flows, fees and
net arithmetic agree under one named accounting contract.

## Implications

- A fee or net-profit mutation must make parity fail even when all prices match.
- Cost views such as gross, TradingView net and project comparison net must stay
  separately named.
- No downstream evaluator may deduct the same commission twice.
- The governing check is `scripts/reconcile_pine_eth_15m_tradingview.py`.
