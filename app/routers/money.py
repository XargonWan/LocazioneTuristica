from fastapi import APIRouter, Request, Form, Depends
from typing import List
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from app.db import SessionLocal
from app.models import Expense, Income, Apartment, PropertyManager, Platform, Company, Attachment
from app.auth_utils import admin_required, get_current_user

router = APIRouter(prefix="/money")
templates = Jinja2Templates(directory="app/templates")


@router.get("/expenses")
async def expenses_index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        expenses = db.query(Expense).order_by(Expense.date.desc()).limit(50).all()
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        attachments = db.query(Attachment).all()
        return templates.TemplateResponse("expenses_index.html", {"request": request, "expenses": expenses, "apartments": apartments, "pms": pms})
    finally:
        db.close()


@router.post("/expenses/add")
async def add_expense(request: Request, gross_amount: float = Form(...), vat_percent: float = Form(22.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), notes: str = Form(''), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        # Create recurrence record if needed
        recurrence_id = None
        if recurrence and recurrence in ("monthly", "yearly"):
            from app.models import Recurrence
            r = Recurrence(type=recurrence, start_date=date)
            db.add(r)
            db.commit()
            recurrence_id = r.id
        e = Expense(apartment_id=apartment_id, date=date, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=recurrence_id, notes=notes)
        db.add(e)
        db.commit()
        # Attach any selected attachments
        if attachment_ids:
            from app.models import Attachment
            for aid in attachment_ids:
                a = db.query(Attachment).filter(Attachment.id == aid).first()
                if a:
                    a.expense_id = e.id
                    db.add(a)
            db.commit()
        return RedirectResponse(url="/money/expenses", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()

@router.get('/expenses/{expense_id}/edit')
async def edit_expense_get(request: Request, expense_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        e = db.query(Expense).filter(Expense.id == expense_id).first()
        if not e:
            return RedirectResponse(url='/money/expenses')
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        companies = db.query(Company).all()
        attached = db.query(Attachment).filter(Attachment.expense_id == e.id).all()
        return templates.TemplateResponse('expense_edit.html', {"request": request, "expense": e, "apartments": apartments, "pms": pms, "companies": companies, "attached": attached})
    finally:
        db.close()

@router.post('/expenses/{expense_id}/edit')
async def edit_expense_post(request: Request, expense_id: int, gross_amount: float = Form(...), vat_percent: float = Form(22.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), notes: str = Form(''), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        e = db.query(Expense).filter(Expense.id == expense_id).first()
        if not e:
            return RedirectResponse(url='/money/expenses')
        e.gross_amount = gross_amount
        e.vat_percent = vat_percent
        e.net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        e.date = date
        e.apartment_id = apartment_id
        e.associated_pm_id = associated_pm_id
        e.associated_company_id = associated_company_id
        e.notes = notes
        db.add(e)
        db.commit()
        return RedirectResponse(url='/money/expenses')
    finally:
        db.close()

@router.post('/expenses/{expense_id}/delete')
async def delete_expense(request: Request, expense_id: int, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        e = db.query(Expense).filter(Expense.id == expense_id).first()
        if e:
            db.delete(e)
            db.commit()
        return RedirectResponse(url='/money/expenses')
    finally:
        db.close()

@router.get("/incomes")
async def incomes_index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        incomes = db.query(Income).order_by(Income.date.desc()).limit(50).all()
        apartments = db.query(Apartment).all()
        platforms = db.query(Platform).all()
        attachments = db.query(Attachment).all()
        return templates.TemplateResponse("incomes_index.html", {"request": request, "incomes": incomes, "apartments": apartments, "platforms": platforms})
    finally:
        db.close()


@router.post("/incomes/add")
async def add_income(request: Request, gross_amount: float = Form(...), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), notes: str = Form(''), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
        net_after_pm = round(net_amount - pm_amount, 2)
        # Create recurrence if needed
        recurrence_id = None
        if recurrence and recurrence in ("monthly", "yearly"):
            from app.models import Recurrence
            r = Recurrence(type=recurrence, start_date=date)
            db.add(r)
            db.commit()
            recurrence_id = r.id
        e = Income(apartment_id=apartment_id, platform_id=platform_id, date=date, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=pm_amount, net_after_pm=net_after_pm, recurrence_id=recurrence_id, associated_pm_id=associated_pm_id, notes=notes)
        db.add(e)
        db.commit()
        # Attach any selected attachments
        if attachment_ids:
            from app.models import Attachment
            for aid in attachment_ids:
                a = db.query(Attachment).filter(Attachment.id == aid).first()
                if a:
                    a.income_id = e.id
                    db.add(a)
            db.commit()
        return RedirectResponse(url="/money/incomes", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()

@router.get('/incomes/{income_id}/edit')
async def edit_income_get(request: Request, income_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        e = db.query(Income).filter(Income.id == income_id).first()
        if not e:
            return RedirectResponse(url='/money/incomes')
        apartments = db.query(Apartment).all()
        platforms = db.query(Platform).all()
        pms = db.query(PropertyManager).all()
        attached = db.query(Attachment).filter(Attachment.income_id == e.id).all()
        return templates.TemplateResponse('income_edit.html', {"request": request, "income": e, "apartments": apartments, "platforms": platforms, "pms": pms, "attached": attached})
    finally:
        db.close()

@router.post('/incomes/{income_id}/edit')
async def edit_income_post(request: Request, income_id: int, gross_amount: float = Form(...), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), notes: str = Form(''), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        e = db.query(Income).filter(Income.id == income_id).first()
        if not e:
            return RedirectResponse(url='/money/incomes')
        e.gross_amount = gross_amount
        e.vat_percent = vat_percent
        e.net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        e.pm_percent = pm_percent
        e.pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
        e.net_after_pm = round(e.net_amount - e.pm_amount, 2)
        e.date = date
        e.apartment_id = apartment_id
        e.platform_id = platform_id
        e.associated_pm_id = associated_pm_id
        e.notes = notes
        db.add(e)
        db.commit()
        return RedirectResponse(url='/money/incomes')
    finally:
        db.close()

@router.post('/incomes/{income_id}/delete')
async def delete_income(request: Request, income_id: int, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        e = db.query(Income).filter(Income.id == income_id).first()
        if e:
            db.delete(e)
            db.commit()
        return RedirectResponse(url='/money/incomes')
    finally:
        db.close()
