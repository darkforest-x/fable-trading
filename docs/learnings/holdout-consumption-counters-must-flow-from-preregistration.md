# Holdout 消费次数必须从预注册单向流入回执

- **问题**：复用旧的多币日榜抓取器时，抓取回执把 `holdout_consumption_number_for_this_configuration` 固定写成 `1`，会让第二次使用同一权重的实验留下错误审计身份，即使新预注册已经正确写成 `2`。
- **死胡同**：只在新实验脚本里校验预注册次数并不够；公共抓取器仍会在更下游重新制造一个字面量，预注册、抓取回执、扫描回执和 artifact registry 因而可能互相矛盾。事后手改 JSON 也会破坏首次运行证据与哈希链。
- **有效路径**：把次数视为预注册事实，公共抓取器只通过一个 fail-closed 读取函数取得正整数，再原样写入回执；加载冻结快照时继续核对回执值与预注册值相等。本轮在任何新市场数据读取前提交该修复，避免修补既成回执。
- **通用规则**：所有实验身份字段（holdout 次数、配置哈希、模型哈希、数据边界）都只能从预注册单向传播；通用 builder 禁止重新默认或硬编码。新增复用路径时，第一步应搜索回执构建代码中的字面量并加入“预注册值 = 回执值”测试。
- **牵连**：`scripts/scan_15m_ma_launch_t3_daily_movers.py`、各实验 `preregistration.json`、`fetch_receipt.json`、`scan_receipt.json`、`artifacts/registry.yaml`；受 AGENTS.md holdout 纪律约束。
