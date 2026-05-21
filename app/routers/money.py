from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from typing import List
from urllib.parse import parse_qsl, urlencode, urlsplit
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from app.constants import DEFAULT_IVA, DEFAULT_STAMP_DUTY, DIRECT_BOOKING_PLATFORM_NOTE
from app.db import SessionLocal
from app.models import Expense, Income, Apartment, PropertyManager, Platform, Company, Attachment, Recurrence, Cleaning
from app.auth_utils import admin_required, get_current_user
from app.debug import log_request_form
from app.utils import (
    get_income_effective_amount,
    get_income_pm_amount,
    get_income_pm_base_amount,
    get_income_stamp_duty_amount,
    get_setting_float,
    advance_recurrence_date,
    expand_open_recurrences_to_current_year,
    format_date_value,
    normalize_recurrence_date,
    parse_date_value,
    sync_recurrence_entries,
)

router = APIRouter(prefix="/money")
from app.main import templates


def _current_year():
    from datetime import datetime

    return datetime.now().year


def _build_route_url(path: str, **params) -> str:
    clean_params = {key: value for key, value in params.items() if value not in (None, '')}
    if not clean_params:
        return path
    return f"{path}?{urlencode(clean_params)}"


def _align_overview_redirect_year(next_url: str | None, entry_date) -> str | None:
    if not next_url:
        return next_url
    try:
        parsed_entry_date = parse_date_value(entry_date)
    except (TypeError, ValueError):
        return next_url
    if not parsed_entry_date:
        return next_url

    parsed_next_url = urlsplit(next_url)
    if parsed_next_url.path != "/overview":
        return next_url

    aligned_query = []
    year_found = False
    for key, value in parse_qsl(parsed_next_url.query, keep_blank_values=True):
        if key == 'year':
            aligned_query.append((key, str(parsed_entry_date.year)))
            year_found = True
        else:
            aligned_query.append((key, value))
    if not year_found:
        aligned_query.append(('year', str(parsed_entry_date.year)))

    return parsed_next_url._replace(query=urlencode(aligned_query)).geturl()


async def _collect_uploaded_attachment_payloads(files: list[UploadFile] | None):
    from app.routers.attachments import AttachmentUploadValidationError, collect_uploaded_attachment_payloads

    try:
        return await collect_uploaded_attachment_payloads(files)
    except AttachmentUploadValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc


def _attach_existing_attachments(db, attachment_ids: List[int] = None, expense_id: int = None, income_id: int = None):
    if not attachment_ids:
        return
    for attachment_id in attachment_ids:
        attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
        if not attachment:
            continue
        if expense_id is not None:
            attachment.expense_id = expense_id
            attachment.income_id = None
        if income_id is not None:
            attachment.income_id = income_id
            attachment.expense_id = None
        db.add(attachment)


def _persist_uploaded_attachment_payloads(db, uploaded_files, expense_id: int = None, income_id: int = None):
    if not uploaded_files:
        return
    from app.routers.attachments import persist_uploaded_attachments

    persist_uploaded_attachments(db, uploaded_files, expense_id=expense_id, income_id=income_id)


def _find_recurrence_for_date(db, entry_date):
    if not entry_date:
        return None
    query = db.query(Recurrence).filter(Recurrence.start_date <= entry_date)
    query = query.filter((Recurrence.end_date == None) | (Recurrence.end_date >= entry_date))
    return query.order_by(Recurrence.start_date.desc(), Recurrence.id.desc()).first()


def _create_recurrence(db, recurrence_type, recurrence_start, recurrence_end, fallback_date, notes=''):
    if recurrence_type not in ('monthly', 'yearly'):
        return None
    recurrence = Recurrence(
        type=recurrence_type,
        start_date=normalize_recurrence_date(recurrence_start) or fallback_date,
        end_date=normalize_recurrence_date(recurrence_end),
        notes=notes,
    )
    db.add(recurrence)
    db.commit()
    return recurrence


def _expected_recurrence_entry_date(recurrence, reference_date):
    start_value = parse_date_value(recurrence.start_date if recurrence else None)
    reference_value = parse_date_value(reference_date)
    if not start_value or not reference_value or not recurrence or recurrence.type not in ('monthly', 'yearly'):
        return format_date_value(reference_value) or reference_date

    if recurrence.type == 'monthly':
        delta = ((reference_value.year - start_value.year) * 12) + (reference_value.month - start_value.month)
    else:
        delta = reference_value.year - start_value.year

    if delta <= 0:
        return format_date_value(start_value)
    return format_date_value(advance_recurrence_date(start_value, recurrence.type, steps=delta))


def _rejoin_detached_expense(db, expense, recurrence):
    target_date = _expected_recurrence_entry_date(recurrence, expense.date)
    source = (
        db.query(Expense)
        .filter(Expense.recurrence_id == recurrence.id, Expense.date == target_date)
        .order_by(Expense.id.asc())
        .first()
    )
    if not source:
        source = (
            db.query(Expense)
            .filter(Expense.recurrence_id == recurrence.id)
            .order_by(Expense.date.asc(), Expense.id.asc())
            .first()
        )

    if source:
        _populate_expense_fields(
            expense,
            float(source.gross_amount or 0.0),
            float(source.vat_percent or 0.0),
            float(source.net_amount or 0.0),
            target_date,
            source.apartment_id,
            source.associated_pm_id,
            source.associated_company_id,
            source.notes or '',
            bool(source.is_cleaning),
        )
        expense.category = source.category
    else:
        expense.date = target_date

    expense.recurrence_id = recurrence.id
    expense.orig_recurrence_id = None
    db.add(expense)
    db.flush()

    duplicate = (
        db.query(Expense)
        .filter(Expense.recurrence_id == recurrence.id, Expense.date == expense.date, Expense.id != expense.id)
        .order_by(Expense.id.asc())
        .first()
    )
    if duplicate:
        db.delete(duplicate)

    db.commit()


def _rejoin_detached_income(db, income, recurrence):
    target_date = _expected_recurrence_entry_date(recurrence, income.date)
    source = (
        db.query(Income)
        .filter(Income.recurrence_id == recurrence.id, Income.date == target_date)
        .order_by(Income.id.asc())
        .first()
    )
    if not source:
        source = (
            db.query(Income)
            .filter(Income.recurrence_id == recurrence.id)
            .order_by(Income.date.asc(), Income.id.asc())
            .first()
        )

    if source:
        _populate_income_fields(
            income,
            float(source.gross_amount or 0.0),
            float(source.vat_percent or 0.0),
            float(source.pm_percent or 0.0),
            target_date,
            source.apartment_id,
            source.platform_id,
            source.associated_pm_id,
            source.notes or '',
        )
    else:
        income.date = target_date

    income.recurrence_id = recurrence.id
    income.orig_recurrence_id = None
    db.add(income)
    db.flush()

    duplicate = (
        db.query(Income)
        .filter(Income.recurrence_id == recurrence.id, Income.date == income.date, Income.id != income.id)
        .order_by(Income.id.asc())
        .first()
    )
    if duplicate:
        db.delete(duplicate)

    db.commit()


def _sync_expense_recurrence(db, recurrence, expense, reset=False):
    sync_recurrence_entries(
        db,
        Expense,
        recurrence,
        source_entry=expense,
        current_year=_current_year(),
        reset=reset,
    )
    db.commit()


def _sync_income_recurrence(db, recurrence, income, reset=False):
    sync_recurrence_entries(
        db,
        Income,
        recurrence,
        source_entry=income,
        current_year=_current_year(),
        reset=reset,
    )
    db.commit()


def _resolve_taxable_amounts(gross_amount, vat_percent, explicit_net_amount):
    resolved_gross_amount = None if gross_amount is None else round(float(gross_amount), 2)
    resolved_net_amount = None if explicit_net_amount is None else round(float(explicit_net_amount), 2)
    vat_factor = 1 + (float(vat_percent or 0.0) / 100.0)

    if resolved_gross_amount is None and resolved_net_amount is None:
        raise ValueError("Either gross_amount or net_amount is required")

    if vat_factor <= 0:
        raise ValueError("vat_percent must be greater than -100")

    if resolved_gross_amount is None:
        resolved_gross_amount = round(resolved_net_amount * vat_factor, 2)

    if resolved_net_amount is None:
        resolved_net_amount = round(resolved_gross_amount / vat_factor, 2)

    return resolved_gross_amount, resolved_net_amount


def _resolve_expense_amounts(gross_amount, vat_percent, explicit_net_amount):
    return _resolve_taxable_amounts(gross_amount, vat_percent, explicit_net_amount)


def _resolve_income_amounts(gross_amount, vat_percent, explicit_net_amount):
    return _resolve_taxable_amounts(gross_amount, vat_percent, explicit_net_amount)


def _populate_expense_fields(expense, gross_amount, vat_percent, net_amount, entry_date, apartment_id, associated_pm_id, associated_company_id, notes, is_cleaning):
    expense.gross_amount = gross_amount
    expense.vat_percent = vat_percent
    expense.net_amount = net_amount
    expense.pm_percent = 0.0
    expense.pm_amount = 0.0
    expense.net_after_pm = float(net_amount or 0.0)
    expense.date = entry_date
    expense.apartment_id = apartment_id
    expense.associated_pm_id = associated_pm_id
    expense.associated_company_id = associated_company_id
    expense.notes = notes
    expense.is_cleaning = is_cleaning


def _resolve_income_pm(db, apartment_id, associated_pm_id, associate_pm, pm_percent):
    resolved_pm_id = associated_pm_id
    resolved_pm_percent = float(pm_percent or 0.0)
    if associate_pm and not resolved_pm_id and apartment_id:
        apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
        if apt and apt.property_manager_id:
            resolved_pm_id = apt.property_manager_id
            pm = db.query(PropertyManager).filter(PropertyManager.id == resolved_pm_id).first()
            if pm and resolved_pm_percent == 0.0:
                resolved_pm_percent = float(pm.percent or 0.0)
    return resolved_pm_id, resolved_pm_percent


def _resolve_income_stamp_duty(has_stamp_duty, stamp_duty_amount, default_stamp_duty_amount):
    stamp_enabled = str(has_stamp_duty or '').lower() in ('1', 'true', 'on', 'yes')
    if not stamp_enabled:
        return False, 0.0
    resolved_stamp_duty_amount = default_stamp_duty_amount if stamp_duty_amount is None else float(stamp_duty_amount)
    if resolved_stamp_duty_amount < 0:
        raise ValueError('stamp_duty_amount must be greater than or equal to 0')
    return True, round(resolved_stamp_duty_amount, 2)


def _get_default_income_vat_percent():
    return get_setting_float('default_iva', DEFAULT_IVA, minimum=0.0)


def _get_default_stamp_duty_amount():
    return get_setting_float('default_stamp_duty', DEFAULT_STAMP_DUTY, minimum=0.0)


def _get_direct_booking_platform(db):
    return (
        db.query(Platform)
        .filter(Platform.notes == DIRECT_BOOKING_PLATFORM_NOTE)
        .order_by(Platform.id.asc())
        .first()
    )


def _populate_income_fields(income, gross_amount, vat_percent, pm_percent, net_amount, entry_date, apartment_id, platform_id, associated_pm_id, notes, has_stamp_duty=False, stamp_duty_amount=0.0):
    net_amount = round(float(net_amount or 0.0), 2)
    resolved_stamp_duty_amount = round(float(stamp_duty_amount or 0.0), 2) if has_stamp_duty else 0.0
    pm_base_amount = round(net_amount - resolved_stamp_duty_amount, 2)
    pm_amount = round(pm_base_amount * (pm_percent / 100.0), 2)
    income.gross_amount = gross_amount
    income.vat_percent = vat_percent
    income.net_amount = net_amount
    income.has_stamp_duty = has_stamp_duty
    income.stamp_duty_amount = resolved_stamp_duty_amount
    income.pm_percent = pm_percent
    income.pm_amount = pm_amount
    income.net_after_pm = round(pm_base_amount - pm_amount, 2)
    income.date = entry_date
    income.apartment_id = apartment_id
    income.platform_id = platform_id
    income.associated_pm_id = associated_pm_id
    income.notes = notes


@router.get("/expenses")
async def expenses_index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        from sqlalchemy.orm import joinedload
        next_url = request.query_params.get('next') or None
        default_date = request.query_params.get('date') or ''
        create_mode = request.query_params.get('mode') == 'create' or bool(next_url or default_date)
        expenses = []
        attachments_by_expense = {}
        platform_name_by_expense = {}
        if not create_mode:
            expenses = db.query(Expense).options(joinedload(Expense.recurrence)).order_by(Expense.date.desc()).limit(50).all()
            if expenses:
                expense_ids = [e.id for e in expenses if e.id]
                if expense_ids:
                    ats = db.query(Attachment).filter(Attachment.expense_id.in_(expense_ids)).all()
                    for a in ats:
                        attachments_by_expense.setdefault(a.expense_id, []).append(a)
                    cleanings = (
                        db.query(Cleaning)
                        .options(joinedload(Cleaning.income).joinedload(Income.platform))
                        .filter(Cleaning.expense_id.in_(expense_ids))
                        .all()
                    )
                    for cleaning in cleanings:
                        linked_income = getattr(cleaning, 'income', None)
                        linked_platform = getattr(linked_income, 'platform', None) if linked_income else None
                        platform_name_by_expense[cleaning.expense_id] = linked_platform.name if linked_platform else None
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        cleaning_companies = db.query(Company).filter(Company.is_cleaning_company == True).order_by(Company.company_name).all()
        attachments = (
            db.query(Attachment)
            .filter(Attachment.expense_id == None, Attachment.income_id == None)
            .order_by(Attachment.created_at.desc())
            .all()
        )
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
        # build a mapping for apartment to PM metadata used by the client-side defaults
        apt_pm_map = {}
        for apt in apartments:
            if apt.property_manager_id:
                pm = db.query(PropertyManager).filter(PropertyManager.id == apt.property_manager_id).first()
                apt_pm_map[apt.id] = {"percent": float(pm.percent or 0.0) if pm else 0.0, "pm_id": pm.id if pm else None}
            else:
                apt_pm_map[apt.id] = {"percent": 0.0, "pm_id": None}
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
            e.platform_name = platform_name_by_expense.get(e.id)
        expense_upload_return = _build_route_url(
            "/money/expenses",
            mode=('create' if create_mode else None),
            date=(default_date or None),
            next=next_url,
        )
        expense_create_url = _build_route_url("/money/expenses", mode='create', next=next_url)
        return templates.TemplateResponse(request, "expenses_index.html", {"expenses": expenses, "apartments": apartments, "pms": pms, "cleaning_companies": cleaning_companies, "attachments": attachments, "attachments_by_expense": attachments_by_expense, "default_apartment_id": default_apartment_id, "default_associated_pm_id": default_associated_pm_id, "default_pm_percent": default_pm_percent, "apt_pm_map": apt_pm_map, "next": next_url, "default_date": default_date, "create_mode": create_mode, "expense_upload_return": expense_upload_return, "expense_create_url": expense_create_url})
    finally:
        db.close()


@router.post("/expenses/add")
async def add_expense(request: Request, gross_amount: float = Form(None), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), is_cleaning: str = Form('0'), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), associate_pm: str = Form(None), notes: str = Form(''), next: str = Form(None), files: list[UploadFile] | None = File(None, alias="file"), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        uploaded_files = await _collect_uploaded_attachment_payloads(files)
        try:
            resolved_gross_amount, resolved_net_amount = _resolve_expense_amounts(gross_amount, vat_percent, net_amount)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if associate_pm and not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
        recurrence_record = _create_recurrence(db, recurrence, recurrence_start, recurrence_end, date, notes=notes)
        e = Expense(recurrence_id=(recurrence_record.id if recurrence_record else None))
        _populate_expense_fields(
            e,
            resolved_gross_amount,
            vat_percent,
            resolved_net_amount,
            date,
            apartment_id,
            associated_pm_id,
            associated_company_id,
            notes,
            (is_cleaning == '1'),
        )
        db.add(e)
        db.commit()
        if recurrence_record:
            _sync_expense_recurrence(db, recurrence_record, e, reset=True)
        _attach_existing_attachments(db, attachment_ids, expense_id=e.id)
        _persist_uploaded_attachment_payloads(db, uploaded_files, expense_id=e.id)
        if attachment_ids or uploaded_files:
            db.commit()
        next_url = _align_overview_redirect_year(next, e.date)
        if next_url:
            return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/money/expenses", status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()

@router.get('/expenses/{expense_id}/edit')
async def edit_expense_get(request: Request, expense_id: int):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        from sqlalchemy.orm import joinedload
        e = db.query(Expense).options(joinedload(Expense.recurrence), joinedload(Expense.orig_recurrence)).filter(Expense.id == expense_id).first()
        if not e:
            return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
        display_recurrence = e.recurrence or e.orig_recurrence
        apartments = db.query(Apartment).all()
        pms = db.query(PropertyManager).all()
        companies = db.query(Company).all()
        attached = db.query(Attachment).filter(Attachment.expense_id == e.id).all()
        next_url = request.query_params.get('next') or None
        # collect all siblings in the same series
        series_items = []
        series_recurrence_id = e.recurrence_id or e.orig_recurrence_id
        if series_recurrence_id:
            series_items = db.query(Expense).filter(Expense.recurrence_id == series_recurrence_id).order_by(Expense.date).all()
        return templates.TemplateResponse(request, 'expense_edit.html', {"expense": e, "apartments": apartments, "pms": pms, "companies": companies, "attached": attached, "next": next_url, "series_items": series_items, "display_recurrence": display_recurrence})
    finally:
        db.close()

@router.api_route('/expenses/{expense_id}/edit', methods=["POST","PUT","PATCH"])
async def edit_expense_post(request: Request, expense_id: int, gross_amount: float = Form(None), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), associated_pm_id: int = Form(None), associated_company_id: int = Form(None), is_cleaning: str = Form('0'), associate_pm: str = Form(None), notes: str = Form(''), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), apply_to: str = Form('single'), next: str = Form(None), files: list[UploadFile] | None = File(None, alias="file"), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        e = db.query(Expense).filter(Expense.id == expense_id).first()
        if not e:
            return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
        form = await request.form()
        uploaded_files = await _collect_uploaded_attachment_payloads(files)
        recurrence = (form.get('recurrence') if form else None) or recurrence
        orig_recur = ((form.get('orig_recurrence_id') if form else None) or e.orig_recurrence_id)
        rejoin_recurrence = form.get('rejoin_recurrence') if form else None
        created_recurrence = None
        next_url = (form.get('next') if form else None) or next
        try:
            resolved_gross_amount, resolved_net_amount = _resolve_expense_amounts(gross_amount, vat_percent, net_amount)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if associate_pm and not associated_pm_id and apartment_id:
            apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
            if apt and apt.property_manager_id:
                associated_pm_id = apt.property_manager_id
        if rejoin_recurrence and not e.recurrence_id and orig_recur:
            try:
                target_recurrence = db.query(Recurrence).filter(Recurrence.id == int(orig_recur)).first()
            except (TypeError, ValueError):
                target_recurrence = None
            if target_recurrence:
                _rejoin_detached_expense(db, e, target_recurrence)
                next_url = _align_overview_redirect_year(next_url, e.date)
                if next_url:
                    return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
                return RedirectResponse(url='/money/expenses', status_code=HTTP_303_SEE_OTHER)
        if not e.recurrence_id and orig_recur and recurrence in ('monthly', 'yearly') and apply_to == 'series':
            try:
                r0 = db.query(Recurrence).filter(Recurrence.id == int(orig_recur)).first()
                if r0 and r0.type == recurrence:
                    e.recurrence_id = r0.id
                    e.orig_recurrence_id = None
                    db.add(e)
                    db.commit()
            except Exception:
                pass
        if not e.recurrence_id and recurrence and recurrence in ('monthly', 'yearly'):
            created_recurrence = _create_recurrence(db, recurrence, recurrence_start, recurrence_end, date, notes=notes)
            e.recurrence_id = created_recurrence.id
            e.orig_recurrence_id = None
            db.add(e)
            db.commit()
        if e.recurrence_id and apply_to == 'series':
            r = db.query(Recurrence).filter(Recurrence.id == e.recurrence_id).first()
            if r:
                if recurrence in ('monthly', 'yearly'):
                    r.type = recurrence
                r.start_date = normalize_recurrence_date(recurrence_start) or date
                r.end_date = normalize_recurrence_date(recurrence_end)
                r.next_date = None
                r.notes = notes
                db.add(r)
                db.commit()
                db.query(Expense).filter(Expense.recurrence_id == r.id, Expense.id != e.id).delete()
                db.commit()
                _populate_expense_fields(
                    e,
                    resolved_gross_amount,
                    vat_percent,
                    resolved_net_amount,
                    r.start_date,
                    apartment_id,
                    (associated_pm_id if associate_pm else None),
                    associated_company_id,
                    notes,
                    (is_cleaning == '1'),
                )
                e.orig_recurrence_id = None
                db.add(e)
                db.commit()
                _sync_expense_recurrence(db, r, e, reset=True)
        else:
            if e.recurrence_id and apply_to == 'single' and not created_recurrence:
                e.orig_recurrence_id = e.recurrence_id
                e.recurrence_id = None
            elif created_recurrence or e.recurrence_id:
                e.orig_recurrence_id = None
            _populate_expense_fields(
                e,
                resolved_gross_amount,
                vat_percent,
                resolved_net_amount,
                date,
                apartment_id,
                (associated_pm_id if associate_pm else None),
                associated_company_id,
                notes,
                (is_cleaning == '1'),
            )
            db.add(e)
            db.commit()
            if created_recurrence:
                _sync_expense_recurrence(db, created_recurrence, e, reset=True)
        _persist_uploaded_attachment_payloads(db, uploaded_files, expense_id=e.id)
        if uploaded_files:
            db.commit()
        next_url = _align_overview_redirect_year(next_url, e.date)
        if next_url:
            return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
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
            resolved_vat_percent = float(vat_percent) if vat_percent is not None else float(o.vat_percent or 0.0)
            if vat_percent is not None:
                o.vat_percent = resolved_vat_percent
            if gross_amount is not None or net_amount is not None or vat_percent is not None:
                source_gross_amount = gross_amount
                if source_gross_amount is None and net_amount is None and vat_percent is not None:
                    source_gross_amount = float(o.gross_amount or 0.0)
                try:
                    resolved_gross_amount, resolved_net_amount = _resolve_expense_amounts(
                        source_gross_amount,
                        resolved_vat_percent,
                        net_amount,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                o.gross_amount = resolved_gross_amount
                o.net_amount = resolved_net_amount
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
        expand_open_recurrences_to_current_year(db)
        from sqlalchemy.orm import joinedload
        next_url = request.query_params.get('next') or None
        default_date = request.query_params.get('date') or ''
        create_mode = request.query_params.get('mode') == 'create' or bool(next_url or default_date)
        focus_income_id = None
        focus_income_raw = request.query_params.get('focus_income_id')
        if focus_income_raw:
            try:
                focus_income_id = int(focus_income_raw)
            except (TypeError, ValueError):
                focus_income_id = None
        incomes = []
        if not create_mode:
            incomes = db.query(Income).options(joinedload(Income.recurrence)).order_by(Income.date.desc()).limit(50).all()
            if focus_income_id and all(inc.id != focus_income_id for inc in incomes):
                focused_income = db.query(Income).options(joinedload(Income.recurrence)).filter(Income.id == focus_income_id).first()
                if focused_income:
                    incomes.insert(0, focused_income)
        apartments = db.query(Apartment).all()
        platforms = db.query(Platform).all()
        direct_booking_platform = _get_direct_booking_platform(db)
        pms = db.query(PropertyManager).all()
        default_vat_percent = _get_default_income_vat_percent()
        default_stamp_duty_amount = _get_default_stamp_duty_amount()
        attachments = (
            db.query(Attachment)
            .filter(Attachment.expense_id == None, Attachment.income_id == None)
            .order_by(Attachment.created_at.desc())
            .all()
        )
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
        # Prefetch associated PM names and numeric fields to avoid lazy-loading in templates
        for inc in incomes:
            try:
                if inc.associated_pm:
                    inc.associated_pm_name = f"{inc.associated_pm.first_name} {inc.associated_pm.last_name}"
                else:
                    inc.associated_pm_name = None
                inc.pm_percent = float(inc.pm_percent or 0.0)
                inc.pm_amount = get_income_pm_amount(inc)
            except Exception:
                inc.associated_pm_name = None
                inc.pm_percent = float(getattr(inc, 'pm_percent', 0.0) or 0.0)
                inc.pm_amount = get_income_pm_amount(inc)
            inc.has_stamp_duty = bool(getattr(inc, 'has_stamp_duty', False))
            inc.stamp_duty_amount = get_income_stamp_duty_amount(inc)
            inc.pm_base_amount = get_income_pm_base_amount(inc)
            inc.net_after_pm = float(getattr(inc, 'net_after_pm', None) or get_income_effective_amount(inc))
            inc.vat_amount = round(float(getattr(inc, 'gross_amount', 0.0) or 0.0) - float(getattr(inc, 'net_amount', 0.0) or 0.0), 2)
            inc.cleaning_emoji = "🧹" if inc.apartment_id else ""
        # build a mapping for apartment to PM metadata used by the client-side defaults
        apt_pm_map = {}
        for apt in apartments:
            if apt.property_manager_id:
                pm = db.query(PropertyManager).filter(PropertyManager.id == apt.property_manager_id).first()
                apt_pm_map[apt.id] = {"percent": float(pm.percent or 0.0) if pm else 0.0, "pm_id": pm.id if pm else None}
            else:
                apt_pm_map[apt.id] = {"percent": 0.0, "pm_id": None}
        income_upload_return = _build_route_url(
            "/money/incomes",
            mode=('create' if create_mode else None),
            date=(default_date or None),
            next=next_url,
        )
        income_create_url = _build_route_url("/money/incomes", mode='create', next=next_url)
        return templates.TemplateResponse(request, "incomes_index.html", {"incomes": incomes, "apartments": apartments, "platforms": platforms, "pms": pms, "attachments": attachments, "attachments_by_income": attachments_by_income, "default_apartment_id": default_apartment_id, "default_associated_pm_id": default_associated_pm_id, "default_pm_percent": default_pm_percent, "default_vat_percent": default_vat_percent, "default_stamp_duty_amount": default_stamp_duty_amount, "direct_booking_platform_id": (direct_booking_platform.id if direct_booking_platform else None), "apt_pm_map": apt_pm_map, "next": next_url, "default_date": default_date, "focus_income_id": focus_income_id, "create_mode": create_mode, "income_upload_return": income_upload_return, "income_create_url": income_create_url})
    finally:
        db.close()


@router.post("/incomes/add")
async def add_income(request: Request, gross_amount: float = Form(None), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), attachment_ids: List[int] = Form(None), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), associate_pm: str = Form(None), has_stamp_duty: str = Form(None), stamp_duty_amount: float = Form(None), notes: str = Form(''), next: str = Form(None), files: list[UploadFile] | None = File(None, alias="file"), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        uploaded_files = await _collect_uploaded_attachment_payloads(files)
        resolved_pm_id, resolved_pm_percent = _resolve_income_pm(db, apartment_id, associated_pm_id, associate_pm, pm_percent)
        default_stamp_duty_amount = _get_default_stamp_duty_amount()
        try:
            resolved_gross_amount, resolved_net_amount = _resolve_income_amounts(gross_amount, vat_percent, net_amount)
            resolved_has_stamp_duty, resolved_stamp_duty_amount = _resolve_income_stamp_duty(has_stamp_duty, stamp_duty_amount, default_stamp_duty_amount)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        recurrence_record = _create_recurrence(db, recurrence, recurrence_start, recurrence_end, date, notes=notes)
        e = Income(recurrence_id=(recurrence_record.id if recurrence_record else None))
        _populate_income_fields(
            e,
            resolved_gross_amount,
            vat_percent,
            resolved_pm_percent,
            resolved_net_amount,
            date,
            apartment_id,
            platform_id,
            (resolved_pm_id if associate_pm else None),
            notes,
            resolved_has_stamp_duty,
            resolved_stamp_duty_amount,
        )
        db.add(e)
        db.commit()
        if recurrence_record:
            _sync_income_recurrence(db, recurrence_record, e, reset=True)
        _attach_existing_attachments(db, attachment_ids, income_id=e.id)
        _persist_uploaded_attachment_payloads(db, uploaded_files, income_id=e.id)
        if attachment_ids or uploaded_files:
            db.commit()
        next_url = _align_overview_redirect_year(next, e.date)
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
        expand_open_recurrences_to_current_year(db)
        from sqlalchemy.orm import joinedload
        e = db.query(Income).options(joinedload(Income.recurrence), joinedload(Income.orig_recurrence)).filter(Income.id == income_id).first()
        if not e:
            return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
        e.has_stamp_duty = bool(getattr(e, 'has_stamp_duty', False))
        e.stamp_duty_amount = get_income_stamp_duty_amount(e)
        e.pm_base_amount = get_income_pm_base_amount(e)
        e.pm_amount = get_income_pm_amount(e)
        e.net_after_pm = float(getattr(e, 'net_after_pm', None) or get_income_effective_amount(e))
        e.vat_amount = round(float(getattr(e, 'gross_amount', 0.0) or 0.0) - float(getattr(e, 'net_amount', 0.0) or 0.0), 2)
        display_recurrence = e.recurrence or e.orig_recurrence
        apartments = db.query(Apartment).all()
        platforms = db.query(Platform).all()
        direct_booking_platform = _get_direct_booking_platform(db)
        pms = db.query(PropertyManager).all()
        default_stamp_duty_amount = _get_default_stamp_duty_amount()
        attached = db.query(Attachment).filter(Attachment.income_id == e.id).all()
        next_url = request.query_params.get('next') or None
        # collect all siblings in the same series
        series_items = []
        series_recurrence_id = e.recurrence_id or e.orig_recurrence_id
        if series_recurrence_id:
            series_items = db.query(Income).filter(Income.recurrence_id == series_recurrence_id).order_by(Income.date).all()
        return templates.TemplateResponse(request, 'income_edit.html', {"income": e, "apartments": apartments, "platforms": platforms, "pms": pms, "attached": attached, "next": next_url, "series_items": series_items, "display_recurrence": display_recurrence, "default_stamp_duty_amount": default_stamp_duty_amount, "direct_booking_platform_id": (direct_booking_platform.id if direct_booking_platform else None)})
    finally:
        db.close()

@router.api_route('/incomes/{income_id}/edit', methods=["POST","PUT","PATCH"])
async def edit_income_post(request: Request, income_id: int, gross_amount: float = Form(None), net_amount: float = Form(None), vat_percent: float = Form(22.0), pm_percent: float = Form(0.0), date: str = Form(...), apartment_id: int = Form(None), platform_id: int = Form(None), associated_pm_id: int = Form(None), associate_pm: str = Form(None), has_stamp_duty: str = Form(None), stamp_duty_amount: float = Form(None), notes: str = Form(''), recurrence: str = Form('none'), recurrence_start: str = Form(None), recurrence_end: str = Form(None), apply_to: str = Form('single'), files: list[UploadFile] | None = File(None, alias="file"), user=Depends(admin_required)):
    await log_request_form(request)
    db = SessionLocal()
    try:
        e = db.query(Income).filter(Income.id == income_id).first()
        if not e:
            return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
        form = await request.form()
        uploaded_files = await _collect_uploaded_attachment_payloads(files)
        recurrence = (form.get('recurrence') if form else None) or recurrence
        orig_recur = ((form.get('orig_recurrence_id') if form else None) or e.orig_recurrence_id)
        rejoin_recurrence = form.get('rejoin_recurrence') if form else None
        created_recurrence = None
        resolved_pm_id, resolved_pm_percent = _resolve_income_pm(db, apartment_id, associated_pm_id, associate_pm, pm_percent)
        next_url = form.get('next') if form else None
        default_stamp_duty_amount = _get_default_stamp_duty_amount()
        try:
            resolved_gross_amount, resolved_net_amount = _resolve_income_amounts(gross_amount, vat_percent, net_amount)
            resolved_has_stamp_duty, resolved_stamp_duty_amount = _resolve_income_stamp_duty(has_stamp_duty, stamp_duty_amount, default_stamp_duty_amount)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if rejoin_recurrence and not e.recurrence_id and orig_recur:
            try:
                target_recurrence = db.query(Recurrence).filter(Recurrence.id == int(orig_recur)).first()
            except (TypeError, ValueError):
                target_recurrence = None
            if target_recurrence:
                _rejoin_detached_income(db, e, target_recurrence)
                next_url = _align_overview_redirect_year(next_url, e.date)
                if next_url:
                    return RedirectResponse(url=next_url, status_code=HTTP_303_SEE_OTHER)
                return RedirectResponse(url='/money/incomes', status_code=HTTP_303_SEE_OTHER)
        if not e.recurrence_id and orig_recur and recurrence in ('monthly','yearly') and apply_to == 'series':
            try:
                r0 = db.query(Recurrence).filter(Recurrence.id == int(orig_recur)).first()
                if r0 and r0.type == recurrence:
                    e.recurrence_id = r0.id
                    e.orig_recurrence_id = None
                    db.add(e)
                    db.commit()
            except Exception:
                pass
        if not e.recurrence_id and recurrence and recurrence in ('monthly', 'yearly'):
            created_recurrence = _create_recurrence(db, recurrence, recurrence_start, recurrence_end, date, notes=notes)
            e.recurrence_id = created_recurrence.id
            e.orig_recurrence_id = None
            db.add(e)
            db.commit()
        if e.recurrence_id and apply_to == 'series':
            r = db.query(Recurrence).filter(Recurrence.id == e.recurrence_id).first()
            if r:
                if recurrence in ('monthly', 'yearly'):
                    r.type = recurrence
                r.start_date = normalize_recurrence_date(recurrence_start) or date
                r.end_date = normalize_recurrence_date(recurrence_end)
                r.next_date = None
                r.notes = notes
                db.add(r)
                db.commit()
                db.query(Income).filter(Income.recurrence_id == r.id, Income.id != e.id).delete()
                db.commit()
                _populate_income_fields(
                    e,
                    resolved_gross_amount,
                    vat_percent,
                    resolved_pm_percent,
                    resolved_net_amount,
                    r.start_date,
                    apartment_id,
                    platform_id,
                    (resolved_pm_id if associate_pm else None),
                    notes,
                    resolved_has_stamp_duty,
                    resolved_stamp_duty_amount,
                )
                e.orig_recurrence_id = None
                db.add(e)
                db.commit()
                _sync_income_recurrence(db, r, e, reset=True)
        else:
            if e.recurrence_id and apply_to == 'single' and not created_recurrence:
                e.orig_recurrence_id = e.recurrence_id
                e.recurrence_id = None
            elif created_recurrence or e.recurrence_id:
                e.orig_recurrence_id = None
            _populate_income_fields(
                e,
                resolved_gross_amount,
                vat_percent,
                resolved_pm_percent,
                resolved_net_amount,
                date,
                apartment_id,
                platform_id,
                (resolved_pm_id if associate_pm else None),
                notes,
                resolved_has_stamp_duty,
                resolved_stamp_duty_amount,
            )
            db.add(e)
            db.commit()
            if created_recurrence:
                _sync_income_recurrence(db, created_recurrence, e, reset=True)
        _persist_uploaded_attachment_payloads(db, uploaded_files, income_id=e.id)
        if uploaded_files:
            db.commit()
        next_url = _align_overview_redirect_year(next_url, e.date)
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
            resolved_vat_percent = float(vat_percent) if vat_percent is not None else float(o.vat_percent or 0.0)
            if vat_percent is not None:
                o.vat_percent = resolved_vat_percent
            if gross_amount is not None or net_amount is not None or vat_percent is not None:
                source_gross_amount = gross_amount
                if source_gross_amount is None and net_amount is None and vat_percent is not None:
                    source_gross_amount = float(o.gross_amount or 0.0)
                try:
                    resolved_gross_amount, resolved_net_amount = _resolve_income_amounts(
                        source_gross_amount,
                        resolved_vat_percent,
                        net_amount,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                o.gross_amount = resolved_gross_amount
                o.net_amount = resolved_net_amount
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
            o.pm_amount = get_income_pm_amount(o)
            o.net_after_pm = get_income_effective_amount(o)
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
