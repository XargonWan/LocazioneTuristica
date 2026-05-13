#!/usr/bin/env python3
"""Migration: add default cleaning company to apartment and default cost fields to company."""
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


def try_exec(sql):
    try:
        cur.execute(sql)
        conn.commit()
        print('Executed:', sql)
    except Exception as e:
        print('Skipped/failed:', sql, e)


# Add default cost fields to company
cur.execute("PRAGMA table_info(company)")
cols = [row[1] for row in cur.fetchall()]

if 'default_gross_amount' not in cols:
    try_exec("ALTER TABLE company ADD COLUMN default_gross_amount REAL;")
else:
    print('company.default_gross_amount already exists')

if 'default_net_amount' not in cols:
    try_exec("ALTER TABLE company ADD COLUMN default_net_amount REAL;")
else:
    print('company.default_net_amount already exists')

# Add default cleaning company FK to apartment
cur.execute("PRAGMA table_info(apartment)")
cols = [row[1] for row in cur.fetchall()]

if 'default_cleaning_company_id' not in cols:
    try_exec("ALTER TABLE apartment ADD COLUMN default_cleaning_company_id INTEGER REFERENCES company(id);")
else:
    print('apartment.default_cleaning_company_id already exists')

conn.close()
print('Migration complete.')
