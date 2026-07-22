# YOLO 重训走局域网 SSH 到 3060，不是 U 盘拷

- **问题**：v14 交接写成「移动硬盘 / 手动拷到 Windows」，Owner 指出项目早有 SSH 传 Windows 的标准流程。
- **死胡同**：把「Windows 训练」理解成「人坐在 Windows 前、从 Mac 拖文件」——忽略了仓库里 `train_on_3060.sh` / H-TS / v9 脚本已经把 Mac→`zzc@192.168.1.5`→`C:/fable` 的 tar+scp 和 WMI 长训写成惯例。
- **有效路径**：传数与开训分离；默认 `FABLE_3060_HOST` + `FABLE_3060_REMOTE`；Mac 一条 `sync_*_to_windows.sh`，长训用 WMI Create 防 SSH 杀进程；取回权重再在 Mac 验收/promote。
- **通用规则**：凡「去 Windows 训 YOLO」，第一步搜 `FABLE_3060_HOST` / `train_on_3060`，禁止默认写 U 盘步骤。
- **牵连**：`scripts/sync_v14_to_windows.sh`、`analysis/p_v14_windows_train.md`、`scripts/train_on_3060.sh`、`scripts/train_owner_hts.sh`
