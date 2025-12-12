#!/usr/bin/env python3
"""Quick script to verify editing a single occurrence unlinks it from the recurrence.
"""
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import Expense, Recurrence

init_db()
print('Starting unlink check...')
db = SessionLocal()
try:
    r = Recurrence(type='monthly', start_date='2025-12-01')
    db.add(r)
    db.commit()
    rec_id = r.id

    e1 = Expense(apartment_id=None, date='2025-12-01', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, recurrence_id=rec_id, notes='gas')
    e2 = Expense(apartment_id=None, date='2026-01-01', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, recurrence_id=rec_id, notes='gas')
    db.add(e1); db.commit(); db.add(e2); db.commit()

    print('Before edit:')
    all_e = db.query(Expense).filter(Expense.recurrence_id == rec_id).all()
    for e in all_e: print(e.id, e.date, float(e.gross_amount), e.recurrence_id)

    # Simulate editing single occurrence: change amount and unlink
    e = db.query(Expense).filter(Expense.id == e1.id).first()
    e.gross_amount = 60.0
    e.net_amount = round(60.0 * (1 - (22.0 / 100.0)), 2)
    e.recurrence_id = None
    e.notes = 'gas updated'
    db.add(e)
    db.commit()

    print('After edit:')
    remain = db.query(Expense).filter(Expense.recurrence_id == rec_id).all()
    for e in remain: print(e.id, e.date, float(e.gross_amount), e.recurrence_id)
    single = db.query(Expense).filter(Expense.id == e1.id).first()
    print('Single record now:', single.id, single.date, float(single.gross_amount), single.recurrence_id, single.notes)

finally:
    db.close()

print('Done.')
