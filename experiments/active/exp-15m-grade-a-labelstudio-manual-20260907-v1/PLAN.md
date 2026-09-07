# Grade-A full-event manual labeling in Label Studio

Owner requested importing the datasets into Label Studio for manual annotation on
2026-09-07, then challenged limiting the work to the 240 sampled candidates.
The full scope is the current close-MA training/development dataset plus the
candidate calibration pool. This replaces a sampling-only interpretation.

- The training dataset has 32,000 images from 4,172 event groups, not 32,000
  independent examples. Review one original representative per event, retaining
  all original variant identities, time splits and coordinate transforms.
- Preserve the original representative PNG byte for byte. Choose greatest
  window length, then greatest post-core context, then variant index and ID.
  Attach the earliest-starting original variant as optional earlier context.
- Generate a physically separate future reference using at most 40 additional
  contiguous 15-minute bars after the representative window. Explicitly show
  shorter context when archives end or the fixed holdout boundary intervenes.
  No unauthorized holdout OHLCV may be materialized.
- Label only the original input image from blank. Do not import old targets or
  predictions. Human choices are LONG/SHORT rectangles, explicit no-target, or
  uncertain. Empty submissions, conflicting no-target plus rectangles, multiple
  cores, out-of-range geometry and uncertain answers require later review.
- The candidate project preserves the existing 240 events and 36 repeat items,
  together with the existing isolated future40 reference images. It is a second
  annotation project, not an additional version of the 32,000-image dataset.
- Store the full mapping to all 32,000 variants. Normalized boxes cannot be
  copied between differently scaled charts. Future processing must transform
  through bar/price coordinates, check containment and preserve original splits.
  No labels are written back and no training or promotion is authorized here.
- Keep existing Label Studio projects intact. Import into two clearly titled
  projects, register image storage for every image field, prevent duplicate
  imports and verify the resources and actual browser view.

Source builders must be committed before artifact generation. The Label Studio
service's document root is discovered from its API; this local instance uses
`reports/`. Relative links under `reports/label_studio/` expose only the two
dedicated packs while image artifacts remain under `datasets/`.

Validation: representative replay; source/input hashes; one event per main task;
240+36 candidate identity; all variants accounted for; 40-bar/boundary/gap checks;
blank task payloads; idempotent import; each image field fetch; UI inspection.
This is annotation workflow work: no return, AUC or economic metric is claimed.
