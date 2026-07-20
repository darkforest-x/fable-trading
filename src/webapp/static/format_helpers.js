/**
 * Pure display helpers for the dashboard (Beijing time, lag labels).
 * CommonJS-friendly for unit tests; also attaches to globalThis.FableFmt.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.FableFmt = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const BJ_OFFSET_MS = 8 * 3600 * 1000;

  function fmtBjTime(input, opts) {
    const seconds = opts && opts.seconds;
    if (input == null || input === "") return "—";
    let d;
    if (typeof input === "number") {
      const ms = input < 1e12 ? input * 1000 : input;
      d = new Date(ms);
    } else if (input instanceof Date) {
      d = input;
    } else {
      let s = String(input).trim();
      if (/^\d{4}-\d{2}-\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
        s = s.replace(" ", "T");
        if (!s.endsWith("Z")) s += "Z";
      }
      d = new Date(s);
    }
    if (Number.isNaN(d.getTime())) {
      return String(input).slice(0, 16).replace("T", " ");
    }
    const bj = new Date(d.getTime() + BJ_OFFSET_MS);
    const p = (n) => String(n).padStart(2, "0");
    let out =
      bj.getUTCFullYear() +
      "-" +
      p(bj.getUTCMonth() + 1) +
      "-" +
      p(bj.getUTCDate()) +
      " " +
      p(bj.getUTCHours()) +
      ":" +
      p(bj.getUTCMinutes());
    if (seconds) out += ":" + p(bj.getUTCSeconds());
    return out;
  }

  function fmtChartTime(t) {
    if (t == null) return "";
    return fmtBjTime(typeof t === "number" ? t : Number(t));
  }

  function chartTickMarkBj(time) {
    if (time == null) return "";
    if (typeof time === "object" && time.year != null) {
      const p = (n) => String(n).padStart(2, "0");
      return time.year + "-" + p(time.month) + "-" + p(time.day);
    }
    const s = fmtBjTime(typeof time === "number" ? time : Number(time));
    return s.length >= 16 ? s.slice(5, 16) : s;
  }

  /** Human lag: minutes → "12m" / "1.5h" / "—" */
  function fmtLagMin(lagMin, freshMax) {
    const max = freshMax == null ? 20 : Number(freshMax);
    if (lagMin == null || lagMin === "" || Number.isNaN(Number(lagMin))) {
      return { text: "—", fresh: false, cls: "" };
    }
    const n = Number(lagMin);
    const fresh = n <= max;
    const text = n >= 60 ? (n / 60).toFixed(1) + "h" : Math.round(n) + "m";
    return { text: text + (fresh ? "" : " ·事后"), fresh: fresh, cls: fresh ? "pos" : "neg" };
  }

  function fmtPct(x, digits) {
    const d = digits == null ? 2 : digits;
    if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
    return (100 * x).toFixed(d) + "%";
  }

  function fmtPF(x) {
    if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
    return Number(x).toFixed(2);
  }

  return {
    BJ_OFFSET_MS: BJ_OFFSET_MS,
    fmtBjTime: fmtBjTime,
    fmtChartTime: fmtChartTime,
    chartTickMarkBj: chartTickMarkBj,
    fmtLagMin: fmtLagMin,
    fmtPct: fmtPct,
    fmtPF: fmtPF,
  };
});
