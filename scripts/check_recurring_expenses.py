#!/usr/bin/env python3
"""Create a recurring expense via DB-layer simulation and check that occurrences are materialized."""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import Expense, Recurrence
from datetime import datetime

init_db()
db = SessionLocal()
try:
    # Cleanup any test recurrence entries
    db.query(Expense).filter(Expense.notes.like('rec_test%')).delete()
    db.query(Recurrence).filter(Recurrence.notes.like('rec_test%')).delete()
    db.commit()
    # Create recurrence and original expense
    r = Recurrence(type='monthly', start_date='2025-06-05', notes='rec_test')
    db.add(r); db.commit()
    e = Expense(apartment_id=None, date='2025-06-05', gross_amount=100.0, vat_percent=22.0, net_amount=100.0*(1-0.22), pm_percent=0.0, pm_amount=0.0, net_after_pm=100.0*(1-0.22), recurrence_id=r.id, notes='rec_test')
    db.add(e); db.commit()
    # Now run the same materialization logic as in router
    start = datetime.strptime(r.start_date, '%Y-%m-%d')
    def add_months(dt, months):
        y = dt.year + (dt.month - 1 + months) // 12
        m = (dt.month - 1 + months) % 12 + 1
        d = min(dt.day, 28)
        return datetime(y, m, d)
    for i in range(1, 12):
        nd = add_months(start, i).strftime('%Y-%m-%d')
        new_e = Expense(apartment_id=None, date=nd, gross_amount=100.0, vat_percent=22.0, net_amount=100.0*(1-0.22), pm_percent=0.0, pm_amount=0.0, net_after_pm=100.0*(1-0.22), recurrence_id=r.id, notes='rec_test')
        db.add(new_e)
    db.commit()
    items = db.query(Expense).filter(Expense.recurrence_id==r.id).order_by(Expense.date).all()
    print('Recurrence created with id', r.id, 'total occurrences:', len(items))
    for it in items:
        print(' -', it.date)
finally:
    db.close()
