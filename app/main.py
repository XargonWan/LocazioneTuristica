import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from .backup import create_backup, finish_request_backup_tracking, get_request_backup_state, start_request_backup_tracking
from .db import init_db, SessionLocal
from .models import Attachment, Income, Expense
from .auth_utils import get_current_user
from .utils import expand_open_recurrences_to_current_year, get_income_effective_amount, get_income_pm_amount, get_income_pm_base_amount, get_income_stamp_duty_amount, get_pm_payment_settlement_amount, get_setting_int

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
        # Compute basic monthly totals for requested year (defaults to current year)
        from datetime import datetime
        current_year = datetime.now().year
        # allow overriding via query param
        year = current_year
        try:
            qyear = request.query_params.get('year')
            if qyear is not None:
                year = int(qyear)
        except Exception:
            # ignore bad input and stick with current year
            year = current_year
        incomes = db.query(Income).options(joinedload(Income.recurrence), joinedload(Income.associated_pm)).all()
        expenses = db.query(Expense).options(joinedload(Expense.recurrence), joinedload(Expense.associated_pm)).all()

        def recurrence_payload(entry):
            recurrence = getattr(entry, 'recurrence', None)
            if not recurrence or not getattr(recurrence, 'type', None):
                return {
                    'recurrence_type': None,
                    'recurrence_label': None,
                    'recurrence_start': None,
                    'recurrence_end': None,
                }
            return {
                'recurrence_type': recurrence.type,
                'recurrence_label': 'Mensile' if recurrence.type == 'monthly' else 'Annuale' if recurrence.type == 'yearly' else recurrence.type,
                'recurrence_start': recurrence.start_date,
                'recurrence_end': recurrence.end_date,
            }
        # determine which years have any entries so we can limit navigation
        years_with_data = set()
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            years_with_data.add(d.year)
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            years_with_data.add(d.year)
        sorted_years = sorted(years_with_data)
        prev_year = None
        next_year = None
        if sorted_years:
            # find nearest neighbors around selected year
            for y in reversed(sorted_years):
                if y < year:
                    prev_year = y
                    break
            for y in sorted_years:
                if y > year:
                    next_year = y
                    break
        # build month ledger; income will be net amounts after pm if available
        months = {m: {'income': 0.0, 'expense': 0.0} for m in range(1, 13)}
        # track gross PM due separately so we can still display it
        for m in months:
            months[m]['pm_due'] = 0.0
        annual_income_net_total = 0.0
        annual_expense_total = 0.0
        annual_pm_accrued_total = 0.0
        annual_pm_paid_total = 0.0
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                pm_amount = get_income_pm_amount(inc)
                income_before_pm = get_income_pm_base_amount(inc)
                # use net_after_pm if it exists (computed when income is created/edited);
                # fall back to net_amount minus pm_amount to avoid counting VAT or PM twice
                net_val = get_income_effective_amount(inc)
                months[d.month]['income'] += net_val
                months[d.month]['pm_due'] += pm_amount
                annual_income_net_total += income_before_pm
                annual_pm_accrued_total += pm_amount
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                # expenses are counted as their gross amounts (as requested by user)
                gross_amount = float(exp.gross_amount)
                months[d.month]['expense'] += gross_amount
                # if this expense represents a payment to a PM, reduce the outstanding due
                if exp.associated_pm_id:
                    settlement_amount = get_pm_payment_settlement_amount(exp)
                    months[d.month]['pm_due'] -= settlement_amount
                    annual_pm_paid_total += settlement_amount
                else:
                    annual_expense_total += gross_amount
        months_list = [{'month': m, 'income': months[m]['income'], 'expense': months[m]['expense']} for m in sorted(months.keys())]
        # include pm_due in months list for template
        for m in months_list:
            m['pm_due'] = months[m['month']]['pm_due']
        current_year_income_ids = []
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year and inc.id:
                current_year_income_ids.append(inc.id)

        current_year_expense_ids = []
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year and exp.id:
                current_year_expense_ids.append(exp.id)

        attachments_by_income = {}
        if current_year_income_ids:
            for attachment in db.query(Attachment).filter(Attachment.income_id.in_(current_year_income_ids)).all():
                attachments_by_income.setdefault(attachment.income_id, []).append(attachment)

        attachments_by_expense = {}
        if current_year_expense_ids:
            for attachment in db.query(Attachment).filter(Attachment.expense_id.in_(current_year_expense_ids)).all():
                attachments_by_expense.setdefault(attachment.expense_id, []).append(attachment)

        # Build per-month entries lists for incomes and expenses so the template can render details
        entries_by_month = {m: [] for m in range(1, 13)}
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                associated_pm_name = None
                try:
                    if inc.associated_pm:
                        associated_pm_name = f"{inc.associated_pm.first_name} {inc.associated_pm.last_name}"
                except Exception:
                    associated_pm_name = None
                item = {'type': 'income', 'date': d, 'raw_date': inc.date, 'gross_amount': float(inc.gross_amount), 'notes': inc.notes if getattr(inc, 'notes', None) else '', 'id': inc.id, 'apartment_id': getattr(inc, 'apartment_id', None), 'associated_pm_name': associated_pm_name, 'pm_percent': float(getattr(inc, 'pm_percent', 0.0) or 0.0), 'pm_amount': get_income_pm_amount(inc), 'net_after_pm': get_income_effective_amount(inc), 'pm_base_amount': get_income_pm_base_amount(inc), 'stamp_duty_amount': get_income_stamp_duty_amount(inc), 'has_stamp_duty': bool(getattr(inc, 'has_stamp_duty', False)), 'cleaning_emoji': '🧹' if getattr(inc, 'apartment_id', None) else ''}
                item.update(recurrence_payload(inc))
                entries_by_month[d.month].append(item)
                # include net_amount so overview modals can display netto computed from VAT
                entries_by_month[d.month][-1]['net_amount'] = float(getattr(inc, 'net_amount', 0.0) or 0.0)
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                associated_pm_name = None
                try:
                    if exp.associated_pm:
                        associated_pm_name = f"{exp.associated_pm.first_name} {exp.associated_pm.last_name}"
                except Exception:
                    associated_pm_name = None
                # expenses no longer expose PM percentage/amount
                item = {'type': 'expense', 'date': d, 'gross_amount': float(exp.gross_amount), 'notes': exp.notes if getattr(exp, 'notes', None) else '', 'id': exp.id, 'associated_pm_name': associated_pm_name, 'pm_percent': 0.0, 'pm_amount': 0.0, 'net_after_pm': float(getattr(exp, 'net_after_pm', 0.0) or 0.0)}
                item.update(recurrence_payload(exp))
                entries_by_month[d.month].append(item)
        # Sort entries in each month by date ascending (earliest first)
        for m in range(1,13):
            entries_by_month[m].sort(key=lambda x: x['date'], reverse=False)
        # now that months_list has been populated using net-after-PM values,
        # totals should reflect the same base
        total_income = sum([m['income'] for m in months_list])
        total_expense = sum([m['expense'] for m in months_list])
        pm_paid_total = annual_pm_paid_total
        pm_paid_pct = round((pm_paid_total / annual_income_net_total) * 100, 2) if annual_income_net_total > 0 else 0.0
        pm_due_total = max(annual_pm_accrued_total - annual_pm_paid_total, 0.0)
        grand_total_real = annual_income_net_total - annual_expense_total - annual_pm_paid_total
        grand_total_virtual = grand_total_real - pm_due_total
        return templates.TemplateResponse(request, "overview.html", {'months': months_list, 'year': year, 'current_year': current_year, 'prev_year': prev_year, 'next_year': next_year, 'available_years': sorted_years, 'entries_by_month': entries_by_month, 'attachments_by_income': attachments_by_income, 'attachments_by_expense': attachments_by_expense, 'total_income': total_income, 'total_expense': total_expense, 'pm_paid_total': pm_paid_total, 'pm_paid_pct': pm_paid_pct, 'annual_income_net_total': annual_income_net_total, 'annual_expense_total': annual_expense_total, 'annual_pm_accrued_total': annual_pm_accrued_total, 'annual_pm_paid_total': annual_pm_paid_total, 'annual_pm_due_total': pm_due_total, 'grand_total_real': grand_total_real, 'grand_total_virtual': grand_total_virtual})
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
from .routers import anagrafiche, auth, money, attachments, pages, cleaning  # noqa

app.include_router(auth.router)
app.include_router(anagrafiche.router)
app.include_router(money.router)
app.include_router(attachments.router)
app.include_router(cleaning.router)
app.include_router(pages.router)

