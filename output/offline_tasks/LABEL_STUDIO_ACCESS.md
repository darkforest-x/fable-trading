# Label Studio 本地访问（仅本机）

- URL: **http://127.0.0.1:8081**
- Email: **`fable-review@example.com`**
- Password: **`fable-review-local`**

## 为什么以前登录失败

旧账号 `fable-review@local` **不是合法 Email**（Django `EmailField` 校验失败），
登录页却统一提示 “The email and password you entered don't match.”，看起来像密码错。

已创建可用账号：`fable-review@example.com`（密码同上）。

## 首次建项目

1. 打开 http://127.0.0.1:8081 用上面账号登录
2. Create Project → `dense_15m_val_audit`
3. Labeling Interface 粘贴 `output/label_studio/label_config.xml`
4. Import `output/label_studio/tasks_val.json`

## 重启

```bash
docker start fable_label_studio
```
