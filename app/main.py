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

# Sessions
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize DB on startup
@app.on_event("startup")
async def startup_event():
    init_db()


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
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                months[d.month]['income'] += float(inc.gross_amount)
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if d.year == year:
                months[d.month]['expense'] += float(exp.gross_amount)
        months_list = [{'month': m, 'income': months[m]['income'], 'expense': months[m]['expense']} for m in sorted(months.keys())]
        return templates.TemplateResponse("overview.html", {"request": request, 'months': months_list, 'year': year})
    finally:
        db.close()


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

