# OSS architecture benchmark — labeling / review tooling

**Result: PASS (recommendation: keep Label Studio + FiftyOne hybrid)**  
**When:** 2026-07-10  
**Branch:** `codex/grok-2day`  
**Scope:** Architecture comparison only — no new VPS deploy, no training jobs, no secrets.

## Hypothesis

For fable’s 80-sample YOLO box audit (prelabels, rectangle edit, auth, self-hosted VPS), Label Studio remains the best **public manual editor**, with FiftyOne retained as **local triage**; CVAT is the only serious migration candidate if box UX becomes a bottleneck; labelme is offline-only and not multi-user.

## Method

- Official repos + latest release tags via GitHub API (2026-07-10).
- Map to fable constraints: 80-image pack, no holdout, no live trading, self-signed VPS ok, isolated QA env, executor off.
- Current production proof: Phase C (`dense_15m_val_audit`, 80 tasks, 53 prelabels) on LS 1.15.0 at `https://103.214.174.58:8081`.

## Pinned upstream references

| Tool | Repo | License (SPDX) | Latest release pin | Default-branch HEAD (2026-07-10) |
|------|------|----------------|--------------------|----------------------------------|
| Label Studio | [HumanSignal/label-studio](https://github.com/HumanSignal/label-studio) | Apache-2.0 | **1.23.0** (2026-03-13) | `63fdee07510d873e39920b1f75daedae721e71ca` |
| CVAT Community | [cvat-ai/cvat](https://github.com/cvat-ai/cvat) | MIT | **v2.69.0** (2026-06-22) | `390e7759183c5561d88d797fb0fc2fc7109fbf26` |
| FiftyOne | [voxel51/fiftyone](https://github.com/voxel51/fiftyone) | Apache-2.0 | **v1.18.0** (2026-07-02) | `c44a1917c10e18f482bd090e7430036e54dafba3` |
| labelme | [wkentaro/labelme](https://github.com/wkentaro/labelme) | GPL-3.0 | **v7.0.2** (2026-07-03) | `8e54eee4b71a47e0427f2f9bc47633bfae4825ec` |

**Deployed today:** Label Studio **1.15.0** (API `version` field during Phase C). Docs for local storage: https://labelstud.io/guide/storage_local (requires LocalFiles source connection + env flags — Phase C learnings).

## Comparison (fable-relevant axes)

| Axis | Label Studio (current) | CVAT Community | FiftyOne App | labelme |
|------|------------------------|----------------|--------------|---------|
| Primary role | Multi-format annotation server | CV-first annotation platform | Dataset triage + eval UI | Desktop single-user editor |
| License | Apache-2.0 | MIT | Apache-2.0 | **GPL-3.0** (copyleft risk if embedded) |
| Self-host cost | Low (single process + nginx) — **already live** | Medium–high (compose: server, workers, DB, Redis, optional clickhouse) | Low local; not ideal as public multi-user labeler | Lowest (local GUI only) |
| Auth | Built-in login; signup can be disabled | Full RBAC/org model | Local app / token patterns; collaboration weaker in OSS | None (local files) |
| Prelabel / import | JSON import + predictions; flexible labeling config XML | Native YOLO/COCO import/export; AI assist (SAM etc.) | Load YOLO/GT fields; integrations to external annotators | Open image dir; limited prelabel automation |
| Rectangle edit UX | Good enough for 80-sample audit (Phase C proven) | Best-in-class for vision boxes/video | View/filter strong; native annotation improving but enterprise features gated | Fine for offline single user |
| Export to YOLO | Possible via converters / export formats | **First-class** YOLO / Ultralytics export | Python API → write YOLO labels | VOC/JSON; convert scripts common |
| Multi-user | Yes (project-level) | Yes (stronger roles) | Weak for concurrent remote labeling | No |
| Mobile | Usable SPA (Phase C mobile render ok) | Desktop-oriented | Desktop | Desktop |
| Ops risk on shared VPS | Bounded MemoryMax unit (done) | Higher RAM/CPU for full stack | Local-only preferred | N/A |
| Fit to 80-sample audit | **Proven** | Overkill unless box UX fails | **Already in use** for hard-example triage | OK offline backup |

## Recommendation

**Keep hybrid: Label Studio (public manual box editor) + FiftyOne (local triage). Do not migrate to CVAT this week.**

Rationale:

1. **Phase C already unblocks the owner** on the exact 80-sample pack with auth, prelabels, and edit persistence.
2. CVAT’s YOLO-native export is attractive, but deploy blast radius on a 4 GB shared VPS (dashboard + YOLO proxy + LS) is high; no owner pain signal yet that LSF rectangle UX is insufficient for 80 images.
3. FiftyOne stays local for mistakenness / hard lists (existing offline tooling) — matches methodology note “Label Studio public; FiftyOne local”.
4. labelme is a fine emergency offline path but GPL + no multi-user + no VPS auth story.

## Explicit non-actions

- No CVAT docker compose installed.
- No Label Studio upgrade to 1.23.0 in this iteration (upgrade = separate bounded task: backup sqlite, retest local-files storage, re-QA 80 tasks).
- No ENABLE_JOB_EXECUTOR change; E2.1b observe-only.

## Next testable hypothesis

If during owner review, **box edit latency or YOLO export friction** is the bottleneck, run a **bounded CVAT pilot** on a throwaway port with the same 80 images only (no production cutover). Pass = export YOLO labels match LS geometry within pixel tolerance on 5 stems.

If review is smooth, next value is **label-writeback pipeline design** (LS export → reviewed YOLO → frozen fingerprint) — not another tool.

## Risk / honesty

- Benchmark is architecture-level from official repos/docs + fable Phase C evidence, not a timed UX study with human annotators.
- Auto-labeling / SAM features in CVAT and FiftyOne Enterprise were not exercised.
- Stars/release dates are snapshots; re-pin before any copy-paste of deployment manifests.
