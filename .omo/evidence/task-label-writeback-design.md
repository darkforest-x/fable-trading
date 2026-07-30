# Label writeback design + 5-stem dry-run

**Result: PASS (design + dry-run; no datasets overwrite)**
**When:** 2026-07-10
**Branch:** `codex/grok-2day`

## Hypothesis

Reviewed (or prelabel) boxes can be exported from Label Studio to YOLO txt with a
content fingerprint under `output/`, never mutating `datasets/dense_15m_full`
without owner approval.

## Design

```
LS API (auth)
  → prefer annotations over predictions
  → rectanglelabels % → YOLO cx cy w h (class dense_cluster=0)
  → write output/label_studio/writeback_dryrun/<stem>.txt
  → MANIFEST.json with per-file sha256 + manifest_sha256
  → promote step (future, owner-gated): copy into a versioned labels tree
     with frozen fingerprint; never silent overwrite of training labels
```

### Fingerprint scheme

- Per-stem: SHA-256 of YOLO txt body.
- Manifest: SHA-256 of canonical JSON (`sort_keys=True`) of metadata + items
  (computed before embedding `manifest_sha256` field — field stores that hash).
- Future promote: record `manifest_sha256` + git commit + project_id + export time
  in a `LABEL_FINGERPRINT.md` next to labels; training must pin fingerprint.

### Guards

- Output path hard-rejects any `datasets/` component.
- Dry-run default `--limit 5`.
- No YOLO retrain; no holdout; E2.1b observe-only.

## Commands

```bash
.venv_label_studio_qa/bin/python scripts/export_ls_yolo_writeback.py --limit 5
# → output/label_studio/writeback_dryrun/{5 stems}.txt + MANIFEST.json
```

## Dry-run results (2026-07-10)

| stem | source | boxes | sha256 prefix |
|------|--------|------:|---------------|
| BNB_USDT_016560 | prediction | 1 | 8beeaf6da1c3 |
| BTC_USDT_015260 | prediction | 3 | 2bb04ac4bea5 |
| XAUT_USDT_017260 | prediction | 1 | 19a4eb345dc3 |
| ETH_USDT_015960 | prediction | 4 | 75d9a72aeec0 |
| BTC_USDT_016960 | prediction | 2 | f9a91b9553fb |

- `manifest_sha256` prefix: `e44266ca50aaa715` (regenerate on re-run).
- `git status datasets/`: clean (no touch).
- Note: no human annotations yet on these stems — dry-run used **predictions**
  (prelabels). After owner review, re-export will prefer annotations.

## Comparison vs baseline

| | Before | After |
|--|--------|-------|
| LS → YOLO path | none | scripted dry-run |
| datasets/ labels | frozen | still frozen |
| Fingerprint | none | MANIFEST per export |

## Bottleneck / next hypothesis

Owner must manually review boxes in LS; until annotations exist, writeback only
mirrors prelabels. Next value: owner review session, then re-export full 80 with
annotation-preferred policy and optional promote-to-versioned-tree (owner-gated).

## Risk / honesty

- Coordinate transform assumes LS percent space on full image (standard for Image+RectangleLabels).
- Does not validate against PNG pixel dims in dry-run (could add image size probe later).
- Manifest hash embeds item hashes; reordering stems changes manifest hash.
