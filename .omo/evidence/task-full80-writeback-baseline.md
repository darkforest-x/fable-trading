# Full-80 writeback baseline + export fix

**Result: PASS**  
**When:** 2026-07-10  
**Branch:** `codex/grok-2day`

## Hypothesis

`--limit 80` must export the full review pack (80 tasks), not only tasks with
prelabels. Owner annotations are still zero; a fingerprinted baseline under
`output/` enables post-review diffs without touching `datasets/`.

## Predeclared pass/fail

| Criterion | Result |
|-----------|--------|
| MANIFEST covers 80 stems | PASS (`n_stems=80`, `project_task_count=80`) |
| Source census accurate | PASS `annotation=0`, `prediction=53`, `none=27` |
| `datasets/` untouched | PASS (no git changes under datasets/) |
| Secrets absent from manifest | PASS |
| Prefer annotations over predictions | PASS (policy retained; none present yet) |

## Bug found on real surface

Prior export filtered `total_predictions > 0`, so `--limit 80` yielded only
**53** stems. Fixed: page all project tasks, export up to limit, empty stems
write empty YOLO txt with `source=none`, clear stale `*.txt` before re-export,
emit `source_counts` in MANIFEST.

## Commands

```bash
.venv_label_studio_qa/bin/python scripts/export_ls_yolo_writeback.py --limit 80
# → output/label_studio/writeback_dryrun/{80 stems}.txt + MANIFEST.json
```

## Results

| Metric | Value |
|--------|------:|
| EXPORT_N | 80 |
| annotation | 0 |
| prediction | 53 |
| none | 27 |
| manifest_sha256 prefix | `cc462204c4650986` |

## Comparison vs baseline (5-stem dry-run)

| | 5-stem design | Full-80 baseline |
|--|---------------|------------------|
| stems | 5 | 80 |
| sources | prediction only | prediction 53 + none 27 |
| datasets/ | frozen | frozen |
| owner annotations | 0 | 0 |

## Bottleneck / next hypothesis

Owner has not labeled any of the 80 tasks (`num_tasks_with_annotations=0`).
Writeback still mirrors prelabels + empties. Next value remains owner review in
LS; re-export will flip sources to `annotation` and allow promote proposal.

Parallel queue: Todo 5 (P2.5 local harden) is unblocked and independent.

## Risk / honesty

- Empty `source=none` stems are valid review targets (no prelabel); promote of
  empty labels must stay owner-gated.
- Coordinate transform still unvalidated against PNG dims (deferred).
- E2.1b not touched; holdout sealed.
