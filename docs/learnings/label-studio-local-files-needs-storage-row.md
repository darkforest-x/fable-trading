# Label Studio local-files 404 needs LocalFilesImportStorage

- **问题**：VPS 上 `LOCAL_FILES_SERVING_ENABLED=true` 且文件在 `DOCUMENT_ROOT` 下仍可读，但浏览器/API 对 `/data/local-files/?d=...` 一律 404，预标图空白。
- **死胡同**：只查 env / 重启 unit / 校路径拼写；以为 404 是 nginx 或文件权限。其实 403 才表示 serving disabled；404 是“无权限映射”分支。
- **有效路径**：读 LS 1.15 `core/views.localfiles_data`：必须存在 `LocalFilesImportStorage`，且请求路径落在某 storage.path 前缀下且用户对该 project 有权限，才 `RangedFileResponse`。path 必须是 DOCUMENT_ROOT 的 **子目录**（根目录本身校验失败）。
- **通用规则**：接 LS local-files 时，env 打开后立刻 `POST /api/storages/localfiles/`；import JSON 去掉 `completed_by`，预标走 predictions。
- **牵连**：`scripts/init_label_studio_vps_project.py`、`scripts/deploy_label_studio_vps.sh` access note、Phase C evidence。
