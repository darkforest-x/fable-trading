# Lightweight config tests need lazy training imports

## Problem

Detection configuration tests only inspect frozen augmentation and training
options, but importing their modules eagerly loaded torch and Ultralytics. The
repository's lightweight CI environment therefore failed during test collection
before any assertion ran.

## Approach

Keep constants, validation and option construction importable without the
training stack. Import torch only when choosing a device and import Ultralytics
only when starting training. An explicitly supplied test device avoids both.

## Verification

The system Python environment has no torch, yet the complete repository suite
now collects and passes all 209 tests. The real training functions retain the
same imports immediately before their first use.

## Reuse

Separate configuration contracts from heavyweight runtime adapters. Pure
configuration tests should not require GPU frameworks merely to import a
module.
