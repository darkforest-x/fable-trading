/* Descriptive views of an explicitly selected result table, never new evidence. */
export const numeric = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value));
export function numericColumns(table) {
 return (table.columns || []).filter(key => {
  const values=(table.rows || []).map(row=>row[key]).filter(v=>v!==null&&v!==undefined&&String(v).trim()!=='');
  return values.length>=3 && values.every(numeric);
 });
}
export function correlation(table, columns) {
 const rows=(table.rows||[]).filter(row=>columns.every(c=>numeric(row[c])));
 const vectors=columns.map(c=>rows.map(row=>Number(row[c])));
 return {count:rows.length, columns, matrix:vectors.map(a=>vectors.map(b=>{
  if(rows.length<3)return null;
  const ma=a.reduce((n,v)=>n+v,0)/a.length,mb=b.reduce((n,v)=>n+v,0)/b.length;
  const da=a.map(v=>v-ma),db=b.map(v=>v-mb);
  const denom=Math.sqrt(da.reduce((n,v)=>n+v*v,0)*db.reduce((n,v)=>n+v*v,0));
  return denom?Math.max(-1,Math.min(1,da.reduce((n,v,i)=>n+v*db[i],0)/denom)):null;
 }))};
}
