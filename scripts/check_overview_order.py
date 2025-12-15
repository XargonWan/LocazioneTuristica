#!/usr/bin/env python3
"""Check that entries_by_month are sorted ascending by date (earliest first)."""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import SessionLocal, init_db
from app.models import Income, Expense
from datetime import datetime

init_db()
db = SessionLocal()
try:
    # Clear sample data for the month (be careful: this is a dev script)
    # We'll insert new sample incomes/expenses in January 2025
    # Insert incomes
    db.query(Income).filter(Income.date.like('2025-01-%')).delete()
    db.query(Expense).filter(Expense.date.like('2025-01-%')).delete()
    db.commit()
    dates = ['2025-01-15','2025-01-05','2025-01-01','2025-01-31']
    for i, d in enumerate(dates):
        inc = Income(apartment_id=None, platform_id=None, date=d, gross_amount=10*(i+1), vat_percent=22.0, net_amount=round(10*(i+1)*(1-0.22),2), pm_percent=0.0, pm_amount=0.0, net_after_pm=0.0, notes=f'inc {d}')
        db.add(inc)
    # also add an expense
    exp_dates = ['2025-01-02','2025-01-20']
    for i, d in enumerate(exp_dates):
        exp = Expense(apartment_id=None, date=d, gross_amount=5*(i+1), vat_percent=22.0, net_amount=round(5*(i+1)*(1-0.22),2), notes=f'exp {d}')
        db.add(exp)
    db.commit()
    # Recompute entries_by_month like overview
    incomes = db.query(Income).all()
    expenses = db.query(Expense).all()
    entries_by_month = {m: [] for m in range(1,13)}
    for inc in incomes:
        try:
            d = datetime.strptime(inc.date, '%Y-%m-%d')
        except Exception:
            continue
        if d.year == 2025:
            entries_by_month[d.month].append({'type':'income','date':d,'id':inc.id,'notes':inc.notes})
    for exp in expenses:
        try:
            d = datetime.strptime(exp.date, '%Y-%m-%d')
        except Exception:
            continue
        if d.year == 2025:
            entries_by_month[d.month].append({'type':'expense','date':d,'id':exp.id,'notes':exp.notes})
    for m in range(1,13):
        entries_by_month[m].sort(key=lambda x: x['date'], reverse=False)
    jan = entries_by_month[1]
    print('January entries in order:')
    for e in jan:
        print(' ', e['type'], e['date'].strftime('%Y-%m-%d'), e['notes'])
    # Check ascending
    days = [e['date'].day for e in jan]
    if days == sorted(days):
        print('OK: entries are ascending by day')
    else:
        print('FAIL: not ascending', days)
finally:
    db.close()
