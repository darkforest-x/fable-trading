# SPIKE V1 Mac monitor migration：信号浏览、冻结回放图与服务验收记录

## `29bc1eb` / `22e0d70` 后新 generation 运行验收（2026-09-11 12:05 BJT）

- **新实例的完整轮，不借用前代 receipt**：parent `48527` 与 isolated worker `48534` 仍存活，generation `1925e322af3e4d699928c7706b28b667` 已从 `started_at_ms=1789099366956` 完成至 `finished_at_ms=1789099476352`：`1,434 / 1,434`、errors `0`、`109.4s`。这是 `29bc1eb` 的 MD/SB 显示字段与 checkpoint migration 已载入后的新 generation；它不是前一 `40fe…` 的 `81.33s` receipt，二者的 changed 集合不同，不能合并成单一等工作量基准。该轮计时为 changed `841` / unchanged `593`，fetch 累计 `742.583s`（8 路 I/O 聚合）、analyze wall/CPU `46.878 / 44.531s`、checkpoint `12.554 / 10.503s`。
- **通知截止已追平**：worker 写入的 `v1:last_closed:*` 是 candle open 加 timeframe 的**实际收盘时刻**（`yoyo/monitor/v1_worker.py:127-129`）。在 12:05 BJT 的只读快照，以 `floor_tf(now - 30min)` 为 required actual close，30m / 1H / 4H 各有 `478 / 478` 达标、欠账 `0`；每周期的最小和最大 stored close 都为 `04:00Z`，而 required close 分别为 `03:30Z / 03:00Z / 00:00Z`。因此不是把 bar open 误当 close，也不是用“now-last-close <30min”错误要求 1H/4H 即刻同步。
- **通知协议与历史安全状态未变**：`notification_policy:v1_bark_arm` 仍只允许 `spike-burst-v1-raw-notifications-v1` 和 `spike-burst-v1-yolo-confirmation-v1`，范围为 30m / 1H / 4H，且明确 `telegram=disabled`。SQLite 只读计数为 Telegram `outbox=0`、Bark `bark_outbox=0`；按 event payload `source='replay'` 联结两类 outbox 均为 `0`；仅有 2 个 `model_candidates`，均为 `disabled`。这是当前队列/协议证据，不是任何终端 Bark 送达声明。
- **测试诚实边界与最终 UI 证据**：本次 V1 聚焦测试为 `31 passed`。遗留 `tests/monitor/test_service.py tests/monitor/test_yolo_detector.py tests/monitor/test_signals.py` 为 `13 passed, 61 failed`：旧 IMACD fixtures 未传 tick、仍引用移除的 `signals._compute`，并使用已移除 5m/15m；实现者核对 `29bc1eb` 相对 parent 的 `signals.py` 仅加入 sb 的读取/序列化，并未改变这些接口或 timeframe。因此不把全仓测试称为绿色，也不在本轮重写遗留 fixture。root 实机验 DATA/30m 回放：95 根 OHLC、6MA、蓝 MD、金 SB、零轴、未裁切的 `initial_stop=0.1908` 与历史启动三角均可见（`analysis/output/spike_v1_ui_20260911/v1-marker-e7dae09-replay.png` / `.txt`；DOM marker `0.1958`）。随后静态 `e7dae09` 把当前 raw kind `spike_burst_v1` 纳入箭头筛选、无需 Python reload；STABLE/1H live 已实测启动三角、MD/SB、零轴与 `initial_stop=0.02902` 均可见，DOM marker `0.03019`（`.../v1-marker-current-kind-live.png` / `.txt`）。该静态变更另有 `23` 个 JS 测试通过，包括无 selected arrow 的 chart events 与 YOLO parent-bar 坐标断言；root 已将 IAB tab 置为 blank，未留下页面轮询。

## `ProcessType=Interactive` 单变量 after（2026-09-11 11:55 BJT）

- **完整 after receipt**：`226cc9f` 只在 LaunchAgent 生成配置中显式加入 `ProcessType=Interactive`（同时有 focused plist test；不改 scanner recurrence、信号、cutover 或通知规则）。一次受控 reload 后为 parent `47627`、worker `47633`、generation `40fe15c2e0e542deb8eae160bf7c973f`。after receipt 是 `analysis/output/spike_v1_ui_20260911/process_type_after_40fe15c2.json`，与 before receipt `.../process_type_before_d29f081b.json` 同字段相联。after 首轮为 `1,434 / 1,434`、errors `0`、`44.94s`，worker-start 至 scan-start hydrate `4.575s`，outbox 与 Bark outbox 均为空；按 actual close/FRESH 公式，30m / 1H / 4H 截止欠账均为 0。
- **前后 wall/CPU 差异支持默认 Standard 限制假设，但不是唯一因果证明**：before 的 484 cell 有 changed `142` / unchanged `342`，analyze wall/CPU `263.489 / 11.028s`（每 changed `1.856s / 77.7ms`），checkpoint `62.709 / 1.965s`（每 changed `441.6 / 13.8ms`）；after 的完整 1,434 cell 有 changed `317` / unchanged `1,117`，analyze `17.286 / 16.715s`（每 changed `54.5 / 52.7ms`），checkpoint `5.044 / 4.354s`（每 changed `15.9 / 13.7ms`）。after 的 thread CPU 分别占 wall 96.7% / 86.3%，而 before 仅 4.19% / 3.13%。这是一次明确单字段、同机、同 scanner 计时的强对照，支持 launchd 默认 Standard 资源分类是此前 off-CPU wall 的主要解释；但两次 changed 集合和系统时刻不完全相同，故不将其表述为数学上唯一因果。
- **bootstrap 与 UI 收敛边界**：该受控启动前曾有一次 I/O bootstrap 失败，只有在确认旧 job 已退出、plist 合法后才成功启动；它不是对同一运行 job 的重复 reload。root 的最终 IAB 已验 history/raw/30m 500 / 1,000 / 1,500、realized/censored 联结、手动 refresh 一次 signals 后周期只读 status，以及从信号收盘后显示的完整初始 SL `0.1908`；证据为 `analysis/output/spike_v1_ui_20260911/history-risk-1f7166d-final.txt`、`...-network.json`、`...-expanded.png`。冻结 DATA 的 md/sb 副图仍无线条，原因待只读核对，故不能冒充所有图表数据层均完成。

## 新实例计时与 UI 复验（2026-09-11 11:44 BJT）

- **新 generation，不能借用旧 `591d…`**：唯一受控 reload 后 parent 为 `46104`，SQLite scan meta 的新 generation 为 `d29f081bf6b74523b13a3c5d13b81936`，实际 scanner worker 为 `46222`。其 21/1,434 的首次有界读取为 errors `0`、changed `7`、unchanged `14`，是新实例的早期增量样本，不能与旧 worker PID 或旧 phase totals 混用。
- **wall/CPU 已直接区分，等待来源仍未细分**：`dbea0e8` 的 main-thread `thread_time` 字段在该样本中给出 analyze wall `15,439.861ms`、CPU `495.522ms`，checkpoint wall `4,631.611ms`、CPU `119.229ms`。即 changed-cell 的 analyze wall 中约 `14,944ms` 及 checkpoint wall 中约 `4,512ms` 不是主线程在实际消耗 CPU。`analyze()` 本身没有 SQLite 路径，故 SQLite 写锁不能解释前一项差额；但这些字段不能继续分辨 OS 调度、解释器/库等待或页故障，不能借此指称 QoS 或其它任务。此前 3 秒 sample 与内存快照仍只支持“当时主线程在 pandas/NumPy 计算，fetch threads 等队列；无即时 SQLite/IO/GC 或内存压力栈”。
- **未启用 Python 分配/调用跟踪**：服务源码与 monitor tests 没有 `tracemalloc`、`sys.settrace`、`sys.setprofile`、`cProfile`、pyinstrument 或 line-profiler 路径。LaunchAgent 的非敏感环境变量名只有 `PYTHONDONTWRITEBYTECODE`、`PYTHONUNBUFFERED`；唯一 trace 开关是 server 的 `FABLE_MONITOR_DISPATCH_TRACE` 路由阶段日志。因此没有证据支持 runtime allocation profiler 造成 hydrate、feature 或 gzip 变慢。
- **launchd/taskpolicy 的可证边界**：本机 `launchd.plist(5)` 说明无 `ProcessType` 等同 Standard，且未指定时系统可施加轻度 CPU/IO 限制；Interactive 才是 app 同级的无该限制分类。当前 job 的 plist 没有 `ProcessType`、`Nice`、`LowPriorityIO` 或资源限制；`launchctl print` 只有 jetsam `daemon`/priority `40`（内存压力分类，不能证明 Darwin-background）。`ps` 的 `nice=0`、priority `20` 同样不显示 Darwin background/AppNap/QoS。`taskpolicy(8)` 没有只读 PID 查询：`-B -p` 会把目标移出 PRIO_DARWIN_BG，`-b -p` 会把目标设为 PRIO_DARWIN_BG。原状态不可观察，故不能安全做“无 restart 后再恢复原状”的 PID 调度试验；未实际改变调度。若 owner 另行授权，唯一可审计的对照是显式 `ProcessType=Interactive` 后一次受控 reload，再比较同一 `dbea0e8` 计时，并通过回退 plist+reload 恢复，而非冒充已确认的 background 根因。
- **Interactive 单变量 before receipt**：在任何 ProcessType 变更前，`d29f…` 的 `484 / 1,434`、errors `0` 快照已写为 `analysis/output/spike_v1_ui_20260911/process_type_before_d29f081b.json`。其中 changed `142`、unchanged `342`；analyze wall/CPU 为 `263,488.725 / 11,028.239ms`，checkpoint wall/CPU 为 `62,708.844 / 1,965.483ms`。同一 captured-at 时按 actual close 计算，30m 与 1H 均 434/478 达标、欠 44；4H 478/478。该 receipt 固定 generation、worker、FRESH 公式和 close 口径，供之后同字段 after 对照；它不证明默认 Standard 已是根因。
- **UI 仍有一项布局缺陷**：新版历史 30m 首页成功返回，`initial_stop=0.1908` 已进入主图；但 SVG 右侧轴标签仍被全局边界裁切。实现者正在仅静态修正，未再次 reload。在实际标签可见前，历史风险图仍未整体验收通过。

## 最新扫描阶段（2026-09-11 11:29 BJT）

- **首轮已完整，但不是稳态通过**：同一 parent `41343` / scanner `41671` / generation `591d…` 的 reload 后首轮已于 `finished_at_ms=1789097135018` 完成 `1,434 / 1,434`、errors `0`，从 `started_at_ms=1789095313025` 计 `1,821.99s`（30.37min）。累计 phase wall 计数为 fetch `608.913s`、analyze `1,179.952s`、checkpoint `287.140s`；fetch 并发而其它路径串行，三者不能相加为总 wall。完整首轮证明 checkpoint hydrate 后可追平和无 cell error，**不满足**“全市场一轮少于 15min”这一更高性能目标。
- **第二轮已明确开始，尚无完成证据**：sleep 后该相同 worker 在 `started_at_ms=1789097255203` 写入新轮；11:29 只读 meta 为 `12 / 1,434`、errors `0`。前 12 cell 的 fetch/analyze/checkpoint 分别累计 `16.648s / 32.335s / 5.007s`（max analyze `6.242s`），是过短且波动的启动样本，不能外推成正常轮耗时或性能改善。当前 scan schema 不记录 changed/skipped 数，故不能把非零 phase total 误报为准确的 changed 数；它只证明至少一部分 cell 未走 unchanged fast path。部署任何后续静态风险线修复前，本条是唯一可用的 reload 后 baseline。
- **3 秒 stack evidence（有限窗口）**：对 scanner `41671` 的一次 macOS `sample` 保存在 `analysis/output/spike_v1_ui_20260911/scanner41671-sample-20260911T1132.txt`。主线程落在 Python→pandas/NumPy 特征运算栈；6 个 fetch worker 均在 `_queue` / `PyThread_acquire_lock_timed` / `__psynch_cvwait` 等待任务。该窗口没有主线程 SQLite、gzip/zlib、socket/HTTP、IO wait 或 GC collector 栈。故它支持“当时由主线程特征/replay 计算推进、fetch workers 未并行计算”，但只有 3 秒，不能量化为整轮耗时占比或证明全机调度归因。同期 scanner RSS 约 62.8MiB、sample physical footprint 628.2MiB；`vm_stat` 的 throttled pages 为 0、系统 free 42%，没有即时内存压力/换页证据。

## UI 复验增量（2026-09-11 11:23 BJT）

- **历史分页与轮询修复通过该项场景**：root 的真实 IAB 在静态 `15b91a8` 下完成 history/raw/30m `500 → 1,000 → 1,500`，三个 signals 请求分别为 `1.610s / 4.239s / 7.489s`；默认 chart 为 `5.194s`。连续多次 15 秒 tick 仅请求 status、没有重复 signals、没有 `loadingFailed`。完整网络记录为 `analysis/output/spike_v1_ui_20260911/history-static-15b91a8-network.json`；详情例证为 `.../history-static-15b91a8-realized.txt`（TQQQ 30m，净 R `-1.199`、保护止损、标为未独立收益审核）和 `.../history-static-15b91a8-1500.txt` 的 censored 行（未实现、不计收益）。`87d1d5d` 另增加 Node VM 和真实 `MouseEvent` 入口测试。此结果只验证已加载历史的停止轮询与前三页串行分页。
- **整体历史风险图仍未验收**：同次复验发现列表投影遗漏 `initial_stop`，导致选择历史信号时主图缺初始风险线；原有风险线文字也会落在裁切区域。实现者已定位为 `store.py` 投影白名单遗漏并在修复，且须把初始 SL 从信号收盘后显示，不回填到信号前，也不伪造动态止损。在字段和布局恢复、一次 IAB 复验实际风险线之前，不能把历史回放 UI 或风险图标为整体通过。此处不涉及通知、V1 replay 或任何 Python 服务 reload。

## 最新运行快照（2026-09-11 11:19 BJT）

本节是同一 `591d…` generation 的低频后续读取；它不代替下方 11:02 快照，也不把静态 `15b91a8` 的待验 IAB 行为写成已通过。

- **进度与耗时**：parent `41343` 仍监听、scanner `41671` 仍为其 child。只读 status 随后持久 scan meta 先后为 `1,137`、`1,188 / 1,434`，均为 errors `0`、`scanning`、同一 generation；非原子读取间的 51 cell 推进不是 generation 切换。由 `started_at_ms=1789095313025` 至本次读取约 `1,442s`（24.0min），仍余 246 cell，故完整增量轮尚未完成。
- **30m 欠账的可区分原因**：按本次 `floor(now − 30min)`，30m required actual close 仍为 `02:30Z`；426/478 达标、欠 52，且欠账 52 个的 `last_closed` 都为 `02:00Z`（其余为 `02:30Z` 的 228 个、`03:00Z` 的 198 个）。1H、4H 仍为 478/478 达标。没有 error sample 或 unavailable 记录。该形态与 `v1_worker.py:88-170` 的固定 `instrument × timeframe` 单次队列和未完成的后段 cell 一致：8 路仅并发 public fetch，而 `future.result()` 后的 recurrence replay/analyze、market/meta checkpoint 都由 worker 主线程顺序执行。累计 `analyze=902.8s`、`checkpoint=223.7s`，约为 24.0min wall 的 78%（fetch 累计 462.2s 是并发任务和 wall 不可直接相加）。因此目前有代码与计时支持的解释是**未完成的一轮主线程计算/持久化尾段**，不是通知拒绝、Bark/TG 队列、或已观测的 API 错误；是否能在完整轮后持续满足 30m 截止，仍需完整轮的下一次读数。
- **通知与静态验收边界未变**：Bark arm 仍为 raw+YOLO、30m/1H/4H；Telegram disabled；Telegram/Bark outbox 均为空，candidate 只有 2 个 disabled 历史项。`15b91a8` 仅提交了“已加载 replay 卡片不轮询”的静态修复，未触发 Python reload；尚待 root 的真实 IAB 回执，故历史重复首页请求仍保持下方“未通过”状态。

## 最新运行快照（2026-09-11 11:02 BJT）

本节只追加本次 `0055c75` 受控 reload 后的新实例事实；下方 10:25 及更早段落保留当时的进程、generation 与验收边界。

- **实例与本轮扫描**：父进程为 `41343`（10:47:50 BJT 启动，监听 `127.0.0.1:8766`），隔离 scanner 为 `41671`（10:50:16 BJT），generation `591d2349a6144e6bbbc716bde7d06356`。11:02 的只读 `/api/status` 为 `660 / 1,434`、errors `0`、`scanning`；因此本 generation 尚未完成一轮，不能把此前 generation 的完成数或截止账移植过来。root 记录本次 checkpoint hydrate 为 214s，此为启动成本，不能当稳态请求或增量轮耗时。
- **按实际 close 的通知截止账**：worker 把 `last_closed` 记录为 candle open 加周期（`yoyo/monitor/v1_worker.py:123`）。以 11:02 BJT 的 `floor(now − 30min)` 复算，30m 的 required actual close 为 `02:30Z`，246/478 已达标、欠账 232（最早 `02:00Z`，最新 `03:00Z`）；1H required `02:00Z`、478/478；4H required `00:00Z`、478/478。30m 欠账是该 generation 尚在扫描中的快照，尚不能据此宣布持续增量验收通过或失败；1H/4H 本快照已追平。
- **通知协议仍安全**：`v1_bark_arm` 仍只允许 raw 与 YOLO 两协议、`30m / 1H / 4H`，并明确 `telegram=disabled`。此刻 Telegram outbox、Bark outbox 都为 0；model candidate 只有 2 个历史 disabled 项。没有 replay candidate 或历史补发队列。这是队列/协议状态，不是任何设备送达证明。
- **历史首页投影已部署，但重复自动刷新尚未验收通过**：`0055c75` 已载入。root 的 IAB 记录显示 history/raw/30m 首页 500 条以约 431KB 在 `7.549s` 返回（旧页约 1.28MB）；但 15 秒自动 tick 又拉取同一冻结首页，并与约 10 秒的默认 chart 请求重叠，重复页在 `12.034s` 被浏览器 abort。证据在 `analysis/output/spike_v1_ui_20260911/history-projection-0055c75-network.json`。因此“首个历史 500 页能返回”通过，“已加载历史期间不重复重拉同页”仍**未通过**。root 已关闭该 IAB tab，故没有残留浏览器轮询；`v6_volume_price` 正在处理仅静态的已加载历史轮询规则，尚未由本记录宣布部署或通过。

## 最新运行快照（2026-09-11 10:25 BJT）

本节是对下方 08:21 快照的增量记录；早期记录保留其发生时的事实和路径，不倒改为当前状态。

- **扫描与通知截止**：同一服务父进程仍为 `22793`（08:17 启动）；当前隔离 scanner worker 为 `37053`，generation `842411f9217144c087051be68ff24fde`，10:25 读取为 `1,311 / 1,434`、errors `0`，所以该 generation **尚未完成**完整增量轮。worker 的 `last_closed` 是 K 线 open 时间加周期，而非 open 本身（`v1_worker.py:123`）。以 10:21 BJT 的 `floor(now − 30min)` 通知截止复算，30m / 1H / 4H 都是 `478 / 478` 达标、欠账 `0`：相应最小实际 close 为 `01:30Z / 01:00Z / 00:00Z`。FLOW 4H 为 active、非 stale、721 根、0 gap、实际 close `00:00Z`。这些是持久 checkpoint 的通知截止状态，不能移植成新 generation 已完整扫描的结论。
- **Bark、Telegram 与 cutover**：`v1_bark_arm` 保留 `30m / 1H / 4H` 和 `telegram=disabled`；raw 与 YOLO 两条 Bark cutover receipt 均存在。此刻 `bark_outbox=0`、Telegram `outbox=0`、replay candidate `0`，两个遗留 live candidate 都是 disabled。也就是说没有历史回放补发或 Telegram 队列；此项只证明当前队列安全，不是手机送达证据。
- **历史 30m 首页 UI 故障已实机复现**：root 在独立 IAB tab 由 live 158 条切至“历史回放 → 原始 V1 → 30m”后，首个 `limit=500` 请求反复在浏览器的 12s `AbortController` 截止前未回包，页面显示“指标启动：服务响应超时；运行状态：服务响应超时”，加载 `0` 条；DOM 与截图见 `analysis/output/spike_v1_ui_20260911/history-initial-500-20260911T1025.txt` / `.png`。这推翻了“仅第三 cursor 页”或“孤立 5s GET 即代表 UI 正常”的结论。
- **trace 的已区分原因**：10:23:31 与 10:23:37 的两个 replay/raw/30m `rows=500` 分别到达 handler 后 `14.575s`、`16.450s`，超过前端 12s 截止；同时 `/api/markets` 一次耗时 `23.463s`，其它 live signals 也出现 15–20s 甚至 40s exit。前端 abort 不会取消正在执行的同步 FastAPI handler，15 秒轮询/重试可与未结束的请求叠加；多个 JSON decode/encode 路径竞争同一解释器，因此这是 payload/GIL 积压，**不是只在 cursor SQL 上的故障**。无重叠的同一真实 cursor GET 曾为 HTTP 200、500 条、`1,281,002B`、`5.192s`，显示 500 页负载是放大器而非单独充分条件。父进程在 `def1fb3`（08:21 提交）之前启动，当前 trace 没有该提交新增的 `signals:sqlite/decode` marker，故它确认尚未载入；只有下一次已授权 reload 才可细分 SQLite 与 decode，不能以提高 12s timeout 代替修复。
- **最小可实现范围**：同一筛选下 500 个原 payload 为 `1,234,350B`；`covered_ledger` 占 `817,289B`（其中仅文件路径/SHA 的 evidence `556,508B`）。卡片和详情只读取 `covered_ledger.link_status` 与 `outcome.status/exit_reason/exit_time_ms/net_r`，图表已按 `event_id` 读取完整冻结 OHLC。服务端仅给列表投影这些 UI 字段可降到 `328,882B`（节省 `905,468B`，`73.4%`），同时保留 `get_event(event_id)` 的完整证据给图表/取证。这是当前唯一有字段级证据的最小修复方向；不更改事件、cutover 或通知规则。

## 当前验收状态（2026-09-11 08:21 BJT）

本节覆盖下方早期阶段的 `1,019` 回放、首轮扫描和“仍待桌面/窄屏”的临时表述；旧段落保留为当时发生的审计记录，不改写其路径或结果。

- **冻结回放与逐笔证据**：当前三周期运行库为 **6,185** 条 `replay/raw`，均联结至同一 SHA 命名不可变账本快照：**6,116 realized、69 censored、0 unverified/mismatch**。censored 不显示退出、净 R、胜率、PF 或收益；realized 的单笔字段仍标为“未独立收益审核”，不是策略 edge 或账户收益。回放导入与重链从未创建 candidate、Bark 或 Telegram outbox；当前两类 outbox 仍为 0。
- **当前运行实例**：前一代 `c1903…` 已完成 **1,434 / 1,434**、errors `0` 的增量轮；在 `now_ms=1789085850215`，依固定 `FRESH_MS=30min` 计算的 30m / 1H / 4H 通知截止欠账均为 **0 / 478**。随后为摘要回填防护与 trace 只执行一次有目的 reload：父进程 `22793`、generation `aa9b7d21f9aa4ff8bab0140366c5c13e`、worker `22886`；08:21 的新轮为 **12 / 1,434**、errors `0`，不能把前一轮完成数移植给新 generation。当前 1,434 个紧凑市场摘要已由 scanner 回填；摘要表不含 chart/events。
- **分页 UI 的真实边界**：静态 `0d0a5eb` 在 root 的历史 30m 实机复验中成功读取 **500 → 1,000**；下一 cursor 页超时并保留 1,000 条与明确错误。更早版本曾成功读至 2,500 条，且 Unicode `龙虾` 的 Gate/Binance 筛选和同源冻结图已验，但这不能替代本次串行 cursor 的完整验收。reload 后一次固定的第 3 页 cursor 请求返回 500 行、1,280,998 bytes，TTFB `5.923s`、总计 `5.936s`；trace 为 entry→handler `276ms`、storage+decode 合计阶段 `3.074s`、handler return→middleware exit 约 `2.67s`。它尚未复现超时，也没有充分细分 SQLite 与 JSON decode，不能据此归因或仅提高超时。
- **已部署与待部署边界**：`8ec2727` 的市场摘要回填初始化保护和 `9dfa121` 的无内容 phase trace 已载入 `22793`；`def1fb3` 进一步仅在 trace 打开时拆分 SQLite fetch 与 JSON decode，尚未载入，避免在新轮刚开始时再次 restart。运行中的 V1、cutover、新鲜度、风险和通知规则不变。

没有新的实盘、收益、模型泛化或手机实际送达结论。前一 generation 的完整增量轮和按 `FRESH_MS=30min` 的三周期截止账已经通过；新 generation 的完整轮，以及 cursor 超时的 SQLite/decode/encode 分段证据仍是未完成验收项。

## 早期阶段结论（审计记录）

本轮把本机 `127.0.0.1:8766` 的监控协议收敛到冻结的 **SPIKE V1 长多 30m / 1H / 4H**：收盘后的原始 V1 启动和额外 YOLO 确认是独立事件、独立 Bark 阶段；历史回放只用于浏览且绝不补发 Bark。已导入的 1,019 条 V1 回放信号可按 event id 读取同源冻结 OHLC，并在前端明确标为历史复盘上下文；它们已另行联结到 v2 覆盖账本，但联结不是盈利或策略有效性证明。

本机服务可监听且关键 `/api/status` 超时根因已经通过 trace 确认并修复；当前首轮扫描仍在进行，不能把进程存活或部分完成误称为市场就绪。共享 IAB 已完成一张实时卡的实际预览验收，桌面/窄屏的全部交互仍待补齐。没有任何盈利、模型泛化或 Bark 手机送达声明。

## 协议与数据边界

| 项目 | 当前行为 |
| --- | --- |
| 实时信号 | 原版 SPIKE V1、仅 long、30m / 1H / 4H 的已收盘 bar |
| 通知 | `source=live, confirmation=raw` 与随后 `source=live, confirmation=yolo` 分别去重、分别允许 Bark；Telegram 关闭 |
| 回放 | `source=replay, confirmation=raw`；无候选、无 outbox、无通知 |
| 回放图 | 只由已保存 event id 定位 `venue/symbol/timeframe/bar_open_ms`；按需读取冻结 gzip OHLC；后续 K 线标为历史复盘，未输入扫描器或 YOLO |
| 回测指标 | 回放只显示有四元组与 SHA 证据的 v2 单笔退出/净 R；已实现与 censored 分开，均标为未独立收益审核，不推断 PnL |

回放导入命令已在此配置下执行一次：

```bash
python3 -m yoyo.monitor.replay_import \
  experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz \
  --database "$HOME/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3" \
  --import-id spike-v1-covered-ledger-20260911-unverified-partial
```

结果为 783 条新导入、236 条既有相同记录、11 条不属于 V1 30m/1H/4H 的记录跳过；运行库最终有 1,019 条 `replay/raw`。对这 1,019 条已导入记录按文件存在性检查，冻结 OHLC 覆盖为 1,019/1,019；这里的覆盖只说明可画出对应源 K 线，不说明交易完整性或策略表现。

## 回放账本逐笔联结（`9015364`、`f831db3`）

signal-only importer 保持不复制 outcome；随后独立命令以 `venue/symbol/timeframe_min/signal_bar_open_ms` 精确联结运行库中的 1,019 条 replay/raw 与冻结 `covered-v2-next-open-risk-realized-event-sequence` ledger。实际运行得到 1,001 条 realized、18 条 censored、0 unmatched/source mismatch/OHLC missing；四元组、monitor journal id 和 ledger source-event id 均无重复。每条 payload 同时保存两种 id、Pine/source、ledger、coverage receipt 和冻结 OHLC SHA。

此操作只调用已有事件的 payload update，不经 event insertion、outbox 或 candidate 路径；运行后 Bark outbox 仍为 0。随后发现旧链接指向会被覆盖的汇总路径，故 `e55a421` 先将 1,019 条降为不显示 outcome 的 stale evidence 并保留旧 id/SHA 审计，再复制当前 ledger/coverage receipt 成 SHA 命名不可变副本后重链。最终仍为 1,001 realized、18 censored、0 mismatch/missing，双方 id 各 1,019 唯一，Bark/TG outbox 均为 0。前端为 realized 行显示冻结账本的退出与“单笔净 R · 非账户收益”，为 censored 行明确显示“样本结束时尚未退出；不计胜率、PF 或净收益”；若未来证据漂移则显示“账本证据已过期 · 不展示收益”。逐笔 receipt、命令和完整边界见 [p1_spike_v1_replay_ledger_link_20260911.md](p1_spike_v1_replay_ledger_link_20260911.md)。

历史遗留处理限定为源为空、协议为旧 IMACD 的 9,160 条错误 journal 行；它们已在有完整数据库备份后移除。迁移期间另发现 5 个可由 canonical V1 raw identity 替换的旧重复 id，已幂等清理；11 个无 canonical 替代的旧 id 未伪造替代记录。没有再次清库，也没有触及本次 live/replay 行。

## 回放图实际接口核验

清理 trace 后，对本机 API 读取一条已导入记录：

| 字段 | 实测 |
| --- | --- |
| event id | `120295e8085b51f7fc21ca36` |
| 来源 | Binance `ARCUSDT`，30m，`bar_open_ms=1788971400000` |
| `/api/replay/chart` | HTTP 200，105 根实际可用上下文 K 线 |
| 定位 | 返回 target K 线时间与该 event 的 `bar_open_ms` 完全相同 |
| target OHLC | 0.07349 / 0.07850 / 0.07297 / 0.07774 |
| 未来上下文 | `historical_review_only_not_model_input` |

该端点拒绝客户端提供 venue、symbol 或时间；只从数据库中的 event 读取。缺冻结文件会显式返回 `frozen_ohlc_missing`，不会改画当前 OKX 行情。

## HTTP 与扫描真实状态

诊断 trace 先排除了 `/api/status` 自身的数据库读取：它的 handler 可在约 16ms 到达。真正阻塞来自 `/api/markets`：旧实现会同步 JSON 解码 214 行完整 market payload，合计约 11.5MB（每行含 chart/events），并在共享解释器路径序列化响应。trace 记录的两次旧请求内耗时为 25.792s 与 19.595s。

提交 `181193f` 后，概览改读排除 `chart/events` 的摘要，选中图才按主键读取完整记录。受当前机器资源竞争影响，摘要仍需 SQLite 解析旧 JSON 输入，trace 内为 5.569s，实际响应约 6.323s，输出仅 85,842 bytes；但紧跟其后的 `/api/status` 成功返回，handler 约 488ms、请求总计 556ms，没有再次零字节超时。若未来必须进一步压低市场概览延迟，正确范围是写入时保存紧凑 summary 列或表，不能把 chart/events 再塞回概览。

最终 cleanup reload 后的单次实际 API 检查：

| 检查 | 结果 |
| --- | --- |
| `/api/health` | HTTP 200，`service_alive=true`，扫描写入当时为 `30 / 1434`、errors `0`；但 `market_ready=false`、`model_ready=false`、`ok=false` |
| `/api/status` | HTTP 200，非 stale 快照；紧邻读取的已缓存扫描快照为 `27 / 1434`、errors `0`，模型仍 `loading`。扫描写入与状态快照不同步，故不把两者混为一个精确完成计数 |
| `/api/markets` | HTTP 200，214 条摘要：30m 72、1H 71、4H 71，三周期这些已记录行均为 ready；没有 `chart/events` 字段 |
| Bark 状态 | `v1_bark_arm` 已设置；当前 V1 bark outbox 为 0，未发送测试消息 |

因此三周期已开始扫描但远未完成，当前自然 live raw 为 22 条、live YOLO 为 0 条。没有把待预热、扫描中或模型加载中表示为可通知状态。

## 发布后运行验收（`cc6bf62`）

`cc6bf62` 保留完整 720 根的冻结特征与 replay，只在 worker 内避免构造随后会丢弃的前 480 条 chart JSON；state、events 与末 240 条 chart 的逐值等价测试通过。它还将无候选时的 YOLO 状态从误导性的 `loading` 改为 `idle`：该状态仍为 `loaded=false`、不等于 ready，也不会改变任何通知判定。

发布后的第一次读取看到 `117 / 1434`，但其 `scan.started_at_ms` 早于新 monitor 的 `started_at_ms` 约 15 分钟，故它是重启前轮次留下的持久 meta，不能作为此次发布的吞吐证据。进程树复核只剩新 parent `72471` 与其两个 multiprocessing child `72653` / `72777`，没有旧孤儿 writer。新 scanner 随后写入新的 `scan.started_at_ms=1789068913215`；低频连续两点从 `3 / 1434` 到 `39 / 1434`、errors `0`，约 33 cells/min。这只是当前资源条件下的一分钟观测，不是 15 分钟 SLA 或全轮预测。

同一最终检查的 `/api/health` 为 HTTP 200 / 696ms，`market_ready=false`、`model_ready=false`、`ok=false`；`/api/status` 为 HTTP 200 / 384ms，scanner 仍为 `scanning`。YOLO 为 `idle`、`loaded=false`、queue `0`、processed endpoints `0`、last error `null`，即没有 raw candidate 时按设计延迟加载，而不是已经推理就绪。首轮全市场尚未完成，不能称 ready。

## Cutover 候选与实际卡片预览

冷首轮发现 IBM `4H` 的 raw 记录 `0b2652408b7f176fa6b5f831`：它在 Bark arm 前约 24.8 小时收盘，但仍落在旧的九根候选窗口内。raw 的 Bark 判定已正确拒绝它，所以 outbox 始终为零；问题是旧代码仍会把它送入 YOLO 候选。`3f98028` 现以同一 raw eligibility 判定注册候选，并在模型 worker 处理持久候选前再次验证原始 bar 的 cutover。这个历史候选在数据库中标为 `disabled/raw_not_eligible:before_bark_activation`，原始事件和审计保留；没有删除 live/replay 记录、没有发送错误通知，也没有为这项小修重启正在缓存首轮的 worker。

另一个真实前端合同错误出现在实时 DATA 30m 卡片。浏览器内显示周期为 `30`，而 `/api/chart` 只接受 `30m`；`85566e2` 在请求边界显式映射 `30/60/240 → 30m/1H/4H`，未知周期 fail closed。已直接读取 `DATA-USDT-SWAP` 的 `/api/chart?timeframe=30m`，返回 240 根 K 线。共享 IAB 刷新后实际点“页内预览”，右侧呈现价格刻度、6 MA、120 根信号附近 K 线、V1 风险线和时间轴；截图为 `analysis/output/spike_v1_ui_20260911/desktop-live-before-theme.png`。图中未出现服务 HTTP 400 或伪造行情。

实机继续覆盖了深/浅主题、放大、滚轮缩放和拖拽：缩放后时间轴从 `9/8 17:00` 延至 `9/10 16:30`，拖拽后为 `9/8 05:00` 至 `9/10 04:30`，风险线和 K 线保持同一视图；收起时 dialog 已关闭。桌面与 390×844 窄屏也成功读取 Binance AIXBT 4H 的同源冻结 OHLC：信号 `2026-09-05 12:00`、收盘 `0.02197`、风险 `0.01915`，图中明确注明信号后的 K 线仅供回看。窄屏 `clientWidth=scrollWidth=390`，详情、放大/收起和返回卡片均可用。相应截图在 `analysis/output/spike_v1_ui_20260911/`。

随后发现 source/周期切换时，已飞行的实时请求可在回放筛选之前落地，短暂重选 IBM live 详情。`ad6a98e` 用 `source + timeframe + revision` 作为查询上下文：切换时先清空旧卡片/详情/图表为加载态，旧回包不再写入；若切换发生在同步期间，则当前上下文自动排队重取。图表回包还必须匹配当前 id、source、confirmation 与收盘时刻。受控 IAB 复验中，DATA live 立即切到 replay 4H 后，页面显示“正在读取历史回放”、卡片为空、详情为空且没有 IBM/DATA 残留；AIXBT 的展示符号已从 `AIXBTUSDT · USDT` 收敛为 `AIXBT · USDT`，预览 aria 标签加入 venue。

`c70f10f` 进一步清除了空详情时遗留的 chart aria，并使加载态 aria 直接描述当前 source、合约和周期。最终受控检查中，replay ARC Binance 1H 实际载入 98 根冻结 K 线（`2026-09-06` 至 `2026-09-10`）、风险 `0.0714`；工具 DOM 已确认真实图表数据，筛选截图为 `desktop-replay-1h-filter.png`。随后从 1H 切到 30m 仍立即回到空列表/空详情加载态，没有旧卡片、旧图或旧 aria。

最后，ARC Binance 30m 也从同一冻结来源真实载入 105 根 K 线、风险 `0.07145`（`desktop-replay-30m-filter.png`）。因此当前桌面和窄屏均已验 live/replay、30m/1H/4H、主题、选择、放大/收起、缩放、拖拽、同源历史图和筛选加载态；没有重复执行这些 UI 场景。

独立复核还验证 `3f98028` 的候选守门按 **raw bar 的原始收盘时刻** 重判，所以不会把合法的 1H/4H 等待窗口误杀。`e5f006b` 对第二阶段明确分时钟：raw 仍必须在当时通过 cutover/新鲜度，YOLO 追加事件则按自己的确认收盘时刻发送 Bark。回归覆盖“raw 已两小时、当前 1H 确认刚收盘 → 入库并追加 Bark”及“确认自身过期 → 仅入库、不补发”。这个 Python 后端提交尚未载入正在完成冷首轮的 worker；静态前端提交无需重启已由 IAB 验收。

在 2026-09-11 的低频只读检查中，当前同一 scan `started_at_ms=1789068913215` 处于 `875 / 1434`（status 快照为 871）、errors 0；`/api/health` 为 636ms，`/api/status` 为 491ms。模型已实际 `ready`、`loaded=true`，已处理 7 个 endpoint，队列 0，唯一候选为上述 disabled 历史项。`market_ready=false` 仍正确，因为首轮没有完成；Bark outbox 的 pending/sent/failed/unknown 均为 0。

## 实现与验证

本轮 monitor 提交链：`bf08b3c`、`8129087`、`603ea45`、`07e2db7`、`468fc5a`、`229b654`、`6b11464`、`a481311`、`1e5a286`、`7ce4971`、`df6c8f6`、`63e0b29`、`f35caf4`、`181193f`、`cc6bf62`、`3f98028`、`85566e2`、`ad6a98e`、`a006636`、`c70f10f`、`e5f006b`。其中 `f35caf4` 使 replay 图依赖在选卡时才导入，避免 pandas/NumPy/PyArrow 在 FastAPI 监听前阻塞；`181193f` 是上述 overview blocker 的最小修复；`cc6bf62` 是保留因果输入的显示物化优化与 V1 gate 测试迁移；后续提交分别保护 cutover 候选、实时图表周期、筛选后详情、跨查询上下文的旧回包、加载态 aria 和延迟确认的通知时钟。

本轮实际执行的定向代码验证：

```bash
python3 -m pytest -q \
  tests/monitor/test_v1_chart_limit.py \
  tests/monitor/test_v1_worker_cache.py \
  tests/monitor/test_model_gate.py \
  tests/monitor/test_spike_v1_yolo_extra.py \
  tests/monitor/test_spike_v1_health.py
python3 -m py_compile yoyo/monitor/signals.py yoyo/monitor/v1_worker.py yoyo/monitor/model_gate.py
node --check yoyo/monitor/static/app.js
node --test tests/monitor/frontend_cards.test.cjs tests/monitor/frontend_theme.test.cjs
```

本次最终 V1 gate/worker focused suite 为 46 passed；此前回放/API 相邻 suite 为 10 passed。它们不是两个独立测试集，不应相加。`test_model_gate.py` 已迁到 current V1 live/raw/long/closed 合同，保留到期、延迟确认、去重、重启和因果端点覆盖；旧 IMACD `md` 失效断言以 V1 source/confirmation/direction/side/risk fail-closed 注册测试替代。随后对 cutover 修复的组合 suite 为 25 passed，前端 Node 合同测试为 10 passed，延迟确认 focused suite 为 11 passed；它们含重叠文件，不能相加。此前独立 CUA surface 的 `getTab('3', {browser:'1'})` 返回 `Browser is not available: 1`，但 root 已用共享 IAB 完成上述真实卡片与窄屏验收。静态/API 合同检查不替代尚未完成的全市场扫描。

## 扫描覆盖显示（`4419b1b`）

首页曾将持久化的 `counts.ready` 显示为“窗口已就绪”，但它可以来自上一轮 feature phase，不能证明新一轮已追平交易所收盘；短历史合约也可能未达到 340 根 warmup。现在概览只显示本轮 `scan.completed / scan.total`：扫描中为“已扫描 · 预热/追平中”，整轮完成后为“已覆盖，按收盘刷新”。它不把 `phase=ready` 或强制 `stale=false` 当作最新 bar 的证据。

## Scanner recurrence checkpoint（`64d4308`，待单次受控 reload）

诊断确认 scanner 的完整 OHLC recurrence 过去只在 worker RAM；`markets.chart` 只有 240 根，不能替代 V1 的 340 根 warmup，也不能安全拼接增量。`64d4308` 新增 monitor 私有 SQLite 的 gzip raw checkpoint：每个成功获取的完整序列在 replay/market write 前保存，重启时只有通过周期对齐、连续性、有限 OHLCV 和价格边界校验的序列才会恢复；异常或 gap payload fail closed，回到 cold fetch。

定向恢复测试以 341 根种子重启并追加一根，断言恢复后的 V1 state 和末 240 chart 与完整序列重放相同；另测 gap checkpoint 不会进入 recurrence seed。它不裁剪输入、不改 Pine、特征、cutover 或通知逻辑。

在第一轮完成后仅执行一次受控 LaunchAgent reload；旧 RAM seed 因旧 worker 尚未具备 checkpoint 能力而不可迁移，这是首次部署的明确边界。新 worker 的 scan `started_at_ms=1789077675850` 从 0 进至 21/1,434、errors 0，并已有 22 个 checkpoint。model ready 后，两个重启前遗留且早于 Bark cutover 的 IBM 4H、STABLE 1H candidates 都变为 `disabled/raw_not_eligible:before_bark_activation`；Bark pending/failed/unknown 保持 0，未发生历史补发。

## 运行环境与未完成项

受控 trace 已从 LaunchAgent 移除并以无 trace 的 cleanup reload 启动。启动仍受本机其它 ccxt-mcp/ChatGPT 作业竞争影响：诊断时系统 load 约 100、CPU idle 0%、swap 约 6.4GB；本任务无权停止其它会话。监听器可在约一分钟后建立，因而不能以冷启动瞬时健康响应设定性能承诺。

此前本任务暂停的 `yoyo.evaluation.spike_v1_twoyear_allmarkets fetch --venue binance`（PID 51814）已核对命令身份并 `SIGCONT` 恢复，进程从 `T+` 变为 `S+`。它是独立两年采集，不是本轮 monitor 结果；恢复不表示其评估账本问题已解决。

后续仍需在资源可用时完成：全 1,434 单元扫描，并在不丢失内存 OHLC cache 的受控 reload 时载入 `e5f006b`。更广泛 V1 或 YOLO 有效性、收益和手机实际送达均不在本次功能验收范围。

## 冻结 replay 的热点与吞吐边界（`5dceb5f`，未部署）

2026-09-11 对新 cold scanner 的只读采样显示，它近 20 个单元约为 4.5--10.4 秒/单元；采样栈主要落在 pandas/NumPy，而不是网络、SQLite WAL 或 gzip。该观察发生在系统 load 约 118、swap 使用约 9.42/10GB 的资源竞争下，scanner 本身约 3.9% CPU，因此它不能推出稳态延迟或 15 分钟全市场 SLA。

`5dceb5f` 是一项不改变冻结 V1 输入或规则的等价物化优化：`features(frame)` 与 `replay(feature_frame, tick)` 仍对每个单元的完整 720 根历史执行；只在两者返回后，一次性取得 OHLCV、特征与 replay 列数组，替代逐 bar 的 `iterrows()` 和 Series `.iloc`。raw events、state 与末 240 根 chart 仍由同一完整 replay 输出构造，未截断 warmup、未改周期、风险、cutover 或通知。worker 同时在持久 `scan` meta 中记录每轮 fetch/analyze/checkpoint 的总计与最大毫秒数，供下一次受控发布后的实际瓶颈归因；当前运行 PID 没有载入该源码，未因本优化重启。

固定的 720 根 1H 因果 fixture 与变更前 `HEAD` 实现逐字输出相等；新的 SHA-256 为 `2ecb21f572e9403dd00fd8733626e9621285e63a978d37e891a2b1da2d135bec`。同一输入、本机五次串行调用的均值约从 172.04ms 降至 52.43ms（最小值 160.38ms 到 47.97ms）。这只是离线函数耗时，不含 OKX、SQLite、系统调度或全轮吞吐，不能作为线上速度承诺。

最新共享 IAB 的回放 QA 还实际选中了 Binance `AKTUSDT` 30m、UTC `2026-09-08 15:00` 的 linked censored 行：同源图有 120 根真实 K 线和风险 `0.5385`，页面不显示退出或单笔净 R，并说明样本结束时尚未退出、不计胜率/PF/净收益。已实现行继续只显示“回测退出”“单笔净 R · 非账户收益，未独立核验”；这些字段是有 receipt 的账本联结，不是策略收益宣称。

本提交的定向验证为 24 个 Python tests、12 个 Node 前端合同 tests 和 `py_compile`。旧 `tests/monitor/test_signals.py` 仍针对已经移除的 IMACD API，单独运行会失败，未把该陈旧 suite 计入通过数，也没有为它放宽 V1 协议。

## `5dceb5f` 受控 checkpoint reload 实测

独立只读复核通过后，按授权只执行一次 LaunchAgent reload。新父进程为 `8843`（run 4）；新 scanner 写入 `started_at_ms=1789078801148`。reload 前数据库已有 121 个完整 checkpoint；新 worker 启动时恢复这些已有 seed，随后在初始冷轮写入 129/1,434、errors 0、checkpoint 130。它证明**已有的 121 个序列**可以 hydrate，不能证明其余 1,313 个单元已经有缓存或已经追平。

新代码的分阶段计时已落到持久 `scan.timing_ms`：该观察点为 fetch total 51.542s / max 8.516s，analyze total 19.074s / max 5.334s，checkpoint total 4.683s / max 0.842s。这些是截至 129 个单元的累计，不是全轮 SLA。服务在子进程启动时短暂返回 503；随后 `/api/health` 与 `/api/status` 都为 HTTP 200（此次读取约 644ms / 560ms），状态快照 non-stale。YOLO 为 idle、loaded=false、queue 0；Bark 的 pending/sent/failed/unknown 全为 0，Telegram 仍 disabled。没有为了这次检查再次 reload、没有历史补发。

约 24 分钟后的低频读取仍是同一 `started_at_ms=1789078801148`，进度为 714/1,434、errors 0。该时 `health` 约 2.533s、`status` 约 431ms，`market_ready=false`；Bark outbox 的 pending/sent/failed/unknown 仍全为 0，Telegram 继续 disabled。这只能证明新的 checkpoint-aware worker 持续推进，不能证明全部合约已追平、模型已产生候选或通知已实际送达。

## 回放全量浏览的分页边界

目前数据库只装入 1,019 条已有回放；当前不可变三周期账本有 6,185 条，另有 5,166 条待按 signal-only 方式接入。为避免接入后 API 仅返回最近 2,000 条而让早期记录不可访问，`/api/signals` 现支持稳定的 `(bar_close_ms,event_id)` 降序 cursor。信号页在用户点“加载更早记录”时最多追加一页 2,000 条 raw 记录；周期/source 切换会清空旧 cursor，定时刷新不会覆盖已经加载的旧页。此路径不改变回放不通知、long-only、候选或账本 outcome 规则。

## 冷扫完成与增量验收待续

`e55a421` 重链后不触发 scanner、候选或通知。低频读取显示同一新 cold scan `started_at_ms=1789078801148` 已在 `finished_at_ms=1789080909373` 完成 **1,434 / 1,434** 单元、`errors=0`，持续 `2108.22` 秒。该轮累计计时为 fetch `3443.925s`、analyze `1283.350s`、checkpoint `407.454s`；它们跨单元累加，不能相加成单条路径延迟或宣称 15 分钟时效。此时 `/api/health` 为 HTTP 200 / `1.377s`，`/api/status` 为 HTTP 200 / `0.316s`。

完成首轮只说明当前 catalog 每个单元至少被本次 worker 处理过：短历史、下一根收盘时效和新鲜通知仍须由下一增量轮分别验收。状态的下一轮计划为 `1789081029373`；本记录不以 completed/ready 字段代替逐周期最新收盘证明，也不为此重启服务。此时 model gate 是 `idle`、`loaded=false`、queue `0`，对应没有合格 raw candidate 的惰性加载，不是假置 ready。Bark pending/sent/failed/unknown 都为 0；Telegram configured/enabled 均为 false，未发送测试通知。


## 全量三周期回放接入与证据状态

在不可变 snapshot 独立复核后，signal-only importer 将全部 6,185 条 30m / 1H / 4H V1 回放记录接入 monitor（5,166 新行，1,019 幂等既有）；68 条 1D 行按当前 UI/通知协议跳过。修正此前 audit 错继承浏览上限的缺陷，以及中文 Binance/Gate 合约被 ASCII 路径校验误报为缺 OHLC 的问题后，cursor 全量重链和 receipt 都覆盖 6,185 行：6,116 条已实现、69 条 censored、0 条 `ohlc_missing`。Unicode 路径仍拒绝 NUL、`/`、`\`，并在 resolve 后精确限制在对应 venue 的冻结目录；页面不会借当前行情或其他 venue 画图。

全量导入和联结不走 `upsert_event()` 的通知参数、candidate 或 Bark sender：Bark/TG outbox 仍是 pending/sent/failed/unknown 全 0，既有 disabled candidates 为 2。前端已有服务器 cursor 路径可从 2,000 条一页继续读取更早 raw 历史；当前运行实例尚未 reload `d03cc3a` / `3af2e8e`，因此这项分页 UI/API 发布验收应在不打断正在进行的增量 scan 后再做。

## 完整增量轮、30 分钟通知截止与 checkpoint reload

首轮完成后，同一运行实例的下一轮从 `started_at_ms=1789081029475` 至 `finished_at_ms=1789082805403`，完成 **1,434 / 1,434**、errors `0`，持续 `1775.93` 秒。它只验证当前机器在当时资源条件下的一整轮完成，不构成固定扫描 SLA。

通知时效按当前固定 `FRESH_MS=30min` 的实际截止定义检查：对每个周期要求 `actual_close >= floor_to_timeframe(now - 30min)`，而不是错误地要求 1H/4H 都等于当前最新收盘。在 `now_ms=1789082257844` 的只读检查中，30m required close 为 `1789079400000`、1H 为 `1789077600000`、4H 为 `1789070400000`；三个周期各 478 个单元都达到各自截止，欠截止单元为 **0**。短历史与 feature warmup 继续单列，未被这一通知截止账掩盖。

随后只执行一次受控 LaunchAgent reload 以载入 cursor 分页和 Unicode 冻结 OHLC 路径修复。reload 前 checkpoint 为 1,434 个非空 seed（每周期 478）；新父进程 `18080` 仍可读取相同的三周期 checkpoint。reload 后元数据短暂显示 `started_at_ms=1789083154333`、`312 / 1,434`、errors `0`；后续以进程启动时间复核发现该时间戳早于新父进程，故它可能是保留的旧 scan meta，**不能**作为本次 hydrate 进度证据。Bark 和 Telegram outbox 均为空，两个历史候选仍为 disabled。完整 checkpoint 的可恢复性仍受 restart/hydrate parity 测试保护；本次运行只证明服务可启动并保留 seed，不把该计数写成新一轮完成。

reload 后首次分页 API 请求在运行负载下两次未在 10 秒和 30 秒内给出首字节；独立只读检查确认同一 cursor SQL 在数据库为 0.164ms，不能把这两次超时归因于分页查询。当前服务 PID 的无 trace 运行无法事后定位调度边界；没有因此更改 SQL/async 或再次 reload。分页与中文合约的真实 UI 验收仍待一次低负载受控操作。

## 分页响应与 Unicode 浏览修正（待低负载 UI 复验）

对新进程 `19133` 的一次固定 `replay/raw/30m/limit=1` 请求，dispatch trace 记录 `entry → exit` 为 `574ms`，HTTP 200 的 TTFB 为 `0.580s`、总耗时 `0.664s`，返回 `next_cursor=true`。同一 trace 中，浏览器首屏并发的 `limit=2000` 信号请求曾在约 `19–28s` 后才 exit；独立 SQLite 同过滤查询为 `0.164ms`，因此不能把整条 HTTP 延迟归咎于 cursor SQL。trace 临时环境已从 LaunchAgent 取消；当前进程不再为关闭 trace 而重启。

前端将每次信号 API 读取改为 500 条，服务端保留 2,000 条 cap；“显示更多（当前页卡片）”与“加载更早记录（cursor）”现在是并列入口，首屏不再把 500 或 2,000 写成历史总数。此前中文合约名在前端搜索中被 ASCII 归一化为空输入，导致筛选后残留无关 DATA 缓存卡；搜索现保留 Unicode 字母/数字，且当前筛选不存在已选卡时清空详情和图。相关静态提交为 `f8025dc`、`ab54034`、`6022205`；它们只在页面刷新后生效，不需要再 reload scanner。

目前这三项还缺一次低负载的真实 IAB 验收：历史 30m 首屏应显示当前页与独立“加载更早记录”，点击后能跨 cursor；输入 `龙虾_USDT` 应只显示对应中文合约并读取同源冻结图。由于此前浏览器请求出现超时，不能把静态测试或 limit-1 探针替代为这项 UI 成功证据。

`5d0e0b1` 还修正 reload 后 scan meta 的来源标识：parent 先写 current generation 的 `starting`，child 在同步前写同 generation、实际 pid 和启动时间；status/health 对 generation 不符的持久 meta fail closed 为 `starting/stale_previous_run`。该源代码尚未载入当前 PID19133，不能用旧 `936/1434` 再宣称 hydrate 新轮进度。
