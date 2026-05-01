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
        from sqlalchemy.orm import joinedload
        expenses = db.query(Expense).options(joinedload(Expense.recurrence)).order_by(Expense.date.desc()).limit(50).all()
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        attachments = db.query(Attachment).all()
        # build recurrence inference for any expense lacking recurrence_id
        from app.models import Recurrence
        for e in expenses:
            if not e.recurrence_id and e.date:
                r = db.query(Recurrence).filter(Recurrence.start_date <= e.date)
                r = r.filter((Recurrence.end_date == None) | (Recurrence.end_date >= e.date)).first()
                if r:
                    e.recurrence = r
                    setattr(e, '_orig_recurrence_id', r.id)
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
            # build a mapping for apartment to its PM percent and PM id for client-side default behavior (used in JS)
            apt_pm_map = {}
            for apt in apartments:
                if apt.property_manager_id:
                    pm = db.query(PropertyManager).filter(PropertyManager.id == apt.property_manager_id).first()
                    apt_pm_map[apt.id] = {"percent": float(pm.percent or 0.0) if pm else 0.0, "pm_id": pm.id if pm else None}
                else:
                    apt_pm_map[apt.id] = {"percent": 0.0, "pm_id": None}
        next_url = request.query_params.get('next') or None
        default_date = request.query_params.get('date') or ''
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
        return templates.TemplateResponse(request, "expenses_index.html", {"expenses": expenses, "apartments": apartments, "pms": pms, "attachments": attachments, "attachments_by_expense": attachments_by_expense, "default_apartment_id": default_apartment_id, "default_associated_pm_id": default_associated_pm_id, "default_pm_percent": default_pm_percent, "apt_pm_map": apt_pm_map, "next": next_url, "default_date": default_date})
    finally:
        db.close()


@router.post("/expenses/add")
async def add_expense(request: Request, gross_amount: float = Form(...), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), is_cleaning: str = Form('0'), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), associate_pm: str = Form(None), notes: str = Form(''), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        # Compute net_amount from gross if not explicitly provided
        if net_amount is None:
            net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        else:
            net_amount = round(float(net_amount), 2)
        # Create recurrence record if needed (start/end may come from form)
        recurrence_id = None
        if recurrence and recurrence in ("monthly", "yearly"):
            from app.models import Recurrence
            # normalize start/end to full YYYY-MM-DD strings
            def norm_date_str(s, default_day="-01"):
                if not s:
                    return None
                if len(s) == 7:  # yyyy-mm
                    return s + default_day
                return s
            start_date_val = norm_date_str(recurrence_start) or date
            end_date_val = norm_date_str(recurrence_end)
            r = Recurrence(type=recurrence, start_date=start_date_val, end_date=end_date_val)
            db.add(r)
            db.commit()
            recurrence_id = r.id
        # Default associated_pm_id to the apartment's PM if user checked the box
        if associate_pm and not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
        # expenses do not have a PM percentage; we treat payment to PM by marking associated_pm_id
        pm_amount = 0.0
        net_after_pm = net_amount
        e = Expense(apartment_id=apartment_id, date=date, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=0.0, pm_amount=pm_amount, net_after_pm=net_after_pm, associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=recurrence_id, notes=notes, is_cleaning=(is_cleaning == '1'))
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
        # If this expense has a recurrence, materialize occurrences within the desired range
        if recurrence_id:
            try:
                from datetime import datetime
                def add_months(dt, months):
                    # months can be positive
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)  # keep safe day to avoid invalid dates (28 ensures feb safeness)
                    return datetime(y, m, d)

                # fetch the stored recurrence in case start/end were normalized
                r = db.query(Recurrence).filter(Recurrence.id == recurrence_id).first()
                start = datetime.strptime(r.start_date, '%Y-%m-%d')
                end = None
                if r.end_date:
                    end = datetime.strptime(r.end_date, '%Y-%m-%d')
                # determine a reasonable default end if none supplied
                if not end:
                    if recurrence == 'monthly':
                        end = add_months(start, 11)
                    elif recurrence == 'yearly':
                        end = start.replace(year=start.year + 3)
                # now iterate from start to end inclusive, skipping the original expense date
                curr = start
                while curr <= end:
                    ds = curr.strftime('%Y-%m-%d')
                    if ds != date:
                        new_e = Expense(apartment_id=apartment_id, date=ds, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=0.0, pm_amount=0.0, net_after_pm=net_amount, associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=recurrence_id, notes=notes)
                        db.add(new_e)
                    # advance
                    if recurrence == 'monthly':
                        curr = add_months(curr, 1)
                    else:
                        curr = curr.replace(year=curr.year + 1)
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
        from sqlalchemy.orm import joinedload
        e = db.query(Expense).options(joinedload(Expense.recurrence)).filter(Expense.id == expense_id).first()
        if not e:
            return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
        # inference: if record has no recurrence_id, look for a matching series by date
        inferred = None
        if not e.recurrence_id and e.date:
            from app.models import Recurrence
            # date string compare works as YYYY-MM-DD
            r = db.query(Recurrence).filter(Recurrence.start_date <= e.date)
            r = r.filter((Recurrence.end_date == None) | (Recurrence.end_date >= e.date)).first()
            if r:
                inferred = r
                e.recurrence = r
        if inferred:
            # attach a temporary attribute for template to render hidden input
            setattr(e, '_orig_recurrence_id', inferred.id)
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        companies = db.query(Company).all()
        attached = db.query(Attachment).filter(Attachment.expense_id == e.id).all()
        next_url = request.query_params.get('next') or None
        _ = e.recurrence
        return templates.TemplateResponse(request, 'expense_edit.html', {"expense": e, "apartments": apartments, "pms": pms, "companies": companies, "attached": attached, "next": next_url})
    finally:
        db.close()

@router.api_route('/expenses/{expense_id}/edit', methods=["POST","PUT","PATCH"])
async def edit_expense_post(request: Request, expense_id: int, gross_amount: float = Form(...), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), is_cleaning: str = Form('0'), associate_pm: str = Form(None), notes: str = Form(''), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), apply_to: str = Form('single'), next: str = Form(None), user=Depends(admin_required)):
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
        orig_recur = form.get('orig_recurrence_id') if form else None
        print('EDIT EXPENSE recurrence value:', recurrence, 'orig', orig_recur)
        created_recurrence = False
        # Determine effective net amount to use for materialized occurrences
        if net_amount is None:
            effective_net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        else:
            effective_net_amount = round(float(net_amount), 2)
        # if user has toggled associate_pm but not selected a PM, default from apartment
        if associate_pm and not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
        # if record was detached but part of a known series, reattach before creating new one
        if not e.recurrence_id and orig_recur and recurrence in ('monthly', 'yearly'):
            try:
                from app.models import Recurrence
                r0 = db.query(Recurrence).filter(Recurrence.id == int(orig_recur)).first()
                if r0 and r0.type == recurrence:
                    e.recurrence_id = r0.id
                    db.add(e)
                    db.commit()
            except Exception:
                pass
        if not e.recurrence_id and recurrence and recurrence in ('monthly', 'yearly'):
            print('Entering recurrence creation block')
            from app.models import Recurrence
            # normalize dates
            def norm_date_str(s, default_day="-01"):
                if not s:
                    return None
                if len(s) == 7:
                    return s + default_day
                return s
            start_date_val = norm_date_str(recurrence_start) or date
            end_date_val = norm_date_str(recurrence_end)
            r = Recurrence(type=recurrence, start_date=start_date_val, end_date=end_date_val, notes=notes)
            db.add(r)
            db.commit()
            print('Created Recurrence id', r.id)
            e.recurrence_id = r.id
            print('Setting expense.recurrence_id to', r.id)
            db.add(e)
            db.commit()
            # materialize occurrences for the full range, skipping the original row
            try:
                from datetime import datetime
                def add_months(dt, months):
                    # months can be positive
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)
                    return datetime(y, m, d)
                start = datetime.strptime(r.start_date, '%Y-%m-%d')
                end = None
                if r.end_date:
                    end = datetime.strptime(r.end_date, '%Y-%m-%d')
                # reasonable defaults if no end provided
                if not end:
                    if recurrence == 'monthly':
                        end = add_months(start, 11)
                    else:
                        end = start.replace(year=start.year + 3)
                curr = start
                while curr <= end:
                    ds = curr.strftime('%Y-%m-%d')
                    if ds != date:
                        print('Materialize expense date (edit) ds=%s' % ds)
                        new_e = Expense(apartment_id=apartment_id, date=ds, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=effective_net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(effective_net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, associated_company_id=associated_company_id, recurrence_id=r.id, notes=notes, is_cleaning=(is_cleaning == '1'))
                        db.add(new_e)
                    if recurrence == 'monthly':
                        curr = add_months(curr, 1)
                    else:
                        curr = curr.replace(year=curr.year + 1)
                db.commit()
            except Exception as ex:
                import traceback
                print('Materialize expense (edit) failed:', ex)
                traceback.print_exc()
            created_recurrence = True

        # If this expense belongs to a recurrence and the user wants to apply to the whole series, regenerate the series
        if e.recurrence_id and apply_to == 'series':
            from app.models import Recurrence
            r = db.query(Recurrence).filter(Recurrence.id == e.recurrence_id).first()
            if r:
                def norm_date_str(s, default_day="-01"):
                    if not s:
                        return None
                    if len(s) == 7:
                        return s + default_day
                    return s
                # update metadata based on form values (date, start/end, notes)
                if recurrence_start:
                    r.start_date = norm_date_str(recurrence_start) or r.start_date
                else:
                    r.start_date = date
                if recurrence_end:
                    r.end_date = norm_date_str(recurrence_end)
                r.notes = notes
                db.add(r)
                db.commit()
                # now regenerate all occurrences based on updated recurrence
                # keep the current expense object as the instance for start_date
                # delete all others
                db.query(Expense).filter(Expense.recurrence_id == r.id, Expense.id != e.id).delete()
                db.commit()
                # update e fields to match new values
                e.date = r.start_date
                e.gross_amount = gross_amount
                e.vat_percent = vat_percent
                if net_amount is None:
                    e.net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
                else:
                    e.net_amount = round(float(net_amount), 2)
                e.is_cleaning = (is_cleaning == '1')
                # clear PM-related fields on expense
                e.pm_percent = 0.0
                e.pm_amount = 0.0
                e.net_after_pm = float(e.net_amount or 0.0)
                e.apartment_id = apartment_id
                e.associated_pm_id = associated_pm_id if associate_pm else None
                e.associated_company_id = associated_company_id
                e.notes = notes
                db.add(e)
                db.commit()
                # regenerate following occurrences up to end
                try:
                    from datetime import datetime
                    def add_months(dt, months):
                        y = dt.year + (dt.month - 1 + months) // 12
                        m = (dt.month - 1 + months) % 12 + 1
                        d = min(dt.day, 28)
                        return datetime(y, m, d)
                    start_dt = datetime.strptime(r.start_date, '%Y-%m-%d')
                    end = None
                    if r.end_date:
                        end = datetime.strptime(r.end_date, '%Y-%m-%d')
                    if not end:
                        if r.type == 'monthly':
                            end = add_months(start_dt, 11)
                        else:
                            end = start_dt.replace(year=start_dt.year + 3)
                    curr = start_dt
                    while curr <= end:
                        ds = curr.strftime('%Y-%m-%d')
                        if ds != e.date:
                            new_e = Expense(apartment_id=apartment_id, date=ds, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=e.net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(e.net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=(associated_pm_id if associate_pm else None), associated_company_id=associated_company_id, recurrence_id=r.id, notes=notes, is_cleaning=(is_cleaning == '1'))
                            db.add(new_e)
                        if r.type == 'monthly':
                            curr = add_months(curr, 1)
                        else:
                            curr = curr.replace(year=curr.year + 1)
                    db.commit()
                except Exception as ex:
                    import traceback
                    print('Regeneration of series failed:', ex)
                    traceback.print_exc()
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
            e.pm_percent = 0.0
            e.pm_amount = 0.0
            e.net_after_pm = float(e.net_amount or 0.0)
            e.date = date
            e.apartment_id = apartment_id
            e.associated_pm_id = associated_pm_id if associate_pm else None
            e.associated_company_id = associated_company_id
            e.notes = notes
            e.is_cleaning = (is_cleaning == '1')
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


@router.post('/expenses/bulk_edit')
async def bulk_edit_expenses(request: Request, ids: str = Form(...), notes: str = Form(None), net_amount: float = Form(None), gross_amount: float = Form(None), apartment_id: int = Form(None), vat_percent: float = Form(None), associated_pm_id: int = Form(None), date: str = Form(None), associated_company_id: int = Form(None), is_cleaning: str = Form(None), recurrence: str = Form('none'), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        id_list = [int(x) for x in ids.split(',') if x]
        occs = db.query(Expense).filter(Expense.id.in_(id_list)).all()
        for o in occs:
            if notes is not None and notes != '':
                o.notes = notes
            if gross_amount is not None:
                o.gross_amount = float(gross_amount)
            if vat_percent is not None:
                o.vat_percent = float(vat_percent)
            if net_amount is not None:
                o.net_amount = float(net_amount)
            else:
                if gross_amount is not None and vat_percent is not None:
                    o.net_amount = round(float(gross_amount) * (1 - (float(vat_percent) / 100.0)), 2)
            # pm_percent is ignored for expenses
            if apartment_id:
                o.apartment_id = apartment_id
            if associated_pm_id:
                o.associated_pm_id = associated_pm_id
            if associated_company_id:
                o.associated_company_id = associated_company_id
            if date:
                o.date = date
            if is_cleaning is not None:
                o.is_cleaning = (is_cleaning == '1')
            # clear any pm-related amounts
            o.pm_amount = 0.0
            o.net_after_pm = float(o.net_amount or 0.0)
            db.add(o)
        db.commit()
        return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post('/expenses/bulk_delete')
async def bulk_delete_expenses(request: Request, ids: str = Form(...), delete_series_if_present: str = Form(None), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        id_list = [int(x) for x in ids.split(',') if x]
        if delete_series_if_present:
            for eid in id_list:
                exp = db.query(Expense).filter(Expense.id == eid).first()
                if exp and exp.recurrence_id:
                    rec_id = exp.recurrence_id
                    db.query(Expense).filter(Expense.recurrence_id == rec_id).delete()
                    from app.models import Recurrence
                    db.query(Recurrence).filter(Recurrence.id == rec_id).delete()
            db.commit()
        else:
            db.query(Expense).filter(Expense.id.in_(id_list)).delete(synchronize_session=False)
            db.commit()
        return RedirectResponse(url=(next or '/money/expenses'), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()

@router.get("/incomes")
async def incomes_index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        from sqlalchemy.orm import joinedload
        incomes = db.query(Income).options(joinedload(Income.recurrence)).order_by(Income.date.desc()).limit(50).all()
        apartments = db.query(Apartment).all()
        platforms = db.query(Platform).all()
        attachments = db.query(Attachment).all()
        attachments_by_income = {}
        if incomes:
            # infer recurrence for incomes without id
            from app.models import Recurrence
            for inc in incomes:
                if not inc.recurrence_id and inc.date:
                    r = db.query(Recurrence).filter(Recurrence.start_date <= inc.date)
                    r = r.filter((Recurrence.end_date == None) | (Recurrence.end_date >= inc.date)).first()
                    if r:
                        inc.recurrence = r
                        setattr(inc, '_orig_recurrence_id', r.id)
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
        default_date = request.query_params.get('date') or ''
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
            # build a mapping for apartment to its PM percent and PM id for client-side default behavior (used in JS)
            apt_pm_map = {}
            for apt in apartments:
                if apt.property_manager_id:
                    pm = db.query(PropertyManager).filter(PropertyManager.id == apt.property_manager_id).first()
                    apt_pm_map[apt.id] = {"percent": float(pm.percent or 0.0) if pm else 0.0, "pm_id": pm.id if pm else None}
                else:
                    apt_pm_map[apt.id] = {"percent": 0.0, "pm_id": None}
        return templates.TemplateResponse(request, "incomes_index.html", {"incomes": incomes, "apartments": apartments, "platforms": platforms, "attachments": attachments, "attachments_by_income": attachments_by_income, "default_apartment_id": default_apartment_id, "default_associated_pm_id": default_associated_pm_id, "default_pm_percent": default_pm_percent, "next": next_url, "default_date": default_date})
    finally:
        db.close()


@router.post("/incomes/add")
async def add_income(request: Request, gross_amount: float = Form(...), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), associate_pm: str = Form(None), notes: str = Form(''), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
        # If an apartment is provided but no associated_pm_id and user opted in, set it to the apartment's pm
        if associate_pm and not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
                # if pm_percent not provided (0), use property manager default
                pm = db.query(PropertyManager).filter(PropertyManager.id == associated_pm_id).first()
                if pm and (pm_percent is None or float(pm_percent) == 0.0):
                    pm_percent = float(pm.percent or 0.0)
        # repeat logic if still missing (legacy duplicate block)
        if associate_pm and not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
                pm = db.query(PropertyManager).filter(PropertyManager.id == associated_pm_id).first()
                if pm and (pm_percent is None or float(pm_percent) == 0.0):
                    pm_percent = float(pm.percent or 0.0)
        # Now compute pm_amount based on (maybe updated) pm_percent
        pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
        net_after_pm = round(net_amount - pm_amount, 2)
        # Create recurrence if needed (respect start/end)
        recurrence_id = None
        if recurrence and recurrence in ("monthly", "yearly"):
            from app.models import Recurrence
            def norm_date_str(s, default_day="-01"):
                if not s:
                    return None
                if len(s) == 7:
                    return s + default_day
                return s
            start_date_val = norm_date_str(recurrence_start) or date
            end_date_val = norm_date_str(recurrence_end)
            r = Recurrence(type=recurrence, start_date=start_date_val, end_date=end_date_val)
            db.add(r)
            db.commit()
            recurrence_id = r.id
        e = Income(apartment_id=apartment_id, platform_id=platform_id, date=date, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=pm_amount, net_after_pm=net_after_pm, recurrence_id=recurrence_id, associated_pm_id=(associated_pm_id if associate_pm else None), notes=notes)
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
        # If this income has a recurrence, materialize occurrences within the requested range
        if recurrence_id:
            try:
                from datetime import datetime
                def add_months(dt, months):
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)
                    return datetime(y, m, d)

                r = db.query(Recurrence).filter(Recurrence.id == recurrence_id).first()
                start = datetime.strptime(r.start_date, '%Y-%m-%d')
                end = None
                if r.end_date:
                    end = datetime.strptime(r.end_date, '%Y-%m-%d')
                if not end:
                    if recurrence == 'monthly':
                        end = add_months(start, 11)
                    else:
                        end = start.replace(year=start.year + 3)
                curr = start
                while curr <= end:
                    ds = curr.strftime('%Y-%m-%d')
                    if ds != date:
                        new_e = Income(apartment_id=apartment_id, platform_id=platform_id, date=ds, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=associated_pm_id, recurrence_id=recurrence_id, notes=notes)
                        db.add(new_e)
                    if recurrence == 'monthly':
                        curr = add_months(curr, 1)
                    else:
                        curr = curr.replace(year=curr.year + 1)
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
        from sqlalchemy.orm import joinedload
        e = db.query(Income).options(joinedload(Income.recurrence)).filter(Income.id == income_id).first()
        if not e:
            return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
        inferred = None
        if not e.recurrence_id and e.date:
            r = db.query(Recurrence).filter(Recurrence.start_date <= e.date)
            r = r.filter((Recurrence.end_date == None) | (Recurrence.end_date >= e.date)).first()
            if r:
                inferred = r
                e.recurrence = r
        if inferred:
            setattr(e, '_orig_recurrence_id', inferred.id)
        apartments = db.query(Apartment).all()
        platforms = db.query(Platform).all()
        pms = db.query(PropertyManager).all()
        attached = db.query(Attachment).filter(Attachment.income_id == e.id).all()
        next_url = request.query_params.get('next') or None
        _ = e.recurrence
        return templates.TemplateResponse(request, 'income_edit.html', {"income": e, "apartments": apartments, "platforms": platforms, "pms": pms, "attached": attached, "next": next_url})
    finally:
        db.close()

@router.api_route('/incomes/{income_id}/edit', methods=["POST","PUT","PATCH"])
async def edit_income_post(request: Request, income_id: int, gross_amount: float = Form(...), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), associate_pm: str = Form(None), notes: str = Form(''), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), apply_to: str = Form('single'), user=Depends(admin_required)):
    await log_request_form(request)
    print('EDIT INCOME POST CALLED for', income_id)
    db = SessionLocal()
    try:
        e = db.query(Income).filter(Income.id == income_id).first()
        if not e:
              return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
        # Handle recurrence conversion: if entry was not recurring and user selected a recurrence, create or reattach it
        form = await request.form()
        orig_recur = form.get('orig_recurrence_id') if form else None
        print('EDIT INCOME recurrence value (param):', recurrence, 'orig', orig_recur)
        print('Current e.recurrence_id:', e.recurrence_id)
        print('Cond checks:', not e.recurrence_id, bool(recurrence), recurrence in ('monthly','yearly'))
        created_recurrence = False
        # if the record was previously part of a series but got detached, reattach (only if type matches)
        if not e.recurrence_id and orig_recur and recurrence in ('monthly','yearly'):
            try:
                from app.models import Recurrence
                r0 = db.query(Recurrence).filter(Recurrence.id == int(orig_recur)).first()
                if r0 and r0.type == recurrence:
                    e.recurrence_id = r0.id
                    db.add(e)
                    db.commit()
            except Exception:
                pass
        if not e.recurrence_id and recurrence and recurrence in ('monthly', 'yearly'):
            from app.models import Recurrence
            def norm_date_str(s, default_day="-01"):
                if not s:
                    return None
                if len(s) == 7:
                    return s + default_day
                return s
            start_date_val = norm_date_str(recurrence_start) or date
            end_date_val = norm_date_str(recurrence_end)
            r = Recurrence(type=recurrence, start_date=start_date_val, end_date=end_date_val, notes=notes)
            db.add(r)
            db.commit()
            e.recurrence_id = r.id
            db.add(e)
            db.commit()
            # materialize occurrences for the requested range
            try:
                from datetime import datetime
                def add_months(dt, months):
                    y = dt.year + (dt.month - 1 + months) // 12
                    m = (dt.month - 1 + months) % 12 + 1
                    d = min(dt.day, 28)
                    return datetime(y, m, d)
                start = datetime.strptime(r.start_date, '%Y-%m-%d')
                end = None
                if r.end_date:
                    end = datetime.strptime(r.end_date, '%Y-%m-%d')
                if not end:
                    if recurrence == 'monthly':
                        end = add_months(start, 11)
                    else:
                        end = start.replace(year=start.year + 3)
                curr = start
                while curr <= end:
                    ds = curr.strftime('%Y-%m-%d')
                    if ds != date:
                        new_i = Income(apartment_id=apartment_id, platform_id=platform_id, date=ds, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=round(gross_amount * (1 - (vat_percent / 100.0)), 2), pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(round(gross_amount * (1 - (vat_percent / 100.0)), 2) - round(gross_amount * (pm_percent / 100.0), 2), 2), associated_pm_id=associated_pm_id, recurrence_id=r.id, notes=notes)
                        db.add(new_i)
                    if recurrence == 'monthly':
                        curr = add_months(curr, 1)
                    else:
                        curr = curr.replace(year=curr.year + 1)
                db.commit()
            except Exception:
                pass
            created_recurrence = True

        # Handle editing apply scope
        if e.recurrence_id and apply_to == 'series':
            from app.models import Recurrence
            r = db.query(Recurrence).filter(Recurrence.id == e.recurrence_id).first()
            if r:
                def norm_date_str(s, default_day="-01"):
                    if not s:
                        return None
                    if len(s) == 7:
                        return s + default_day
                    return s
                if recurrence_start:
                    r.start_date = norm_date_str(recurrence_start) or r.start_date
                else:
                    r.start_date = date
                if recurrence_end:
                    r.end_date = norm_date_str(recurrence_end)
                r.notes = notes
                db.add(r)
                db.commit()
                # remove other occurrences
                db.query(Income).filter(Income.recurrence_id == r.id, Income.id != e.id).delete()
                db.commit()
                # update base entry
                e.date = r.start_date
                e.gross_amount = gross_amount
                e.vat_percent = vat_percent
                e.net_amount = round(gross_amount * (1 - (vat_percent / 100.0)), 2)
                e.pm_percent = pm_percent
                e.pm_amount = round(gross_amount * (pm_percent / 100.0), 2)
                e.net_after_pm = round(e.net_amount - e.pm_amount, 2)
                e.apartment_id = apartment_id
                e.platform_id = platform_id
                e.associated_pm_id = associated_pm_id if associate_pm else None
                e.notes = notes
                db.add(e)
                db.commit()
                # regenerate occurrences
                try:
                    from datetime import datetime
                    def add_months(dt, months):
                        y = dt.year + (dt.month - 1 + months) // 12
                        m = (dt.month - 1 + months) % 12 + 1
                        d = min(dt.day, 28)
                        return datetime(y, m, d)
                    start_dt = datetime.strptime(r.start_date, '%Y-%m-%d')
                    end = None
                    if r.end_date:
                        end = datetime.strptime(r.end_date, '%Y-%m-%d')
                    if not end:
                        if r.type == 'monthly':
                            end = add_months(start_dt, 11)
                        else:
                            end = start_dt.replace(year=start_dt.year + 3)
                    curr = start_dt
                    while curr <= end:
                        ds = curr.strftime('%Y-%m-%d')
                        if ds != e.date:
                            new_i = Income(apartment_id=apartment_id, platform_id=platform_id, date=ds, gross_amount=gross_amount, vat_percent=vat_percent, net_amount=e.net_amount, pm_percent=pm_percent, pm_amount=round(gross_amount * (pm_percent / 100.0), 2), net_after_pm=round(e.net_amount - (round(gross_amount * (pm_percent / 100.0), 2)), 2), associated_pm_id=(associated_pm_id if associate_pm else None), recurrence_id=r.id, notes=notes)
                            db.add(new_i)
                        if r.type == 'monthly':
                            curr = add_months(curr, 1)
                        else:
                            curr = curr.replace(year=curr.year + 1)
                    db.commit()
                except Exception:
                    pass
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
            if associate_pm and not associated_pm_id and apartment_id:
                apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
                if apt and apt.property_manager_id:
                    associated_pm_id = apt.property_manager_id
            e.platform_id = platform_id
            e.associated_pm_id = associated_pm_id if associate_pm else None
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


@router.post('/incomes/bulk_edit')
async def bulk_edit_incomes(request: Request, ids: str = Form(...), notes: str = Form(None), net_amount: float = Form(None), gross_amount: float = Form(None), apartment_id: int = Form(None), vat_percent: float = Form(None), associated_pm_id: int = Form(None), pm_percent: float = Form(None), date: str = Form(None), platform_id: int = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        id_list = [int(x) for x in ids.split(',') if x]
        occs = db.query(Income).filter(Income.id.in_(id_list)).all()
        for o in occs:
            if notes is not None and notes != '':
                o.notes = notes
            if gross_amount is not None:
                o.gross_amount = float(gross_amount)
            if vat_percent is not None:
                o.vat_percent = float(vat_percent)
            if net_amount is not None:
                o.net_amount = float(net_amount)
            else:
                # recompute net if gross/vat provided or leave as is
                if gross_amount is not None and vat_percent is not None:
                    o.net_amount = round(float(gross_amount) * (1 - (float(vat_percent) / 100.0)), 2)
            if pm_percent is not None:
                o.pm_percent = float(pm_percent)
            if apartment_id:
                o.apartment_id = apartment_id
            if associated_pm_id:
                o.associated_pm_id = associated_pm_id
            if platform_id:
                o.platform_id = platform_id
            if date:
                o.date = date
            # recompute pm_amount and net_after_pm
            o.pm_amount = round(float(o.gross_amount) * (float(o.pm_percent or 0.0) / 100.0), 2)
            o.net_after_pm = round((float(o.net_amount or 0.0)) - o.pm_amount, 2)
            db.add(o)
        db.commit()
        return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post('/incomes/bulk_delete')
async def bulk_delete_incomes(request: Request, ids: str = Form(...), delete_series_if_present: str = Form(None), next: str = Form(None), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        id_list = [int(x) for x in ids.split(',') if x]
        if delete_series_if_present:
            # if requested, delete entire series for each selected income that has recurrence
            for iid in id_list:
                inc = db.query(Income).filter(Income.id == iid).first()
                if inc and inc.recurrence_id:
                    rec_id = inc.recurrence_id
                    db.query(Income).filter(Income.recurrence_id == rec_id).delete()
                    from app.models import Recurrence
                    db.query(Recurrence).filter(Recurrence.id == rec_id).delete()
            db.commit()
        else:
            db.query(Income).filter(Income.id.in_(id_list)).delete(synchronize_session=False)
            db.commit()
        return RedirectResponse(url=(next or '/money/incomes'), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()
