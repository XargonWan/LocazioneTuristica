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

# Get columns of expense
cur.execute("PRAGMA table_info(expense)")
cols = [row[1] for row in cur.fetchall()]
print('Existing columns in expense:', cols)

# Define new columns to add
new_cols = [
    ("pm_percent","REAL DEFAULT 0.0"),
    ("pm_amount","REAL DEFAULT 0.0"),
    ("net_after_pm","REAL DEFAULT 0.0"),
]

for name, definition in new_cols:
    if name not in cols:
        sql = f"ALTER TABLE expense ADD COLUMN {name} {definition};"
        print('Executing:', sql)
        try:
            cur.execute(sql)
            conn.commit()
            print('Added column', name)
        except Exception as e:
            print('Failed to add', name, e)
    else:
        print('Column already exists:', name)

conn.close()
print('Done.')
