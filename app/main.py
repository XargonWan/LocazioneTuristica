import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from .db import init_db, SessionLocal
from .models import Income, Expense
from .auth_utils import get_current_user

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
        # Compute basic monthly totals for current year
        from datetime import datetime
        year = datetime.now().year
        incomes = db.query(Income).all()
        expenses = db.query(Expense).all()
        months = {m: {'income': 0.0, 'expense': 0.0} for m in range(1, 13)}
        # track amount due to PM per month separately from expenses
        for m in months:
            months[m]['pm_due'] = 0.0
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                months[d.month]['income'] += float(inc.gross_amount)
                months[d.month]['pm_due'] += float(getattr(inc, 'pm_amount', 0.0) or 0.0)
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                months[d.month]['expense'] += float(exp.gross_amount)
        months_list = [{'month': m, 'income': months[m]['income'], 'expense': months[m]['expense']} for m in sorted(months.keys())]
        # include pm_due in months list for template
        for m in months_list:
            m['pm_due'] = months[m['month']]['pm_due']
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
                entries_by_month[d.month].append({'type': 'income', 'date': d, 'gross_amount': float(inc.gross_amount), 'notes': inc.notes if getattr(inc, 'notes', None) else '', 'id': inc.id, 'associated_pm_name': associated_pm_name, 'pm_percent': float(getattr(inc, 'pm_percent', 0.0) or 0.0), 'pm_amount': float(getattr(inc, 'pm_amount', 0.0) or 0.0)})
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
                entries_by_month[d.month].append({'type': 'expense', 'date': d, 'gross_amount': float(exp.gross_amount), 'notes': exp.notes if getattr(exp, 'notes', None) else '', 'id': exp.id, 'associated_pm_name': associated_pm_name, 'pm_percent': float(getattr(exp, 'pm_percent', 0.0) or 0.0), 'pm_amount': float(getattr(exp, 'pm_amount', 0.0) or 0.0), 'net_after_pm': float(getattr(exp, 'net_after_pm', 0.0) or 0.0)})
        # Sort entries in each month by date ascending (earliest first)
        for m in range(1,13):
            entries_by_month[m].sort(key=lambda x: x['date'], reverse=False)
        total_income = sum([m['income'] for m in months_list])
        total_expense = sum([m['expense'] for m in months_list])
        pm_paid_total = 0.0
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                pm_paid_total += float(getattr(inc, 'pm_amount', 0.0) or 0.0)
        pm_paid_pct = round((pm_paid_total / total_income) * 100, 2) if total_income > 0 else 0.0
        return templates.TemplateResponse("overview.html", {"request": request, 'months': months_list, 'year': year, 'entries_by_month': entries_by_month, 'total_income': total_income, 'total_expense': total_expense, 'pm_paid_total': pm_paid_total, 'pm_paid_pct': pm_paid_pct})
    finally:
        db.close()


@app.post("/overview")
async def overview_post(request: Request):
    # Accept POST to /overview (round-trip) and redirect to /overview GET to avoid 405 for clients that POST
    return RedirectResponse(url="/overview")


@app.get("/login")
async def login(request: Request):
    # Simple login template, actual POST handled in auth router
    return templates.TemplateResponse("login.html", {"request": request})


# Include routers
from .routers import anagrafiche, auth, money, attachments, pages  # noqa

app.include_router(auth.router)
app.include_router(anagrafiche.router)
app.include_router(money.router)
app.include_router(attachments.router)
app.include_router(pages.router)

