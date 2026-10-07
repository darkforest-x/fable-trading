import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import assert from "node:assert/strict";
import JSZip from "jszip";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const repositoryRoot = "/Users/zhangzc/fable-trading";
const experimentDir = path.join(
  repositoryRoot,
  "experiments/active/exp-spike-v128-retest-entry-20260923-v1",
);
const sourceDir = path.join(experimentDir, "symbol_detail_20260925_v2");
const outputDir = path.join(
  experimentDir,
  "user_delivery_20260925",
);
const outputName = "回测明细_20260723-20260923.xlsx";
const zipName = "回测明细_20260723-20260923.zip";
const outputPath = path.join(outputDir, outputName);
const zipPath = path.join(outputDir, zipName);
const receiptPath = path.join(outputDir, "delivery_receipt.json");

const sourceNames = [
  "逐币原版回踩对照.csv",
  "symbol_metrics.csv",
  "trade_details_beijing.csv",
];
const sourceReceiptPath = path.join(sourceDir, "receipt.json");
const sourceConfigPath = path.join(experimentDir, "config.json");

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function excelColumn(indexZeroBased) {
  let value = indexZeroBased + 1;
  let letters = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    letters = String.fromCharCode(65 + remainder) + letters;
    value = Math.floor((value - 1) / 26);
  }
  return letters;
}

function readCsvRows(csvText, sheetName) {
  const cleanText = csvText.replace(/^\uFEFF/, "");
  return Workbook.fromCSV(cleanText, { sheetName }).then((csvWorkbook) => {
    const matrix = csvWorkbook.worksheets
      .getItem(sheetName)
      .getUsedRange()
      .values;
    assert.ok(matrix.length > 0, sheetName + " parsed with no header row");
    const headers = matrix[0].map((value) => String(value ?? ""));
    const rows = matrix
      .slice(1)
      .filter((row) => row.some((value) => value !== null && value !== ""));
    assert.ok(
      rows.every((row) => row.length === headers.length),
      sheetName + " rows have inconsistent column counts",
    );
    return { headers, rows };
  });
}

function getColumnIndex(headers, name) {
  const index = headers.indexOf(name);
  assert.notEqual(index, -1, "Missing source column: " + name);
  return index;
}

function parseNumber(value, label) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  assert.ok(Number.isFinite(number), "Invalid number in " + label + ": " + value);
  return number;
}

function formatComparisonValue(header, value) {
  if (value === null || value === undefined || value === "") return null;
  if (header === "合约" || header === "模式") return String(value);
  return parseNumber(value, header);
}

function formatTradeValue(header, value) {
  if (value === null || value === undefined || value === "") return null;
  if (header === "censored") {
    return String(value).toLowerCase() === "true" ? "是" : "否";
  }

  const translatedCategories = {
    arm: {
      v9_both: "普通多空",
      joint: "联合多头",
    },
    policy: {
      baseline: "原版",
      retest: "回踩",
    },
    status: {
      closed: "已平仓",
      censored_boundary: "窗口边界截尾",
    },
    censored: {
      true: "是",
      false: "否",
    },
  };
  if (translatedCategories[header]) {
    const rawValue = String(value);
    return translatedCategories[header][rawValue] ?? rawValue;
  }

  const numericColumns = new Set([
    "timeframe_min",
    "side",
    "entry_price",
    "initial_stop",
    "exit_price",
    "net_r",
    "net_bp",
    "gross_bp",
  ]);
  if (numericColumns.has(header)) return parseNumber(value, header);
  return String(value);
}

function dateInBeijing(utcValue) {
  const timestamp = Date.parse(utcValue);
  assert.ok(Number.isFinite(timestamp), "Invalid UTC date: " + utcValue);
  return new Date(timestamp + 8 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 16)
    .replace("T", " ");
}

function equalCell(expected, actual, context) {
  if (expected === null || expected === undefined) {
    assert.ok(
      actual === null || actual === undefined || actual === "",
      context + " expected blank but got " + JSON.stringify(actual),
    );
    return;
  }
  if (typeof expected === "number") {
    assert.equal(typeof actual, "number", context + " should stay numeric");
    assert.ok(
      Math.abs(expected - actual) <= 1e-10,
      context + " mismatch: " + expected + " vs " + actual,
    );
    return;
  }
  assert.equal(actual, expected, context + " mismatch");
}

function compareSampleRows(sheet, sheetName, expectedRows, columnCount, samples) {
  return samples.map((rowIndex) => {
    const actualRow = sheet
      .getRangeByIndexes(rowIndex + 1, 0, 1, columnCount)
      .values[0];
    const expectedRow = expectedRows[rowIndex];
    expectedRow.forEach((expected, columnIndex) => {
      equalCell(
        expected,
        actualRow[columnIndex],
        sheetName + " row " + (rowIndex + 2) + " col " + (columnIndex + 1),
      );
    });
    return { data_row_index_zero_based: rowIndex, values_checked: columnCount };
  });
}

function setTableStyle(sheet, table, rowCount, columnCount, widths, columnKind) {
  const endColumn = excelColumn(columnCount - 1);
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;

  const header = sheet.getRange("A1:" + endColumn + "1");
  header.format.fill = "#1F4E78";
  header.format.font = {
    name: "Arial",
    size: 10,
    bold: true,
    color: "#FFFFFF",
  };
  header.format.horizontalAlignment = "center";
  header.format.verticalAlignment = "center";
  header.format.wrapText = true;
  header.format.rowHeight = 40;

  const body = sheet.getRangeByIndexes(1, 0, rowCount, columnCount);
  body.format.font = { name: "Arial", size: 10, color: "#1F2937" };
  body.format.verticalAlignment = "center";

  for (let column = 0; column < columnCount; column += 1) {
    const entireColumn = sheet.getRangeByIndexes(0, column, rowCount + 1, 1);
    entireColumn.format.columnWidth = widths[column];
    const dataColumn = sheet.getRangeByIndexes(1, column, rowCount, 1);
    dataColumn.format.horizontalAlignment =
      columnKind[column] === "text" ? "left" : "right";
    if (columnKind[column] === "integer") {
      dataColumn.format.numberFormat = "#,##0";
    } else if (columnKind[column] === "percent") {
      dataColumn.format.numberFormat = "0.00%";
    } else if (columnKind[column] === "bp") {
      dataColumn.format.numberFormat = "#,##0.00;(#,##0.00);0.00";
    } else if (columnKind[column] === "r") {
      dataColumn.format.numberFormat = "0.000;(0.000);0.000";
    } else if (columnKind[column] === "number") {
      dataColumn.format.numberFormat = "#,##0.00;(#,##0.00);0.00";
    }
  }
}

function compareColumnKind(header) {
  if (header === "合约" || header === "模式") return "text";
  if (header === "周期分钟") return "integer";
  if (header.includes("净胜率小数")) return "percent";
  if (header.toLowerCase().includes("bp")) return "bp";
  if (header.includes("R")) return "r";
  if (/K线数|断档数|候选数|确认数|入场数|已平仓数|截尾未完成数|笔数/.test(header)) {
    return "integer";
  }
  return "number";
}

const config = JSON.parse(await fs.readFile(sourceConfigPath, "utf8"));
const sourceReceipt = JSON.parse(await fs.readFile(sourceReceiptPath, "utf8"));
const sourceBuffers = new Map();
const sourceHashes = {};
for (const name of sourceNames) {
  const buffer = await fs.readFile(path.join(sourceDir, name));
  sourceBuffers.set(name, buffer);
  sourceHashes[name] = sha256(buffer);
  assert.equal(
    sourceHashes[name],
    sourceReceipt.files[name],
    name + " SHA-256 differs from source receipt",
  );
}
assert.deepEqual(
  sourceReceipt.source_config,
  config,
  "Experiment config differs from the config embedded in the source receipt",
);

const comparisonSource = await readCsvRows(
  sourceBuffers.get("逐币原版回踩对照.csv").toString("utf8"),
  "ComparisonSource",
);
const metricsSource = await readCsvRows(
  sourceBuffers.get("symbol_metrics.csv").toString("utf8"),
  "MetricsSource",
);
const tradeSource = await readCsvRows(
  sourceBuffers.get("trade_details_beijing.csv").toString("utf8"),
  "TradeSource",
);

assert.equal(sourceReceipt.symbols, 638);
assert.equal(comparisonSource.rows.length, 2552);
assert.equal(metricsSource.rows.length, 5104);
assert.equal(tradeSource.rows.length, 12131);

const readySymbolIndex = getColumnIndex(
  metricsSource.headers,
  "valid_ready_window_bars",
);
const metricSymbolIndex = getColumnIndex(metricsSource.headers, "symbol");
const readySymbols = new Set(
  metricsSource.rows
    .filter((row) => Number(row[readySymbolIndex] || 0) > 0)
    .map((row) => String(row[metricSymbolIndex])),
);
assert.equal(readySymbols.size, 583);

const comparisonColumns = {
  symbol: getColumnIndex(comparisonSource.headers, "合约"),
  timeframe: getColumnIndex(comparisonSource.headers, "周期分钟"),
  mode: getColumnIndex(comparisonSource.headers, "模式"),
};
const configurations = [
  {
    sheetName: "普通15m",
    tableName: "Ordinary15m",
    timeframe: "15",
    mode: "普通多空",
  },
  {
    sheetName: "普通1h",
    tableName: "Ordinary1h",
    timeframe: "60",
    mode: "普通多空",
  },
  {
    sheetName: "联合15m",
    tableName: "Joint15m",
    timeframe: "15",
    mode: "联合多头",
  },
  {
    sheetName: "联合1h",
    tableName: "Joint1h",
    timeframe: "60",
    mode: "联合多头",
  },
];

const sourceUniverse = new Set(
  comparisonSource.rows.map((row) =>
    String(row[comparisonColumns.symbol]),
  ),
);
assert.equal(sourceUniverse.size, 638);

const workbook = Workbook.create();
const expectedSheets = new Map();
const sheetSourceChecks = {};

for (const configSheet of configurations) {
  const sourceRows = comparisonSource.rows.filter(
    (row) =>
      String(row[comparisonColumns.timeframe]) === configSheet.timeframe &&
      String(row[comparisonColumns.mode]) === configSheet.mode,
  );
  assert.equal(
    sourceRows.length,
    638,
    configSheet.sheetName + " should include every archived contract",
  );
  assert.equal(
    new Set(sourceRows.map((row) => String(row[comparisonColumns.symbol]))).size,
    638,
    configSheet.sheetName + " contains duplicate contracts",
  );
  assert.deepEqual(
    new Set(sourceRows.map((row) => String(row[comparisonColumns.symbol]))),
    sourceUniverse,
    configSheet.sheetName + " contract universe differs from the source",
  );

  const outputRows = sourceRows.map((row) =>
    comparisonSource.headers.map((header, index) =>
      formatComparisonValue(header, row[index]),
    ),
  );
  const headers = comparisonSource.headers;
  const sheet = workbook.worksheets.add(configSheet.sheetName);
  const lastColumn = excelColumn(headers.length - 1);
  const lastRow = outputRows.length + 1;
  sheet.getRange("A1:" + lastColumn + lastRow).values = [
    headers,
    ...outputRows,
  ];
  const table = sheet.tables.add(
    "A1:" + lastColumn + lastRow,
    true,
    configSheet.tableName,
  );
  const kinds = headers.map(compareColumnKind);
  const widths = headers.map((header) => {
    if (header === "合约") return 15;
    if (header === "周期分钟") return 12;
    if (header === "模式") return 14;
    if (header.includes("bp")) return 17;
    if (header.includes("净胜率")) return 15;
    if (header.includes("R")) return 16;
    if (/K线数|断档数|候选数|确认数|入场数|已平仓数|截尾未完成数|笔数/.test(header)) {
      return 14;
    }
    return 16;
  });
  setTableStyle(sheet, table, outputRows.length, headers.length, widths, kinds);

  expectedSheets.set(configSheet.sheetName, {
    rows: outputRows,
    headers,
    sourceRows,
  });
}

const tradeHeaders = [
  "合约",
  "周期分钟",
  "模式",
  "入场方案",
  "交易唯一键",
  "方向代码",
  "信号锚点收盘时间（北京时间）",
  "信号收盘时间（北京时间）",
  "入场时间（北京时间）",
  "出场时间（北京时间）",
  "入场价格",
  "初始止损价",
  "出场价格",
  "出场原因代码",
  "交易状态",
  "窗口截尾",
  "净R",
  "净收益（bp）",
  "毛收益（bp）",
];
const tradeSourceHeaders = tradeSource.headers;
const tradeFieldMap = [
  "symbol",
  "timeframe_min",
  "arm",
  "policy",
  "trade_key",
  "side",
  "anchor_close",
  "signal_close",
  "entry_time",
  "exit_time",
  "entry_price",
  "initial_stop",
  "exit_price",
  "exit_reason",
  "status",
  "censored",
  "net_r",
  "net_bp",
  "gross_bp",
];
assert.equal(tradeHeaders.length, tradeSourceHeaders.length);
assert.deepEqual(
  tradeFieldMap,
  tradeSourceHeaders,
  "Trade sheet translation map must preserve source column order",
);
const tradeRows = tradeSource.rows.map((row) =>
  tradeFieldMap.map((field, index) => formatTradeValue(field, row[index])),
);
const tradeSheet = workbook.worksheets.add("逐笔交易");
const tradeLastColumn = excelColumn(tradeHeaders.length - 1);
const tradeLastRow = tradeRows.length + 1;
tradeSheet.getRange("A1:" + tradeLastColumn + tradeLastRow).values = [
  tradeHeaders,
  ...tradeRows,
];
const tradeTable = tradeSheet.tables.add(
  "A1:" + tradeLastColumn + tradeLastRow,
  true,
  "TradeDetails",
);
const tradeKinds = tradeFieldMap.map((field) => {
  if (
    [
      "timeframe_min",
      "side",
      "entry_price",
      "initial_stop",
      "exit_price",
      "net_r",
      "net_bp",
      "gross_bp",
    ].includes(field)
  ) {
    if (field === "net_r") return "r";
    if (field.endsWith("_bp")) return "bp";
    if (["timeframe_min", "side"].includes(field)) return "integer";
    return "number";
  }
  return "text";
});
const tradeWidths = [
  15, 12, 15, 13, 42, 12, 27, 27, 27, 27, 15, 15, 15, 22, 18, 12, 12, 17,
  17,
];
setTableStyle(
  tradeSheet,
  tradeTable,
  tradeRows.length,
  tradeHeaders.length,
  tradeWidths,
  tradeKinds,
);

const statusIndex = getColumnIndex(tradeSource.headers, "status");
const statusCounts = {};
for (const row of tradeSource.rows) {
  const status = String(row[statusIndex]);
  statusCounts[status] = (statusCounts[status] ?? 0) + 1;
}
assert.deepEqual(statusCounts, {
  closed: 11877,
  censored_boundary: 254,
});
expectedSheets.set("逐笔交易", {
  rows: tradeRows,
  headers: tradeHeaders,
  sourceRows: tradeSource.rows,
});

const aggregateRows = sourceReceipt.aggregate_checks.map((check) => [
  Number(check.timeframe_min),
  check.arm === "v9_both" ? "普通多空" : "联合多头",
  check.policy === "baseline" ? "原版" : "回踩",
  Number(check.closed),
  Number(check.matched),
]);
assert.equal(aggregateRows.length, 8);

const notes = workbook.worksheets.add("说明");
notes.showGridLines = false;
notes.getRange("A1").values = [["回测详细数据说明"]];
notes.getRange("A1").format.font = {
  name: "Arial",
  size: 15,
  bold: true,
  color: "#1F4E78",
};
notes.getRange("A3:B15").values = [
  ["统计范围", "Binance USDⓈ-M 归档合约，共 638 个合约。"],
  [
    "就绪数据",
    "583 个合约在 15m 或 60m 至少一个周期有就绪K线（valid_ready_window_bars > 0）。",
  ],
  [
    "数据窗口",
    "北京时间 " +
      dateInBeijing(config.start) +
      " 至 " +
      dateInBeijing(config.end) +
      "。",
  ],
  [
    "时间切分",
    "北京时间 " +
      dateInBeijing(config.split) +
      "。跨切分持仓单独统计，未并入早段或后段闭合笔数。",
  ],
  [
    "往返成本",
    Number(config.round_trip_cost * 10000) +
      " bp（" +
      (config.round_trip_cost * 100).toFixed(2) +
      "%），已计入净收益字段。",
  ],
  [
    "闭合统计",
    "收益统计使用已平仓交易。逐笔 CSV 共 11,877 条已平仓、254 条窗口边界截尾记录；截尾记录不计入闭合收益统计。",
  ],
  [
    "随机配对子集",
    "随机对照按同一方案的已平仓成交匹配。配对指标只覆盖 matched 笔数；未匹配值保持空白。逐币 p 值为探索性统计，未做跨合约多重校正。",
  ],
  [
    "空白与零",
    "没有交易时，收益和派生指标留空；数值 0 表示结果为零。没有交易与没有就绪行情是不同状态。",
  ],
  [
    "收益含义",
    "净R以每笔交易的初始风险为基准。净R合计是逐笔R之和，不代表账户或组合收益。",
  ],
  [
    "交易标签",
    "逐笔表模式：v9_both 对应普通多空，joint 对应联合多头；方案：baseline 对应原版，retest 对应回踩。时间戳保留 +08:00。",
  ],
  [
    "市场口径",
    "Binance USDⓈ-M 归档合约汇总，不代表 TradingView 原生数据 parity。",
  ],
  [
    "结果范围",
    "这是既有结果的回顾性整理，没有重新回测或计算模型分数、AUC、top-decile 选样。",
  ],
  [
    "来源",
    "config.json、receipt.json、逐币原版回踩对照.csv、symbol_metrics.csv、trade_details_beijing.csv。",
  ],
];
notes.getRange("D17").values = [
  ["分周期、模式与方案的闭合笔数及随机配对笔数（来源 receipt.json）"],
];
notes.getRange("D18:H26").values = [
  ["周期分钟", "模式", "方案", "已平仓笔数", "随机配对笔数"],
  ...aggregateRows,
];
const aggregateTable = notes.tables.add(
  "D18:H26",
  true,
  "AggregateClosedMatched",
);
aggregateTable.style = "TableStyleMedium2";
aggregateTable.showFilterButton = true;
notes.getRange("D18:H18").format.fill = "#1F4E78";
notes.getRange("D18:H18").format.font = {
  name: "Arial",
  size: 10,
  bold: true,
  color: "#FFFFFF",
};
notes.getRange("D18:H18").format.wrapText = true;
notes.getRange("D18:H18").format.horizontalAlignment = "center";
notes.getRange("D18:H18").format.verticalAlignment = "center";
notes.getRange("D18:H18").format.rowHeight = 32;
notes.getRange("A3:B15").format.font = {
  name: "Arial",
  size: 10,
  color: "#1F2937",
};
notes.getRange("A3:A15").format.font = {
  name: "Arial",
  size: 10,
  bold: true,
  color: "#1F4E78",
};
notes.getRange("A3:B15").format.wrapText = true;
notes.getRange("A3:B15").format.verticalAlignment = "top";
notes.getRange("A1:A26").format.columnWidthPx = 135;
notes.getRange("B1:B26").format.columnWidthPx = 600;
notes.getRange("C1:C26").format.columnWidthPx = 18;
notes.getRange("D1:D26").format.columnWidthPx = 130;
notes.getRange("E1:F26").format.columnWidthPx = 140;
notes.getRange("G1:H26").format.columnWidthPx = 165;
notes.getRange("D19:D26").format.numberFormat = "0";
notes.getRange("G19:H26").format.numberFormat = "#,##0";
notes.getRange("D17:H17").format.font = {
  name: "Arial",
  size: 11,
  bold: true,
  color: "#1F4E78",
};
notes.getRange("A3:B15").format.autofitRows();
  expectedSheets.set("说明", {
  rows: [
    ["回测详细数据说明"],
    ...notes.getRange("A3:B15").values,
    ...notes.getRange("D18:H26").values,
  ],
  headers: [],
  sourceRows: [],
});

assert.equal(configurations.length + 2, 6);

const workbookSummary = await workbook.inspect({
  kind: "sheet,table",
  maxChars: 6000,
  tableMaxRows: 3,
  tableMaxCols: 8,
});
console.log("WORKBOOK STRUCTURE");
console.log(workbookSummary.ndjson.slice(0, 1800));
for (const configSheet of configurations) {
  const check = await workbook.inspect({
    kind: "table",
    sheetId: configSheet.sheetName,
    range: "A1:H4",
    include: "values,formulas",
    tableMaxRows: 4,
    tableMaxCols: 8,
  });
  console.log("SAMPLE " + configSheet.sheetName);
  console.log(check.ndjson.slice(0, 500));
}
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log("FORMULA ERROR SCAN");
console.log(formulaErrors.ndjson);

const xlsxBlob = await SpreadsheetFile.exportXlsx(workbook);
await xlsxBlob.save(outputPath);

const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const comparisonSampleIndices = [0, 319, 637];
for (const configSheet of configurations) {
  const sheet = reopened.worksheets.getItem(configSheet.sheetName);
  const expected = expectedSheets.get(configSheet.sheetName);
  const values = sheet.getUsedRange().values;
  assert.equal(
    values.length,
    639,
    configSheet.sheetName + " should contain 638 data rows and one header",
  );
  assert.deepEqual(
    values[0],
    expected.headers,
    configSheet.sheetName + " headers changed on XLSX export",
  );
  assert.equal(sheet.tables.items.length, 1);
  sheetSourceChecks[configSheet.sheetName] = compareSampleRows(
    sheet,
    configSheet.sheetName,
    expected.rows,
    expected.headers.length,
    comparisonSampleIndices,
  );
}
{
  const sheet = reopened.worksheets.getItem("逐笔交易");
  const values = sheet.getUsedRange().values;
  assert.equal(values.length, 12132);
  assert.deepEqual(values[0], tradeHeaders);
  assert.equal(sheet.tables.items.length, 1);
  sheetSourceChecks["逐笔交易"] = compareSampleRows(
    sheet,
    "逐笔交易",
    tradeRows,
    tradeHeaders.length,
    [0, Math.floor(tradeRows.length / 2), tradeRows.length - 1],
  );
}
assert.equal(reopened.worksheets.getItem("说明").tables.items.length, 1);

const renderJobs = [
  ["普通15m", "A1:H8"],
  ["普通1h", "A1:H8"],
  ["联合15m", "A1:H8"],
  ["联合1h", "A1:H8"],
  ["逐笔交易", "A1:J8"],
  ["逐笔交易", "K1:S8"],
  ["说明", "A1:H26"],
];
for (let index = 0; index < renderJobs.length; index += 1) {
  const [sheetName, range] = renderJobs[index];
  const preview = await reopened.render({
    sheetName,
    range,
    scale: 1,
    format: "png",
  });
  const previewBytes = new Uint8Array(await preview.arrayBuffer());
  const safeSheetName = sheetName.replace(/[^A-Za-z0-9\u4e00-\u9fff]/g, "_");
  await fs.writeFile(
    path.join(outputDir, ".preview_" + safeSheetName + "_" + index + ".png"),
    previewBytes,
  );
}

const zip = new JSZip();
zip.file(outputName, await fs.readFile(outputPath));
for (const name of sourceNames) {
  zip.file(name, sourceBuffers.get(name));
}
zip.file("receipt.json", await fs.readFile(sourceReceiptPath));
zip.file("config.json", await fs.readFile(sourceConfigPath));
const zipBuffer = await zip.generateAsync({
  type: "nodebuffer",
  compression: "DEFLATE",
  compressionOptions: { level: 6 },
});
await fs.writeFile(zipPath, zipBuffer);

const outputHash = sha256(await fs.readFile(outputPath));
const zipHash = sha256(await fs.readFile(zipPath));
const deliveryReceipt = {
  generated_on: "2026-09-25",
  workbook: {
    path: outputPath,
    sha256: outputHash,
    sheets: [
      { name: "普通15m", header_rows: 1, data_rows: 638 },
      { name: "普通1h", header_rows: 1, data_rows: 638 },
      { name: "联合15m", header_rows: 1, data_rows: 638 },
      { name: "联合1h", header_rows: 1, data_rows: 638 },
      { name: "逐笔交易", header_rows: 1, data_rows: 12131 },
      { name: "说明", data_rows: 24 },
    ],
    sample_source_checks: sheetSourceChecks,
    filters_and_frozen_headers: true,
  },
  zip: {
    path: zipPath,
    sha256: zipHash,
    entries: [
      outputName,
      ...sourceNames,
      "receipt.json",
      "config.json",
    ],
  },
  source_sha256: sourceHashes,
  source_receipt: {
    symbols: sourceReceipt.symbols,
    comparison_rows: sourceReceipt.comparison_rows,
    metric_rows: sourceReceipt.metric_rows,
    trade_rows: tradeSource.rows.length,
    ready_symbols: readySymbols.size,
    trade_status_counts: statusCounts,
  },
};
await fs.writeFile(
  receiptPath,
  JSON.stringify(deliveryReceipt, null, 2) + "\n",
  "utf8",
);
await fs.rm(outputPath + ".inspect.ndjson", { force: true });
console.log("OUTPUT " + outputPath);
console.log("XLSX_SHA256 " + outputHash);
console.log("ZIP " + zipPath);
console.log("ZIP_SHA256 " + zipHash);
console.log("RECEIPT " + receiptPath);
