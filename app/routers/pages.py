from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Request, Depends, Form
from starlette.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.backup import BackupError, create_backup
from app.db import SessionLocal
from app.models import Settings
from app.auth_utils import admin_required
from app.auth_utils import get_current_user
from fastapi.responses import JSONResponse
from app.models import Income, Expense
from starlette.status import HTTP_303_SEE_OTHER
from app.utils import expand_open_recurrences_to_current_year, get_income_effective_amount as calculate_income_effective_amount, get_income_pm_amount as calculate_income_pm_amount, get_income_pm_base_amount, get_pm_payment_cash_amount, get_pm_payment_settlement_amount

router = APIRouter()
from app.main import templates


STATS_ALL_YEARS = 0


def _append_query_params(url: str, **params):
    target_url = url or "/settings"
    parsed = urlsplit(target_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = str(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _is_settings_url(url: str | None):
    target_url = url or "/settings"
    return urlsplit(target_url).path == "/settings"


def _get_stats_available_years(db):
    years_with_data = set()
    for model in (Income, Expense):
        for row in db.query(model.date).all():
            try:
                years_with_data.add(int(row[0][:4]))
            except Exception:
                pass
    return sorted(years_with_data)


def _normalize_stats_year(year: int | None, available_years: list[int]) -> int:
    if year in (None, STATS_ALL_YEARS):
        return STATS_ALL_YEARS
    if year in available_years:
        return year
    return STATS_ALL_YEARS


def _matches_stats_year(entry_year: int, selected_year: int) -> bool:
    return selected_year == STATS_ALL_YEARS or entry_year == selected_year


def _income_nights(inc, entry_date: datetime) -> int:
    """Notti occupate da un'entrata: check-out meno data (check-in), minimo 1 notte."""
    if inc.check_out:
        try:
            check_out = datetime.strptime(inc.check_out, '%Y-%m-%d')
        except Exception:
            check_out = None
        if check_out and check_out > entry_date:
            return (check_out - entry_date).days
    return 1


def _get_income_pm_id(entry) -> int | None:
    return getattr(entry, 'associated_pm_id', None) or (entry.apartment.property_manager_id if getattr(entry, 'apartment', None) else None)


def _get_income_pm_amount(entry, pms_by_id=None) -> float:
    pm_id = _get_income_pm_id(entry)
    if pm_id and pms_by_id:
        pm = pms_by_id.get(pm_id)
        if pm:
            return calculate_income_pm_amount(entry, pm_percent=float(getattr(pm, 'percent', 0.0) or 0.0))
    return calculate_income_pm_amount(entry)


@router.get('/stats')
async def stats_view(request: Request, year: int = None):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        # Build per-anagrafica totals for the stats page
        from app.models import PropertyManager, Company, Platform

        available_years = _get_stats_available_years(db)
        now = datetime.now().year
        year = _normalize_stats_year(year, available_years)
        pms = db.query(PropertyManager).all()
        pms_by_id = {pm.id: pm for pm in pms}
        companies = db.query(Company).all()
        platforms = db.query(Platform).all()
        # pm totals: commissioni maturate dal PM (quanto ha guadagnato)
        pm_totals = {}
        expenses = db.query(Expense).all()
        incomes = db.query(Income).all()
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if not _matches_stats_year(d.year, year):
                continue
            pm_id = _get_income_pm_id(inc)
            if not pm_id:
                continue
            pm_amount = _get_income_pm_amount(inc, pms_by_id)
            pm_totals[pm_id] = pm_totals.get(pm_id, 0.0) + pm_amount
        # company totals (expenses)
        company_totals = {}
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if not _matches_stats_year(d.year, year):
                continue
            if exp.associated_company_id:
                company_totals[exp.associated_company_id] = company_totals.get(exp.associated_company_id, 0.0) + float(exp.gross_amount or 0.0)
        # platform totals (incomes)
        platform_totals = {}
        platform_income_count = {}
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if not _matches_stats_year(d.year, year):
                continue
            if inc.platform_id:
                platform_totals[inc.platform_id] = platform_totals.get(inc.platform_id, 0.0) + float(inc.gross_amount or 0.0)
                platform_income_count[inc.platform_id] = platform_income_count.get(inc.platform_id, 0) + 1
        return templates.TemplateResponse(request, 'stats.html', {"pms": pms, "companies": companies, "platforms": platforms, "pm_totals": pm_totals, "company_totals": company_totals, "platform_totals": platform_totals, "platform_income_count": platform_income_count, "year": year, "now": now, "available_years": available_years})
    finally:
        db.close()

@router.get('/api/stats/monthly')
async def api_stats_monthly(year: int = None, request: Request = None, pm_id: int = None, company_id: int = None, platform_id: int = None):
    if request and not get_current_user(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        from app.models import PropertyManager

        available_years = _get_stats_available_years(db)
        year = _normalize_stats_year(year, available_years)
        pms_by_id = {pm.id: pm for pm in db.query(PropertyManager).all()}
        incomes = db.query(Income).all()
        expenses = db.query(Expense).all()
        # note: income values are now reported after VAT and PM deductions
        months = {m: {'income': 0.0, 'expense': 0.0, 'pm_due': 0.0} for m in range(1, 13)}
        total_income = 0.0
        total_income_before_pm = 0.0
        total_non_pm_expense = 0.0
        total_pm_accrued = 0.0
        total_pm_paid = 0.0
        total_pm_payment_cash = 0.0
        income_count = 0
        total_nights = 0
        total_gross = 0.0
        income_years = set()
        for inc in incomes:
            try:
                d = datetime.strptime(inc.date, '%Y-%m-%d')
            except Exception:
                continue
            if not _matches_stats_year(d.year, year):
                continue
            # Apply optional filters
            if pm_id:
                inc_pm_id = _get_income_pm_id(inc)
                if inc_pm_id != pm_id:
                    continue
            if platform_id and inc.platform_id != platform_id:
                continue
            income_years.add(d.year)
            pm_amount = _get_income_pm_amount(inc, pms_by_id)
            # Use net_after_pm when available, otherwise compute from net - pm
            amt = float(getattr(inc, 'net_after_pm', None) or 0.0)
            if not amt:
                amt = calculate_income_effective_amount(inc)
            income_before_pm = get_income_pm_base_amount(inc)
            if not income_before_pm and amt:
                income_before_pm = amt + pm_amount
            months[d.month]['income'] += amt
            months[d.month]['pm_due'] += pm_amount
            total_income += amt
            total_income_before_pm += income_before_pm
            total_pm_accrued += pm_amount
            income_count += 1
            total_nights += _income_nights(inc, d)
            total_gross += float(inc.gross_amount or 0.0)
        total_expense = 0.0
        for exp in expenses:
            try:
                d = datetime.strptime(exp.date, '%Y-%m-%d')
            except Exception:
                continue
            if not _matches_stats_year(d.year, year):
                continue
            if pm_id and exp.associated_pm_id != pm_id:
                continue
            if company_id and exp.associated_company_id != company_id:
                continue
            amt = float(exp.gross_amount or 0.0)
            months[d.month]['expense'] += amt
            total_expense += amt
            if exp.associated_pm_id:
                pm_payment_settlement = get_pm_payment_settlement_amount(exp)
                months[d.month]['pm_due'] -= pm_payment_settlement
                total_pm_paid += pm_payment_settlement
                total_pm_payment_cash += get_pm_payment_cash_amount(exp)
            else:
                total_non_pm_expense += amt
        month_names = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
        result = [{'month': m, 'month_name': month_names[m], 'income': months[m]['income'], 'expense': months[m]['expense'], 'pm_due': months[m]['pm_due']} for m in sorted(months.keys())]
        pm_due = total_pm_accrued - total_pm_paid
        grand_total_real = total_income_before_pm - total_non_pm_expense - total_pm_payment_cash
        grand_total_virtual = grand_total_real - pm_due
        nights_denominator = 365
        if year == STATS_ALL_YEARS and income_years:
            nights_denominator = 365 * len(income_years)
        nights_percent = round((total_nights / nights_denominator) * 100, 2) if nights_denominator else 0.0
        totals = {
            'income': total_income,
            'expense': total_expense,
            'pm_paid': total_pm_paid,
            'pm_due': pm_due,
            'grand_total_real': grand_total_real,
            'grand_total_virtual': grand_total_virtual,
            'income_count': income_count,
            'nights': total_nights,
            'nights_denominator': nights_denominator,
            'nights_percent': nights_percent,
            'gross_per_night': round(total_gross / total_nights, 2) if total_nights else 0.0,
            'net_per_night': round(total_income / total_nights, 2) if total_nights else 0.0,
        }
        if total_income:
            totals['pm_percent'] = round((total_pm_paid / total_income) * 100, 2)
        else:
            totals['pm_percent'] = 0.0
        return JSONResponse(content={'year': year, 'data': result, 'totals': totals})
    finally:
        db.close()


@router.get('/settings')
async def settings_view(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url='/login')
    db = SessionLocal()
    try:
        settings = db.query(Settings).all()
    except Exception:
        settings = []
    next_url = request.query_params.get('next') or '/settings'
    return templates.TemplateResponse(request, 'settings.html', {"settings": settings, "next": next_url})


@router.post('/settings/update')
async def settings_update(request: Request, key: str = Form(...), value: str = Form(...), next: str = Form(None), user=Depends(admin_required)):
    db = SessionLocal()
    try:
        target_url = next or '/settings'
        cleaned_value = value.strip()
        if key == 'backup_auto_retention':
            try:
                if int(cleaned_value) < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                if not _is_settings_url(target_url):
                    target_url = '/settings'
                return RedirectResponse(
                    url=_append_query_params(target_url, settings='invalid_backup_retention'),
                    status_code=HTTP_303_SEE_OTHER,
                )
        s = db.query(Settings).filter(Settings.key == key).first()
        if not s:
            s = Settings(key=key, value=cleaned_value)
            db.add(s)
        else:
            s.value = cleaned_value
            s.updated_at = datetime.utcnow()
            db.add(s)
        db.commit()
        if _is_settings_url(target_url):
            target_url = _append_query_params(target_url, settings='saved')
        return RedirectResponse(
            url=target_url,
            status_code=HTTP_303_SEE_OTHER,
        )
    finally:
        db.close()


@router.post('/settings/backup/manual')
async def settings_backup_manual(request: Request, next: str = Form(None), user=Depends(admin_required)):
    try:
        archive_path = create_backup(
            kind='manual',
            include_attachments=True,
            apply_rotation=False,
        )
        return RedirectResponse(
            url=_append_query_params(next or '/settings', backup='manual_success', archive=archive_path.name),
            status_code=HTTP_303_SEE_OTHER,
        )
    except (BackupError, OSError):
        return RedirectResponse(
            url=_append_query_params(next or '/settings', backup='manual_error'),
            status_code=HTTP_303_SEE_OTHER,
        )

