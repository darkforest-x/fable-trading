/* Data-backed platform map and model registry for the local research workspace. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const finite = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const count = (value) => finite(value) ? Number(value).toLocaleString("zh-CN") : "—";
  const arr = (value) => Array.isArray(value) ? value : [];
  const display = (value) => {
    if (value === null || value === undefined || value === "") return "未记录";
    if (Array.isArray(value)) return value.length ? value.map(display).join("、") : "未记录";
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  };
  const textBlock = (value) => {
    if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return `<span class="platform-muted">未记录</span>`;
    if (typeof value === "object") return `<pre class="platform-pre">${esc(display(value))}</pre>`;
    return `<span>${esc(String(value))}</span>`;
  };
  const safeExternal = (value) => {
    try {
      const url = new URL(String(value || ""));
      return url.protocol === "https:" && !url.username && !url.password ? url.href : "";
    } catch { return ""; }
  };
  const routeViews = new Set(["platform", "models", "datasets", "factors", "strategies", "paper", "experiments", "backtests", "yolo", "vision", "signals", "joints", "breaks", "system"]);
  const routeNames = { platform: "体系总览", models: "模型中心", datasets: "数据集", factors: "因子库", strategies: "策略库", paper: "模拟实盘", experiments: "实验登记", backtests: "回测任务", yolo: "YOLO 工作流", vision: "VLM 工作流", signals: "信号中心", joints: "突破+spike", breaks: "趋势线突破", system: "运行状态" };
  const summaryNames = { datasets: "数据集", factors: "因子", models: "模型", strategies: "策略", experiments: "实验", backtest_runs: "回测运行", paper_runs: "模拟运行" };
  const statusNames = { research: "研究中", hypothesis: "待验证", implemented: "已实现", active: "登记中", ready: "已登记", review: "待复核", rejected: "已否定", archived: "已归档", missing: "缺失", verified: "身份校验通过", inspected: "已检查", review_required: "需要复核", incomplete: "信息不完整", stale: "校验记录过期", pass: "通过", mismatch: "不匹配", unknown: "未知", not_rehashed: "未重新计算哈希", declared_only: "仅登记声明", computed: "本次计算", disabled: "未执行", failed: "校验失败" };
  const stageNames = { research: "研究中", rejected: "已否定", archived: "已归档" };
  const platformState = { active: false, loaded: false, loading: false, error: "", data: null };
  const modelState = { active: false, loaded: false, loading: false, error: "", items: [], families: [], gates: {}, family: "all", query: "", saving: new Set(), auditing: new Set(), auditErrors: {} };
  const pipelineState = { loaded: false, loading: false, saving: false, running: false, runError: "", runSymbols: "BTCUSDT", error: "", items: [], templates: [], options: {}, selectedId: "", draft: null };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store", headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) }, ...options,
    });
    let body = null;
    const type = response.headers?.get?.("content-type") || "";
    if (type.includes("json")) body = await response.json();
    if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `服务返回 HTTP ${response.status}`);
    return body;
  }

  function listValue(value) {
    if (Array.isArray(value)) return value.length ? value.map((item) => `<li>${esc(display(item))}</li>`).join("") : "<li>未记录</li>";
    return `<li>${esc(display(value))}</li>`;
  }
  function safeViewLink(view) {
    if (!routeViews.has(String(view || ""))) return "";
    const label = routeNames[view] || view;
    return `<a class="research-button" href="#${esc(view)}">${esc(label)} ↗</a>`;
  }
  function componentMarkup(component) {
    const view = safeViewLink(component.view);
    return `<article class="platform-component"><div class="platform-component-heading"><strong>${esc(component.name || component.id || "未命名模块")}</strong><span class="research-badge">${esc(display(component.kind))}</span></div><p>${esc(display(component.description))}</p><div class="platform-component-meta"><span>${esc(statusNames[component.status] || display(component.status))}</span>${component.source_path ? `<code>${esc(component.source_path)}</code>` : ""}</div>${view}</article>`;
  }
  const layerCountUnits = { data: "数据集", dataset: "数据集", datasets: "数据集", features: "因子", feature: "因子", factors: "因子", labels: "标签", feature_label: "因子 / 标签", feature_labels: "因子 / 标签", model: "模型", models: "模型", strategy: "策略", strategies: "策略", evaluation: "回测任务", backtest: "回测任务", backtests: "回测任务", forward: "模拟运行", paper: "模拟运行", runtime: "模拟运行" };
  function layerMarkup(layer, index) {
    const components = arr(layer.components);
    const reportedCount = finite(layer.count) ? layer.count : components.length;
    const countUnit = layerCountUnits[String(layer.id || "").toLowerCase()] || "条登记";
    return `<article class="platform-layer"><div class="platform-layer-top"><span class="platform-layer-index">${String(index + 1).padStart(2, "0")}</span><span class="research-badge">${esc(layer.id || "layer")}</span><span class="platform-layer-count">${count(reportedCount)} ${countUnit}</span></div><h3>${esc(layer.name || "未命名层")}</h3><p>${esc(display(layer.description))}</p><div class="platform-io"><div><strong>输入</strong>${textBlock(layer.inputs)}</div><div><strong>输出</strong>${textBlock(layer.outputs)}</div></div><div class="platform-components">${components.length ? components.map(componentMarkup).join("") : `<p class="platform-muted">此层尚未登记组件明细。</p>`}</div></article>`;
  }
  function summaryMarkup(summary) {
    return Object.entries(summaryNames).map(([key, label]) => `<article><span>${label}</span><strong>${count(summary?.[key])}</strong></article>`).join("");
  }
  function pipelineMarkup(item) {
    if (typeof item === "string") return `<article class="platform-pipeline"><p>${esc(item)}</p></article>`;
    if (!item || typeof item !== "object") return "";
    const steps = arr(item.steps || item.stages || item.layers);
    return `<article class="platform-pipeline"><div><h3>${esc(item.name || item.label || item.title || item.id || "研究流程")}</h3>${item.status ? `<span class="research-badge">${esc(statusNames[item.status] || item.status)}</span>` : ""}</div>${item.description ? `<p>${esc(item.description)}</p>` : ""}${steps.length ? `<ol>${steps.map((step) => `<li>${esc(typeof step === "object" ? step.name || step.label || display(step) : step)}</li>`).join("")}</ol>` : ""}${item.source_path ? `<code>${esc(item.source_path)}</code>` : ""}</article>`;
  }
  function renderPlatform() {
    const root = $("platform-workspace");
    if (!root) return;
    if (platformState.error) {
      root.innerHTML = `<div class="research-error" role="alert">${esc(platformState.error)} <button type="button" class="research-button" data-platform-refresh>重试</button></div><section id="platform-pipelines" class="platform-pipeline-registry">${pipelineRegistryHTML()}</section>`;
      bindPlatformEvents(root);
      return;
    }
    if (!platformState.loaded) {
      root.innerHTML = `<p class="research-empty">${platformState.loading ? "正在读取体系登记…" : "体系登记尚未读取。"}</p><section id="platform-pipelines" class="platform-pipeline-registry">${pipelineRegistryHTML()}</section>`;
      bindPlatformEvents(root);
      return;
    }
    const data = platformState.data || {};
    const layers = arr(data.layers);
    const sources = arr(data.sources).map((source) => {
      const url = safeExternal(source?.url);
      return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(source.name || "来源")} ↗</a>` : esc(source?.name || "来源")}${source?.url && !url ? `<small class="platform-muted"> · 链接未使用（仅接受 HTTPS）</small>` : ""}</li>`;
    }).join("");
    const crossCutting = arr(data.cross_cutting);
    const pipelines = arr(data.pipelines);
    root.innerHTML = `<div class="platform-hero research-objective"><span class="research-kicker">SPIKE · 研究与运行</span><h2>共用数据、特征和模型，按策略组织回测与前向验证</h2><p>下面展示体系组件与各层资产登记。架构关系不代表模型已经准确或策略已经通过。</p></div>
      <div class="platform-summary">${summaryMarkup(data.summary || {})}</div>
      <div class="platform-section-heading"><div><h2>研究与运行链路</h2><p>数据 → 特征 / 标签 → 模型 → 策略 → 评估 / 回测 → 前向运行</p></div><button type="button" class="research-button" data-platform-refresh${platformState.loading ? " disabled" : ""}>${platformState.loading ? "正在刷新…" : "刷新体系"}</button></div>
      ${layers.length ? `<div class="platform-layer-grid">${layers.map(layerMarkup).join("")}</div>` : `<p class="research-empty">体系接口尚未登记架构层。</p>`}
      <div class="platform-lower-grid"><section class="platform-support"><div class="platform-section-heading"><div><h2>跨层约束</h2><p>贯穿各层的共同约束。</p></div></div>${crossCutting.length ? `<ul>${crossCutting.map((item) => `<li>${esc(display(item))}</li>`).join("")}</ul>` : `<p class="platform-muted">暂无跨层约束登记。</p>`}</section>
      <section class="platform-support"><div class="platform-section-heading"><div><h2>登记流程</h2><p>仅展示已登记流程，不在此页运行任务。</p></div></div>${pipelines.length ? `<div class="platform-pipeline-list">${pipelines.map(pipelineMarkup).join("")}</div>` : `<p class="platform-muted">流程信息尚未提供。</p>`}</section></div>
      <div class="platform-provenance"><strong>体系来源</strong>${sources ? `<ul>${sources}</ul>` : `<span class="platform-muted">未登记外部来源。</span>`}</div>
      <section id="platform-pipelines" class="platform-pipeline-registry">${pipelineRegistryHTML()}</section>`;
    bindPlatformEvents(root);
  }
  async function refreshPlatform() {
    if (platformState.loading) return;
    platformState.loading = true;
    pipelineState.loading = true;
    platformState.error = "";
    pipelineState.error = "";
    renderPlatform();
    try {
      const [platformResult, pipelineResult] = await Promise.allSettled([api("/api/research/platform"), api("/api/research/pipelines")]);
      if (platformResult.status === "fulfilled") {
        const data = platformResult.value;
        if (!data || typeof data !== "object" || !Array.isArray(data.layers)) platformState.error = "体系登记数据格式有误。";
        else { platformState.data = data; platformState.loaded = true; }
      } else platformState.error = platformResult.reason?.message || "读取体系登记失败。";
      if (pipelineResult.status === "fulfilled") {
        const data = pipelineResult.value;
        if (!data || !Array.isArray(data.items)) pipelineState.error = "研究流程登记数据格式有误。";
        else {
          pipelineState.items = data.items;
          pipelineState.templates = arr(data.templates);
          pipelineState.options = data.options || {};
          pipelineState.loaded = true;
        }
      } else pipelineState.error = pipelineResult.reason?.message || "读取研究流程登记失败。";
    } finally {
      platformState.loading = false;
      pipelineState.loading = false;
      renderPlatform();
    }
  }

  const routeLabels = { rules: "规则路线", yolo_lgbm: "YOLO + LightGBM", vlm: "VLM 形态路线" };
  function selectedPipeline() { return pipelineState.items.find((item) => String(item.id) === String(pipelineState.selectedId)); }
  function multiSelect(name, label, options, selectedValues) {
    const selected = new Set(arr(selectedValues).map(String));
    return `<label>${label}<select name="${name}" multiple size="${Math.min(5, Math.max(2, arr(options).length))}">${arr(options).map((item) => `<option value="${esc(item.id)}"${selected.has(String(item.id)) ? " selected" : ""}>${esc(item.name || item.id)}${item.family ? ` · ${esc(item.family)}` : ""}</option>`).join("")}</select><small>可多选；关联只建立研究登记，不会自动运行。</small></label>`;
  }
  function singleSelect(name, label, options, selectedValue) {
    return `<label>${label}<select name="${name}"><option value="">未关联</option>${arr(options).map((item) => `<option value="${esc(item.id)}"${String(item.id) === String(selectedValue || "") ? " selected" : ""}>${esc(item.name || item.id)}</option>`).join("")}</select></label>`;
  }
  function pipelineValidationHTML(validation) {
    if (!validation || typeof validation !== "object") return `<p class="platform-muted">路线验证尚未返回。研究计划仍可保存。</p>`;
    const status = validation.status === "blocked" ? "路线受阻" : validation.status === "ready" ? "路线无阻塞项" : "状态未确认";
    const blockers = arr(validation.blockers);
    return `<strong class="pipeline-validation-status">${esc(status)}</strong>${blockers.length ? `<ul>${blockers.map((blocker) => `<li>${esc(display(blocker))}</li>`).join("")}</ul>` : validation.status === "blocked" ? `<p>路线标记为受阻，但没有列出原因。</p>` : ""}<div class="pipeline-mode-gates">${["backtest", "paper"].map((mode) => {
      const gate = validation[mode];
      const label = mode === "backtest" ? "回测" : "模拟";
      const gateStatus = gate?.allowed === true ? "已接通" : gate?.allowed === false ? "尚未接通" : "状态未知";
      return `<p><span>${label}：</span>${gateStatus}${gate?.reason ? ` · ${esc(gate.reason)}` : ""}</p>`;
    }).join("")}</div><div class="model-research-links"><a class="research-button" href="#backtests">进入回测任务 ↗</a><a class="research-button" href="#paper">进入模拟实盘 ↗</a></div>`;
  }
  function pipelineRunFormHTML(item) {
    const gates = item.validation || {};
    const modes = [["backtest", "回测", gates.backtest], ["paper", "模拟", gates.paper]].filter(([, , gate]) => gate?.allowed === true);
    if (!modes.length) return `<div class="pipeline-run"><strong>运行受限</strong><p>当前验证门没有允许的运行方式。计划仍可保存、关联和归档。</p><a class="research-button" href="#backtests">回测任务 ↗</a><a class="research-button" href="#paper">模拟实盘 ↗</a></div>`;
    const firstMode = modes[0][0];
    return `<div class="pipeline-run"><h4>从此流程启动</h4><p>只有已接通的方式会出现在选择框；运行由你点击触发。</p>${pipelineState.runError ? `<div class="research-error" role="alert">${esc(pipelineState.runError)}</div>` : ""}<form data-pipeline-run-form data-pipeline-id="${esc(item.id)}" data-revision="${esc(item.revision ?? "")}"><label>运行方式<select name="mode">${modes.map(([id, label]) => `<option value="${id}">${label}</option>`).join("")}</select></label><label>合约 <small>逗号分隔；回测示例 BTCUSDT，模拟示例 BTC-USDT-SWAP</small><input name="symbols" value="${esc(pipelineState.runSymbols)}" required></label><div class="pipeline-backtest-timeframes"${firstMode === "backtest" ? "" : " hidden"}><strong>回测固定周期</strong><span>15m + 1H（由冻结回测引擎决定）</span></div><label class="pipeline-paper-timeframes"${firstMode === "paper" ? "" : " hidden"}>模拟观察周期<select name="timeframes" multiple size="4">${["15m", "30m", "1H", "4H"].map((timeframe) => `<option value="${timeframe}"${timeframe === "15m" ? " selected" : ""}>${timeframe}</option>`).join("")}</select><small>可多选。</small></label><button class="research-button primary" type="submit"${pipelineState.running ? " disabled" : ""}>${pipelineState.running ? "正在提交…" : modes.length === 1 ? `启动${modes[0][1]}` : "启动选定运行"}</button></form></div>`;
  }
  function pipelineRegistryHTML() {
    const pipelines = pipelineState.items;
    const selected = selectedPipeline();
    const draft = selected || pipelineState.draft || {};
    const options = pipelineState.options || {};
    const pipelineButtons = pipelines.length ? `<div class="pipeline-record-list">${pipelines.map((item) => `<button class="pipeline-record${String(item.id) === String(pipelineState.selectedId) ? " selected" : ""}" type="button" data-pipeline-select="${esc(item.id)}"><span><strong>${esc(item.name || item.id)}</strong><small>${esc(routeLabels[item.route] || item.route || "未记录路线")} · v${esc(item.revision ?? "?")}</small></span><span class="research-badge">${esc(stageNames[item.stage] || item.stage || "未记录")}</span></button>`).join("")}</div>` : `<p class="platform-muted">尚无已登记流程。</p>`;
    const loadState = pipelineState.loading && !pipelineState.loaded ? `<p class="research-empty">正在读取研究流程…</p>` : pipelineState.error ? `<div class="research-error" role="alert">${esc(pipelineState.error)} <button type="button" class="research-button" data-pipeline-refresh>重试流程目录</button></div>` : "";
    const templates = pipelineState.templates.length ? `<div class="pipeline-templates"><h3>路线模板</h3>${pipelineState.templates.map((template) => {
      const route = routeLabels[template.route] ? template.route : routeLabels[template.id] ? template.id : "";
      return `<article><strong>${esc(template.name || template.id)}</strong><p>${esc(template.description || "未提供说明")}</p>${route ? `<button type="button" class="research-button" data-pipeline-template="${esc(template.id)}">套用到计划</button>` : ""}</article>`;
    }).join("")}</div>` : "";
    const form = pipelineState.loaded ? `<div class="pipeline-edit-stack"><form class="pipeline-form" data-pipeline-form data-pipeline-id="${esc(selected?.id || "")}" data-revision="${esc(selected?.revision ?? "")}"><div class="pipeline-form-heading"><h3>${selected ? `编辑：${esc(selected.name || selected.id)}` : "创建研究流程计划"}</h3><button class="research-button" type="button" data-pipeline-new>新建计划</button></div><label>流程名称<input name="name" required maxlength="120" value="${esc(draft.name || "")}" placeholder="例如：突破形态验证流程"></label><label>路线<select name="route">${Object.entries(routeLabels).map(([route, label]) => `<option value="${route}"${(draft.route || "rules") === route ? " selected" : ""}>${label}</option>`).join("")}</select></label><div class="pipeline-ref-grid">${multiSelect("dataset_ids", "数据集", options.datasets, draft.dataset_ids)}${multiSelect("factor_ids", "因子", options.factors, draft.factor_ids)}${multiSelect("model_ids", "模型", options.models, draft.model_ids)}</div><div class="pipeline-ref-grid">${singleSelect("strategy_id", "策略", options.strategies, draft.strategy_id)}${singleSelect("experiment_id", "关联实验", options.experiments, draft.experiment_id)}<label>登记阶段<select name="stage"><option value="research"${(draft.stage || "research") === "research" ? " selected" : ""}>研究中</option><option value="archived"${draft.stage === "archived" ? " selected" : ""}>已归档</option></select></label></div><label>流程备注<textarea name="notes" rows="3" maxlength="12000">${esc(draft.notes || "")}</textarea></label>${selected ? `<div class="pipeline-validation"><div><h4>路线与运行检查</h4><p>阻塞状态不妨碍保存研究计划；可用方式需由接口明确允许并再次点击确认。</p></div>${pipelineValidationHTML(selected.validation)}</div>` : `<p class="research-caption">保存后由服务校验路线的适配器与资格；当前不会自动运行。</p>`}<div class="pipeline-save-row"><span class="research-caption">关联数据、因子、模型、策略和实验，仅用于版本化研究计划。</span><button class="research-button primary" type="submit"${pipelineState.saving ? " disabled" : ""}>${pipelineState.saving ? "正在保存…" : selected ? "保存版本" : "保存计划"}</button></div></form>${selected ? pipelineRunFormHTML(selected) : ""}</div>` : "";
    return `<div class="platform-section-heading"><div><h2>版本化研究流程</h2><p>计划可保存，即使路线因缺少适配器而受阻；明确允许的方式可从此处单独启动。</p></div><button type="button" class="research-button" data-pipeline-refresh${pipelineState.loading ? " disabled" : ""}>${pipelineState.loading ? "正在刷新…" : "刷新流程"}</button></div>${loadState}${templates}<div class="pipeline-registry-grid"><div><h3>已登记流程</h3>${pipelineButtons}</div><div>${form}</div></div>`;
  }
  function renderPipelineRegistry() {
    const root = $("platform-pipelines");
    if (root) root.innerHTML = pipelineRegistryHTML();
  }
  async function refreshPipelines() {
    if (pipelineState.loading) return;
    pipelineState.loading = true;
    pipelineState.error = "";
    renderPipelineRegistry();
    try {
      const data = await api("/api/research/pipelines");
      if (!data || !Array.isArray(data.items)) throw new Error("研究流程登记数据格式有误。");
      pipelineState.items = data.items;
      pipelineState.templates = arr(data.templates);
      pipelineState.options = data.options || {};
      pipelineState.loaded = true;
      if (!selectedPipeline()) pipelineState.selectedId = "";
    } catch (error) {
      pipelineState.error = error.message || "读取研究流程登记失败。";
    } finally {
      pipelineState.loading = false;
      renderPipelineRegistry();
    }
  }
  function bindPlatformEvents(root) {
    if (root.dataset.bound) return;
    root.dataset.bound = "true";
    root.addEventListener("click", (event) => {
      if (event.target.closest?.("[data-platform-refresh]")) { refreshPlatform(); return; }
      if (event.target.closest?.("[data-pipeline-refresh]")) { refreshPipelines(); return; }
      if (event.target.closest?.("[data-pipeline-new]")) { pipelineState.selectedId = ""; pipelineState.draft = null; renderPipelineRegistry(); return; }
      const template = event.target.closest?.("[data-pipeline-template]");
      if (template) {
        const record = pipelineState.templates.find((item) => item.id === template.dataset.pipelineTemplate);
        const route = routeLabels[record?.route] ? record.route : routeLabels[record?.id] ? record.id : "";
        pipelineState.selectedId = "";
        pipelineState.draft = record && route ? { name: record.name || "", route, notes: record.description || "", stage: "research" } : null;
        renderPipelineRegistry();
        return;
      }
      const select = event.target.closest?.("[data-pipeline-select]");
      if (select) { pipelineState.selectedId = select.dataset.pipelineSelect; pipelineState.draft = null; pipelineState.runError = ""; renderPipelineRegistry(); }
    });
    root.addEventListener("submit", (event) => {
      const runForm = event.target.closest?.("form[data-pipeline-run-form]");
      if (runForm) {
        event.preventDefault();
        runPipeline(runForm);
        return;
      }
      const form = event.target.closest?.("form[data-pipeline-form]");
      if (!form) return;
      event.preventDefault();
      savePipeline(form);
    });
    root.addEventListener("change", (event) => {
      if (event.target.name !== "mode" || !event.target.closest?.("form[data-pipeline-run-form]")) return;
      const form = event.target.closest("form[data-pipeline-run-form]");
      const backtest = form.querySelector(".pipeline-backtest-timeframes");
      const paper = form.querySelector(".pipeline-paper-timeframes");
      if (backtest) backtest.hidden = event.target.value !== "backtest";
      if (paper) paper.hidden = event.target.value !== "paper";
    });
  }
  async function savePipeline(form) {
    if (pipelineState.saving) return;
    const values = new FormData(form);
    const id = form.dataset.pipelineId;
    const payload = { name: String(values.get("name") || "").trim(), route: values.get("route"),
      dataset_ids: values.getAll("dataset_ids"), factor_ids: values.getAll("factor_ids"), model_ids: values.getAll("model_ids"),
      strategy_id: values.get("strategy_id") || "", experiment_id: values.get("experiment_id") || "", notes: values.get("notes") || "", stage: values.get("stage") || "research" };
    if (id) payload.expected_revision = finite(form.dataset.revision) ? Number(form.dataset.revision) : form.dataset.revision;
    pipelineState.saving = true;
    renderPipelineRegistry();
    try {
      const saved = await api(id ? `/api/research/pipelines/${encodeURIComponent(id)}` : "/api/research/pipelines", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
      const item = saved?.item || saved;
      if (item?.id) pipelineState.selectedId = String(item.id);
      pipelineState.draft = null;
      await refreshPipelines();
    } catch (error) {
      pipelineState.error = error.message || "保存研究流程失败。";
      renderPipelineRegistry();
    } finally {
      pipelineState.saving = false;
      renderPipelineRegistry();
    }
  }
  function requestId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `pipeline-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  async function runPipeline(form) {
    if (pipelineState.running) return;
    const values = new FormData(form);
    const id = form.dataset.pipelineId;
    const mode = String(values.get("mode") || "");
    const symbols = [...new Set(String(values.get("symbols") || "").split(/[\s,，]+/).map((symbol) => symbol.trim().toUpperCase()).filter(Boolean))];
    const timeframes = mode === "backtest" ? ["15m", "1H"] : values.getAll("timeframes").map(String);
    if (!id || !["backtest", "paper"].includes(mode)) { pipelineState.runError = "流程或运行方式无效。"; renderPipelineRegistry(); return; }
    if (!symbols.length) { pipelineState.runError = "请输入至少一个合约。"; renderPipelineRegistry(); return; }
    if (!timeframes.length) { pipelineState.runError = "模拟运行至少选择一个周期。"; renderPipelineRegistry(); return; }
    const revision = form.dataset.revision;
    pipelineState.running = true;
    pipelineState.runError = "";
    renderPipelineRegistry();
    try {
      const result = await api(`/api/research/pipelines/${encodeURIComponent(id)}/runs`, { method: "POST", body: JSON.stringify({
        expected_revision: finite(revision) ? Number(revision) : revision, mode, symbols, timeframes, request_id: requestId(),
      }) });
      const kind = result?.kind || mode;
      const destination = kind === "paper" ? "#paper" : "#backtests";
      if (window.location) window.location.hash = destination;
    } catch (error) {
      pipelineState.runError = error.message || "无法启动该流程运行。";
    } finally {
      pipelineState.running = false;
      renderPipelineRegistry();
    }
  }

  const modelStatus = (value) => statusNames[value] || String(value || "未记录");
  const stageOptions = (value) => ["research", "rejected", "archived"].map((stage) => `<option value="${stage}"${value === stage ? " selected" : ""}>${stageNames[stage]}</option>`).join("");
  const familyFor = (item) => modelState.families.find((family) => family.id === item.family);
  const eligibility = (value) => value === true ? "登记为是（未复核）" : value === false ? "登记为否" : "未登记";
  function auditMarkup(audit) {
    if (!audit) return `<p class="platform-muted">尚未运行工程身份校验。</p>`;
    if (typeof audit !== "object") return `<p>${esc(display(audit))}</p>`;
    const checked = audit.checked_at ? `<span>${esc(audit.checked_at)}</span>` : "";
    const checks = arr(audit.checks);
    return `<div class="platform-audit-summary"><span class="research-badge status-${esc(audit.status || "unknown")}">${esc(modelStatus(audit.status))}</span>${checked}</div>${checks.length ? `<ul class="platform-audit-checks">${checks.map((check) => `<li><strong>${esc(check.name || "检查项")}</strong><span class="research-badge">${esc(modelStatus(check.status))}</span><p>${esc(display(check.detail))}</p></li>`).join("")}</ul>` : `<p class="platform-muted">接口未返回检查明细。</p>`}`;
  }
  function modelCard(item) {
    const id = String(item.id ?? "");
    const family = familyFor(item);
    const saving = modelState.saving.has(id);
    const auditing = modelState.auditing.has(id);
    const notes = item.annotation_notes || "";
    const artifacts = arr(item.artifact_ids);
    const experiments = arr(item.experiment_ids);
    const viewLink = family ? safeViewLink(family.view) : "";
    const auditError = modelState.auditErrors[id] ? `<p class="research-error" role="alert">${esc(modelState.auditErrors[id])}</p>` : "";
    const stage = ["research", "rejected", "archived"].includes(item.stage) ? item.stage : ["research", "rejected", "archived"].includes(item.status) ? item.status : "research";
    return `<article class="model-card"><div class="model-card-top"><div><span class="research-badge">${esc(family?.name || item.family || "未分类")}</span><span class="research-badge">${esc(item.kind || "模型")}</span></div><span class="research-badge status-${esc(item.status || "unknown")}">${esc(modelStatus(item.status))}</span></div>
      <h3>${esc(item.name || id || "未命名模型")}</h3><p class="model-description">${esc(item.notes ? Array.isArray(item.notes) ? item.notes.join(" · ") : item.notes : item.objective || "模型说明尚未登记。")}</p>
      <dl class="model-facts"><dt>本机文件</dt><dd>${item.local_exists === true ? "登记位置存在" : item.local_exists === false ? "登记位置缺失" : "未知"}</dd><dt>特征数量</dt><dd>${count(item.feature_count)}</dd><dt>训练资格</dt><dd>${eligibility(item.training_eligible)}</dd><dt>生产资格</dt><dd>${eligibility(item.production_eligible)}</dd><dt>关联实验</dt><dd>${experiments.length ? experiments.map((experimentId) => `<button type="button" class="platform-link-button" data-experiment="${esc(experimentId)}">${esc(experimentId)} ↗</button>`).join(" ") : "未关联"}</dd><dt>登记制品</dt><dd>${artifacts.length ? artifacts.map((artifactId) => `<span class="research-mono">${esc(artifactId)}</span>`).join(" · ") : "未关联登记制品"}</dd></dl>
      <div class="model-card-actions"><button type="button" class="research-button" data-model-audit="${esc(id)}"${auditing || saving ? " disabled" : ""}>${auditing ? "正在校验…" : "校验制品身份"}</button>${viewLink}</div>
      ${auditError}<details class="model-evidence"><summary>来源、特征语义与校验结果</summary><dl class="model-facts model-source-facts"><dt>源码位置</dt><dd><code>${esc(display(item.source_path))}</code></dd><dt>元数据位置</dt><dd><code>${esc(display(item.metadata_path))}</code></dd><dt>特征语义</dt><dd>${textBlock(item.feature_semantics)}</dd><dt>研究目标</dt><dd>${textBlock(item.objective)}</dd><dt>身份校验</dt><dd>${auditMarkup(item.audit)}</dd></dl></details>
      <details class="model-edit"><summary>研究备注</summary><form data-model-edit="${esc(id)}" data-revision="${esc(item.revision ?? "")}" class="model-edit-form"><label>研究阶段<select name="stage">${stageOptions(stage)}</select></label><label>备注<textarea name="notes" rows="3" maxlength="12000">${esc(notes)}</textarea></label><button class="research-button primary" type="submit"${saving || auditing ? " disabled" : ""}>${saving ? "正在保存…" : "保存备注"}</button><span class="research-caption">保存研究状态与备注，不会运行训练或提升资格。</span></form></details>
      <span class="research-caption">身份校验只核对登记源文件，不代表准确率、研究结论或生产准入。</span></article>`;
  }
  function modelSearchText(item) {
    const family = familyFor(item);
    return [item.id, item.name, item.family, family?.name, item.kind, item.status, item.source_path, item.metadata_path, item.feature_semantics, item.objective, item.notes, ...arr(item.experiment_ids), ...arr(item.artifact_ids)].map(display).join(" ").toLocaleLowerCase();
  }
  function filteredModels() {
    const query = modelState.query.trim().toLocaleLowerCase();
    return modelState.items.filter((item) => (modelState.family === "all" || item.family === modelState.family) && (!query || modelSearchText(item).includes(query)));
  }
  function renderModelCount() {
    const node = $("model-result-count");
    if (node) node.textContent = `显示 ${count(filteredModels().length)} / ${count(modelState.items.length)} 项`;
  }
  function modelResultsMarkup() {
    if (!modelState.loaded && modelState.loading) return `<p class="research-empty">正在读取模型登记…</p>`;
    if (modelState.error && !modelState.loaded) return `<div class="research-error" role="alert">${esc(modelState.error)} <button type="button" class="research-button" data-model-refresh>重试</button></div>`;
    const items = filteredModels();
    const error = modelState.error ? `<div class="research-error" role="alert">${esc(modelState.error)} <button type="button" class="research-button" data-model-refresh>重试</button></div>` : "";
    const empty = modelState.items.length === 0 ? `<p class="research-empty">模型接口没有登记制品。</p>` : items.length === 0 ? `<p class="research-empty">没有匹配的模型。</p>` : "";
    return `${error}${empty || `<div class="model-grid">${items.map(modelCard).join("")}</div>`}`;
  }
  function renderModelResults() {
    const results = $("model-results");
    if (results) results.innerHTML = modelResultsMarkup();
  }
  function gateCard(name, gate) {
    if (!gate || typeof gate !== "object") return `<article class="model-gate"><span>${name}</span><strong>未登记</strong><p>当前门状态不可用。</p></article>`;
    return `<article class="model-gate"><span>${name}</span><strong>${gate.allowed === true ? "当前开放" : gate.allowed === false ? "尚未开放" : "状态未知"}</strong>${gate.reason ? `<p>${esc(gate.reason)}</p>` : ""}</article>`;
  }
  function renderModels() {
    const root = $("models-workspace");
    if (!root) return;
    const familyOptions = modelState.families.map((family) => `<option value="${esc(family.id)}"${family.id === modelState.family ? " selected" : ""}>${esc(family.name || family.id)} · ${count(family.count)}</option>`).join("");
    root.innerHTML = `<div class="platform-hero research-callout"><strong>模型登记与工程身份检查</strong><p>模型制品、来源、特征语义和研究关联来自现有登记。身份检查不运行推理或训练，也不代表模型准确率或生产准入。此页不提供训练或 promote 操作。</p><div class="model-research-links"><a class="research-button" href="#yolo">YOLO 工作流 ↗</a><a class="research-button" href="#vision">VLM 工作流 ↗</a><a class="research-button" href="#factors">因子库 ↗</a></div></div>
      <div class="model-gates">${gateCard("训练门", modelState.gates.training)}${gateCard("生产门", modelState.gates.production)}</div>
      <div class="research-toolbar model-toolbar"><div><input id="model-search" type="search" value="${esc(modelState.query)}" aria-label="搜索模型" placeholder="搜索模型、路径、特征或关联实验…"><select id="model-family" aria-label="模型系列"><option value="all">全部系列 · ${count(modelState.items.length)}</option>${familyOptions}</select><span id="model-result-count" class="research-caption">显示 ${count(filteredModels().length)} / ${count(modelState.items.length)} 项</span></div><button type="button" class="research-button" data-model-refresh${modelState.loading ? " disabled" : ""}>${modelState.loading ? "正在刷新…" : "刷新登记"}</button></div>
      <div id="model-results" aria-live="polite">${modelResultsMarkup()}</div>`;
    bindModelEvents(root);
  }
  async function refreshModels() {
    if (modelState.loading) return;
    modelState.loading = true;
    modelState.error = "";
    renderModels();
    try {
      const data = await api("/api/research/models");
      if (!data || !Array.isArray(data.items) || !Array.isArray(data.families)) throw new Error("模型登记数据格式有误。");
      modelState.items = data.items;
      modelState.families = data.families;
      modelState.gates = data.gates || {};
      modelState.loaded = true;
    } catch (error) {
      modelState.error = error.message || "读取模型登记失败。";
    } finally {
      modelState.loading = false;
      renderModels();
    }
  }
  async function auditModel(id) {
    if (!id || modelState.auditing.has(id)) return;
    modelState.auditing.add(id);
    delete modelState.auditErrors[id];
    renderModelResults();
    try {
      const result = await api(`/api/research/models/${encodeURIComponent(id)}/audit`, { method: "POST" });
      const item = modelState.items.find((model) => String(model.id) === String(id));
      if (item) item.audit = result;
    } catch (error) {
      modelState.auditErrors[id] = error.message || "模型身份校验失败。";
    } finally {
      modelState.auditing.delete(id);
      renderModelResults();
    }
  }
  async function saveModel(form) {
    const id = form.dataset.modelEdit;
    if (!id || modelState.saving.has(id)) return;
    const values = new FormData(form);
    const revision = form.dataset.revision;
    modelState.saving.add(id);
    modelState.error = "";
    renderModels();
    try {
      await api(`/api/research/models/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({
        expected_revision: finite(revision) ? Number(revision) : revision, stage: values.get("stage"), notes: values.get("notes") || "",
      }) });
      modelState.saving.delete(id);
      await refreshModels();
    } catch (error) {
      modelState.error = error.message || "保存模型研究备注失败。";
      renderModels();
    } finally {
      modelState.saving.delete(id);
    }
  }
  function bindModelEvents(root) {
    if (root.dataset.bound) return;
    root.dataset.bound = "true";
    root.addEventListener("input", (event) => {
      if (event.target.id === "model-search") {
        modelState.query = event.target.value;
        renderModelResults();
        renderModelCount();
      }
    });
    root.addEventListener("change", (event) => {
      if (event.target.id === "model-family") {
        modelState.family = event.target.value;
        renderModels();
        $("model-family")?.focus?.();
      }
    });
    root.addEventListener("click", (event) => {
      if (event.target.closest?.("[data-model-refresh]")) { refreshModels(); return; }
      const audit = event.target.closest?.("[data-model-audit]");
      if (audit) { auditModel(audit.dataset.modelAudit); return; }
    });
    root.addEventListener("submit", (event) => {
      const form = event.target.closest?.("form[data-model-edit]");
      if (!form) return;
      event.preventDefault();
      saveModel(form);
    });
  }

  window.SpikePlatform = {
    setActive(active) { platformState.active = Boolean(active); if (platformState.active && !platformState.loaded) return refreshPlatform(); },
    refresh: refreshPlatform,
  };
  window.SpikeModels = {
    setActive(active) { modelState.active = Boolean(active); if (modelState.active && !modelState.loaded) return refreshModels(); },
    refresh: refreshModels,
  };
})();
