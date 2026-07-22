# pad200 sample20 纠正对照

打开：`open analysis/output/v13_train_sample20_corrected/index.html`

| audit_status | 含义 |
|---|---|
| BUG_wrong_window | 数据集按 end_incl 切，但 v11 存档图是 start 窗 → **框罩错 K 线** |
| ok_end_incl | bulk 窗与存档一致，坐标自洽 |
| drift_both_high | 存档 PNG 与当前 kline 两边 MAD>5（币种漂移） |
| bg | 空标背景 |

原「Owner 看过的」错数据集画廊仍在 `../v13_train_sample20/`。
