# Bootstrap remote package directories before rsync

- **问题**：A first-time VPS deploy failed before package installation because rsync targeted nginx's `sites-available` directory, which does not exist until nginx is installed.
- **死胡同**：The script correctly installed nginx later, but ordering that install after configuration sync made the clean-host path impossible while upgraded hosts appeared healthy.
- **有效路径**：Create the small required remote directory skeleton before rsync, then let package installation and service validation proceed normally. Lock the ordering with a static deployment-contract test.
- **通用规则**：Test deployment scripts against an absent-package filesystem state; every rsync destination must be created before the first transfer.
- **牵连**：`scripts/deploy_label_studio_vps.sh`, nginx first-install path, `tests/test_label_studio_vps_deploy.py`.
