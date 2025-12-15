from fastapi import APIRouter, Request, Form, Depends
from typing import List
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from app.db import SessionLocal
from app.models import Expense, Income, Apartment, PropertyManager, Platform, Company, Attachment
from app.auth_utils import admin_required, get_current_user
from app.debug import log_request_form

router = APIRouter(prefix="/money")
from app.main import templates


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
        # Build a mapping from expense id to attachments list for templates
        attachments_by_expense = {}
        if expenses:
            expense_ids = [e.id for e in expenses if e.id]
            if expense_ids:
                ats = db.query(Attachment).filter(Attachment.expense_id.in_(expense_ids)).all()
                for a in ats:
                    attachments_by_expense.setdefault(a.expense_id, []).append(a)
        default_apartment_id = None
        default_associated_pm_id = None
        default_pm_percent = 0.0
        if apartments and len(apartments) == 1:
            default_apartment_id = apartments[0].id
            if apartments[0].property_manager_id:
                default_associated_pm_id = apartments[0].property_manager_id
                pm = db.query(PropertyManager).filter(PropertyManager.id == default_associated_pm_id).first()
                if pm:
                    default_pm_percent = float(pm.percent or 0.0)
        # build a mapping for apartment to its PM percent for client-side default behavior
        apt_pm_map = {}
        for apt in apartments:
            if apt.property_manager_id:
                pm = db.query(PropertyManager).filter(PropertyManager.id == apt.property_manager_id).first()
                apt_pm_map[apt.id] = float(pm.percent or 0.0) if pm else 0.0
            else:
                apt_pm_map[apt.id] = 0.0
        next_url = request.query_params.get('next') or None
        # Prefetch associated PM names and numeric fields to avoid lazy-loading in templates
        for e in expenses:
            try:
                if e.associated_pm:
                    e.associated_pm_name = f"{e.associated_pm.first_name} {e.associated_pm.last_name}"
                else:
                    e.associated_pm_name = None
                e.pm_percent = float(e.pm_percent or 0.0)
                e.pm_amount = float(e.pm_amount or 0.0)
                e.net_after_pm = float(e.net_after_pm or 0.0)
            except Exception:
                e.associated_pm_name = None
                e.pm_percent = float(getattr(e, 'pm_percent', 0.0) or 0.0)
                e.pm_amount = float(getattr(e, 'pm_amount', 0.0) or 0.0)
                e.net_after_pm = float(getattr(e, 'net_after_pm', 0.0) or 0.0)
        return templates.TemplateResponse("expenses_index.html", {"request": request, "expenses": expenses, "apartments": apartments, "pms": pms, "attachments": attachments, "attachments_by_expense": attachments_by_expense, "default_apartment_id": default_apartment_id, "default_associated_pm_id": default_associated_pm_id, "default_pm_percent": default_pm_percent, "apt_pm_map": apt_pm_map, "next": next_url})
    finally:
        db.close()


@router.post("/expenses/add")
async def add_expense(request: Request, gross_amount: float = Form(...), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), notes: str = Form(''), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        # Compute net_amount from gross if not explicitly provided
        if net_amount is None:
            net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        else:
            net_amount = round(float(net_amount), 2)
        # Create recurrence record if needed
        recurrence_id = None
        if recurrence and recurrence in ("monthly", "yearly"):
            from app.models import Recurrence
            r = Recurrence(type=recurrence, start_date=date)
            db.add(r)
            db.commit()
            recurrence_id = r.id
        # Default associated_pm_id to the apartment's PM if not provided
        if not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
        # If we have an associated PM but no pm_percent provided (or zero), use the PM default
        if associated_pm_id and (pm_percent is None or float(pm_percent) == 0.0):
            pm = db.query(PropertyManager).filter(PropertyManager.id == associated_pm_id).first()
            if pm:
                pm_percent = float(pm.percent or 0.0)
        pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
        net_after_pm = round(net_amount - pm_amount, 2)
        e = Expense(apartment_id=apartment_id, date=date, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=pm_amount, net_after_pm=net_after_pm, associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=recurrence_id, notes=notes)
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
        # If this expense has a recurrence, materialize future occurrences for the next months/years
        if recurrence_id:
            try:
                from datetime import datetime
                def add_months(dt, months):
                    # months can be positive
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)  # keep safe day to avoid invalid dates (28 ensures feb safeness)
                    return datetime(y, m, d)

                start = datetime.strptime(date, '%Y-%m-%d')
                # if monthly, create next 11 months; if yearly, create next 3 years
                if recurrence in ('monthly',):
                    for i in range(1, 12):
                        nd = add_months(start, i).strftime('%Y-%m-%d')
                        new_e = Expense(apartment_id=apartment_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=recurrence_id, notes=notes)
                        db.add(new_e)
                    db.commit()
                elif recurrence in ('yearly',):
                    for i in range(1, 4):
                        nd = start.replace(year=start.year + i).strftime('%Y-%m-%d')
                        new_e = Expense(apartment_id=apartment_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=recurrence_id, notes=notes)
                        db.add(new_e)
                    db.commit()
            except Exception:
                # don't break the add flow if materialization fails
                pass
        # Redirect back to provided next url if present, otherwise default to expenses list
            # If the form included a recurrence and the entry is not part of a series, create it now (defensive)
            try:
                form = await request.form()
                rec2 = form.get('recurrence') if form else None
                if rec2 and rec2 in ('monthly', 'yearly') and not e.recurrence_id:
                    from app.models import Recurrence
                    r = Recurrence(type=rec2, start_date=date, notes=notes)
                    db.add(r)
                    db.commit()
                    print('Created Recurrence id (defensive expense):', r.id)
                    e.recurrence_id = r.id
                    db.add(e)
                    db.commit()
                    try:
                        from datetime import datetime
                        def add_months(dt, months):
                            y = dt.year + (dt.month - 1 + months) // 12
                            m = (dt.month - 1 + months) % 12 + 1
                            d = min(dt.day, 28)
                            return datetime(y, m, d)
                        start = datetime.strptime(date, '%Y-%m-%d')
                        if rec2 in ('monthly',):
                            for i in range(1, 12):
                                nd = add_months(start, i).strftime('%Y-%m-%d')
                                print('Materialize expense date:', nd)
                                new_e = Expense(apartment_id=apartment_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=r.id, notes=notes)
                                db.add(new_e)
                            db.commit()
                        elif rec2 in ('yearly',):
                            for i in range(1, 4):
                                nd = start.replace(year=start.year + i).strftime('%Y-%m-%d')
                                new_e = Expense(apartment_id=apartment_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=r.id, notes=notes)
                                db.add(new_e)
                            db.commit()
                    except Exception:
                        pass
            except Exception as ex:
                print('Materialize incomes failed:', ex)
            # Redirect back to provided next url if present, otherwise default to expenses list
            if next:
                return RedirectResponse(url=next, status_code=HTTP_303_SEE_OTHER)
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
            return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        companies = db.query(Company).all()
        attached = db.query(Attachment).filter(Attachment.expense_id == e.id).all()
        next_url = request.query_params.get('next') or None
        return templates.TemplateResponse('expense_edit.html', {"request": request, "expense": e, "apartments": apartments, "pms": pms, "companies": companies, "attached": attached, "next": next_url})
    finally:
        db.close()

@router.api_route('/expenses/{expense_id}/edit', methods=["POST","PUT","PATCH"])
async def edit_expense_post(request: Request, expense_id: int, gross_amount: float = Form(...), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), notes: str = Form(''), recurrence: str = Form('none'), apply_to: str = Form('single'), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        e = db.query(Expense).filter(Expense.id == expense_id).first()
        if not e:
            return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
        # Allow converting a single expense to a recurring series if recurrence is provided on edit
        form = await request.form()
        # debug: log incoming form keys when converting to recurrence
        try:
            print('EDIT EXPENSE FORM KEYS:', list(form.keys()))
        except Exception as ex:
            print('Materialize expenses failed:', ex)
            pass
        recurrence = form.get('recurrence') if form else None
        print('EDIT EXPENSE recurrence value:', recurrence)
        created_recurrence = False
        # Determine effective net amount to use for materialized occurrences
        if net_amount is None:
            effective_net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        else:
            effective_net_amount = round(float(net_amount), 2)
        if not e.recurrence_id and recurrence and recurrence in ('monthly', 'yearly'):
            print('Entering recurrence creation block')
            from app.models import Recurrence
            r = Recurrence(type=recurrence, start_date=date, notes=notes)
            db.add(r)
            db.commit()
            print('Created Recurrence id', r.id)
            e.recurrence_id = r.id
            print('Setting expense.recurrence_id to', r.id)
            db.add(e)
            db.commit()
            # materialize future occurrences
            try:
                from datetime import datetime
                def add_months(dt, months):
                    # months can be positive
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)
                    return datetime(y, m, d)
                start = datetime.strptime(date, '%Y-%m-%d')
                if recurrence in ('monthly',):
                    for i in range(1, 12):
                        nd = add_months(start, i).strftime('%Y-%m-%d')
                        print('Materialize expense date (edit) i=%s:' % i, nd)
                        new_e = Expense(apartment_id=apartment_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=effective_net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(effective_net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=r.id, notes=notes)
                        db.add(new_e)
                    db.commit()
                elif recurrence in ('yearly',):
                    for i in range(1, 4):
                        nd = start.replace(year=start.year + i).strftime('%Y-%m-%d')
                        print('Materialize expense date (edit) i=%s:' % i, nd)
                        new_e = Expense(apartment_id=apartment_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=effective_net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(effective_net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=r.id, notes=notes)
                        db.add(new_e)
                    db.commit()
            except Exception as ex:
                import traceback
                print('Materialize expense (edit) failed:', ex)
                traceback.print_exc()
            created_recurrence = True

        # If this expense belongs to a recurrence and the user wants to apply to the whole series, update all occurrences
        if e.recurrence_id and apply_to == 'series':
            occs = db.query(Expense).filter(Expense.recurrence_id == e.recurrence_id).all()
            for o in occs:
                o.gross_amount = gross_amount
                o.vat_percent = vat_percent
                o.net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
                o.pm_percent = pm_percent
                o.pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
                o.net_after_pm = round(o.net_amount - o.pm_amount, 2)
                o.date = date
                o.apartment_id = apartment_id
                o.associated_pm_id = associated_pm_id
                o.associated_company_id = associated_company_id
                o.notes = notes
                db.add(o)
            # Also update recurrence metadata
            from app.models import Recurrence
            r = db.query(Recurrence).filter(Recurrence.id == e.recurrence_id).first()
            if r:
                r.start_date = date
                r.notes = notes
                db.add(r)
            db.commit()
        else:
            # Apply only to single occurrence: unlink from recurrence and update fields
            if e.recurrence_id and apply_to == 'single' and not created_recurrence:
                e.recurrence_id = None
            e.gross_amount = gross_amount
            e.vat_percent = vat_percent
            if net_amount is None:
                e.net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
            else:
                e.net_amount = round(float(net_amount), 2)
            e.pm_percent = pm_percent
            e.pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
            e.net_after_pm = round(e.net_amount - e.pm_amount, 2)
            e.date = date
            e.apartment_id = apartment_id
            e.associated_pm_id = associated_pm_id
            e.associated_company_id = associated_company_id
            e.notes = notes
            db.add(e)
            db.commit()
        # Redirect to next if provided
        if next:
            return RedirectResponse(url=next, status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()

@router.post('/expenses/{expense_id}/delete')
async def delete_expense(request: Request, expense_id: int, user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        e = db.query(Expense).filter(Expense.id == expense_id).first()
        form = await request.form()
        delete_scope = (form.get('delete_scope') if form else 'single')
        next_url = (form.get('next') if form else None)
        if e:
            if delete_scope == 'series' and e.recurrence_id:
                # delete all occurrences with the same recurrence_id
                rec_id = e.recurrence_id
                db.query(Expense).filter(Expense.recurrence_id == rec_id).delete()
                # also remove recurrence metadata
                from app.models import Recurrence
                db.query(Recurrence).filter(Recurrence.id == rec_id).delete()
                db.commit()
            else:
                db.delete(e)
                db.commit()
        if next_url:
            return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
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
        attachments_by_income = {}
        if incomes:
            income_ids = [inc.id for inc in incomes if inc.id]
            if income_ids:
                ats = db.query(Attachment).filter(Attachment.income_id.in_(income_ids)).all()
                for a in ats:
                    attachments_by_income.setdefault(a.income_id, []).append(a)
        # Determine defaults for new income form
        default_apartment_id = None
        default_associated_pm_id = None
        default_pm_percent = 0.0
        if apartments and len(apartments) == 1:
            default_apartment_id = apartments[0].id
            if apartments[0].property_manager_id:
                default_associated_pm_id = apartments[0].property_manager_id
                pm = db.query(PropertyManager).filter(PropertyManager.id == default_associated_pm_id).first()
                if pm:
                    default_pm_percent = float(pm.percent or 0.0)
        next_url = request.query_params.get('next') or None
        # Prefetch associated PM names and numeric fields to avoid lazy-loading in templates
        for inc in incomes:
            try:
                if inc.associated_pm:
                    inc.associated_pm_name = f"{inc.associated_pm.first_name} {inc.associated_pm.last_name}"
                else:
                    inc.associated_pm_name = None
                inc.pm_percent = float(inc.pm_percent or 0.0)
                inc.pm_amount = float(inc.pm_amount or 0.0)
            except Exception:
                inc.associated_pm_name = None
                inc.pm_percent = float(getattr(inc, 'pm_percent', 0.0) or 0.0)
                inc.pm_amount = float(getattr(inc, 'pm_amount', 0.0) or 0.0)
        # build a mapping for apartment to its PM percent for client-side default behavior (used in JS)
        apt_pm_map = {}
        for apt in apartments:
            if apt.property_manager_id:
                pm = db.query(PropertyManager).filter(PropertyManager.id == apt.property_manager_id).first()
                apt_pm_map[apt.id] = float(pm.percent or 0.0) if pm else 0.0
            else:
                apt_pm_map[apt.id] = 0.0
        return templates.TemplateResponse("incomes_index.html", {"request": request, "incomes": incomes, "apartments": apartments, "platforms": platforms, "attachments": attachments, "attachments_by_income": attachments_by_income, "default_apartment_id": default_apartment_id, "default_associated_pm_id": default_associated_pm_id, "default_pm_percent": default_pm_percent, "next": next_url})
    finally:
        db.close()


@router.post("/incomes/add")
async def add_income(request: Request, gross_amount: float = Form(...), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), notes: str = Form(''), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        # If an apartment is provided but no associated_pm_id, set it to the apartment's pm
        if not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
                # if pm_percent not provided (0), use property manager default
                pm = db.query(PropertyManager).filter(PropertyManager.id == associated_pm_id).first()
                if pm and (pm_percent is None or float(pm_percent) == 0.0):
                    pm_percent = float(pm.percent or 0.0)
        # If an apartment is provided but no associated_pm_id, set it to the apartment's pm
        if not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
                # if pm_percent not provided (0), use property manager default
                pm = db.query(PropertyManager).filter(PropertyManager.id == associated_pm_id).first()
                if pm and (pm_percent is None or float(pm_percent) == 0.0):
                    pm_percent = float(pm.percent or 0.0)
        # Now compute pm_amount based on (maybe updated) pm_percent
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
        # If this income has a recurrence, materialize future occurrences (monthly/yearly)
        if recurrence_id:
            try:
                from datetime import datetime
                def add_months(dt, months):
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)
                    return datetime(y, m, d)

                start = datetime.strptime(date, '%Y-%m-%d')
                if recurrence in ('monthly',):
                    for i in range(1, 12):
                        nd = add_months(start, i).strftime('%Y-%m-%d')
                        new_e = Income(apartment_id=apartment_id, platform_id=platform_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, recurrence_id=recurrence_id, notes=notes)
                        db.add(new_e)
                    db.commit()
                elif recurrence in ('yearly',):
                    for i in range(1, 4):
                        nd = start.replace(year=start.year + i).strftime('%Y-%m-%d')
                        new_e = Income(apartment_id=apartment_id, platform_id=platform_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, recurrence_id=recurrence_id, notes=notes)
                        db.add(new_e)
                    db.commit()
            except Exception:
                pass
        # Respect next redirect if provided
        form = await request.form()
        next_url = form.get('next') if form else None
        if next_url:
            return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
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
            return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
        apartments = db.query(Apartment).all()
        platforms = db.query(Platform).all()
        pms = db.query(PropertyManager).all()
        attached = db.query(Attachment).filter(Attachment.income_id == e.id).all()
        next_url = request.query_params.get('next') or None
        return templates.TemplateResponse('income_edit.html', {"request": request, "income": e, "apartments": apartments, "platforms": platforms, "pms": pms, "attached": attached, "next": next_url})
    finally:
        db.close()

@router.api_route('/incomes/{income_id}/edit', methods=["POST","PUT","PATCH"])
async def edit_income_post(request: Request, income_id: int, gross_amount: float = Form(...), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), notes: str = Form(''), recurrence: str = Form('none'), apply_to: str = Form('single'), user=Depends(admin_required)):
    await log_request_form(request)
    print('EDIT INCOME POST CALLED for', income_id)
    db = SessionLocal()
    try:
        e = db.query(Income).filter(Income.id == income_id).first()
        if not e:
              return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
        # Handle recurrence conversion: if entry was not recurring and user selected a recurrence, create it
        # 'recurrence' parameter is now provided by FastAPI via Form; use that
        print('EDIT INCOME recurrence value (param):', recurrence)
        print('Current e.recurrence_id:', e.recurrence_id)
        print('Cond checks:', not e.recurrence_id, bool(recurrence), recurrence in ('monthly','yearly'))
        created_recurrence = False
        if not e.recurrence_id and recurrence and recurrence in ('monthly', 'yearly'):
            from app.models import Recurrence
            r = Recurrence(type=recurrence, start_date=date, notes=notes)
            db.add(r)
            db.commit()
            e.recurrence_id = r.id
            db.add(e)
            db.commit()
            # materialize future occurrences for the new recurrence
            try:
                from datetime import datetime
                def add_months(dt, months):
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)
                    return datetime(y, m, d)
                start = datetime.strptime(date, '%Y-%m-%d')
                if recurrence in ('monthly',):
                    for i in range(1, 12):
                        nd = add_months(start, i).strftime('%Y-%m-%d')
                        new_i = Income(apartment_id=apartment_id, platform_id=platform_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=round(gross_amount * (1 - (vat_percent / 100.0)), 2), pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(round(gross_amount * (1 - (vat_percent / 100.0)), 2) - round(gross_amount * (pm_percent / 100.0), 2), 2), associated_pm_id=associated_pm_id, recurrence_id=r.id, notes=notes)
                        db.add(new_i)
                    db.commit()
                elif recurrence in ('yearly',):
                    for i in range(1, 4):
                        nd = start.replace(year=start.year + i).strftime('%Y-%m-%d')
                        new_i = Income(apartment_id=apartment_id, platform_id=platform_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=round(gross_amount * (1 - (vat_percent / 100.0)), 2), pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(round(gross_amount * (1 - (vat_percent / 100.0)), 2) - round(gross_amount * (pm_percent / 100.0), 2), 2), associated_pm_id=associated_pm_id, recurrence_id=r.id, notes=notes)
                        db.add(new_i)
                    db.commit()
            except Exception:
                pass
            created_recurrence = True

        # Handle editing apply scope
        if e.recurrence_id and apply_to == 'series':
            occs = db.query(Income).filter(Income.recurrence_id == e.recurrence_id).all()
            for o in occs:
                o.gross_amount = gross_amount
                o.vat_percent = vat_percent
                o.net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
                o.pm_percent = pm_percent
                o.pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
                o.net_after_pm = round(o.net_amount - o.pm_amount, 2)
                o.date = date
                o.apartment_id = apartment_id
                o.platform_id = platform_id
                o.associated_pm_id = associated_pm_id
                o.notes = notes
                db.add(o)
            from app.models import Recurrence
            r = db.query(Recurrence).filter(Recurrence.id == e.recurrence_id).first()
            if r:
                r.start_date = date
                r.notes = notes
                db.add(r)
            db.commit()
        else:
            # If we just created a recurrence above, don't unlink it when apply_to is 'single'
            if e.recurrence_id and apply_to == 'single' and not created_recurrence:
                e.recurrence_id = None
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
        # If the form included a recurrence and the entry is not part of a series, create it now (defensive)
        try:
            form = await request.form()
            rec2 = form.get('recurrence') if form else None
            if rec2 and rec2 in ('monthly', 'yearly') and not e.recurrence_id:
                from app.models import Recurrence
                r = Recurrence(type=rec2, start_date=date, notes=notes)
                db.add(r)
                db.commit()
                print('Created Recurrence id (defensive):', r.id)
                e.recurrence_id = r.id
                db.add(e)
                db.commit()
                # materialize occurrences
                try:
                    from datetime import datetime
                    def add_months(dt, months):
                        y = dt.year + (dt.month - 1 + months) // 12
                        m = (dt.month - 1 + months) % 12 + 1
                        d = min(dt.day, 28)
                        return datetime(y, m, d)
                    start = datetime.strptime(date, '%Y-%m-%d')
                    if rec2 in ('monthly',):
                        for i in range(1, 12):
                            nd = add_months(start, i).strftime('%Y-%m-%d')
                            new_i = Income(apartment_id=apartment_id, platform_id=platform_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=round(gross_amount * (1 - (vat_percent / 100.0)), 2), pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(round(gross_amount * (1 - (vat_percent / 100.0)), 2) - round(gross_amount * (pm_percent / 100.0), 2), 2), associated_pm_id=associated_pm_id, recurrence_id=r.id, notes=notes)
                            db.add(new_i)
                        db.commit()
                    elif rec2 in ('yearly',):
                        for i in range(1, 4):
                            nd = start.replace(year=start.year + i).strftime('%Y-%m-%d')
                            new_i = Income(apartment_id=apartment_id, platform_id=platform_id, date=nd, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=round(gross_amount * (1 - (vat_percent / 100.0)), 2), pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(round(gross_amount * (1 - (vat_percent / 100.0)), 2) - round(gross_amount * (pm_percent / 100.0), 2), 2), associated_pm_id=associated_pm_id, recurrence_id=r.id, notes=notes)
                            db.add(new_i)
                        db.commit()
                except Exception:
                    pass
        except Exception:
            form = await request.form()
        next_url = form.get('next') if form else None
        if next_url:
            return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()

@router.post('/incomes/{income_id}/delete')
async def delete_income(request: Request, income_id: int, user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        e = db.query(Income).filter(Income.id == income_id).first()
        form = await request.form()
        delete_scope = (form.get('delete_scope') if form else 'single')
        next_url = (form.get('next') if form else None)
        if e:
            if delete_scope == 'series' and e.recurrence_id:
                rec_id = e.recurrence_id
                db.query(Income).filter(Income.recurrence_id == rec_id).delete()
                from app.models import Recurrence
                db.query(Recurrence).filter(Recurrence.id == rec_id).delete()
                db.commit()
            else:
                db.delete(e)
                db.commit()
        if next_url:
            return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()
