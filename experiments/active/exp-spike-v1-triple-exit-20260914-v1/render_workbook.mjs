// Research exports from the frozen bar replay; never reconstruct fills in XLSX.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [payloadPath, destination] = process.argv.slice(2);
if (!payloadPath || !destination) throw new Error('Usage: render_workbook.mjs payload.json output.xlsx');
const payload = JSON.parse(await fs.readFile(payloadPath, 'utf8'));
const wb = Workbook.create();
const previewDir = path.join(path.dirname(payloadPath), 'workbook_previews');
await fs.mkdir(previewDir, { recursive: true });
const previews = [];
function columnName(count) {
  let result = '';
  while (count > 0) { count--; result = String.fromCharCode(65 + count % 26) + result; count = Math.floor(count / 26); }
  return result;
}
for (const spec of payload.sheets) {
  const sheet = wb.worksheets.add(spec.name);
  sheet.showGridLines = false;
  const cols = spec.headers.length;
  const n = spec.rows.length;
  const area = sheet.getRangeByIndexes(0, 0, Math.max(n + 5, 8), cols);
  area.format.font = { name: 'Helvetica Neue', size: 11, color: '#243246' };
  area.format.rowHeight = 21;
  area.format.columnWidth = 16;
  sheet.getCell(0, 0).values = [[spec.title]];
  sheet.getCell(0, 0).format.font = { name: 'Helvetica Neue', size: 18, bold: true, color: '#172337' };
  sheet.getCell(1, 0).values = [[spec.subtitle || '']];
  sheet.getCell(1, 0).format.font = { italic: true, color: '#64748B', size: 10 };
  sheet.getRangeByIndexes(3, 0, 1, cols).values = [spec.headers];
  sheet.getRangeByIndexes(3, 0, 1, cols).format = {
    fill: '#253B57', font: { bold: true, color: '#FFFFFF', name: 'Helvetica Neue', size: 11 },
    rowHeight: 36, wrapText: true,
  };
  if (n) sheet.getRangeByIndexes(4, 0, n, cols).values = spec.rows;
  for (const [col, width] of Object.entries(spec.widths || {})) {
    sheet.getRangeByIndexes(0, Number(col), Math.max(n + 5, 8), 1).format.columnWidth = width;
  }
  for (const [col, fmt] of Object.entries(spec.formats || {})) {
    if (n) sheet.getRangeByIndexes(4, Number(col), n, 1).setNumberFormat(fmt);
  }
  for (const [col, formulas] of Object.entries(spec.formulas || {})) {
    if (formulas.length !== n) throw new Error('formula row count mismatch');
    sheet.getRangeByIndexes(4, Number(col), n, 1).formulas = formulas.map(x => [x]);
  }
  for (const col of spec.rColumns || []) {
    if (!n) continue;
    const range = sheet.getRangeByIndexes(4, col, n, 1);
    range.conditionalFormats.add('cellIs', { operator: 'lessThan', formula: 0, format: { font: { color: '#B33B43' } } });
    range.conditionalFormats.add('cellIs', { operator: 'greaterThan', formula: 0, format: { font: { color: '#15715A' } } });
  }
  if (n > 16) sheet.freezePanes.freezeRows(4);
  if (n && spec.filter !== false) sheet.tables.add(`A4:${columnName(cols)}${n + 4}`, true, `StudyTable${previews.length + 1}`);
  const filename = path.join(previewDir, `${String(previews.length + 1).padStart(2, '0')}.png`);
  const blob = await wb.render({ sheetName: spec.name, range: `A1:${String.fromCharCode(65 + Math.min(cols, 9) - 1)}${Math.min(n + 4, 15)}`, scale: 1.4 });
  await fs.writeFile(filename, new Uint8Array(await blob.arrayBuffer()));
  previews.push({ sheet: spec.name, filename });
}
console.log((await wb.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',
 options: { useRegex: true, maxResults: 30 }, maxChars: 3000 })).ndjson);
console.log((await wb.inspect({ kind: 'table', range: `${payload.sheets[0].name}!A4:I10`, include: 'values,formulas', tableMaxRows: 7, tableMaxCols: 9, maxChars: 4000 })).ndjson);
await fs.mkdir(path.dirname(destination), { recursive: true });
const out = await SpreadsheetFile.exportXlsx(wb);
await out.save(destination);
await fs.writeFile(path.join(previewDir, 'manifest.json'), JSON.stringify(previews, null, 2));
console.log(JSON.stringify({ output: destination, previews }));
