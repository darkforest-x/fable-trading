# Label Studio 本地访问

- URL: http://127.0.0.1:8081
- 用户: fable-review@local
- 密码: fable-review-local
- 用户已通过 CLI 创建（id=1）

## 首次建项目（仅一次）

1. 登录
2. Create Project → `dense_15m_val_audit`
3. Labeling Interface 粘贴: `output/label_studio/label_config.xml`
4. Import: `output/label_studio/tasks_val.json`（80 张）

## 重启

docker start fable_label_studio
