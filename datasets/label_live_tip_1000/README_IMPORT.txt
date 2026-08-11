Live tip labeling pack (empty labels)
====================================
1. docker compose -f scripts/label_studio_compose.yml up -d
2. Open http://127.0.0.1:8081
3. New project → Labeling Interface → paste output/label_studio/label_config_v2.xml
4. Import → output/label_studio/tasks_live_tip_1000_chunk1.json (…chunk2…)
   (datasets/ is already mounted; image URLs use d=label_live_tip_1000/images/train/…)
5. Or: PYTHONPATH=. python3 scripts/ls_auto_import.py live_tip_1000_c1 output/label_studio/tasks_live_tip_1000_chunk1.json
6. Draw dense_cluster on the RIGHT tip when present; submit.

Images: datasets/label_live_tip_1000/images/train
Labels: datasets/label_live_tip_1000/labels/train (empty .txt)
