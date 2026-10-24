import React, {useMemo, useState} from "react";
import {
  DataComponent, DataTable, Dropdown, ReportSection, RichNarrative, Section,
  SectionNavigator, useDataApp,
} from "../../data-app-public.jsx";

const EXIT_LABELS = {monday_full: "周一对侧全平", opposite_1618_full: "另一侧 1.618 全平", half_each: "两目标各半", shared_entry: "三方案共同入场"};
const ASSET_LABELS = {BTC: "BTC", ETH: "ETH", XAU: "XAUUSD · 黄金", XAUUSD: "XAUUSD · 黄金", ALT: "山寨币", ALTCOINS: "山寨币"};
const PERIOD_LABELS = {all: "全期", earlier: "前期（切点前）", later: "后期（切点起）", pre_2023: "前期（2023-01-01 UTC前）", post_2023: "后期（2023-01-01 UTC起）"};
const FIELD_LABELS = {
  section: "策略环节", item: "规则项", long: "多头规则", short: "空头规则", detail: "执行说明",
  exit_mode: "退出方式", exit_policy: "退出方式", exit_mode_label: "退出方式",
  asset_group: "品种组", group: "品种组", asset: "品种组", asset_group_label: "品种组",
  period: "时段", segment: "时段", period_label: "时段", grain: "统计粒度", cut_dimension: "切片维度", cut_value: "切片值",
  direction: "方向", side: "方向", basis: "收益口径", basis_label: "收益口径", opportunity_period_basis: "机会期望分期时间锚", outcome: "结果", status: "状态",
  sample_kind: "样本类型", sample_id: "样本ID", symbol: "品种", seed: "随机种子", sampling_pool: "抽样池与范围",
  opportunity_n: "机会数", invalidated_n: "失效数", reclaim_n: "站回数", confirmed_n: "确认数",
  submitted_n: "提交挂单数", canceled_n: "撤单数", unfilled_n: "未成交数", filled_n: "成交数",
  closed_n: "闭合数", censored_n: "截尾数", wins: "盈利数", losses: "亏损数", flat: "持平数", win_rate: "胜率（显示为百分比）",
  mean_win_r: "平均盈利R", mean_loss_r: "平均亏损R", planned_rr_median: "计划盈亏比中位数",
  paired_signal_planned_rr: "严格配对策略计划RR均值", paired_control_planned_rr: "严格配对对照计划RR均值",
  mean_gross_r: "毛均R", mean_fee_r: "均费用R", mean_net_r: "净均R", mean_r: "均值R（按basis）",
  median_r: "R中位数（按basis）", total_r: "R总和（按basis）", total_net_r: "净R总和", mean_bp: "均bp（按basis）", mean_net_bp: "净均bp",
  common_closed_n: "三方案共同闭合笔数", common_mean_r: "共同闭合均R", common_win_rate: "共同闭合胜率", common_mean_bp: "共同闭合均bp", common_mean_fee_r: "共同闭合平均费用R", common_median_holding_hours: "共同闭合持仓中位小时",
  payoff: "平均盈亏比", profit_factor: "盈亏因子",
  unreachable_target_n: "目标非正价的已成交数", same_bar_target_stop_n: "退出柱同时覆盖TP/SL数（单目标）",
  median_holding_hours: "中位持仓小时", p90_holding_hours: "持仓时长P90（小时）",
  mfe_r_mean: "MFE均值R（持仓内）", mae_r_mean: "MAE均值R（持仓内）",
  profitable_excursion_then_sl_n: "先有盈利浮盈后止损数", longest_loss_streak: "最长连亏数",
  longest_stop_streak: "最长连续止损数", loss_streak_span: "最长连亏区间与交易ID",
  longest_single_symbol_loss_streak: "单品种最长连亏数", single_symbol_streak_span: "单品种最长连亏区间",
  fee_positive_excursion_then_sl_n: "扣费后曾盈利再止损数", tp_legs_then_sl_n: "一腿止盈后另一腿止损数",
  eligible_control_n: "合格挂单时机对照数", submitted_control_n: "提交对照挂单数",
  filled_control_n: "已成交对照数", closed_control_n: "已闭合对照数", nonfill_control_n: "未成交对照数",
  signal_fill_rate: "策略机会成交率", random_fill_rate: "对照机会成交率",
  eligible_signal_n: "合格信号数", fully_matched_signal_n: "完整三对照可配对信号数",
  complete_control_group_n: "完整3/3对照组数", controls_per_signal: "每信号对照数",
  complete_opportunity_pairs: "机会期望配对信号数",
  attempt_signal_mean_r: "机会期望策略R/信号",
  attempt_control_mean_r: "机会期望对照R/信号",
  attempt_paired_excess_r: "机会期望配对超额R/信号",
  matched_signal_mean_r: "严格配对信号均R", matched_control_mean_r: "严格配对对照均R",
  matched_signal_mean_net_r: "严格配对策略净均R", matched_control_mean_net_r: "严格配对对照净均R",
  paired_excess_net_r: "严格配对净超额R", matched_signal_mean_gross_r: "严格配对策略毛均R",
  matched_control_mean_gross_r: "严格配对对照毛均R", paired_p_holm: "多重比较校正p值",
  independent_market_weeks: "独立市场周数", inference: "推断方法说明",
  paired_excess_r: "配对超额R", paired_ci_low: "配对区间下界R", paired_ci_high: "配对区间上界R", paired_p: "原始配对p值（未校正）",
  opportunity_id: "机会ID", signal_id: "信号ID", trigger_week: "触发周", week: "触发周",
  touch_time_utc: "首次触价UTC", first_touch_at: "首次触价时点", trigger_time: "触发时点",
  level: "触价水平",
  monitor_start_utc: "监控开始UTC", invalidated_at_utc: "失效UTC", reclaim_time_utc: "首次站回UTC",
  confirmed_at_utc: "确认完成UTC", confirmation_at: "确认完成时点", invalidation_at: "失效时点",
  submitted_at_utc: "提交挂单UTC", submitted_at: "提交挂单时点", canceled_at_utc: "撤单UTC",
  resolution_interval_start: "订单结果所在柱开始UTC", resolution_interval_end: "订单结果所在柱结束UTC",
  actual_pending_amendment_count: "实际改单次数", actual_pending_amendments: "实际改单时间与价格",
  cancel_reason: "撤单原因", invalidation_reason: "失效原因", decision_reason: "判定说明", reason: "状态/原因",
  order_id: "订单ID", limit_price: "挂单价", stop_price: "止损价", target_price: "目标价",
  entry_price: "入场/挂单价", entry: "入场/挂单价", stop: "止损价", population: "样本总体",
  trade_id: "交易ID", fill_id: "成交ID", entry_at_utc: "入场UTC",
  entry_interval_start: "实际成交柱开始时点（分期锚）", entry_time: "保守入场柱结束时点",
  exit_at_utc: "退出UTC", exit_time: "退出时点", exit_price: "退出价（各半为两腿均价）", target_monday: "周一对侧目标", target_far: "另一侧1.618目标", exit_price_scope: "退出价格口径", closed: "是否闭合", censored: "是否截尾",
  gross_r: "毛R", fee_r: "费用R", net_r: "净R", return_r: "收益R", return_pct: "收益百分比", net_bp: "净收益bp",
  mfe_r: "MFE R（持仓内）", mae_r: "MAE R（持仓内）", mfe_r_mean: "MFE均值R（持仓内）", mae_r_mean: "MAE均值R（持仓内）",
  profitable_excursion_then_sl: "先有盈利浮盈后止损", holding_hours: "持仓小时", timestamp_utc: "成交时点UTC",
  time: "成交时点", price: "成交价", quantity: "数量", notional: "名义金额", fee_rate: "费率",
  fee_type: "费类", fee_class: "费类", fee: "费用", commission: "手续费金额",
  count: "数量", n: "数量", stage: "漏斗阶段", stage_order: "阶段顺序", denominator: "分母", rate: "比例",
  evidence_as_of: "证据截止日期", source_first: "行情覆盖起点", source_last: "行情覆盖终点",
  first_time_utc: "覆盖起点UTC", last_time_utc: "覆盖终点UTC", first: "品种覆盖起点UTC", last: "品种覆盖终点UTC",
  unexpected_gap_count: "5m非连续间隔数", blocked_monday_weeks: "不可用周一数", conflicting_minutes: "冲突分钟数", excluded_five_minute_bars: "排除5m柱数",
  source_rows: "源K线数", opportunities: "来源机会数", submitted: "来源挂单数",
  native_positions: "原生持仓数", native_fills: "原生成交数", coverage_asset_count: "来源覆盖品种数",
  coverage_assets_ending_before_source_last: "早于全局最新尾部的品种数", record_type: "记录类型",
  complete: "全量回放完成标记", completed_symbols: "已完成品种数", expected_symbols: "预期品种数",
  random_control_mean_r: "严格配对随机对照均R",
};
const METRIC_FIELDS = [
  "exit_mode", "asset_group", "period", "basis", "opportunity_period_basis", "grain", "cut_dimension", "cut_value",
  "opportunity_n", "invalidated_n", "reclaim_n", "confirmed_n", "submitted_n", "canceled_n",
  "unfilled_n", "filled_n", "closed_n", "censored_n", "unreachable_target_n", "same_bar_target_stop_n", "wins", "losses", "flat", "win_rate",
  "mean_win_r", "mean_loss_r", "planned_rr_median", "mean_gross_r", "mean_fee_r", "mean_net_r",
  "common_closed_n", "common_mean_r", "common_win_rate", "common_mean_bp", "mean_r", "median_r", "total_r", "total_net_r", "mean_bp", "mean_net_bp", "payoff", "profit_factor", "median_holding_hours",
  "p90_holding_hours", "mfe_r_mean", "mae_r_mean", "profitable_excursion_then_sl_n",
  "fee_positive_excursion_then_sl_n", "tp_legs_then_sl_n", "longest_loss_streak", "longest_stop_streak",
  "loss_streak_span", "longest_single_symbol_loss_streak", "single_symbol_streak_span", "eligible_signal_n",
  "eligible_control_n", "submitted_control_n", "filled_control_n", "closed_control_n", "nonfill_control_n",
  "signal_fill_rate", "random_fill_rate",
  "fully_matched_signal_n", "complete_control_group_n", "controls_per_signal",
  "complete_opportunity_pairs", "attempt_signal_mean_r", "attempt_control_mean_r", "attempt_paired_excess_r",
  "matched_signal_mean_r", "matched_control_mean_r", "paired_excess_r",
  "paired_signal_planned_rr", "paired_control_planned_rr", "matched_signal_mean_net_r",
  "matched_control_mean_net_r", "paired_excess_net_r", "matched_signal_mean_gross_r",
  "matched_control_mean_gross_r", "paired_ci_low", "paired_ci_high", "paired_p", "paired_p_holm", "independent_market_weeks",
];
const LEDGER_FIELDS = {
  opportunities: ["opportunity_id", "exit_mode", "asset_group", "symbol", "side", "week", "level", "first_touch_at", "trigger_time", "confirmation_at", "invalidation_at", "reason", "status", "direction", "period", "trigger_week", "touch_time_utc", "monitor_start_utc", "invalidated_at_utc", "reclaim_time_utc", "confirmed_at_utc", "submitted_at_utc", "invalidation_reason", "decision_reason"],
  orders: ["order_id", "opportunity_id", "exit_mode", "asset_group", "symbol", "side", "submitted_at", "resolution_interval_start", "resolution_interval_end", "limit_price", "stop_price", "target_price", "population", "actual_pending_amendment_count", "actual_pending_amendments", "canceled_at_utc", "cancel_reason", "status"],
  trades: ["trade_id", "opportunity_id", "population", "exit_mode", "asset_group", "symbol", "side", "entry_interval_start", "entry_time", "exit_time", "entry_price", "stop_price", "return_r", "return_pct", "basis", "net_r", "gross_r", "fee_r", "net_bp", "closed", "censored", "direction", "entry_at_utc", "entry", "stop", "target_price", "target_monday", "target_far", "exit_at_utc", "exit_price", "outcome", "mfe_r", "mae_r", "profitable_excursion_then_sl", "holding_hours"],
  fills: ["fill_id", "trade_id", "order_id", "opportunity_id", "population", "exit_mode", "asset_group", "symbol", "direction", "side", "time", "timestamp_utc", "price", "quantity", "notional", "commission", "fee_rate", "fee_class", "fee_type", "fee", "fee_r"],
};

function valueOf(row, fields) {
  for (const field of fields) if (row && row[field] !== undefined) return row[field];
  return undefined;
}
function fieldOf(rows, fields) {
  return fields.find((field) => rows.some((row) => row && row[field] !== undefined));
}
function uniqueValues(rows, field) {
  return [...new Set(rows.map((row) => row && row[field]).filter((value) => value !== undefined && value !== null && value !== "").map(String))]
    .sort((a, b) => a.localeCompare(b, "zh-CN", {numeric: true}));
}
function labelValue(value, labels) {
  return value == null ? "—" : labels[String(value)] || String(value);
}
function cellText(value, field) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  const labels = {
    exit_mode: EXIT_LABELS, asset_group: ASSET_LABELS, period: PERIOD_LABELS,
    side: {1: "多头", "-1": "空头"}, population: {signal: "策略", random: "随机对照"},
    basis: {gross: "毛收益", net: "扣费后"},
    outcome: {win: "盈利", loss: "亏损", flat: "持平", censored: "未决"},
    fee_class: {maker: "挂单费", taker: "吃单费", unknown_not_modeled: "费用未知"},
    status: {filled: "已成交", expired: "到期未成交", confirmed: "已确认", pending: "观察中", invalid: "已失效", invalid_tick_geometry: "取整后无法挂单"},
  };
  const reasons = {third_new_extreme: "累计第三次刷新极值", returned_to_monday_range: "入场前已回周一区间", unknown_crypto_prefix: "本周前缀数据不完整", touch_15m_incomplete: "触价15m柱不完整", invalid_unobservable: "连续行情不可观察", no_causal_volatility_bucket: "缺少因果波动桶", source_gap: "行情缺口", gap_cancel: "数据断档撤单", no_warmup: "均线未预热", no_confirmation: "未形成四根确认", invalid_amendment_geometry: "改单价格不再有效"};
  if (["status", "reason", "invalidation_reason", "cancel_reason"].includes(field) && reasons[value]) return reasons[value];
  if (labels[field]?.[value]) return labels[field][value];
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number" && ["win_rate", "rate", "fee_rate", "signal_fill_rate", "random_fill_rate", "common_win_rate"].includes(field)) {
    const digits = field === "fee_rate" ? 4 : 2;
    return (value * 100).toLocaleString("zh-CN", {maximumFractionDigits: digits}) + "%";
  }
  if (typeof value === "number" && ["paired_p", "paired_p_holm", "p_value", "adjusted_p_value"].includes(field)) {
    return value.toLocaleString("zh-CN", {maximumSignificantDigits: 4, useGrouping: false});
  }
  if (typeof value === "number") {
    const digits = /bp|rate|_p$|pct/i.test(field) ? 3 : 6;
    return value.toLocaleString("zh-CN", {maximumFractionDigits: digits});
  }
  return String(value);
}
function columnsFor(rows, preferred, strict = false) {
  const keys = [...new Set(rows.flatMap((row) => Object.keys(row || {})))]
    .filter((key) => !["image_data_url", "image_src", "plot_data", "checks_json", "record_type"].includes(key));
  const preferredKeys = (preferred || []).filter((key) => keys.includes(key));
  const selected = (strict ? preferredKeys : [...preferredKeys, ...keys.filter((key) => !(preferred || []).includes(key))]).slice(0, 60);
  return selected.map((field) => ({
    field,
    label: FIELD_LABELS[field] || field.replaceAll("_", " "),
    renderCell: (value) => cellText(value, field),
  }));
}
function exportRows(rows, filename) {
  if (!rows.length) return;
  const excluded = new Set(["image_data_url", "image_src", "plot_data"]);
  const keys = [...new Set(rows.flatMap((row) => Object.keys(row || {})))].filter((key) => !excluded.has(key));
  const escape = (value) => "\"" + String(value == null ? "" : typeof value === "object" ? JSON.stringify(value) : value).replaceAll("\"", "\"\"") + "\"";
  const csv = "\ufeff" + keys.map(escape).join(",") + "\n" + rows.map((row) => keys.map((key) => escape(row[key])).join(",")).join("\n");
  const href = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
function rowFilter(rows, selections) {
  return rows.filter((row) => Object.entries(selections).every(([selection, chosen]) => {
    if (chosen === "ALL" || (selection === "exit" && chosen === "shared_entry")) return true;
    const candidates = {
      exit: ["exit_mode", "exit_policy"], asset: ["asset_group", "group", "asset"],
      period: ["period", "segment", "time_period"], direction: ["direction", "side"], population: ["population"],
    }[selection];
    if (!candidates) return true;
    const field = candidates.find((candidate) => row && row[candidate] !== undefined);
    if (selection === "exit" && row[field] === "shared_entry") return true;
    return !field || String(row[field]) === chosen;
  }));
}
function filterChoices(rows, fields, labels) {
  const field = fieldOf(rows, fields);
  const values = field ? uniqueValues(rows, field) : [];
  return {
    choices: ["ALL", ...values],
    choiceLabels: {ALL: "全部", ...Object.fromEntries(values.map((value) => [value, labelValue(value, labels)]))},
  };
}
function DownloadBar({rows, allRows, filename}) {
  return <div className="ma6-download-row">
    <button type="button" disabled={!rows.length} onClick={() => exportRows(rows, filename + "-当前下拉筛选.csv")}>下载当前下拉筛选 CSV</button>
    <button type="button" disabled={!allRows.length} onClick={() => exportRows(allRows, filename + "-全部.csv")}>下载全部 CSV</button>
    <span>按下拉选项导出全部匹配行及页面审查列；文字搜索与排序只影响屏幕。原始全字段CSV在交付文件夹“全部逐笔明细”。</span>
  </div>;
}
function EvidenceTable({id, title, queryId, sourceRows, displayRows, preferred, strictColumns = false, controls, note, searchable = true}) {
  const columns = columnsFor(displayRows.length ? displayRows : sourceRows, preferred, strictColumns);
  return <DataComponent id={id} title={title} queryId={queryId} kind="table" variant="card"
    sourceRows={sourceRows} displayRows={displayRows} headerControls={controls}>
    {note && <p className="ma6-table-note">{note}</p>}
    {displayRows.length
      ? <DataTable rows={displayRows} columns={columns} searchable={searchable} label={title}/>
      : <p className="ma6-empty" data-reviewed-rows>当前数据包没有可展示的行。这不代表零机会、零成交或零收益；请从来源菜单确认对应导出是否已提供。</p>}
  </DataComponent>;
}

function MetricRows({id, title, rows, selections, setSelections, showCuts = false, controlOnly = false}) {
  const hasGrain = rows.some((row) => row.grain === "summary" || row.grain === "cut");
  const grainRows = hasGrain
    ? rows.filter((row) => row.grain === (showCuts ? "cut" : "summary"))
    : rows;
  const cutField = fieldOf(rows, ["cut_dimension", "dimension"]);
  const aggregateRows = cutField ? grainRows.filter((row) => {
    const value = row[cutField];
    return value == null || value === "" || ["all", "overall", "summary", "aggregate", "total"].includes(String(value).toLowerCase());
  }) : grainRows;
  const baseRows = showCuts ? grainRows : (hasGrain ? grainRows : aggregateRows.length ? aggregateRows : grainRows);
  const filtered = rowFilter(baseRows, selections);
  const dimensions = uniqueValues(rows, fieldOf(rows, ["cut_dimension", "dimension"]) || "cut_dimension");
  const withCuts = showCuts && selections.dimension && selections.dimension !== "ALL"
    ? filtered.filter((row) => String(row.cut_dimension ?? row.dimension) === selections.dimension)
    : filtered;
  const sourceRows = rows;
  const displayRows = withCuts.map((row) => ({
    ...row,
    exit_mode_label: labelValue(valueOf(row, ["exit_mode", "exit_policy"]), EXIT_LABELS),
    asset_group_label: labelValue(valueOf(row, ["asset_group", "group", "asset"]), ASSET_LABELS),
    period_label: labelValue(valueOf(row, ["period", "segment", "time_period"]), PERIOD_LABELS),
    basis_label: labelValue(row.basis, {gross: "毛收益", net: "扣费净收益"}),
  }));
  const controls = <div className="ma6-filter-row">
    {[
      ["exit", "退出方式", ["exit_mode", "exit_policy"], EXIT_LABELS],
      ["asset", "品种组", ["asset_group", "group", "asset"], ASSET_LABELS],
      ["period", "时段", ["period", "segment", "time_period"], PERIOD_LABELS],
    ].map(([key, label, fields, labels]) => {
      const options = filterChoices(baseRows, fields, labels);
      return <Dropdown key={key} label={label} showLabel value={selections[key]} {...options}
        onChange={(value) => setSelections((state) => ({...state, [key]: value}))}/>;
    })}
    {showCuts && dimensions.length > 0 && <Dropdown label="分解维度" showLabel value={selections.dimension}
      choices={["ALL", ...dimensions]}
      choiceLabels={{ALL: "全部切片", ...Object.fromEntries(dimensions.map((value) => [value, FIELD_LABELS[value] || value]))}}
      onChange={(value) => setSelections((state) => ({...state, dimension: value}))}/>}
  </div>;
  const preferred = controlOnly
    ? ["exit_mode_label", "asset_group_label", "period_label", "basis_label", "eligible_signal_n", "eligible_control_n", "submitted_control_n", "filled_control_n", "closed_control_n", "nonfill_control_n", "complete_opportunity_pairs", "attempt_signal_mean_r", "attempt_control_mean_r", "attempt_paired_excess_r", "fully_matched_signal_n", "complete_control_group_n", "controls_per_signal", "matched_signal_mean_r", "matched_control_mean_r", "paired_excess_r", "paired_ci_low", "paired_ci_high", "paired_p_holm", "paired_p", "independent_market_weeks"]
    : ["exit_mode_label", "asset_group_label", "period_label", "basis_label", ...METRIC_FIELDS.slice(4)];
  return <EvidenceTable id={id} title={title} queryId="metrics" sourceRows={sourceRows} displayRows={displayRows}
    preferred={preferred} strictColumns={controlOnly} controls={controls}
    note={controlOnly ? "提交队列机会期望 attempt_*_r 按 submitted_at 分期、以全部合格机会为分母，未成交按零暴露计入期望但不计为盈亏交易或逐笔胜率；matched_*_r 与 paired_excess_r 只用严格3/3成交且闭合的完整组。两类分母分开。" : showCuts ? "只显示 grain=cut 的年份／月份／多空／单品种切片；闭合交易分期按 entry_interval_start（实际成交柱开始时间），跨切点仓位剔除；win_rate 源值为0–1，表内按百分比显示。" : "只显示 grain=summary 的36个退出方式 × 品种组 × 时段汇总行；闭合交易分期按 entry_interval_start（实际成交柱开始时间），跨切点仓位剔除。提交队列机会期望另按 submitted_at 分期。win_rate 源值为0–1，表内按百分比显示；均值R、总R与bp按 basis 展示。"}/>
}

function CompactSummary({rows}) {
  const allPeriod = rows.filter((row) => row.grain === "summary" && String(row.period) === "all");
  const displayRows = allPeriod.map((row) => ({
    ...row,
    exit_mode_label: labelValue(valueOf(row, ["exit_mode", "exit_policy"]), EXIT_LABELS),
    asset_group_label: labelValue(valueOf(row, ["asset_group", "group", "asset"]), ASSET_LABELS),
    basis_label: labelValue(row.basis, {gross: "毛收益", net: "扣费净收益"}),
    random_control_mean_r: row.matched_control_mean_r,
  }));
  const preferred = [
    "exit_mode_label", "asset_group_label", "basis_label", "closed_n", "win_rate",
    "mean_win_r", "mean_loss_r", "mean_r", "profit_factor", "common_closed_n", "common_mean_r", "common_win_rate", "random_control_mean_r",
  ];
  return <EvidenceTable id="compact-summary" title={"全期比较摘要 · " + displayRows.length.toLocaleString("zh-CN") + " 行"}
    queryId="metrics" sourceRows={rows} displayRows={displayRows} preferred={preferred} strictColumns
    note="仅取 period=all 的 grain=summary 行；R按毛／净 basis 解释。共同闭合列只比较三个退出均已结束的相同入场，避免未决分母不同。随机对照列为严格3/3成交闭合配对组的对照均R，不与下方提交队列机会期望混用。覆盖3种退出 × 4个品种组；只显示结果文件实际提供的组合，不为缺失组合补造零行。进行中的部分回放不得作方案排名。"/>
}

function SampleGallery({samples}) {
  const [filters, setFilters] = useState({exit: "ALL", asset: "ALL", direction: "ALL", kind: "ALL", selected: ""});
  const choiceConfig = {
    exit: filterChoices(samples, ["exit_mode", "exit_policy"], EXIT_LABELS),
    asset: filterChoices(samples, ["asset_group", "group", "asset"], ASSET_LABELS),
    direction: filterChoices(samples, ["direction", "side"], {1: "多头", "-1": "空头", long: "多", short: "空", 多: "多", 空: "空"}),
    kind: filterChoices(samples, ["sample_kind", "outcome", "status"], {winner: "盈利", loser: "亏损", unfilled: "未成交", invalidated: "失效"}),
  };
  const matches = samples.filter((row) => {
    const actual = {
      exit: valueOf(row, ["exit_mode", "exit_policy"]), asset: valueOf(row, ["asset_group", "group", "asset"]),
      direction: valueOf(row, ["direction", "side"]), kind: valueOf(row, ["sample_kind", "outcome", "status"]),
    };
    return Object.entries(actual).every(([key, value]) => filters[key] === "ALL" || String(value ?? "") === filters[key]);
  });
  const current = matches.find((row) => String(row.sample_id ?? row.trade_id) === filters.selected) || matches[0];
  const idFor = (row, index) => String(row.sample_id ?? row.trade_id ?? index);
  const bars = Array.isArray(current && current.plot_data) ? current.plot_data
    : Array.isArray(current && current.plot_data && current.plot_data.bars) ? current.plot_data.bars
      : Array.isArray(current && current.plot_data && current.plot_data.candles) ? current.plot_data.candles : [];
  const controls = <div className="ma6-filter-row">
    {[
      ["exit", "退出方式"], ["asset", "品种组"], ["direction", "方向"], ["kind", "样本类型"],
    ].map(([key, label]) => <Dropdown key={key} label={label} showLabel value={filters[key]} {...choiceConfig[key]}
      onChange={(value) => setFilters((state) => ({...state, [key]: value, selected: ""}))}/>)}
  </div>;
  const metaKeys = ["sample_id", "sample_kind", "outcome", "exit_mode", "asset_group", "symbol", "direction", "seed", "sampling_pool", "scope", "trade_id", "opportunity_id", "entry_at_utc", "exit_at_utc", "mfe_r", "mae_r", "holding_hours"];
  const meta = current ? metaKeys.filter((key) => current[key] !== undefined).map((key) => ({key, value: cellText(current[key], key)})) : [];
  const image = current && (current.image_data_url || current.image_src || current.image_url);
  return <DataComponent id="sample-atlas" title="固定种子审查样本 · K线与六均线" queryId="samples"
    kind="custom" variant="card" sourceRows={samples} displayRows={matches} headerControls={controls}>
    <RichNarrative id="samples:intro" className="ma6-inline-narrative"
      value={"样本按上方筛选查看；抽样种子与审查范围随样本记录展示。要求覆盖多种结果类型，某组合没有盈利样本时明确缺样，不以其他组合的图片替代。当前载入 " + samples.length.toLocaleString("zh-CN") + " 条样本记录。"}/>
    {matches.length > 0 ? <>
      <div className="ma6-sample-controls">
        <Dropdown label="选择样本" showLabel value={idFor(current, matches.indexOf(current))}
          choices={matches.map(idFor)}
          choiceLabels={Object.fromEntries(matches.map((row, index) => {
            const id = idFor(row, index);
            const kind = labelValue(row.sample_kind ?? row.outcome, {winner: "盈利", loser: "亏损", unfilled: "未成交", invalidated: "失效"});
            return [id, id + " · " + (row.symbol || "—") + " · " + kind];
          }))}
          onChange={(value) => setFilters((state) => ({...state, selected: value}))}/>
      </div>
      {image ? <div className="ma6-candle-frame" data-reviewed-rows>
        <img src={image} alt={(current.symbol || "样本") + " " + (current.direction || "") + " 六均线与周线结构"} loading="lazy"/>
      </div> : <p className="ma6-empty" data-reviewed-rows>该样本暂未提供K线图；记录和绘图文件路径仍可从导出中检查。</p>}
      {meta.length > 0 && <DataTable rows={meta}
        columns={[{field: "key", label: "样本字段", renderCell: (value) => FIELD_LABELS[value] || value}, {field: "value", label: "记录值"}]}
        searchable={false} label="样本范围、种子与标识"/>}
      {bars.length > 0 && <DataComponent id="sample-bars" title="样本K线、六均线与周线绘图数据" queryId="samples"
        kind="table" variant="card" sourceRows={bars} displayRows={bars}>
        <DataTable rows={bars}
          columns={columnsFor(bars, ["time_utc", "timestamp_utc", "open_time", "open", "high", "low", "close", "sma20", "sma60", "sma120", "ema20", "ema60", "ema120", "monday_high", "monday_low", "upper_1618", "lower_1618", "entry", "stop", "target"])}
          label="样本绘图数据" searchable/>
      </DataComponent>}
    </> : <p className="ma6-empty" data-reviewed-rows>当前筛选没有样本。无样本组合保留为空，不以其他组合的图替代。</p>}
    <DownloadBar rows={matches} allRows={samples} filename="monday-ma6-review-samples"/>
  </DataComponent>;
}

export function ReportContent() {
  const {reviewedRows, appTitle, canEdit, mode, setAppTitle, snapshot} = useDataApp();
  const rules = reviewedRows("rules");
  const summary = reviewedRows("summary");
  const summaryMetadata = summary.filter((row) => row.record_type === "global");
  const sourceCoverage = summary.filter((row) => row.record_type === "asset");
  const metrics = reviewedRows("metrics");
  const funnel = reviewedRows("funnel");
  const opportunities = reviewedRows("opportunities");
  const orders = reviewedRows("orders");
  const trades = reviewedRows("trades");
  const fills = reviewedRows("fills");
  const samples = reviewedRows("samples");
  const [metricFilters, setMetricFilters] = useState({exit: "ALL", asset: "ALL", period: "all", dimension: "ALL"});
  const [ledgerFilters, setLedgerFilters] = useState({exit: "ALL", asset: "ALL", period: "ALL", direction: "ALL", population: "ALL"});
  const ready = Boolean(snapshot && snapshot.report && snapshot.report.resultRowsExist !== undefined
    ? snapshot.report.resultRowsExist : (metrics.length || funnel.length));
  const exportsReady = Boolean(snapshot && snapshot.report && snapshot.report.resultExportsComplete);
  const replayComplete = snapshot && snapshot.report ? snapshot.report.replayComplete : null;
  const replayProgress = snapshot && snapshot.report ? snapshot.report : {};
  const isPartialReplay = replayComplete === false;
  const replayCompletionUnconfirmed = replayComplete !== true && replayComplete !== false && ready;
  const progressLabel = Number.isFinite(replayProgress.completedSymbols) && Number.isFinite(replayProgress.expectedSymbols)
    ? `历史回放进行中 ${replayProgress.completedSymbols}/${replayProgress.expectedSymbols}`
    : "历史回放进行中";
  const status = isPartialReplay ? `${progressLabel} · 仅已完成品种`
    : replayComplete === true ? (ready ? (snapshot.buildStatus === "complete" ? "历史回放与报告核验已完成" : "历史回放已完成 · 结果待复核") : "历史回放已完成 · 暂无可显示汇总行")
      : replayCompletionUnconfirmed ? "回放完成状态未确认 · 暂不排名"
      : exportsReady ? (ready ? "结果已载入 · 待复核" : "结果导出已载入 · 请检查汇总行")
        : ready ? "部分结果已载入 · 待复核" : "策略规则与数据范围已整理";
  const displayTitle = isPartialReplay
    ? `周一 1.618 六均线回踩 · ${progressLabel}`
    : replayComplete === true
      ? "周一 1.618 六均线回踩 · 历史回放已完成"
      : "周一 1.618 六均线回踩策略研究";
  const heroTitle = canEdit && mode === "edit" && appTitle ? appTitle : displayTitle;
  const deckCopy = isPartialReplay
    ? `${progressLabel}。当前收益与对照统计只包含已完成品种，不作方案排名；完整范围以回放摘要中的 complete 标记为准。`
    : replayComplete === true
      ? "历史回放摘要已标记完成。本页展示策略规则、来源覆盖、对照口径、逐笔流水与审查样本；各项收益仍按表中毛／净 basis 和各自分母解释。"
      : "本页说明周一 1.618 触价后的六均线回踩规则、数据范围与回放状态。只有回放摘要明确标记 complete=true，结果才视为全量；当前没有结果行也不代表零交易或亏损。";
  const overviewStatusCopy = ready
    ? isPartialReplay
      ? `${progressLabel}。当前收益与对照统计只包含已完成品种，不能据此给三种方案排名。闭合交易前后期按实际成交柱开始时间 entry_interval_start 分组并剔除跨切点持仓；提交队列机会期望按 submitted_at 分期；首次触价漏斗按 first_touch_at 分期。三者分母不同，提交队列机会期望不是逐笔胜率。`
      : "结果表已载入。闭合交易前后期按实际成交柱开始时间 entry_interval_start 分组并剔除跨切点持仓；提交队列机会期望按 submitted_at 分期；首次触价漏斗按 first_touch_at 分期。三者分母不同，提交队列机会期望不是逐笔胜率。全期不做切点剔除；收益按毛／净basis及严格成交配对口径分别解读。"
    : isPartialReplay
      ? `${progressLabel}，但结果行尚未载入；这不代表零交易或零收益。当前不能作全池排名。`
      : replayComplete === true
        ? "回放摘要标记 complete=true，但当前没有可显示的汇总指标行；请检查结果导出。空表不解释为零交易或亏损。"
        : exportsReady
          ? "结果文件已载入，但没有可展示的指标行；请检查汇总导出。空表不解释为零交易或亏损。"
          : "当前数据包没有可展示的结果行；这不代表零机会、零成交或零收益。结果就绪后会按来源摘要标记完成状态。";
  const ledgers = {opportunities, orders, trades, fills};
  const filteredLedgers = Object.fromEntries(Object.entries(ledgers).map(([key, rows]) => [key, rowFilter(rows, ledgerFilters)]));
  const funnelRows = useMemo(() => rowFilter(funnel, metricFilters), [funnel, metricFilters]);
  const interpretationMarkdown = (typeof summaryMetadata[0]?.interpretation_markdown === "string" && summaryMetadata[0].interpretation_markdown.trim())
    || (typeof snapshot?.report?.interpretationMarkdown === "string" && snapshot.report.interpretationMarkdown.trim())
    || "";
  const summaryMetadataDisplay = summaryMetadata.map(({interpretation_markdown, ...row}) => row);
  const ledgerControls = (rows) => <div className="ma6-filter-row">
    {[
      ["exit", "退出方式", ["exit_mode", "exit_policy"], EXIT_LABELS],
      ["asset", "品种组", ["asset_group", "group", "asset"], ASSET_LABELS],
      ["period", "时段", ["period", "segment", "time_period"], PERIOD_LABELS],
      ["direction", "方向", ["direction", "side"], {1: "多头", "-1": "空头", long: "多", short: "空", 多: "多", 空: "空"}],
      ["population", "策略或对照", ["population"], {signal: "策略", random: "随机对照"}],
    ].map(([key, label, fields, labels]) => <Dropdown key={key} label={label} showLabel value={ledgerFilters[key]}
      {...filterChoices(rows, fields, labels)}
      onChange={(value) => setLedgerFilters((state) => ({...state, [key]: value}))}/>)}
  </div>;
  const sourceRowsByQuery = {rules, summary, metrics, funnel, opportunities, orders, trades, fills, samples};

  return <article className="report-content monday-ma6-report" aria-label="周一 1.618 六均线回踩策略研究报告">
    <header className="report-hero ma6-hero">
      <div className="ma6-kicker">SPIKE / MONDAY 1.618 / SIX-MA RETEST</div>
      <div className="ma6-hero-heading">
        <h1 data-data-app-title contentEditable={canEdit && mode === "edit"} suppressContentEditableWarning
          aria-label={canEdit && mode === "edit" ? "编辑报告标题" : undefined}
          onBlur={canEdit && mode === "edit" ? (event) => setAppTitle(event.currentTarget.textContent.trim() || displayTitle) : undefined}>{heroTitle}</h1>
        <span className={"ma6-status " + (replayComplete === true && ready ? "ma6-status-ready" : "ma6-status-pending")}>{status}</span>
      </div>
      <RichNarrative id="hero:deck" className="report-deck ma6-deck"
        value={deckCopy + " 策略来源：[Notion「小级别确认」](https://app.notion.com/p/4028856479af80039ef9fa7bf3703760)。"}
        sourcePreviews={{"https://app.notion.com/p/4028856479af80039ef9fa7bf3703760": {title: "小级别确认", summary: "Owner 确认首次周一 1.618 触价后观察；15分钟收盘站回六均线并连续守住三根；回踩均线包络后入场，止损置于包络外；空头镜像。累计失效、止损缓冲及目标配置以本轮研究配置为准。", source: "Notion", date: "2026-10-23", approvedForReport: true}}}/>
    </header>

    {(isPartialReplay || replayCompletionUnconfirmed) && <div className="ma6-progress-alert" role="status" data-replay-complete={String(replayComplete)}>
      <strong>{isPartialReplay ? progressLabel : "回放完成状态未确认"}</strong>
      <span>{isPartialReplay
        ? "当前收益与对照统计仅含已完成品种，不作方案排名；不以尚未完成的品种推断全池表现。回放摘要 complete=true 后，此提示才会清除。"
        : "数据表虽有行，但回放摘要尚未提供 complete=true；不能据此称为全量结果或进行全池方案排名。"}</span>
    </div>}

    <SectionNavigator sections={[
      {id: "strategy", label: "规则与因果定义"}, {id: "overview", label: "结果总览"},
      {id: "funnel", label: "机会漏斗与对照"}, {id: "breakdowns", label: "时间与分组"},
      {id: "samples", label: "K线样本"}, {id: "ledgers", label: "全量明细"}, {id: "methods", label: "数据与方法"},
    ]} placement="inline"/>

    <ReportSection id="strategy-evidence" title="六均线规则与确认来源" queryId="rules" sourceRows={rules} showHeading={false}>
      <Section id="strategy" title="01 / 首次 1.618 触价后，等六均线收复再做回踩">
        <RichNarrative id="strategy:narrative" className="ma6-narrative"
          value={"所有条件只读已完成的15分钟K线。多头在周一下方1.618首次触价后观察收盘回到六均线上方；空头是在上方首次触价后收盘跌回六均线下方，条件逐根严格判断。"}/>
        <EvidenceTable id="strategy-rules" title="当前确认的多空镜像规则" queryId="rules" sourceRows={rules} displayRows={rules}
          preferred={["section", "item", "long", "short", "detail"]} searchable={false}
          note="均线为SMA20/60/120与EMA20/60/120；由 yoyo.datasets.ma_rope_filter.add_six_mas 计算，只使用已完成K线。"/>
        <RichNarrative id="strategy:edges" className="ma6-narrative"
          value={"首次触价K线建立运行不利极值；其后每根完整15分钟K线严格刷新该极值时累计一次，仍在监控或挂单状态时累计第三次即失效。首次完整15分钟收盘严格站过六条均线后，之后连续三根完整收盘仍须保持在六均线同侧；失败则计数重置，每周每方向只使用首个有效序列。\n\n回踩挂单在后续完整15分钟收盘后重算：多头挂当时最高MA，空头挂最低MA。止损在最近未成交改单时置于六均线包络外0.5%，成交后冻结；成交后不因新极值撤单，也没有保本或追踪。退出分别为周一对侧全平、另一侧1.618全平、两目标各半。"}/>
      </Section>
    </ReportSection>

    <Section id="overview" title="02 / 三种退出方式的结果总览">
      {interpretationMarkdown && <ReportSection id="parent-interpretation" title="结果解读" queryId="metrics"
        queryIds={["summary", "metrics"]} sourceRowsByQuery={sourceRowsByQuery} showHeading={false}>
        <RichNarrative id="overview:interpretation" className="ma6-narrative" value={interpretationMarkdown}/>
      </ReportSection>}
      <ReportSection id="overview-explainer" title="结果说明" queryId="metrics" sourceRows={metrics} showHeading={false}>
        <RichNarrative id="overview:status" className="ma6-narrative"
          value={overviewStatusCopy}/>
      </ReportSection>
      <EvidenceTable id="global-summary" title="覆盖范围与生成状态" queryId="summary" sourceRows={summary} displayRows={summaryMetadataDisplay}
        preferred={["source_first", "source_last", "coverage_asset_count", "coverage_assets_ending_before_source_last", "complete", "completed_symbols", "expected_symbols", "source_timezone", "status", "generated_at"]}
        strictColumns
        note="覆盖范围来自 artifact summary.json；报告 asOf 只取其中的 source_last。该值是品种源尾的最新时点，不代表每个品种都覆盖到此时。"/>
      <EvidenceTable id="asset-source-coverage" title={"逐品种源覆盖范围 · " + sourceCoverage.length.toLocaleString("zh-CN") + " 个品种"} queryId="summary" sourceRows={summary} displayRows={sourceCoverage}
        preferred={["symbol", "first", "last", "source_rows", "opportunities", "submitted", "native_positions", "native_fills"]}
        note="每行对应 summary.json.coverage 中的一个品种记录；按末端时间搜索可检查不同品种的覆盖截止差异。"/>
      <MetricRows id="exit-comparison" title="退出方式 × BTC / ETH / XAUUSD / 山寨 × 全期 / 前期 / 后期"
        rows={metrics} selections={metricFilters} setSelections={setMetricFilters}/>
      <CompactSummary rows={metrics}/>
      <RichNarrative id="overview:definitions" className="ma6-narrative"
        value={"均值、总和和名义bp按每行 basis 解释，毛收益与扣费净收益分别列出。XAUUSD券商点差及费用未知，因此 net 字段为空、仅报毛收益，不将未知费用记为零。两目标各半由按原始数量各半的两条 native 目标腿线性合计，不存在保本交互，不重复回放。"}/>
    </Section>

    <Section id="funnel" title="03 / 从观察机会到成交，以及两种随机对照分母">
      <RichNarrative id="funnel:definitions" className="ma6-narrative"
        value={"漏斗区分首次触价、监控状态、四根收盘确认、提交挂单、撤销、未成交与成交；闭合和截尾在交易表单列；漏斗时段按 first_touch_at（首次触价）分组。挂单时机候选对照保留全部机会状态；attempt_* 提交队列机会期望按 submitted_at 分期，以合格提交机会为分母，未成交按零暴露计入期望，但不计为亏损或零收益交易，也不进入胜率、实际交易均值或止损统计。严格配对均值差只包含每个信号对应的三个随机对照均已成交、闭合且处于同一连续片段的完整组；其分母是3/3完整成交配对组。两种对照口径不可混用。"}/>
      <EvidenceTable id="opportunity-funnel" title="机会与订单处理漏斗" queryId="funnel" sourceRows={funnel} displayRows={funnelRows}
        preferred={["exit_mode", "asset_group", "period", "stage_order", "stage", "count", "n", "denominator", "rate"]}
        controls={<div className="ma6-filter-row">{[
          ["exit", "退出方式", ["exit_mode", "exit_policy"], EXIT_LABELS],
          ["asset", "品种组", ["asset_group", "group", "asset"], ASSET_LABELS],
          ["period", "时段", ["period", "segment", "time_period"], PERIOD_LABELS],
        ].map(([key, label, fields, labels]) => <Dropdown key={key} label={label} showLabel value={metricFilters[key]}
          {...filterChoices(funnel, fields, labels)}
          onChange={(value) => setMetricFilters((state) => ({...state, [key]: value}))}/>)}</div>}/>
      <MetricRows id="matched-controls" title="提交队列机会期望与严格3/3成交配对" rows={metrics}
        selections={metricFilters} setSelections={setMetricFilters} controlOnly/>
      <RichNarrative id="controls:scope" className="ma6-narrative"
        value={"随机候选按同币、同纽约周、同方向、滞后14根波动分桶筛选，并要求候选当时数据已完整预热、15分钟收盘越过六均线且限价几何有效。候选时点不要求连续三根守线确认，因此是匹配挂单时机对照，并非与策略完全同规则的反事实。attempt_* 对照统计是全部合格机会上的零暴露期望；严格 matched_* 配对仍只纳入策略与三个随机对照均实际成交、闭合并处于同一连续片段的完整3/3组。入场后止损、目标及成本同口径，少于三个完整对照的信号仍保留自身绝对结果。"}/>
    </Section>

    <Section id="breakdowns" title="04 / 年份、月份、多空、品种与前后期切片">
      <RichNarrative id="breakdowns:readme" className="ma6-narrative"
        value={"在下表的分解维度中查看年份、月份、方向和品种。统一展示BTC、ETH、黄金与山寨，并标记全期、2023-01-01 UTC前及其后。各组交易数量、覆盖和持仓分布不同；逐笔R合计不等于等权资金组合，也不会根据小样本切片事后挑交易时段。"}/>
      <MetricRows id="temporal-breakdowns" title="按年份 / 月份 / 多空 / 单品种切片" rows={metrics}
        selections={metricFilters} setSelections={setMetricFilters} showCuts/>
      <RichNarrative id="breakdowns:excursions" className="ma6-narrative"
        value={"结果表包含持仓范围内MFE、MAE、先有盈利浮盈后触发止损数量、最长连亏与持仓时长。这些字段来自实际成交输出，不含入场前浮盈，并剔除无法确定实际暴露范围的入场和退出5分钟K线，因此MFE/MAE及浮盈后止损数量是保守下限；R账本也不代表资金账户回撤或复利曲线。"}/>
    </Section>

    <Section id="samples" title="05 / 固定种子K线样本：赢家、亏损、未成交与失效都能复查">
      <SampleGallery samples={samples}/>
    </Section>

    <Section id="ledgers" title="06 / 全量机会、订单、交易及费用流水">
      <RichNarrative id="ledgers:readme" className="ma6-narrative"
        value={"下表保留策略与随机对照两类记录，可搜索、排序和按退出方式、品种组、时段、多空、策略或对照筛选，并可下载当前筛选或完整原始导出。未成交挂单、撤销与失效机会保留为流程结果，不进入交易盈亏分母；截尾持仓单独列出，不能当成正常止盈或止损闭合。明细时段按各表的事件时间筛选：机会用首次触价，订单用提交，交易和费用用实际入场；跨切点交易保留在明细，前后期汇总会剔除。"}/>
      {Object.entries(ledgers).map(([queryId, rows]) => {
        const titles = {opportunities: "全部机会与失效原因", orders: "提交、改单、撤销与未成交挂单", trades: "实际成交及闭合 / 截尾交易", fills: "逐笔成交与费用类别"};
        return <div className="ma6-ledger-block" key={queryId}>
          <EvidenceTable id={"ledger-" + queryId} title={titles[queryId]} queryId={queryId} sourceRows={rows}
            displayRows={filteredLedgers[queryId]} preferred={LEDGER_FIELDS[queryId]} strictColumns controls={ledgerControls(rows)}
            note={queryId === "fills"
              ? "加密逐笔费用字段按原生成交流水呈现；XAUUSD费用与点差未知，相关汇总只报毛收益。页面保留全部记录的审查列；原始全字段CSV在交付文件夹‘全部逐笔明细’。"
              : "保留源文件全部记录的常用审查列。页面CSV下载同口径；原始全字段及全部改单历史在交付文件夹‘全部逐笔明细’，可按ID关联。"}/>
          <DownloadBar rows={filteredLedgers[queryId]} allRows={rows} filename={"monday-ma6-" + queryId}/>
        </div>;
      })}
    </Section>

    <ReportSection id="method-evidence" title="数据来源与因果边界" queryId="rules"
      queryIds={["rules", "summary", "metrics", "funnel", "opportunities", "orders", "trades", "fills", "samples"]}
      sourceRowsByQuery={sourceRowsByQuery} showHeading={false}>
      <Section id="methods" title="07 / 数据来源、六均线暖机与质量边界">
        <RichNarrative id="methods:body" className="ma6-narrative"
        value={"**可复用数据与补充源。** 本轮沿用已登记的633个合格品种资产池（BTC、ETH、XAUUSD与排除稳定币、黄金代理和传统／指数资产后的山寨组），复用各品种已登记历史行情，不以当前时点当作覆盖终点。逐品种 first/last 保留在覆盖明细表；summary.json 的 source_last 是全体品种中最新的源尾，不能推成每个品种都覆盖到这一时间。多源重叠需按品种和时间戳逐条比对OHLCV；只有完全一致才可去重拼接，冲突必须隔离并报告，不能任选来源覆盖。各品种自然缺口保留，不插值造K线；recent supplement只补同一合格资产池的覆盖尾部，连续性与重叠校验未通过的数据不得生成信号。\n\n**六均线与暖机。** 均线来源为 yoyo.datasets.ma_rope_filter.add_six_mas：SMA由完整窗口rolling mean计算，EMA使用span并以adjust=False递推。信号只读完整15分钟收盘；SMA120至少需要120根有效完整收盘，六条均线齐备前不得确认。缺口两侧不能把此前计数当作连续守线；有效信号起点受各品种覆盖和暖机影响。\n\n**分期时间锚。** 闭合交易前期/后期按 entry_interval_start（实际成交柱开始时间）分组；entry_time 在账本中是保守的入场柱结束时间，跨2023-01-01 UTC切点的持仓从前后期剔除；全期保留完整可闭合样本，不做该切点剔除。attempt_* 是提交队列机会期望，按 submitted_at 分期；提交在切点前、退出跨过切点的样本不进入对应前期/后期机会期望。首次触价漏斗按 first_touch_at 分期。三个时点锚点不同，提交队列期望不是逐笔胜率或交易收益均值。\n\n**黄金时钟和异常。** XAUUSD来自HistData bid-only M1，使用纽约当地时钟解释后按DST转UTC；夏令时歧义或不存在的本地时间不静默挪动。黄金质量适配器排除包含冲突分钟的整个5分钟区间，分段后重新预热和发现周一锚点；受冲突影响的周一不作锚。常规休市和来源缺口仍然可见；成交量、点差与券商费用未知，因此黄金为 gross basis，费用与net指标为空而不是零。\n\n**费用与未建模项。** 加密逐次成交按记录的OKX Lv1研究情景计算：maker 0.02%、taker 0.05%单边。滑点、资金费和容量未建模；Binance历史价格配OKX费率仅是成本情景。half_each由两条原生数量各半的目标腿线性合成，没有保本交互，不另做第三次成交回放。\n\n**对照、缺失和结论范围。** 全部挂单时机对照保留合格候选的未成交、撤单、失效状态；严格配对仅用三个随机对照均成交、闭合并处于同一连续数据片段的信号组，缺一个就不进配对分母。未成交与未闭合不作为已实现盈亏。绝对净收益、相对随机差异、R与名义bp分别回答不同问题。结果只用于研究，不改变生产准入和默认参数。\n\n本页规则行来自本轮Notion与配置；逐笔来源、汇总文件、样本范围及随机种子均通过来源菜单查看。报告构建器仅装载研究结果导出文件，不重放行情或计算盈亏。"} />
      </Section>
    </ReportSection>
  </article>;
}
