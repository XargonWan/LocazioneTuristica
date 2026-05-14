#!/usr/bin/env python3
import os
import sqlite3


DB_PATH = os.getenv('DATABASE_URL', 'data/db.sqlite3')
if DB_PATH.startswith('sqlite:///'):
    dbfile = DB_PATH.replace('sqlite:///', '')
else:
    dbfile = DB_PATH


print('DB file:', dbfile)
conn = sqlite3.connect(dbfile)
cur = conn.cursor()

targets = {
    'expense': [('orig_recurrence_id', 'INTEGER REFERENCES recurrence(id)')],
    'income': [('orig_recurrence_id', 'INTEGER REFERENCES recurrence(id)')],
}

for table_name, columns in targets.items():
    cur.execute(f"PRAGMA table_info({table_name})")
    existing_cols = [row[1] for row in cur.fetchall()]
    print(f'Existing columns in {table_name}:', existing_cols)
    for column_name, definition in columns:
        if column_name in existing_cols:
            print(f'Column already exists in {table_name}:', column_name)
            continue
        sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition};"
        print('Executing:', sql)
        try:
            cur.execute(sql)
            conn.commit()
            print(f'Added column {column_name} to {table_name}')
        except Exception as exc:
            print(f'Failed to add {column_name} to {table_name}:', exc)

conn.close()
print('Done.')