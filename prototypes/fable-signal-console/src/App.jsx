import { useEffect, useMemo, useState } from "react";

import activityIcon from "./assets/activity-icon.svg?raw";
import chartAvax from "./assets/chart-avax.svg?raw";
import chartBtc from "./assets/chart-btc.svg?raw";
import chartEth from "./assets/chart-eth.svg?raw";
import chartLink from "./assets/chart-link.svg?raw";
import chartSol from "./assets/chart-sol.svg?raw";
import trendIcon from "./assets/trend-icon.svg?raw";
import { LiveMarketChart, MARKET_MA_SERIES, ReplayMarketChart } from "./LiveMarketChart.jsx";
import { getModelStatus, runModelInference } from "./modelClient.js";

const CANDIDATES = [
  {
    code: "ETH",
    symbol: "ETH-USDT-SWAP",
    verdict: "通过",
    tone: "pass",
    price: "3,486.20",
    change: "+0.84%",
    positive: true,
    detectedAt: "12:45:08",
    yolo: "0.91",
    quality: "78",
    rank: "0.82",
    rankScore: 82,
    conclusion: "结构收敛 · 动量开始释放",
    action: "进入前向观察，不触发交易执行",
    verdictTitle: "进入前向观察",
    chart: chartEth,
    flow: [
      ["passed", "Pattern Teacher", "等待 latest research adapter", "0.91"],
      ["passed", "Formation Quality", "因果窗口 · 结构一致性", "78/100"],
      ["passed", "LightGBM Ranker", "冻结阈值 · 横截面排序", "PASS"],
      ["passed", "Forward Gate", "新鲜度 · 风险门禁 · 去重", "LOGGED"],
    ],
  },
  {
    code: "SOL",
    symbol: "SOL-USDT-SWAP",
    verdict: "观察",
    tone: "watch",
    price: "148.62",
    change: "+0.31%",
    positive: true,
    detectedAt: "12:44:51",
    yolo: "0.87",
    quality: "72",
    rank: "0.76",
    rankScore: 76,
    conclusion: "形态成立 · 判断分不足",
    action: "等待下一根 15m K 线确认",
    verdictTitle: "等待确认",
    chart: chartSol,
    flow: [
      ["passed", "Pattern Teacher", "等待 latest research adapter", "0.87"],
      ["passed", "Formation Quality", "因果窗口 · 结构一致性", "72/100"],
      ["passed", "LightGBM Ranker", "冻结阈值 · 横截面排序", "PASS"],
      ["blocked", "Forward Gate", "新鲜度 · 风险门禁 · 去重", "HELD"],
    ],
  },
  {
    code: "BTC",
    symbol: "BTC-USDT-SWAP",
    verdict: "拦截",
    tone: "block",
    price: "112,840.0",
    change: "−0.18%",
    positive: false,
    detectedAt: "12:44:32",
    yolo: "0.85",
    quality: "69",
    rank: "0.71",
    rankScore: 71,
    conclusion: "视觉候选 · 波动扩张超限",
    action: "风控门禁拦截，未进入前向账本",
    verdictTitle: "候选已拦截",
    chart: chartBtc,
    flow: [
      ["passed", "Pattern Teacher", "等待 latest research adapter", "0.85"],
      ["passed", "Formation Quality", "因果窗口 · 结构一致性", "69/100"],
      ["watching", "LightGBM Ranker", "冻结阈值 · 横截面排序", "WATCH"],
      ["blocked", "Forward Gate", "新鲜度 · 风险门禁 · 去重", "HELD"],
    ],
  },
  {
    code: "LINK",
    symbol: "LINK-USDT-SWAP",
    verdict: "观察",
    tone: "watch",
    price: "23.914",
    change: "+1.12%",
    positive: true,
    detectedAt: "12:43:58",
    yolo: "0.79",
    quality: "66",
    rank: "0.68",
    rankScore: 68,
    conclusion: "均线密集 · 启动方向未确认",
    action: "保留候选，继续跟踪两轮",
    verdictTitle: "等待确认",
    chart: chartLink,
    flow: [
      ["passed", "Pattern Teacher", "等待 latest research adapter", "0.79"],
      ["passed", "Formation Quality", "因果窗口 · 结构一致性", "66/100"],
      ["watching", "LightGBM Ranker", "冻结阈值 · 横截面排序", "WATCH"],
      ["blocked", "Forward Gate", "新鲜度 · 风险门禁 · 去重", "HELD"],
    ],
  },
  {
    code: "AVAX",
    symbol: "AVAX-USDT-SWAP",
    verdict: "拦截",
    tone: "block",
    price: "29.482",
    change: "−0.42%",
    positive: false,
    detectedAt: "12:43:36",
    yolo: "0.76",
    quality: "58",
    rank: "0.54",
    rankScore: 54,
    conclusion: "候选框偏弱 · 排序未过阈值",
    action: "作为困难负样本写入复盘队列",
    verdictTitle: "候选已拦截",
    chart: chartAvax,
    flow: [
      ["passed", "Pattern Teacher", "等待 latest research adapter", "0.76"],
      ["blocked", "Formation Quality", "因果窗口 · 结构一致性", "58/100"],
      ["blocked", "LightGBM Ranker", "冻结阈值 · 横截面排序", "BLOCK"],
      ["blocked", "Forward Gate", "新鲜度 · 风险门禁 · 去重", "HELD"],
    ],
  },
];

function formatCst(date) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function RawSvg({ markup }) {
  return <span aria-hidden="true" className="source-svg" dangerouslySetInnerHTML={{ __html: markup }} />;
}

function formatInShanghai(date, options) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hourCycle: "h23",
    ...options,
  }).format(date);
}

function replayTimestamp(value) {
  if (!value) return { compact: "—", beijing: "—", utc: "—" };
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { compact: "—", beijing: "—", utc: "—" };
  const compact = formatInShanghai(date, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  const beijing = formatInShanghai(date, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  const utc = date.toISOString().slice(0, 16).replace("T", " ");
  return { compact: `${compact} 北京`, beijing: `${beijing} 北京时间`, utc: `${utc} UTC` };
}

function formatMarketPrice(value) {
  if (!Number.isFinite(value)) return "—";
  const digits = value >= 1_000 ? 1 : value >= 100 ? 2 : value >= 1 ? 3 : 6;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function candidateView(candidate, run, phase, error) {
  if (!run) {
    const loading = phase === "loading";
    const failed = phase === "error";
    return {
      ...candidate,
      verdict: failed ? "离线" : "待跑",
      tone: failed ? "block" : "watch",
      detectedAt: "—",
      eventTime: null,
      yolo: loading ? "…" : "—",
      quality: "—",
      rank: loading ? "…" : "—",
      rankScore: 0,
      ringLabel: loading ? "…" : "WAIT",
      conclusion: failed ? "模型接口未完成回放" : loading ? "真实模型正在推理" : "选择该行运行模型回放",
      action: failed ? error || "请检查本地模型服务" : "预留模型结果；执行器保持关闭。",
      verdictTitle: failed ? "模型服务不可用" : loading ? "模型推理中" : "等待模型回放",
      modelImage: null,
      replayChart: null,
      boxes: [],
      flow: [
        [loading ? "watching" : "blocked", "Pattern Teacher", "等待实际 YOLO 输入", loading ? "RUN" : "WAIT"],
        ["passed", "Input Contract", "PRE-HOLDOUT · READ ONLY", "SAFE"],
        ["blocked", "LightGBM Ranker", "等待最新分侧 selection receipt", "WAIT"],
        ["blocked", "Execution Gate", "不写 forward_log · 不下单", "OFF"],
      ],
    };
  }

  const detector = run.detector;
  const judgment = run.judgment;
  const l1Passed = Boolean(detector.reproduced_expected_class);
  const l2Available = Boolean(judgment.available);
  const l2Passed = l2Available && judgment.passed;
  const tone = run.verdict.tone;
  const verdict = tone === "pass" ? "通过" : tone === "watch" ? "观察" : "拦截";
  const eventTime = judgment.available_at || run.generated_at;
  return {
    ...candidate,
    verdict,
    tone,
    detectedAt: replayTimestamp(eventTime).compact,
    eventTime,
    yolo: detector.max_confidence == null ? "—" : detector.max_confidence.toFixed(2),
    quality: `W${detector.input.window_bars}/${judgment.side.toUpperCase()}`,
    rank: l2Available ? judgment.score.toFixed(6) : "n/a",
    rankScore: l2Available ? Math.round(judgment.percentile * 100) : 0,
    ringLabel: l2Available ? `${Math.round(judgment.percentile * 100)}%` : "N/A",
    conclusion: `${detector.box_count} 个框 · 同一事件 ${judgment.side.toUpperCase()} 分侧回放`,
    action: run.verdict.action,
    verdictTitle: run.verdict.title,
    modelImage: run.image_url,
    replayChart: run.replay_chart,
    boxes: detector.boxes,
    run,
    flow: [
      [l1Passed ? "passed" : "blocked", "Pattern Teacher", `${detector.model_label} · exact pixels`, l1Passed ? detector.max_confidence.toFixed(2) : "NO MATCH"],
      ["passed", "Input Contract", `SAME EVENT · PRE-HOLDOUT · ${detector.input.split.toUpperCase()}`, "SAFE"],
      [l2Passed ? "passed" : l2Available ? "watching" : "blocked", "LightGBM Ranker", l2Available ? `${judgment.model_label} · ${judgment.feature_count} 特征 · q90 ${judgment.threshold.toFixed(6)}` : judgment.reason, l2Passed ? "PASS" : l2Available ? "HELD" : "N/A"],
      ["blocked", "Execution Gate", "active_bundle 未触碰 · forward 不写入", "OFF"],
    ],
  };
}

export function App() {
  const [selectedCode, setSelectedCode] = useState("ETH");
  const [chartMode, setChartMode] = useState("live");
  const [liveMarket, setLiveMarket] = useState({ status: "loading" });
  const [running, setRunning] = useState(true);
  const [now, setNow] = useState(() => new Date());
  const [scanCycle, setScanCycle] = useState(1844);
  const [modelStatus, setModelStatus] = useState(null);
  const [modelStatusError, setModelStatusError] = useState("");
  const [modelRuns, setModelRuns] = useState({});
  const [modelPhases, setModelPhases] = useState({});
  const [modelErrors, setModelErrors] = useState({});

  const selectedBase = useMemo(
    () => CANDIDATES.find((candidate) => candidate.code === selectedCode) ?? CANDIDATES[0],
    [selectedCode],
  );
  const selected = useMemo(
    () => candidateView(selectedBase, modelRuns[selectedCode], modelPhases[selectedCode], modelErrors[selectedCode]),
    [modelErrors, modelPhases, modelRuns, selectedBase, selectedCode],
  );
  const tableCandidates = useMemo(
    () => CANDIDATES.map((candidate) => candidateView(candidate, modelRuns[candidate.code], modelPhases[candidate.code], modelErrors[candidate.code])),
    [modelErrors, modelPhases, modelRuns],
  );
  const selectedRunReady = Boolean(modelRuns[selectedCode]);
  const selectedReplayTime = useMemo(() => replayTimestamp(selected.eventTime), [selected.eventTime]);
  const selectedLiveMarket = liveMarket.symbol === selected.symbol
    ? liveMarket
    : { status: "loading", lastPrice: null, change: null, candleTime: null, receivedAt: null };

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => setScanCycle((cycle) => cycle + 1), 12000);
    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => {
    let current = true;
    getModelStatus()
      .then(({ adapter }) => {
        if (current) setModelStatus(adapter);
      })
      .catch((error) => {
        if (current) setModelStatusError(error.message);
      });
    return () => { current = false; };
  }, []);

  useEffect(() => {
    if (!running || !modelStatus || selectedRunReady) return undefined;
    let current = true;
    setModelPhases((phases) => ({ ...phases, [selectedCode]: "loading" }));
    runModelInference(selectedBase.symbol)
      .then(({ result }) => {
        if (!current) return;
        setModelRuns((runs) => ({ ...runs, [selectedCode]: result }));
        setModelPhases((phases) => ({ ...phases, [selectedCode]: "complete" }));
      })
      .catch((error) => {
        if (!current) return;
        setModelErrors((errors) => ({ ...errors, [selectedCode]: error.message }));
        setModelPhases((phases) => ({ ...phases, [selectedCode]: "error" }));
      });
    return () => { current = false; };
  }, [modelStatus, running, selectedBase.symbol, selectedCode, selectedRunReady]);

  const events = useMemo(() => {
    const run = modelRuns[selectedCode];
    const nowText = formatCst(now);
    return [
      run
        ? [nowText, `${selectedCode} 模型回放完成`, `L1 ${run.detector.box_count} 框 · L2 ${run.judgment.available ? (run.judgment.passed ? "PASS" : "HELD") : "N/A"} · ${run.duration_ms}ms`, "hot"]
        : [nowText, `${selectedCode} 等待模型结果`, modelPhases[selectedCode] === "loading" ? "正在加载 L1 与 L2" : "选择候选后自动运行", ""],
      ["MARKET", selectedLiveMarket.status === "live" ? "OKX 15m 实时流已连接" : "OKX 15m 行情同步", "仅展示 · 不送入模型 · 不写 K 线缓存", selectedLiveMarket.status === "live" ? "hot" : ""],
      ["MODEL", modelStatus ? "最新研究模型适配器已加载" : "连接模型适配器", modelStatus ? `${modelStatus.l1.label} · L2 分侧 · 可替换` : modelStatusError || "正在读取模型身份", modelStatus ? "hot" : ""],
      ["GATE", "L2 实验结论保持 REJECT", "仅做服务烟测 · production_eligible=false", ""],
      ["DATA", "Holdout 门保持关闭", "仅用冻结 pre-holdout replay · 本轮读取 0 行 holdout", ""],
      ["EXEC", "执行器保持关闭", "不写 forward_log · 不触碰 active_bundle", "quiet"],
    ];
  }, [modelPhases, modelRuns, modelStatus, modelStatusError, now, selectedCode, selectedLiveMarket.status]);

  return (
    <main className="site-shell">
      <aside className="brand-rail" aria-label="Fable 系统标识">
        <div className="brand-mark">F</div>
        <div className="rail-line" />
        <span className="rail-copy">PATTERN INTELLIGENCE / LATEST</span>
        <div className="rail-state"><span /> {modelStatus ? "MODEL LINKED" : "MODEL CONNECTING"}</div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="title-lockup">
            <div><p className="eyebrow">FABLE TRADING / SIGNAL OPERATIONS</p><h1>形态信号雷达</h1></div>
            <span className="demo-badge">LIVE CHART / 研究回放</span>
          </div>
          <div className="market-status">
            <div className="market-meta"><span>LOCAL · LIVE VIEW + PRE-HOLDOUT REPLAY · 15m</span><strong>{formatCst(now)} 北京时间</strong></div>
            <button className="scan-button" type="button" aria-pressed={!running} onClick={() => setRunning((value) => !value)}>
              <span className={`live-dot${running ? "" : " paused"}`} />
              {!running ? "继续推理" : modelPhases[selectedCode] === "loading" ? "模型推理中" : modelStatus ? "模型已接入" : "连接模型中"}
            </button>
          </div>
        </header>

        <section className="status-strip" aria-label="系统概览">
          <div className="status-lead">
            <span className="status-kicker">REPLAY CYCLE</span><strong>#{scanCycle}</strong>
            <span className="scan-progress"><i className={running ? "active" : ""} /></span>
          </div>
          <div className="metric"><span>回放样本</span><strong>1</strong><small>当前候选</small></div>
          <div className="metric"><span>YOLO 检出</span><strong>{selected.run?.detector.box_count ?? "—"}</strong><small>实际框</small></div>
          <div className="metric"><span>判断通过</span><strong className="lime">{selected.run?.judgment.available ? (selected.run.judgment.passed ? 1 : 0) : "—"}</strong><small>冻结阈值</small></div>
          <div className="metric"><span>数据边界</span><strong>SAFE</strong><small>PRE-HOLDOUT</small></div>
          <div className="metric executor"><span>OPS EXECUTOR</span><strong>OFF</strong><small>只读模式</small></div>
        </section>

        <section className="primary-grid">
          <article className="chart-panel panel">
            <div className="panel-heading chart-heading">
              <div>
                <div className="symbol-line">
                  <span className="asset-chip">{selected.code}</span><h2>{selected.symbol}</h2>
                  <span className={`state-pill state-${selected.verdict}`} data-tone={selected.tone}>{selected.verdict} · 历史裁决</span>
                </div>
                <p>{chartMode === "live" ? "OKX PUBLIC · 15m LIVE · DISPLAY ONLY" : selected.run ? "EXACT YOLO INPUT · SAME-EVENT PRE-HOLDOUT REPLAY" : "15m · 等待最新模型输入"}</p>
              </div>
              <div className="chart-heading-actions">
                <div className="chart-mode-switch" aria-label="图表模式">
                  <button type="button" data-active={chartMode === "live"} onClick={() => setChartMode("live")}>实时行情</button>
                  <button type="button" data-active={chartMode === "replay"} onClick={() => setChartMode("replay")}>模型回放</button>
                </div>
                <div className="quote">
                  <strong>{chartMode === "live" ? formatMarketPrice(selectedLiveMarket.lastPrice) : selected.run ? `L1 ${selected.yolo}` : selected.price}</strong>
                  {chartMode === "live" ? (
                    <span className={(selectedLiveMarket.change ?? 0) >= 0 ? "positive" : "negative"}>
                      {Number.isFinite(selectedLiveMarket.change) ? `${selectedLiveMarket.change >= 0 ? "+" : ""}${selectedLiveMarket.change.toFixed(2)}%` : selectedLiveMarket.status === "error" ? "OFFLINE" : "CONNECTING"}
                    </span>
                  ) : (
                    <span className={selected.run ? "positive model-score" : selected.positive ? "positive" : "negative"}>{selected.run ? `L2 ${selected.rank}` : <><RawSvg markup={trendIcon} />{selected.change}</>}</span>
                  )}
                </div>
              </div>
            </div>
            <div className="chart-legend" aria-label="均线图例">
              {MARKET_MA_SERIES.map((definition) => (
                <span key={definition.key}><i style={{ background: definition.color }} /> {definition.label}</span>
              ))}
              {chartMode === "live" ? <span className="legend-box live-separation"><i /> 实时行情未送入模型</span> : <span className="legend-box"><i /> YOLO 候选区域</span>}
            </div>
            {chartMode === "live" ? (
              <LiveMarketChart key={selected.symbol} symbol={selected.symbol} onMetaChange={setLiveMarket} />
            ) : selected.replayChart ? (
              <ReplayMarketChart key={selected.run.detector.input.episode_id} symbol={selected.symbol} replay={selected.replayChart} />
            ) : selected.modelImage ? (
              <div className="chart-wrap model-input-frame" aria-label={`${selected.symbol} 实际 YOLO 回放输入`}>
                <img src={selected.modelImage} alt={`${selected.symbol} 冻结 pre-holdout 模型输入`} />
                {selected.boxes.map((box, index) => {
                  const [cx, cy, width, height] = box.xywhn;
                  return <span className="model-box" key={`${cx}-${cy}-${index}`} style={{ left: `${(cx - width / 2) * 100}%`, top: `${(cy - height / 2) * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }}><b>{box.confidence.toFixed(2)}</b></span>;
                })}
                <span className="model-input-badge">EXACT YOLO INPUT · {selected.run.detector.input.pixel_sha256.slice(0, 8)}</span>
              </div>
            ) : (
              <div className="chart-wrap" aria-label={`${selected.symbol} 15 分钟 K 线与均线密集检测框`} dangerouslySetInnerHTML={{ __html: selected.chart }} />
            )}
            {chartMode === "replay" && selected.modelImage && selected.run ? (
              <details className="exact-input-proof">
                <summary>查看模型实际像素输入 · SHA {selected.run.detector.input.pixel_sha256.slice(0, 8)}</summary>
                <div><img src={selected.modelImage} alt={`${selected.symbol} 经哈希验证的原始 YOLO 输入`} /></div>
              </details>
            ) : null}
            {chartMode === "live" ? (
              <div className="chart-caption live-caption">
                <div><span>行情最新推送</span><strong>{selectedLiveMarket.receivedAt ? `${formatCst(new Date(selectedLiveMarket.receivedAt * 1000))} 北京时间` : "连接中"}</strong><small>{selectedLiveMarket.candleTime ? `当前 15m K 线始于 ${formatCst(new Date(selectedLiveMarket.candleTime * 1000))}` : ""}</small></div>
                <div><span>模型状态</span><strong>未对实时行情运行 L1/L2</strong></div>
                <div className="caption-note"><span>数据动作</span><strong>只读展示；不落盘、不进 forward、不进执行器</strong></div>
              </div>
            ) : (
              <div className="chart-caption replay-caption">
                <div title={selectedReplayTime.utc}><span>回放事件时间</span><strong>{selectedReplayTime.beijing}</strong><small>{selectedReplayTime.utc} · 不是当前时间</small></div>
                <div><span>形态结论</span><strong>{selected.conclusion}</strong></div>
                <div className="caption-note"><span>系统动作</span><strong>{selected.action}</strong></div>
              </div>
            )}
          </article>

          <aside className="decision-panel panel">
            <div className="panel-heading"><div><p className="section-index">02 / DECISION TRACE</p><h2>历史回放判断链</h2></div><span className="read-only">REPLAY ONLY</span></div>
            <div className="decision-score">
              <div><span>最终排序分</span><strong>{selected.rank}</strong></div>
              <div className="score-ring" style={{ "--score": `${selected.rankScore * 3.6}deg` }}><span className="ring-label">{selected.ringLabel ?? selected.rankScore}</span></div>
            </div>
            <ol className="decision-flow">
              {selected.flow.map(([state, title, detail, value], index) => (
                <li className={state} key={title}>
                  <span className="flow-node">{String(index + 1).padStart(2, "0")}</span>
                  <div><strong>{title}</strong><p>{detail}</p></div><b>{value}</b>
                </li>
              ))}
            </ol>
            <div className={`verdict verdict-${selected.verdict}`} data-tone={selected.tone}>
              <span>当前裁决</span><strong>{selected.verdictTitle}</strong><p>{selected.action}</p>
            </div>
          </aside>
        </section>

        <section className="lower-grid">
          <article className="signals-panel panel">
            <div className="panel-heading compact">
              <div><p className="section-index">01 / REPLAY CANDIDATES</p><h2>模型回放候选</h2></div>
              <span className="table-hint">选择一行查看判断链</span>
            </div>
            <div className="relative w-full overflow-x-auto">
              <table className="signal-table">
                <thead><tr><th>合约</th><th>回放事件时间（北京）</th><th>YOLO</th><th>质量</th><th>排序分</th><th>裁决</th></tr></thead>
                <tbody>
                  {tableCandidates.map((candidate) => (
                    <tr key={candidate.code} data-state={candidate.code === selectedCode ? "selected" : undefined}>
                      <td><button className="symbol-button" type="button" onClick={() => setSelectedCode(candidate.code)}><span>{candidate.code}</span><strong>{candidate.symbol}</strong></button></td>
                      <td className="mono muted-cell" title={replayTimestamp(candidate.eventTime).utc}>{candidate.detectedAt}</td>
                      <td className="mono">{candidate.yolo}</td><td className="mono">{candidate.quality}</td>
                      <td className="mono score-cell">{candidate.rank}</td>
                      <td><span className={`state-pill state-${candidate.verdict}`} data-tone={candidate.tone}>{candidate.verdict}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>

          <article className="log-panel panel">
            <div className="panel-heading compact"><div><p className="section-index">03 / SERVING LOG</p><h2>服务事件流</h2></div><RawSvg markup={activityIcon} /></div>
            <div className="event-list">
              {events.map(([time, title, detail, state]) => (
                <div className={`event-item ${state}`.trim()} key={time}>
                  <time>{time}</time><span className="event-dot" /><div><strong>{title}</strong><p>{detail}</p></div>
                </div>
              ))}
            </div>
          </article>
        </section>

        <footer className="footer-note">
          <span>LIVE MARKET VIEW / HISTORICAL MODEL REPLAY</span>
          <p>实时 OKX 行情与历史模型回放物理分离：行情只展示，不送入 L1/L2；回放继续使用同一 pre-holdout 事件。L2 结论仍为 REJECT。</p>
          <span>FABLE / 15M</span>
        </footer>
      </div>
    </main>
  );
}
