-- =====================================================================
-- Stores Intelligence Assistant -- Database Schema
-- Stack: Supabase (PostgreSQL)
-- Note: Client-agnostic schema. Replace 'client_id' value at ingestion.
-- =====================================================================

CREATE TABLE IF NOT EXISTS client_materials (
  id              BIGSERIAL PRIMARY KEY,
  stock_code      TEXT NOT NULL,
  description     TEXT NOT NULL,
  category        TEXT,
  unit            TEXT DEFAULT 'kg',
  reorder_point   NUMERIC,
  lead_time_days  INTEGER,
  unit_cost_usd   NUMERIC,
  supplier        TEXT
);

CREATE TABLE IF NOT EXISTS client_stores (
  id                    BIGSERIAL PRIMARY KEY,
  client_id             TEXT NOT NULL,
  txn_date              DATE NOT NULL,
  stock_code            TEXT NOT NULL,
  description           TEXT NOT NULL,
  category              TEXT,
  month                 TEXT,
  year                  INTEGER,
  daily_issues          NUMERIC DEFAULT 0,
  opening_stock         NUMERIC DEFAULT 0,
  receipts              NUMERIC DEFAULT 0,
  transfer_from_sweets  NUMERIC DEFAULT 0,
  transfer_to_sweets    NUMERIC DEFAULT 0,
  syrup                 NUMERIC DEFAULT 0,
  canteen_others        NUMERIC DEFAULT 0,
  total_issues          NUMERIC DEFAULT 0,
  theoretical_closing   NUMERIC DEFAULT 0,
  physical_closing      NUMERIC DEFAULT 0,
  variance              NUMERIC DEFAULT 0,
  variance_flag         TEXT,
  unit_cost_usd         NUMERIC,
  value_usd             NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_client_stores_txn_date ON client_stores (txn_date DESC);
CREATE INDEX IF NOT EXISTS idx_client_stores_client_id ON client_stores (client_id);
CREATE INDEX IF NOT EXISTS idx_client_stores_stock_code ON client_stores (stock_code);