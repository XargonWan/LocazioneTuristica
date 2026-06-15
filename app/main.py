import os
import urllib.parse
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from .backup import create_backup, finish_request_backup_tracking, get_request_backup_state, start_request_backup_tracking
from .db import init_db, SessionLocal
from .models import Attachment, Income, Expense, Cleaning, PropertyManager
from .auth_utils import get_current_user
from .utils import expand_open_recurrences_to_current_year, get_expense_net_amount, get_income_effective_amount, get_income_pm_amount, get_income_pm_base_amount, get_income_stamp_duty_amount, get_pm_payment_settlement_amount, get_setting_int

app = FastAPI(title="LocazioneTuristica")

# Templates
templates = Jinja2Templates(directory="app/templates")

def _format_date(value):
    from datetime import datetime
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime('%d/%m/%Y')
    try:
        # handle strings in YYYY-MM-DD
        return datetime.strptime(value, '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        # fallback: return as-is
        return str(value)

# Register Jinja2 filter
templates.env.filters['format_date'] = _format_date

# Sessions
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize DB on startup
@app.on_event("startup")
async def startup_event():
    init_db()
    # Enable WAL mode for SQLite to handle concurrent background access
    try:
        from app.db import engine
        conn = engine.raw_connection()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()
    except Exception:
        pass
    # Start background iCal sync thread
    try:
        from app.routers.calendar import start_ical_background_sync
        start_ical_background_sync()
    except Exception as exc:
        print(f"Failed to start iCal background sync: {exc}")
    # Print route map to help debug 405 issues at startup
    try:
        print('Registered routes:')
        for r in app.routes:
            try:
                methods = getattr(r, 'methods', None)
                path = getattr(r, 'path', None) or getattr(r, 'path_regex', None) or getattr(r, 'path_format', None)
                print(f" {path} -> {methods}")
            except Exception:
                continue
    except Exception:
        pass


@app.middleware("http")
async def automatic_backup_middleware(request: Request, call_next):
    token = start_request_backup_tracking()
    try:
        response = await call_next(request)
    except Exception:
        finish_request_backup_tracking(token)
        raise

    try:
        state = get_request_backup_state()
        if response.status_code < 400 and state and state.db_changed and not state.backup_created:
            create_backup(
                kind="auto",
                include_attachments=state.attachments_changed,
                apply_rotation=True,
                retention=get_setting_int("backup_auto_retention", 7, minimum=1),
            )
            state.backup_created = True
    except Exception as exc:
        print(f"Automatic backup failed: {exc}")
    finally:
        finish_request_backup_tracking(token)

    return response


@app.middleware("http")
async def log_405_middleware(request: Request, call_next):
    response = await call_next(request)
    if response.status_code == 405:
        try:
            user = request.session.get('username')
        except Exception:
            user = None
        print(f"DEBUG 405: {request.method} {request.url} user={user}")
        # Print all registered routes and their methods to help identify endpoints that exist
        try:
            for r in request.app.routes:
                try:
                    methods = getattr(r, 'methods', None)
                    path = getattr(r, 'path', None) or getattr(r, 'path_regex', None) or getattr(r, 'path_format', None)
                    if methods and path:
                        print(f"ROUTE: {path} METHODS: {methods}")
                except Exception:
                    continue
        except Exception:
            pass
        # Log headers
        try:
            print('HEADERS:')
            for k, v in request.headers.items():
                print(f" {k}: {v}")
        except Exception:
            pass
    return response


@app.get("/")
async def index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/overview")


@app.get("/overview")
async def overview(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login")
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        from sqlalchemy.orm import joinedload
        from datetime import datetime
        current_year = datetime.now().year

        q = request.query_params.get('q', '').strip()
        company_filter = request.query_params.get('company', '').strip()
        pm_id_str = request.query_params.get('pm_id', '').strip()
        type_filter = request.query_params.get('type', '').strip()
        year_str = request.query_params.get('year', '').strip()

        filter_all_years = year_str.lower() == 'all'
        if filter_all_years:
            year = current_year
        else:
            try:
                year = int(year_str) if year_str else current_year
                filter_all_years = False
            except Exception:
                year = current_year
                filter_all_years = True

        filter_pm_id = None
        if pm_id_str:
            try:
                filter_pm_id = int(pm_id_str)
            except Exception:
                pass

        incomes = db.query(Income).options(joinedload(Income.recurrence), joinedload(Income.associated_pm), joinedload(Income.platform)).all()
        expenses = db.query(Expense).options(joinedload(Expense.recurrence), joinedload(Expense.associated_pm), joinedload(Expense.associated_company)).all()
        pms = db.query(PropertyManager).all()

        def recurrence_payload(entry):
            recurrence = getattr(entry, 'recurrence', None)
            if not recurrence or not getattr(recurrence, 'type', None):
                return {'recurrence_type': None, 'recurrence_label': None, 'recurrence_start': None, 'recurrence_end': None}
            return {
                'recurrence_type': recurrence.type,
                'recurrence_label': 'Mensile' if recurrence.type == 'monthly' else 'Annuale' if recurrence.type == 'yearly' else recurrence.type,
                'recurrence_start': recurrence.start_date,
                'recurrence_end': recurrence.end_date,
            }

        years_with_data = set()
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
                years_with_data.add(d.year)
            except Exception:
                continue
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
                years_with_data.add(d.year)
            except Exception:
                continue
        sorted_years = sorted(years_with_data)

        prev_year = None
        next_year = None
        if not filter_all_years and sorted_years:
            for y in reversed(sorted_years):
                if y < year:
                    prev_year = y
                    break
            for y in sorted_years:
                if y > year:
                    next_year = y
                    break

        if hasattr(request, 'url') and request.url:
            base_url = str(request.url)
        else:
            base_url = None
        prev_year_url = None
        next_year_url = None
        if base_url:
            parsed = urllib.parse.urlparse(base_url)
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if prev_year:
                qs['year'] = [str(prev_year)]
                new_qs = urllib.parse.urlencode(qs, doseq=True)
                prev_year_url = f"{parsed.path}?{new_qs}"
            if next_year:
                qs2 = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                qs2['year'] = [str(next_year)]
                new_qs2 = urllib.parse.urlencode(qs2, doseq=True)
                next_year_url = f"{parsed.path}?{new_qs2}"

        def entry_matches(entry, entry_type):
            if type_filter and entry_type != type_filter:
                return False
            try:
                entry_date = datetime.strptime(entry.date, '%Y-%m-%d')
            except Exception:
                return False
            if not filter_all_years and entry_date.year != year:
                return False
            if q:
                notes = getattr(entry, 'notes', '') or ''
                if q.lower() not in notes.lower():
                    return False
            if company_filter:
                name = ''
                if entry_type == 'income':
                    name = (entry.platform.name if getattr(entry, 'platform', None) else '')
                else:
                    name = (entry.associated_company.company_name if getattr(entry, 'associated_company', None) else '')
                if company_filter.lower() not in name.lower():
                    return False
            if filter_pm_id:
                if getattr(entry, 'associated_pm_id', None) != filter_pm_id:
                    return False
            return True

        filtered_incomes = [inc for inc in incomes if entry_matches(inc, 'income')]
        filtered_expenses = [exp for exp in expenses if entry_matches(exp, 'expense')]

        months = {m: {'income': 0.0, 'expense': 0.0, 'pm_due': 0.0} for m in range(1, 13)}
        annual_income_net_total = 0.0
        annual_expense_total = 0.0
        annual_pm_accrued_total = 0.0
        annual_pm_paid_total = 0.0
        for inc in filtered_incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            pm_amount = get_income_pm_amount(inc)
            income_before_pm = get_income_pm_base_amount(inc)
            net_val = get_income_effective_amount(inc)
            months[d.month]['income'] += net_val
            months[d.month]['pm_due'] += pm_amount
            annual_income_net_total += income_before_pm
            annual_pm_accrued_total += pm_amount
        for exp in filtered_expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            gross_amount = float(exp.gross_amount)
            months[d.month]['expense'] += gross_amount
            if exp.associated_pm_id:
                settlement_amount = get_pm_payment_settlement_amount(exp)
                months[d.month]['pm_due'] -= settlement_amount
                annual_pm_paid_total += settlement_amount
            else:
                annual_expense_total += gross_amount
        months_list = [{'month': m, 'income': months[m]['income'], 'expense': months[m]['expense'], 'pm_due': months[m]['pm_due']} for m in sorted(months.keys())]

        filtered_income_ids = [inc.id for inc in filtered_incomes if inc.id]
        filtered_expense_ids = [exp.id for exp in filtered_expenses if exp.id]
        current_year_income_ids = []
        for inc in filtered_incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if not filter_all_years and d.year != year:
                continue
            if inc.id:
                current_year_income_ids.append(inc.id)

        current_year_expense_ids = []
        for exp in filtered_expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if not filter_all_years and d.year != year:
                continue
            if exp.id:
                current_year_expense_ids.append(exp.id)

        cleaning_platform_by_expense_id = {}
        if current_year_expense_ids:
            cleanings = (
                db.query(Cleaning)
                .options(joinedload(Cleaning.income).joinedload(Income.platform))
                .filter(Cleaning.expense_id.in_(current_year_expense_ids))
                .all()
            )
            for cleaning in cleanings:
                if not cleaning.expense_id:
                    continue
                linked_income = getattr(cleaning, 'income', None)
                linked_platform = getattr(linked_income, 'platform', None) if linked_income else None
                cleaning_platform_by_expense_id[cleaning.expense_id] = linked_platform.name if linked_platform else None

        attachments_by_income = {}
        if current_year_income_ids:
            for attachment in db.query(Attachment).filter(Attachment.income_id.in_(current_year_income_ids)).all():
                attachments_by_income.setdefault(attachment.income_id, []).append(attachment)

        attachments_by_expense = {}
        if current_year_expense_ids:
            for attachment in db.query(Attachment).filter(Attachment.expense_id.in_(current_year_expense_ids)).all():
                attachments_by_expense.setdefault(attachment.expense_id, []).append(attachment)

        entries_by_month = {m: [] for m in range(1, 13)}
        for inc in filtered_incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            associated_pm_name = None
            try:
                if inc.associated_pm:
                    associated_pm_name = f"{inc.associated_pm.first_name} {inc.associated_pm.last_name}"
            except Exception:
                associated_pm_name = None
            item = {'type': 'income', 'date': d, 'raw_date': inc.date, 'gross_amount': float(inc.gross_amount), 'notes': inc.notes if getattr(inc, 'notes', None) else '', 'id': inc.id, 'apartment_id': getattr(inc, 'apartment_id', None), 'associated_pm_name': associated_pm_name, 'pm_percent': float(getattr(inc, 'pm_percent', 0.0) or 0.0), 'pm_amount': get_income_pm_amount(inc), 'net_after_pm': get_income_effective_amount(inc), 'pm_base_amount': get_income_pm_base_amount(inc), 'stamp_duty_amount': get_income_stamp_duty_amount(inc), 'has_stamp_duty': bool(getattr(inc, 'has_stamp_duty', False)), 'cleaning_emoji': '🧹' if getattr(inc, 'apartment_id', None) else '', 'platform_name': (inc.platform.name if getattr(inc, 'platform', None) else None)}
            item.update(recurrence_payload(inc))
            item['net_amount'] = float(getattr(inc, 'net_amount', 0.0) or 0.0)
            entries_by_month[d.month].append(item)
        for exp in filtered_expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            associated_pm_name = None
            try:
                if exp.associated_pm:
                    associated_pm_name = f"{exp.associated_pm.first_name} {exp.associated_pm.last_name}"
            except Exception:
                associated_pm_name = None
            item = {'type': 'expense', 'date': d, 'gross_amount': float(exp.gross_amount), 'notes': exp.notes if getattr(exp, 'notes', None) else '', 'id': exp.id, 'associated_pm_name': associated_pm_name, 'pm_percent': 0.0, 'pm_amount': 0.0, 'net_after_pm': float(getattr(exp, 'net_after_pm', 0.0) or 0.0), 'net_amount': get_expense_net_amount(exp), 'platform_name': cleaning_platform_by_expense_id.get(exp.id), 'company_name': (exp.associated_company.company_name if getattr(exp, 'associated_company', None) else None)}
            item.update(recurrence_payload(exp))
            entries_by_month[d.month].append(item)
        for m in range(1, 13):
            entries_by_month[m].sort(key=lambda x: x['date'], reverse=False)

        months_list = [m for m in months_list if entries_by_month[m['month']]]

        total_income = sum([m['income'] for m in months_list])
        total_expense = sum([m['expense'] for m in months_list])
        pm_paid_total = annual_pm_paid_total
        pm_paid_pct = round((pm_paid_total / annual_income_net_total) * 100, 2) if annual_income_net_total > 0 else 0.0
        pm_due_total = annual_pm_accrued_total - annual_pm_paid_total
        grand_total_real = annual_income_net_total - annual_expense_total - annual_pm_paid_total
        grand_total_virtual = grand_total_real - pm_due_total

        filter_active = bool(q or company_filter or filter_pm_id or type_filter)
        if hasattr(request, 'url') and request.url:
            current_return_url = request.url.path + ('?' + request.url.query if request.url.query else '')
        elif request.query_params:
            current_return_url = '/overview?' + urllib.parse.urlencode(request.query_params)
        else:
            current_return_url = '/overview'

        return templates.TemplateResponse(request, "overview.html", {
            'months': months_list, 'year': year, 'current_year': current_year,
            'prev_year': prev_year, 'next_year': next_year,
            'prev_year_url': prev_year_url, 'next_year_url': next_year_url,
            'available_years': sorted_years,
            'entries_by_month': entries_by_month,
            'attachments_by_income': attachments_by_income, 'attachments_by_expense': attachments_by_expense,
            'total_income': total_income, 'total_expense': total_expense,
            'pm_paid_total': pm_paid_total, 'pm_paid_pct': pm_paid_pct,
            'annual_income_net_total': annual_income_net_total, 'annual_expense_total': annual_expense_total,
            'annual_pm_accrued_total': annual_pm_accrued_total, 'annual_pm_paid_total': annual_pm_paid_total,
            'annual_pm_due_total': pm_due_total, 'grand_total_real': grand_total_real, 'grand_total_virtual': grand_total_virtual,
            'pms': pms,
            'filter_q': q, 'filter_company': company_filter, 'filter_pm_id': filter_pm_id,
            'filter_type': type_filter, 'filter_all_years': filter_all_years, 'filter_active': filter_active,
            'current_return_url': current_return_url,
        })
    finally:
        db.close()

@app.post("/overview")
async def overview_post(request: Request):
    # Accept POST to /overview (round-trip) and redirect to /overview GET to avoid 405 for clients that POST
    return RedirectResponse(url="/overview")


@app.get("/login")
async def login(request: Request):
    # Simple login template, actual POST handled in auth router
    return templates.TemplateResponse(request, "login.html", {})


# Include routers
from .routers import anagrafiche, auth, money, attachments, pages, cleaning, tax, calendar  # noqa

app.include_router(auth.router)
app.include_router(anagrafiche.router)
app.include_router(money.router)
app.include_router(attachments.router)
app.include_router(cleaning.router)
app.include_router(pages.router)
app.include_router(tax.router)
app.include_router(calendar.router)

