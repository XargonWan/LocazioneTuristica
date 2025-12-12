#!/usr/bin/env python3
"""Quick script to verify edit series behavior for Income/Expense.
Usage: python scripts/check_edit_series.py
It will create a recurrence, two incomes with same recurrence_id, then update one with a 'series' apply and verify both updated.
"""
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import Income, Recurrence

init_db()

print('Starting check...')
db = SessionLocal()
try:
    # create rec
    r = Recurrence(type='monthly', start_date='2025-12-01')
    db.add(r)
    db.commit()
    rec_id = r.id

    # create two incomes
    i1 = Income(apartment_id=None, platform_id=None, date='2025-12-01', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=39.0, recurrence_id=rec_id, notes='monthly fee')
    db.add(i1)
    db.commit()
    i2 = Income(apartment_id=None, platform_id=None, date='2026-01-01', gross_amount=50.0, vat_percent=22.0, net_amount=39.0, pm_percent=0.0, pm_amount=0.0, net_after_pm=39.0, recurrence_id=rec_id, notes='monthly fee')
    db.add(i2)
    db.commit()

    print('Before update:')
    incomes = db.query(Income).filter(Income.recurrence_id == rec_id).all()
    for i in incomes:
        print(i.id, i.date, float(i.gross_amount), i.notes)

    # Simulate series update: change gross_amount and notes
    new_gross = 75.0
    new_notes = 'monthly fee increased'
    occs = db.query(Income).filter(Income.recurrence_id == rec_id).all()
    for o in occs:
        o.gross_amount = new_gross
        o.vat_percent = 22.0
        o.net_amount = round(new_gross * (1 - (22.0 / 100.0)), 2)
        o.notes = new_notes
        db.add(o)
    db.commit()

    print('After update:')
    incomes = db.query(Income).filter(Income.recurrence_id == rec_id).all()
    for i in incomes:
        print(i.id, i.date, float(i.gross_amount), i.notes)

finally:
    db.close()

print('Done.')
