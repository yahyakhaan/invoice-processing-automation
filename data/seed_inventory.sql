CREATE TABLE IF NOT EXISTS inventory (
    item TEXT PRIMARY KEY,
    stock INTEGER NOT NULL,
    unit_cost REAL
);
INSERT OR REPLACE INTO inventory VALUES ('WidgetA', 15, 250);
INSERT OR REPLACE INTO inventory VALUES ('WidgetB', 10, 500);
INSERT OR REPLACE INTO inventory VALUES ('GadgetX', 5, 750);
INSERT OR REPLACE INTO inventory VALUES ('FakeItem', 0, 1000);

CREATE TABLE IF NOT EXISTS vendors (
    canonical TEXT PRIMARY KEY,
    aliases TEXT
);
INSERT OR REPLACE INTO vendors VALUES (
    'QuickShip',
    '["QuickShip Distributers","QuickShip Distributors","FastShip Ltd.","FastShip"]'
);

CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT PRIMARY KEY,
    usd_per_unit REAL NOT NULL
);
INSERT OR REPLACE INTO fx_rates VALUES ('USD', 1.0);
INSERT OR REPLACE INTO fx_rates VALUES ('EUR', 1.08);

CREATE TABLE IF NOT EXISTS processed_invoices (
    invoice_number TEXT NOT NULL,
    revision TEXT,
    source_path TEXT,
    canonical_hash TEXT,
    outcome TEXT,
    run_id TEXT,
    created_at TEXT,
    PRIMARY KEY (invoice_number, revision, source_path)
);

CREATE TABLE IF NOT EXISTS human_decisions (
    thread_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    actor TEXT,
    rationale TEXT,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS payment_attempts (
    idempotency_key TEXT PRIMARY KEY,
    invoice_number TEXT,
    amount REAL,
    vendor TEXT,
    status TEXT,
    response TEXT,
    created_at TEXT
);
