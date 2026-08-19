# Source repository snapshots (C0)

Generated at: `2026-08-19T12:33:31+00:00`

Frozen reference for the single-repository consolidation. Every migration decision recorded later cites the `head_sha` values below as its source of truth.

## Repositories

| repo | role | branch | HEAD | clean | tracked files | untracked |
|---|---|---|---|---|---|---|
| `fable-trading` | destination | `main` | `59e13a61c43e` | **no** | 36906 | 8569 |
| `darkforest-one` | source | `main` | `fd36dd1adc58` | yes | 68 | 0 |
| `yolo-xx` | source | `main` | `9296cfa8e505` | yes | 150810 | 0 |
| `yoyo-trading` | source | `main` | `784766de45a3` | yes | 15416 | 0 |
| `yoyo-eth` | source | `main` | `6147810afb46` | yes | 1305 | 0 |

## Head commits

- `fable-trading` `59e13a61c43e1e72f397f38985fd8c700533550b` (2026-08-19T19:39:25+08:00)
  - Ignore the review-pack images, keep what rebuilds them
- `darkforest-one` `fd36dd1adc5844f241122c3853eb4d3e675a9c11` (2026-08-03T15:04:36+08:00)
  - Merge pull request #2 from darkforest-x/agent/p1-candidate-dataset
- `yolo-xx` `9296cfa8e5053d86cea44e29dbd45874c3dff689` (2026-08-18T22:51:14+08:00)
  - Quality ranker v1: direction-neutral geometry scores 0.53/0.55, and the lift to 0.64 is leakage
- `yoyo-trading` `784766de45a3b876c986d3ba672779124b46a66f` (2026-08-18T22:53:33+08:00)
  - Fixed W10 gold pipeline, legacy migration, and the L2 walkforward/feedback tooling
- `yoyo-eth` `6147810afb46be1c664128e9a5359e8e7d0a3923` (2026-08-13T18:03:57+08:00)
  - P04: strict platform definition v2 for owner semantic adjudication

## Destination runtime-safety hashes

These are the objects task book section 3.2 forbids changing. C7 re-hashes them and any difference must be explained item by item.

| path | sha256 | size |
|---|---|---|
| `models/ACTIVE` | `899c36259950a3d376067958ec040638253defa9ef545fa51af2a004f95bb6ef` | 53 |
| `models/ACTIVE_PREV` | `5142d8a143799a826de176e75bcf1512070bded1a76cedb1b025c5ac8bca6066` | 53 |
| `models/owner_best.json` | `1fb35c712dbff41789be546d2486f83824fcfebacb3c65b910b5ee7d8ebc47b5` | 1033 |
| `models/active_bundle.example.json` | `fb2bf9dd0fcf65e6214a8014e6e205d6f7fc17e20052b831a4ea4cb8ffeebbf1` | 1659 |
| `data/forward_log.csv` | `6035eb60482481fb60d7e73aa72dd15d1b8884ee4c2da5410fbffa18b17b34bb` | 223 |
| `data/forward_log_ma206.csv` | `e03ada090caec2d7087c2d35bf9a8f1e4ceab16f4be618df0e90a84fe0588a5e` | 26055 |
| `src/costs.py` | `e2245409d815db6b440fe81e552a59fcd6eed1fa7fc606138f3d674fbb5e971e` | 142 |
| `scripts/deploy_vps.sh` | `47389ad26bbdc30fea44bf18c200fda1a9ceb6fd844338fa4c8b534c412210f4` | 2443 |
| `scripts/deploy_vps_short_protocol.sh` | `6ab012d6767dd9ba5228b050999376fd32155edf6df76f90e96b394a012b9741` | 3232 |
| `deploy/fable-forward.timer` | `bfddb136a1fcf7bea2a040c6cd28c14e007a8e7b5e95fa7aa429dce5160b7549` | 289 |
| `deploy/fable-live-health.service` | `d6ecc03f37eb23eda5c09ec4cc29ec57947071037d3d55ec3ab7b9b585510107` | 272 |
| `deploy/fable-live-health.timer` | `c133fad519f9e67dee5f48925e40221f2fe118aa24294e7631d37a3219c7193d` | 185 |
