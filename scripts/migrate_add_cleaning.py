#!/usr/bin/env python3
import sqlite3
import os

DB_PATH = os.getenv('DATABASE_URL', 'data/db.sqlite3')
if DB_PATH.startswith('sqlite:///'):
    dbfile = DB_PATH.replace('sqlite:///', '')
else:
    dbfile = DB_PATH

print('DB file:', dbfile)
conn = sqlite3.connect(dbfile)
cur = conn.cursor()

# helper to run SQL and ignore errors

def try_exec(sql):
    try:
        cur.execute(sql)
        conn.commit()
        print('Executed:', sql)
    except Exception as e:
        print('Skipped/failed:', sql, e)

# add boolean flag to expense
cur.execute("PRAGMA table_info(expense)")
cols = [row[1] for row in cur.fetchall()]
if 'is_cleaning' not in cols:
    try_exec("ALTER TABLE expense ADD COLUMN is_cleaning INTEGER DEFAULT 0;")
else:
    print('expense.is_cleaning already exists')

# add flag to company
cur.execute("PRAGMA table_info(company)")
cols = [row[1] for row in cur.fetchall()]
if 'is_cleaning_company' not in cols:
    try_exec("ALTER TABLE company ADD COLUMN is_cleaning_company INTEGER DEFAULT 0;")
else:
    print('company.is_cleaning_company already exists')

# create cleaning_service table if not present
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cleaning_service'")
if not cur.fetchone():
    try_exec(
        """
        CREATE TABLE cleaning_service (
            id INTEGER PRIMARY KEY,
            company_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            default_amount REAL DEFAULT 0.0,
            is_net INTEGER DEFAULT 0,
            vat_percent REAL DEFAULT 22.0,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
else:
    print('cleaning_service table already exists')

# create cleaning table
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cleaning'")
if not cur.fetchone():
    try_exec(
        """
        CREATE TABLE cleaning (
            id INTEGER PRIMARY KEY,
            apartment_id INTEGER NOT NULL,
            income_id INTEGER,
            company_id INTEGER NOT NULL,
            service_id INTEGER,
            date TEXT,
            gross_amount REAL DEFAULT 0.0,
            vat_percent REAL DEFAULT 22.0,
            net_amount REAL DEFAULT 0.0,
            is_net INTEGER DEFAULT 0,
            notes TEXT,
            expense_id INTEGER,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
else:
    print('cleaning table already exists')
    cur.execute("PRAGMA table_info(cleaning)")
    cols = [row[1] for row in cur.fetchall()]
    if 'income_id' not in cols:
        try_exec("ALTER TABLE cleaning ADD COLUMN income_id INTEGER;")

conn.close()
print('Done.')
