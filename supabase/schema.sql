-- =====================================================================
-- Lobels Stores Intelligence Assistant -- Database Schema
-- Stack: Supabase (PostgreSQL)
-- Run in Supabase -> SQL Editor. Safe to re-run.
--
-- Multi-client design: lobels_stores carries a client_id column, and the
-- app filters every query on it (CLIENT_ID = "lobels" in app.py).
-- lobels_materials is a global reference table with no client_id.
--
-- Access: the app and the sheet sync both use the service key, which
-- bypasses RLS. RLS is enabled with no policies, so the anon and
-- authenticated roles cannot read or write these tables.
-- =====================================================================

-- ── Daily transactions (one row per material per day with issues) ───
CREATE TABLE IF NOT EXISTS lobels_stores (
  id                    BIGSERIAL PRIMARY KEY,
  client_id             TEXT NOT NULL DEFAULT 'lobels',
  txn_date              DATE NOT NULL,
  stock_code            TEXT NOT NULL,
  description           TEXT NOT NULL,
  category              TEXT,
  month                 TEXT,
  year                  INTEGER,
  daily_issues          NUMERIC(14,3) DEFAULT 0,
  opening_stock         NUMERIC(14,3) DEFAULT 0,
  receipts              NUMERIC(14,3) DEFAULT 0,
  transfer_from_sweets  NUMERIC(14,3) DEFAULT 0,
  transfer_to_sweets    NUMERIC(14,3) DEFAULT 0,
  syrup                 NUMERIC(14,3) DEFAULT 0,
  canteen_others        NUMERIC(14,3) DEFAULT 0,
  total_issues          NUMERIC(14,3) DEFAULT 0,
  theoretical_closing   NUMERIC(14,3) DEFAULT 0,
  physical_closing      NUMERIC(14,3) DEFAULT 0,
  variance              NUMERIC(14,3) DEFAULT 0,
  variance_flag         TEXT,
  unit_cost_usd         NUMERIC(14,4),
  value_usd             NUMERIC(14,2)
);

-- ── Materials master (reference data; app reads unit_cost_usd, supplier,
--    reorder_point, lead_time_days) ───────────────────────────────────
--    stock_code is NOT unique: the source Materials Master reuses 1602 and
--    1604 for different materials, and the app joins on description.
CREATE TABLE IF NOT EXISTS lobels_materials (
  id              BIGSERIAL PRIMARY KEY,
  stock_code      TEXT NOT NULL,
  description     TEXT NOT NULL UNIQUE,
  category        TEXT,
  unit            TEXT DEFAULT 'kg',
  reorder_point   NUMERIC(14,3),
  lead_time_days  INTEGER,
  unit_cost_usd   NUMERIC(14,4),
  supplier        TEXT,
  notes           TEXT
);

-- ── Indexes for chatbot queries ─────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_lobels_client   ON lobels_stores (client_id);
CREATE INDEX IF NOT EXISTS idx_lobels_date     ON lobels_stores (txn_date DESC);
CREATE INDEX IF NOT EXISTS idx_lobels_code     ON lobels_stores (stock_code);
CREATE INDEX IF NOT EXISTS idx_lobels_month    ON lobels_stores (client_id, month);
CREATE INDEX IF NOT EXISTS idx_lobels_category ON lobels_stores (category);
CREATE INDEX IF NOT EXISTS idx_lobels_materials_desc ON lobels_materials (description);

-- ── Monthly totals per material (not used by app.py; handy for ad hoc SQL)
CREATE OR REPLACE VIEW lobels_monthly_summary
WITH (security_invoker = true) AS
SELECT
  client_id,
  stock_code,
  description,
  category,
  month,
  year,
  SUM(daily_issues) AS month_issues,
  AVG(variance)     AS avg_variance
FROM lobels_stores
GROUP BY client_id, stock_code, description, category, month, year;

-- ── Lock down: service key only ─────────────────────────────────────
ALTER TABLE lobels_stores    ENABLE ROW LEVEL SECURITY;
ALTER TABLE lobels_materials ENABLE ROW LEVEL SECURITY;
