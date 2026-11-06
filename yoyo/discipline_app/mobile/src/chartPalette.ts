/** One chart palette for every canvas (owner 2026-11-06 “色调保持统一”; Stitch fib-tone / trade-tone).
 * Red/green only for candles, volume, direction and P&L; reference lines are the app blue or neutral grey.
 * The same values are the CSS tokens in liquidGlass.css, so charts and glass UI read as one system. */
export const CHART={
 up:'#30d158',down:'#ff453a',
 upVolume:'rgba(48,209,88,.16)',downVolume:'rgba(255,69,58,.16)',
 accent:'#0a84ff',reference:'#8e8e93',mid:'rgba(142,142,147,.45)',
 entry:'#0a84ff',stop:'#ff453a',target:'#30d158',
 crosshair:'#8e8e93',label:'#3a3a3c',text:'#6e6e73',grid:'rgba(60,60,67,.07)',canvas:'#eef2f7',
} as const;
