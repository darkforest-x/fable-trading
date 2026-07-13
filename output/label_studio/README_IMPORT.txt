Label Studio import pack
========================
1. docker compose -f scripts/label_studio_compose.yml up -d
2. Open http://127.0.0.1:8081  create local account
3. Create project → Settings → Labeling Interface → paste label_config.xml
4. Settings → Cloud Storage (optional) OR use local files already mounted
5. Import → tasks_round4_swap_chunk3.json
6. Review: green boxes = current auto_label (E2). Fix wrong ones, export YOLO later.

Tasks: 500  split=train  seed=20260717
