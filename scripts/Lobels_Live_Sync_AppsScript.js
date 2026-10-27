/**
 * LOBELS BISCUITS — LIVE SUPABASE SYNC
 * Netrisyl Insights
 * ============================================================
 * This script does TWO things:
 *   1. Rebuilds the DATA EXPORT tab from the monthly sheets
 *   2. Pushes that data straight into Supabase
 * So when you edit a monthly sheet, BOTH the dashboard
 * (reads Google Sheet) AND the chatbot (reads Supabase) update.
 *
 * SETUP (one time):
 * 1. Extensions → Apps Script
 * 2. Paste this whole script (replace what's there)
 * 3. Project Settings (gear icon) → Script Properties → add two properties:
 *      SUPABASE_URL          e.g. https://<project-ref>.supabase.co
 *      SUPABASE_SERVICE_KEY  the service_role / secret key
 *    (Never paste the key into this file.)
 * 4. Save → Run → syncToSupabase (approve permissions first run)
 * 5. Triggers (clock icon) → Add Trigger:
 *      Function: onEditSync
 *      Event: From spreadsheet → On edit
 *    Now every edit auto-syncs to Supabase.
 * ============================================================
 */

// ── CONFIG — Supabase credentials live in Script Properties ──
const TABLE        = "lobels_stores";
const CLIENT_ID    = "lobels";

const CONFIG = {
  year: 2026,
  months: ["JAN","FEB","MAR","APR","MAY","JUN",
           "JUL","AUG","SEP","OCT","NOV","DEC"],
  exportSheet: "DATA EXPORT",
  dataStartRow: 4,
  numMaterials: 88,
  cols: {
    stockCode: 2, description: 3, openingStock: 4, receipts: 5,
    transferFromSweets: 6, dayStart: 7, dayEnd: 37,
    transferToSweets: 38, syrup: 39, canteen: 40,
    totalIssues: 41, theoreticalClosing: 42,
    physicalClosing: 43, variance: 44,
  }
};

const CATEGORIES = {
  "1103":"Leaveners","1104":"Leaveners","1105":"Leaveners","1110":"Flavours",
  "1201":"Fats/Oils","1203":"Flavours","1204":"Chocolate","1205":"Chocolate",
  "1206":"Flavours","1207":"Additives","1208":"Colours","1209":"Chocolate",
  "1210":"Chocolate","1211":"Flour/Starch","1212":"Flavours","1213":"Enzymes",
  "1217":"Additives","1218":"Colours","1219":"Flavours","1221":"Chocolate",
  "1222":"Fats/Oils","1224":"Chocolate","1225":"Flavours","1226":"Fats/Oils",
  "1228":"Flavours","1227":"Flavours","1229":"Enzymes","1300":"Dairy",
  "1301":"Fats/Oils","1307":"Colours","1308":"Dairy","1309":"Colours",
  "1311":"Dairy","1409":"Flour/Starch","1504":"Fats/Oils","1505":"Flavours",
  "1511":"Flavours","1508":"Fats/Oils","1522":"Flavours","1523":"Flavours",
  "1538":"Flavours","1600":"Flour/Starch","1601":"Flour/Starch","1602":"Flour/Starch",
  "1603":"Flour/Starch","1604":"Flour/Starch","1605":"Flour/Starch","1606":"Flour/Starch",
  "1611":"Flour/Starch","1612":"Flour/Starch","1613":"Flour/Starch","1614":"Flour/Starch",
  "1615":"Flour/Starch","1703":"Spices","1704":"Sugar/Sweeteners","1706":"Sugar/Sweeteners",
  "1710":"Sugar/Sweeteners","1805":"Dairy","1807":"Dairy","1908":"Spices",
  "1909":"Spices","1910":"Spices","1911":"Additives","1915":"Fats/Oils",
  "1919":"Fats/Oils","2001":"Additives","2003":"Fats/Oils","2004":"Additives",
  "2006":"Sugar/Sweeteners","2008":"Sugar/Sweeteners","2012":"Additives","2014":"Additives",
  "2102":"Sugar/Sweeteners","2104":"Sugar/Sweeteners","2105":"Sugar/Sweeteners",
  "2201":"Sugar/Sweeteners","2203":"Additives","2206":"Leaveners","2207":"Leaveners",
  "2208":"Flour/Starch","2209":"Enzymes","2213":"Flavours","2339":"Flavours",
  "2340":"Flavours","2341":"Inclusions","2342":"Spices","2343":"Flavours","2344":"Flavours"
};

const DAYS_IN_MONTH = [31,28,31,30,31,30,31,31,30,31,30,31];
const MONTH_SHORT = ["Jan","Feb","Mar","Apr","May","Jun",
                     "Jul","Aug","Sep","Oct","Nov","Dec"];

function numOrZero(v) {
  if (v === "" || v === null || v === undefined) return 0;
  const n = parseFloat(String(v).replace(/,/g, ""));
  return isNaN(n) ? 0 : n;
}

// ── Build the flat rows from all monthly sheets ─────────────
function buildRows() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const allRows = [];

  CONFIG.months.forEach((monthName, monthIdx) => {
    const ws = ss.getSheetByName(`${monthName} ${CONFIG.year}`);
    if (!ws) return;
    const daysThisMonth = DAYS_IN_MONTH[monthIdx];
    const data = ws.getRange(CONFIG.dataStartRow, 1,
                             CONFIG.numMaterials, CONFIG.cols.variance).getValues();

    data.forEach(row => {
      const stockCode = String(row[CONFIG.cols.stockCode - 1]).trim();
      const description = String(row[CONFIG.cols.description - 1]).trim();
      if (!stockCode || stockCode === "" || stockCode === "nan") return;

      const opening   = numOrZero(row[CONFIG.cols.openingStock - 1]);
      const receipts  = numOrZero(row[CONFIG.cols.receipts - 1]);
      const tFrom     = numOrZero(row[CONFIG.cols.transferFromSweets - 1]);
      const tTo       = numOrZero(row[CONFIG.cols.transferToSweets - 1]);
      const syrup     = numOrZero(row[CONFIG.cols.syrup - 1]);
      const canteen   = numOrZero(row[CONFIG.cols.canteen - 1]);
      const totalIss  = numOrZero(row[CONFIG.cols.totalIssues - 1]);
      const theoClose = numOrZero(row[CONFIG.cols.theoreticalClosing - 1]);
      const physClose = numOrZero(row[CONFIG.cols.physicalClosing - 1]);
      const variance  = numOrZero(row[CONFIG.cols.variance - 1]);
      const category  = CATEGORIES[stockCode] || "Other";
      const flag = variance < -0.5 ? "LOSS" : variance > 0.5 ? "GAIN" : "OK";

      for (let day = 1; day <= daysThisMonth; day++) {
        const issues = numOrZero(row[CONFIG.cols.dayStart - 1 + (day - 1)]);
        if (issues <= 0) continue;
        const dd = String(day).padStart(2, "0");
        const dateStr = `${CONFIG.year}-${String(monthIdx+1).padStart(2,"0")}-${dd}`;

        allRows.push({
          client_id: CLIENT_ID,
          txn_date: dateStr,
          stock_code: stockCode,
          description: description,
          category: category,
          month: monthName,
          year: CONFIG.year,
          daily_issues: issues,
          opening_stock: opening,
          receipts: receipts,
          transfer_from_sweets: tFrom,
          transfer_to_sweets: tTo,
          syrup: syrup,
          canteen_others: canteen,
          total_issues: totalIss,
          theoretical_closing: theoClose,
          physical_closing: physClose,
          variance: variance,
          variance_flag: flag,
        });
      }
    });
  });
  return allRows;
}

// ── Also write the DATA EXPORT tab (for Looker dashboard) ───
function writeDataExportTab(rows) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(CONFIG.exportSheet);
  if (!sheet) return;
  const lastRow = sheet.getLastRow();
  if (lastRow >= 2) sheet.getRange(2, 1, lastRow - 1, 18).clearContent();
  if (rows.length === 0) return;
  const values = rows.map(r => [
    r.txn_date, r.stock_code, r.description, r.category, r.month, r.year,
    r.daily_issues, r.opening_stock, r.receipts, r.transfer_from_sweets,
    r.transfer_to_sweets, r.syrup, r.canteen_others, r.total_issues,
    r.theoretical_closing, r.physical_closing, r.variance, r.variance_flag
  ]);
  sheet.getRange(2, 1, values.length, 18).setValues(values);
}

// ── Push to Supabase: delete lobels rows, then insert ───────
function getSupabaseConfig() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty("SUPABASE_URL");
  const key = props.getProperty("SUPABASE_SERVICE_KEY");
  if (!url || !key) {
    throw new Error("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in " +
                    "Project Settings → Script Properties.");
  }
  return { url: url.replace(/\/+$/, ""), key: key };
}

function checkResponse(resp, what) {
  const code = resp.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error(`Supabase ${what} failed (HTTP ${code}): ` +
                    resp.getContentText().slice(0, 300));
  }
}

function pushToSupabase(rows) {
  const { url: SUPABASE_URL, key: SUPABASE_KEY } = getSupabaseConfig();
  const headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
  };

  // 1. Delete existing lobels rows
  checkResponse(UrlFetchApp.fetch(
    `${SUPABASE_URL}/rest/v1/${TABLE}?client_id=eq.${CLIENT_ID}`,
    { method: "delete", headers: headers, muteHttpExceptions: true }), "delete");

  // 2. Insert in batches of 500
  const BATCH = 500;
  for (let i = 0; i < rows.length; i += BATCH) {
    const chunk = rows.slice(i, i + BATCH);
    checkResponse(UrlFetchApp.fetch(
      `${SUPABASE_URL}/rest/v1/${TABLE}`,
      { method: "post", headers: headers,
        payload: JSON.stringify(chunk), muteHttpExceptions: true }),
      `insert (rows ${i}-${i + chunk.length})`);
  }
}

// ── Main: rebuild tab + push to Supabase ────────────────────
function syncToSupabase() {
  const rows = buildRows();
  writeDataExportTab(rows);
  pushToSupabase(rows);
  SpreadsheetApp.getActiveSpreadsheet().toast(
    `Synced ${rows.length} rows to Supabase + DATA EXPORT.`,
    "Netrisyl Sync", 5);
}

// ── Auto-trigger on edit of a monthly sheet ─────────────────
function onEditSync(e) {
  const name = e.source.getActiveSheet().getName();
  const isMonthly = CONFIG.months.some(m => name.startsWith(m));
  if (!isMonthly) return;
  syncToSupabase();
}

// ── Menu for manual refresh ─────────────────────────────────
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Netrisyl")
    .addItem("Sync now (Supabase + Dashboard)", "syncToSupabase")
    .addToUi();
}
