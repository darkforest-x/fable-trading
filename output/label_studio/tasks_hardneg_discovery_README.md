# Hardneg discovery LS pack

- **File**: `output/label_studio/tasks_hardneg_discovery.json`
- **n_tasks**: 24
- **Source CSV**: `analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_candidates.csv`
- **Dataset mount**: `dense_owner_v11` (same local-files layout as other LS packs)

## Import
1. `docker compose -f scripts/label_studio_compose.yml up -d`
2. Project labeling interface: paste `output/label_studio/label_config.xml` (dense_cluster)
3. Import → `tasks_hardneg_discovery.json`
4. Review: these boxes are **hardneg mid-cluster candidates** (aftermath remains). Do **not**
   treat as tip gold. Training add-on waits for v13 + owner approve (H-DET-2).

## Constraints
CPU/offline only. No promote. No holdout.
