// Render the frozen symbol aggregation. No strategy inputs or controls are changed.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const dir = path.dirname(new URL(import.meta.url).pathname);
const output = path.join(dir, 'results_v1');
const result = JSON.parse(await fs.readFile(path.join(output, 'result.json'), 'utf8'));
const wb = Workbook.create();
const sh = wb.worksheets.add('币种排名');
sh.showGridLines = false;
sh.tabColor = '#243B53';
const start = 15, last = start + result.rows.length - 1;
sh.getRange(`A1:S${last}`).format.font = { name: 'Arial', size: 10, color: '#243B53' };
sh.getRange(`A1:S${last}`).format.rowHeight = 21;
sh.getRange(`A1:S${last}`).format.verticalAlignment = 'center';
sh.getRange('A2').values = [['SPIKE V13.1 · 15m / 1H 币种排名']];
sh.getRange('A2').format.font = { name: 'Arial', size: 15, bold: true, color: '#162B40' };
sh.getRange('A3').values = [['15m 从 2023-01-01 起；1H 含 19 笔 2022-12 入场。历史截止 2026-09-23 04:00 UTC。']];
sh.getRange('A4').values = [['按周期、多空分别累计净 R 降序；全样本，无最低笔数门槛。筛选表头可查看单组。']];
sh.getRange('A5').values = [['1H 为固定入场的 2ATR 退出重放，未重放提前出场后的新增入场；15m 沿用 4ATR。']];
sh.getRange('A6').values = [['已扣 0.2% 往返成本。胜率按净 R > 0；PF 按盈利 / 亏损净 R 总额，∞ 表示无亏损。']];
sh.getRange('A7').values = [['匹配超额 R 仅比较同币、同方向、同时间块与波动桶的已配对样本；缺失显示 n.a.。']];
sh.getRange('A8').values = [['累计 R 不是账户收益率。全历史排名不能直接作为事前选币规则。']];
sh.getRange('A9:M9').values = [['周期', '方向', '币种数', '范围', '交易次数', '胜率', '累计净R', '平均净R', 'PF', '匹配数', '对照均净R', '匹配超额R', '匹配覆盖']];
const headers = ['周期','方向','组内排名','币种','交易次数','胜率','累计净R','平均净R','PF','匹配数','对照均净R','匹配超额R','匹配覆盖'];
sh.getRange('A14:M14').values = [headers];
sh.getRange('O14:S14').values = [['盈利笔数','盈利净R总和','亏损净R绝对值','匹配信号净R总和','匹配随机净R总和']];
sh.getRange('N14').values = [['说明']];
sh.getRange('O9:S9').values = [['盈利笔数','盈利净R总和','亏损净R绝对值','匹配信号净R总和','匹配随机净R总和']];

function values(r, total = false) {
  return [r.timeframe,r.direction,total ? r.symbols : r.rank,total ? '全部币种' : r.symbol,
    r.trades,null,null,null,null,r.matched,null,null,null,r.pf_r==='inf' ? '无亏损交易' : (r.matched===0 ? '无匹配对照' : ''),
    r.wins,r.positive_net_r,r.negative_net_r_abs,r.matched_target_sum_net_r,r.matched_random_sum_net_r];
}
sh.getRange(`A${start}:S${last}`).values = result.rows.map(r => values(r));
sh.getRange('A10:S13').values = result.totals.map(r => values(r, true));
function formulas(row) {
  return [`=O${row}/E${row}`,`=P${row}-Q${row}`,`=G${row}/E${row}`,
    `=IF(Q${row}=0,IF(P${row}>0,"∞","n.a."),P${row}/Q${row})`];
}
sh.getRange(`F${start}:I${last}`).formulas = result.rows.map((_, i) => formulas(start+i));
sh.getRange('F10:I13').formulas = result.totals.map((_, i) => formulas(10+i));
function controls(row) {
  return [`=IF(J${row}=0,"n.a.",S${row}/J${row})`,
    `=IF(J${row}=0,"n.a.",(R${row}-S${row})/J${row})`,`=J${row}/E${row}`];
}
sh.getRange(`K${start}:M${last}`).formulas = result.rows.map((_, i) => controls(start+i));
sh.getRange('K10:M13').formulas = result.totals.map((_, i) => controls(10+i));

// Totals are calculated from the same additive group components, not averaged ratios.
for (let i = 0; i < result.totals.length; i++) {
  const t = result.totals[i], row = 10+i;
  for (const col of ['E','J','O','P','Q','R','S']) {
    sh.getRange(`${col}${row}`).formulas = [[`=SUMIFS(${col}$${start}:${col}$${last},$A$${start}:$A$${last},A${row},$B$${start}:$B$${last},B${row})`]];
  }
  assert(result.rows.filter(r => r.timeframe === t.timeframe && r.side === t.side).length === t.symbols);
}
// Include every row component in the table so an Excel sort cannot detach helpers.
const table = sh.tables.add(`A14:S${last}`, true, 'SymbolRankings');
table.showFilterButton = true;
table.style = 'TableStyleMedium2';
for (const range of ['A9:M9','A14:M14','O9:S9','O14:S14']) {
  sh.getRange(range).format.fill = '#243B53';
  sh.getRange(range).format.font = { name:'Arial', size:10, bold:true, color:'#FFFFFF' };
  sh.getRange(range).format.horizontalAlignment = 'center';
  sh.getRange(range).format.rowHeight = 26;
}
sh.getRange('A10:M13').format.fill = '#EDF2F7';
sh.getRange('A10:M13').format.font.bold = true;
for (const [col,width] of [['A',9],['B',9],['C',10],['D',23],['E',12],['F',12],['G',15],['H',14],['I',11],['J',11],['K',15],['L',15],['M',13],['N',18],['O',12],['P',20],['Q',20],['R',23],['S',23]]) {
  sh.getRange(`${col}1:${col}${last}`).format.columnWidth = width;
}
sh.getRange(`E10:S${last}`).format.horizontalAlignment = 'right';
sh.getRange(`G10:L${last}`).setNumberFormat('0.000');
sh.getRange(`P10:S${last}`).setNumberFormat('0.000000');
sh.getRange(`F10:F${last}`).setNumberFormat('0.00%');
sh.getRange(`M10:M${last}`).setNumberFormat('0.0%');
sh.getRange(`J10:J${last}`).setNumberFormat('#,##0');
sh.getRange(`E10:E${last}`).setNumberFormat('#,##0');
for (const col of ['G','H','L']) {
  sh.getRange(`${col}${start}:${col}${last}`).conditionalFormats.add('cellIs',
    {operator:'lessThan',formula:0,format:{font:{color:'#AD3030'}}});
}
sh.freezePanes.freezeRows(14);
sh.freezePanes.freezeColumns(4);
const provenance = [
  'N:S 为逐笔聚合底数，与主表同行排序，供公式复核。',
  `15m: ${result.config.source_15m}/trades.csv.gz`,
  `15m 对照: ${result.config.source_15m}/matched_controls.csv.gz`,
  `1H 及对照: ${result.config.source_1h}/trades.csv.gz`,
  '只取 both/closed 与 retest/trail2/incumbent parity 合格样本。',
  '历史快照；组内排名在导出时固定，筛选或重新排序不改变统计口径。'
];
sh.getRange(`A${last+3}:A${last+8}`).values = provenance.map(x=>[x]);
wb.recalculate();

const all = [...result.totals.map((r,i)=>[r,10+i]), ...result.rows.map((r,i)=>[r,start+i])];
for (const [r,row] of all) {
  const v=sh.getRange(`E${row}:M${row}`).values[0];
  for (const [index,key] of [[0,'trades'],[1,'win_rate'],[2,'sum_net_r'],[3,'mean_net_r'],[5,'matched'],[8,'matched_coverage']]) {
    assert(typeof v[index] === 'number' && Math.abs(v[index]-r[key]) < 1e-8, `mismatch ${row} ${key}`);
  }
  if (typeof r.pf_r === 'number') assert(Math.abs(v[4]-r.pf_r)<1e-8);
  if (r.matched) {
    assert(Math.abs(v[6]-r.random_mean_net_r)<1e-8);
    assert(Math.abs(v[7]-r.matched_excess_mean_net_r)<1e-8);
  }
}
// Verify a ratio responds to its component, then restore the historical snapshot.
const before=sh.getRange('O15').values[0][0];
sh.getRange('O15').values=[[before+1]];
assert(Math.abs(sh.getRange('F15').values[0][0]-(before+1)/result.rows[0].trades)<1e-12);
sh.getRange('O15').values=[[before]];
wb.recalculate();
const inspected=await wb.inspect({kind:'region',sheetId:sh.name,range:'A9:M18',maxChars:5000,tableMaxRows:10,tableMaxCols:13});
await fs.writeFile(path.join(output,'workbook-inspect.ndjson'),inspected.ndjson);
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},maxChars:2000});
await fs.writeFile(path.join(output,'workbook-errors.ndjson'),errors.ndjson);
const preview=await wb.render({sheetName:sh.name,range:'A1:M25',scale:1.5,format:'png'});
await fs.writeFile(path.join(output,'preview.png'),new Uint8Array(await preview.arrayBuffer()));
const xlsx=await SpreadsheetFile.exportXlsx(wb);
const filename=path.join(output,'SPIKE_V13.1_币种多空排名.xlsx');
await xlsx.save(filename);
console.log(JSON.stringify({file:filename,rows:result.rows.length,formulaValueChecks:all.length,errors:errors.ndjson}));
