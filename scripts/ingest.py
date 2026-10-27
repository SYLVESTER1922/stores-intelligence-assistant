"""
Load the Lobels stores register workbook into Supabase.

Reads the DATA EXPORT and Materials Master tabs and replaces the
client's rows in lobels_stores and all rows in lobels_materials.

    python scripts/ingest.py --file Stores_Raw_Materials_Register_2026_v2.xlsx --dry-run
    python scripts/ingest.py --file Stores_Raw_Materials_Register_2026_v2.xlsx

Env vars (live run only): SUPABASE_URL, SUPABASE_SERVICE_KEY
Deps: openpyxl, and supabase for the live run.
"""

import argparse
import os
import sys
from collections import Counter
from datetime import datetime

import openpyxl

CLIENT_ID = "lobels"
BATCH = 500

# DATA EXPORT header -> (column, kind)
STORE_COLS = {
    "DATE":                 ("txn_date", "date"),
    "STOCK_CODE":           ("stock_code", "code"),
    "DESCRIPTION":          ("description", "text"),
    "CATEGORY":             ("category", "text"),
    "MONTH":                ("month", "text"),
    "YEAR":                 ("year", "int"),
    "DAILY_ISSUES":         ("daily_issues", "num3"),
    "OPENING_STOCK":        ("opening_stock", "num3"),
    "RECEIPTS":             ("receipts", "num3"),
    "TRANSFER_FROM_SWEETS": ("transfer_from_sweets", "num3"),
    "TRANSFER_TO_SWEETS":   ("transfer_to_sweets", "num3"),
    "SYRUP":                ("syrup", "num3"),
    "CANTEEN_OTHERS":       ("canteen_others", "num3"),
    "TOTAL_ISSUES":         ("total_issues", "num3"),
    "THEORETICAL_CLOSING":  ("theoretical_closing", "num3"),
    "PHYSICAL_CLOSING":     ("physical_closing", "num3"),
    "VARIANCE":             ("variance", "num3"),
    "VARIANCE_FLAG":        ("variance_flag", "text"),
    "UNIT_COST_USD":        ("unit_cost_usd", "num4"),
    "VALUE_USD":            ("value_usd", "num2"),
}

MATERIAL_COLS = {
    "STOCK_CODE":       ("stock_code", "code"),
    "DESCRIPTION":      ("description", "text"),
    "CATEGORY":         ("category", "text"),
    "REORDER_POINT_KG": ("reorder_point", "num3"),
    "UNIT_COST_USD":    ("unit_cost_usd", "num4"),
    "LEAD_TIME_DAYS":   ("lead_time_days", "int"),
    "SUPPLIER":         ("supplier", "text"),
    "NOTES":            ("notes", "text"),
}


def convert(v, kind):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if kind == "date":
        return (v if isinstance(v, datetime) else datetime.fromisoformat(str(v))).date().isoformat()
    if kind == "code":
        return str(int(v)) if isinstance(v, (int, float)) else str(v).strip()
    if kind == "text":
        return str(v).strip()
    if kind == "int":
        return int(float(v))
    return round(float(v), {"num2": 2, "num3": 3, "num4": 4}[kind])


def read_sheet(wb, name, header_row, mapping):
    """Return rows as dicts keyed by DB column; rows with no stock code are skipped."""
    ws = wb[name]
    rows = ws.iter_rows(min_row=header_row, values_only=True)
    header = [str(h).strip().upper() if h else "" for h in next(rows)]
    missing = [h for h in mapping if h not in header]
    if missing:
        sys.exit(f"{name}: missing columns {missing}")
    idx = {h: header.index(h) for h in mapping}
    out = []
    for r in rows:
        r = tuple(r) + (None,) * (len(header) - len(r))  # read-only mode trims trailing blanks
        code = r[idx["STOCK_CODE"]]
        if code is None or str(code).strip() == "":
            continue
        out.append({col: convert(r[idx[h]], kind) for h, (col, kind) in mapping.items()})
    return out


def load(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    stores = read_sheet(wb, "DATA EXPORT", 1, STORE_COLS)
    materials = read_sheet(wb, "Materials Master", 2, MATERIAL_COLS)
    # The sheet ends with a free-text note whose first cell is not a stock code.
    materials = [m for m in materials if m["stock_code"].isdigit()]
    for s in stores:
        s["client_id"] = CLIENT_ID
        # unit_cost_usd / value_usd may be blank in the export
    for m in materials:
        m["unit"] = "kg"
    return stores, materials


def report(stores, materials):
    print(f"lobels_stores:    {len(stores)} rows")
    for mth, n in Counter(s["month"] for s in stores).items():
        print(f"    {mth}: {n}")
    print(f"lobels_materials: {len(materials)} rows")

    problems = []
    descs = Counter(m["description"] for m in materials)
    dup_desc = [d for d, n in descs.items() if n > 1]
    if dup_desc:
        problems.append(f"duplicate material descriptions (the app joins on description): {dup_desc}")
    dup_codes = [c for c, n in Counter(m["stock_code"] for m in materials).items() if n > 1]
    if dup_codes:
        print(f"WARNING: stock codes shared by different materials in Materials Master: {dup_codes}")
    master = {m["description"] for m in materials}
    orphans = sorted({s["description"] for s in stores} - master)
    if orphans:
        print(f"WARNING: materials in DATA EXPORT with no Materials Master row: {orphans}")
    unused = sorted(master - {s["description"] for s in stores})
    if unused:
        print(f"NOTE: Materials Master rows with no issues in DATA EXPORT: {unused}")
    return problems


def insert_all(sb, table, rows):
    for i in range(0, len(rows), BATCH):
        sb.table(table).insert(rows[i:i + BATCH]).execute()
        print(f"  {table}: inserted {min(i + BATCH, len(rows))}/{len(rows)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="path to the register .xlsx")
    ap.add_argument("--dry-run", action="store_true", help="parse and validate only; no database access")
    args = ap.parse_args()

    stores, materials = load(args.file)
    problems = report(stores, materials)
    if problems:
        sys.exit("ERROR: " + "; ".join(problems))
    if args.dry_run:
        print("Dry run OK, nothing written.")
        return

    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    # Replace existing data. Not atomic: if an insert fails, re-run to recover.
    sb.table("lobels_stores").delete().eq("client_id", CLIENT_ID).execute()
    insert_all(sb, "lobels_stores", stores)
    sb.table("lobels_materials").delete().gt("id", 0).execute()
    insert_all(sb, "lobels_materials", materials)

    n_s = sb.table("lobels_stores").select("id", count="exact").eq("client_id", CLIENT_ID).execute().count
    n_m = sb.table("lobels_materials").select("id", count="exact").execute().count
    print(f"Verify: lobels_stores={n_s} (expected {len(stores)}), lobels_materials={n_m} (expected {len(materials)})")
    if (n_s, n_m) != (len(stores), len(materials)):
        sys.exit("ERROR: row counts do not match")
    print("Ingestion complete.")


if __name__ == "__main__":
    main()
