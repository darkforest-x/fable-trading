# Remote dataset preflight must bind per-sample class semantics

## Problem

A YOLO training launcher can preserve the dataset manifest hash and still train
the wrong task. One generic Windows helper rewrote a two-class `data.yaml` as a
single class, and ordinary file-count checks would also miss a pairwise swap in
which a LONG manifest row points at a class-1 label while another SHORT row
points at class 0. Aggregate class counts remain correct in that failure mode.

The remote copy adds a second risk surface: an interrupted transfer or staging
mistake can leave all expected directories present while individual PNG/TXT
bytes differ from the Mac-owned dataset.

## Dead end

Checking only the manifest/build-receipt hashes, total file counts and aggregate
class counts is not sufficient. Those checks prove that the metadata is the
expected metadata; they do not prove that every staged training file implements
that metadata or that each direction has the intended class id.

## Effective approach

Run one committed verifier on both the source dataset and the final remote
staging directory before creating the training process. It must fail closed on:

- exact image/label stem-set or split-path mismatch;
- any PNG canvas other than the preregistered dimensions;
- missing, duplicate or changed file bytes against every manifest SHA-256;
- non-empty negatives, multiple boxes, invalid class ids or out-of-bounds boxes;
- pairwise `LONG -> class 0` and `SHORT -> class 1` disagreement;
- class/event totals or event ids crossing the chronological split.

The launcher also binds the verifier, trainer, base model and launcher itself by
SHA-256 to the preregistration. Training starts only after the remote verifier
emits the exact sentinel containing manifest hash, row count and class names.

## Reusable rule

**A training dataset contract is a per-sample semantic join, not a collection of
matching totals. Validate the same immutable join after the last transport or
rewrite step, on the machine that will actually train.**

## Implemented in

- `scripts/windows/verify_yolo_dataset.py`
- `scripts/train_15m_ma_launch_t3_on_3060.sh`
- `tests/test_verify_yolo_dataset.py`
