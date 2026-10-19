/* Pure learning-workspace contracts: legacy storage, strict prices and causal replay.
 * No exchange API, strategy eligibility or financial execution is involved. */
((host) => {
  "use strict";
  const record = (v) => !!v && typeof v === "object" && !Array.isArray(v);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function number(value) {
    const s = String(value ?? "").trim();
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(s)) return null;
    const n = Number(s); return Number.isFinite(n) ? n : null;
  }
  function highlight(value, query) {
    const s = String(value ?? ""), q = String(query || "").trim();
    if (!q) return esc(s);
    const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    let at = 0, out = "";
    for (const match of s.matchAll(re)) { out += esc(s.slice(at, match.index)) + `<mark>${esc(match[0])}</mark>`; at = match.index + match[0].length; }
    return out + esc(s.slice(at));
  }
  function validatePlan(f) {
    const errors = [];
    const date = new Date(`${f.monday}T00:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.monday || "") || !Number.isFinite(date.valueOf()) || date.toISOString().slice(0,10) !== f.monday || date.getUTCDay() !== 1) errors.push("周一日期必须是有效的星期一（按计划中注明的时区）。");
    if (!String(f.coin || "").trim()) errors.push("请填写币种。");
    if (!["long","short"].includes(f.side)) errors.push("请选择有效方向。");
    const h = number(f.high), l = number(f.low), e = number(f.entry), s = number(f.stop);
    const empty = (v) => String(v ?? "").trim() === "";
    if (!(f.kind === "no_trade" && empty(f.high) && empty(f.low))) {
      if (h == null || l == null || h <= 0 || l <= 0 || h <= l) errors.push("周一高低点必须是完整正数，且高点大于低点。");
    }
    if (!empty(f.entry) || !empty(f.stop) || f.kind === "trade") {
      if (e == null || s == null || e <= 0 || s <= 0) errors.push("入场价和止损价需要同时填写完整正数。");
      else if ((e - s) * (f.side === "long" ? 1 : -1) <= 0) errors.push("多单止损须低于入场价，空单止损须高于入场价。");
    }
    if (!empty(f.result) && number(f.result) == null) errors.push("结果 R 必须是完整数字，例如 -1 或 1.6。");
    if (f.kind !== "trade" && !empty(f.result)) errors.push("只有逐笔交易记录可以填写结果 R；计划和未交易记录保持为空。");
    return errors;
  }
  function calculate(f) {
    const h = number(f.high), l = number(f.low), e = number(f.entry), s = number(f.stop);
    if (h == null || l == null || l <= 0 || h <= l) return null;
    const r = h-l, long = f.side === "long", sg = long ? 1 : -1;
    const tpFar = long ? l+.618*r : h-.618*r, tpNear = long ? h-.618*r : l+.618*r;
    const tp2 = long ? h+.13*r : l-.13*r, ext113 = long ? l-.13*r : h+.13*r;
    const out = {range_pct:r/l,tpFar,tpNear,tp2,ext113};
    if (e > 0 && s > 0 && (e-s)*sg > 0) {
      const risk = Math.abs(e-s);
      Object.assign(out,{risk_pct:risk/e,cost_r:.002*e/risk,rFar:(tpFar-e)*sg/risk,rNear:(tpNear-e)*sg/risk,r2:(tp2-e)*sg/risk});
    }
    return out;
  }
  const safeEntries = (obj) => Object.entries(obj).filter(([key]) => !["__proto__","prototype","constructor"].includes(key));
  function normalize(input, strict = false) {
    const v = input?.store ?? input;
    if (!record(v) || (strict && !record(v.chapters))) throw new Error("备份需要 chapters 对象，不能是空值或数组。");
    for (const key of ["chapters","plans","trash","attempts","learning","replay"]) if (v[key] != null && !record(v[key])) throw new Error(`${key} 必须是对象。`);
    const chapters = {};
    for (const [id,p] of safeEntries(v.chapters || {})) {
      if (!record(p) || !["unread","reading","done"].includes(p.status) || (p.notes != null && typeof p.notes !== "string")) throw new Error(`章节 ${id} 格式无效。`);
      chapters[id] = {...p, notes: String(p.notes || "").slice(0,20000)};
    }
    const plans = {};
    for (const [key,p] of safeEntries(v.plans || {})) {
      if (!record(p)) throw new Error(`记录 ${key} 格式无效。`);
      const id = typeof p.id === "string" && p.id ? p.id : `legacy-${key}`;
      if(plans[id])throw new Error(`记录 ID ${id} 重复，请先核对备份。`);
      const kind=p.kind || (String(p.result ?? "").trim() ? "trade" : "plan");
      if(strict) {
        if(!["plan","trade","no_trade"].includes(kind))throw new Error(`记录 ${key} 类型无效。`);
        for(const field of ["monday","coin","high","low","entry","stop","notes","result"])if(p[field]!=null && !["string","number"].includes(typeof p[field]))throw new Error(`记录 ${key} 的 ${field} 格式无效。`);
        const errors=validatePlan({...p,kind});if(errors.length)throw new Error(`记录 ${key}：${errors[0]}`);
      }
      plans[id] = {...p,id,kind,checks:record(p.checks)?p.checks:{}};
    }
    const attempts={};
    for(const [key,a] of safeEntries(v.attempts||{})) {
      if(!record(a) || !["messages","candles"].includes(a.mode) || typeof a.reason!=="string" || typeof a.saved_at!=="string" || typeof a.cutoff!=="string")throw new Error(`练习 ${key} 格式无效。`);
      attempts[key]={...a,reason:a.reason.slice(0,8000)};
    }
    for(const [key,p] of safeEntries(v.trash||{}))if(!record(p))throw new Error(`回收站 ${key} 格式无效。`);
    return {version:3,chapters,plans,trash:record(v.trash)?v.trash:{},last:typeof v.last === "string"?v.last:null,
      draft:record(v.draft)?v.draft:null,attempts,learning:record(v.learning)?v.learning:{},replay:record(v.replay)?v.replay:{}};
  }
  function merge(current, incoming) {
    const a = normalize(current), b = normalize(incoming,true), chapters = {...b.chapters,...a.chapters};
    for (const [id,p] of safeEntries(b.chapters)) {
      const old = a.chapters[id];
      if (!old || (old.status === "unread" && !old.notes)) chapters[id] = p;
    }
    const plans={...b.plans,...a.plans},trash={...b.trash,...a.trash};
    for(const id of Object.keys(a.trash))if(!a.plans[id])delete plans[id];
    for(const id of Object.keys(plans))delete trash[id];
    return {...a,chapters,plans,trash,attempts:{...b.attempts,...a.attempts},learning:{...b.learning,...a.learning}};
  }
  function visibleBars(bars, cursor) { return bars.slice(0,Math.max(0,Math.min(bars.length,Math.floor(Number(cursor)||0)))); }
  function domain(bars, cursor) {
    const seen = visibleBars(bars,cursor).slice(-90);
    if (!seen.length) return [0,1];
    const low = Math.min(...seen.map(b=>b.low)), high = Math.max(...seen.map(b=>b.high)), pad = (high-low)*.08 || 1;
    return [low-pad,high+pad];
  }
  const api = {number,highlight,validatePlan,calculate,normalize,merge,visibleBars,domain};
  host.SpikeRektproofCore = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
