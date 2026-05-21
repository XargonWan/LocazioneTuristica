import io
import os
import zipfile
from datetime import datetime
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import Response, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from app.db import SessionLocal
from app.models import Attachment, Income, Expense, PropertyManager, Apartment, Platform, Company
from app.auth_utils import admin_required, get_current_user
from app.main import templates
from app.utils import (
    expand_open_recurrences_to_current_year,
    get_income_effective_amount,
    get_income_pm_amount,
    get_income_pm_base_amount,
    get_income_stamp_duty_amount,
    get_pm_payment_cash_amount,
    get_pm_payment_settlement_amount,
)
from fpdf import FPDF

router = APIRouter(prefix="/tax")

MONTH_NAMES = [
    "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]

PDF_VERSION = "1.4"


def _collect_data(db, years, months=None, pm_filter=None):
    expand_open_recurrences_to_current_year(db)
    incomes = db.query(Income).all()
    expenses = db.query(Expense).all()
    pms = db.query(PropertyManager).all()
    apartments = {a.id: a for a in db.query(Apartment).all()}

    def year_matches(d):
        return d.year in years

    def month_matches(d):
        if not months:
            return True
        return d.month in months

    monthly_data = {m: {"income": 0.0, "expense": 0.0, "pm_due": 0.0, "income_count": 0, "expense_count": 0} for m in range(1, 13)}
    total_income_before_pm = 0.0
    total_expense_gross = 0.0
    total_pm_accrued = 0.0
    total_pm_paid = 0.0
    total_pm_payment_cash = 0.0
    total_non_pm_expense = 0.0

    filtered_incomes = []
    filtered_expenses = []

    for inc in incomes:
        try:
            d = datetime.strptime(inc.date, "%Y-%m-%d")
        except Exception:
            continue
        if not year_matches(d):
            continue
        if pm_filter and inc.associated_pm_id != pm_filter and getattr(inc.apartment, "property_manager_id", None) != pm_filter:
            continue
        if not month_matches(d):
            continue
        filtered_incomes.append(inc)
        pm_amt = get_income_pm_amount(inc)
        income_before_pm = get_income_pm_base_amount(inc)
        effective = get_income_effective_amount(inc)
        monthly_data[d.month]["income"] += effective
        monthly_data[d.month]["pm_due"] += pm_amt
        monthly_data[d.month]["income_count"] += 1
        total_income_before_pm += income_before_pm
        total_pm_accrued += pm_amt

    for exp in expenses:
        try:
            d = datetime.strptime(exp.date, "%Y-%m-%d")
        except Exception:
            continue
        if not year_matches(d):
            continue
        if pm_filter and exp.associated_pm_id != pm_filter:
            continue
        if not month_matches(d):
            continue
        filtered_expenses.append(exp)
        gross = float(exp.gross_amount or 0.0)
        monthly_data[d.month]["expense"] += gross
        monthly_data[d.month]["expense_count"] += 1
        total_expense_gross += gross
        if exp.associated_pm_id:
            settlement = get_pm_payment_settlement_amount(exp)
            monthly_data[d.month]["pm_due"] -= settlement
            total_pm_paid += settlement
            total_pm_payment_cash += get_pm_payment_cash_amount(exp)
        else:
            total_non_pm_expense += gross

    pm_due = total_pm_accrued - total_pm_paid
    grand_total_real = total_income_before_pm - total_non_pm_expense - total_pm_payment_cash
    grand_total_virtual = grand_total_real - pm_due

    return {
        "monthly": monthly_data,
        "totals": {
            "income_before_pm": total_income_before_pm,
            "expense_gross": total_expense_gross,
            "pm_accrued": total_pm_accrued,
            "pm_paid": total_pm_paid,
            "pm_due": pm_due,
            "grand_total_real": grand_total_real,
            "grand_total_virtual": grand_total_virtual,
        },
        "incomes": filtered_incomes,
        "expenses": filtered_expenses,
        "pms": pms,
        "apartments": apartments,
    }


def _hide(val, hidden):
    if hidden:
        return "***"
    return f"EUR {val:,.2f}"


def _income_imponibile(inc):
    return get_income_pm_base_amount(inc)


def _income_vat_amount(inc):
    gross = float(inc.gross_amount or 0.0)
    net = float(inc.net_amount or 0.0)
    return round(gross - net, 2)


def _expense_vat_amount(exp):
    gross = float(exp.gross_amount or 0.0)
    net = float(exp.net_amount or 0.0)
    return round(gross - net, 2)


def _detail_sort_key(e):
    try:
        return datetime.strptime(e.date, "%Y-%m-%d")
    except Exception:
        return datetime.min


def _write_detail_table(pdf, items, is_income, apartments, hide_amounts):
    col_w = [22, 56, 32, 32, 32]
    headers = ["Data", "Descrizione", "Imponibile", "IVA", "Totale"]
    pdf.set_font("Helvetica", "B", 7)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 6, h, border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)

    total_imponibile = 0.0
    total_iva = 0.0
    total_totale = 0.0

    for item in sorted(items, key=_detail_sort_key):
        try:
            d = datetime.strptime(item.date, "%Y-%m-%d")
        except Exception:
            d = None
        date_str = d.strftime("%d/%m/%Y") if d else (item.date or "")

        apt = apartments.get(item.apartment_id)
        apt_name = apt.name if apt else ""
        notes = (item.notes or "")[:30]
        desc = (apt_name + (" - " + notes if notes else ""))[:50]

        if is_income:
            imponibile = _income_imponibile(item)
            iva = _income_vat_amount(item)
        else:
            imponibile = float(item.net_amount or 0.0)
            iva = _expense_vat_amount(item)
        totale = imponibile + iva

        total_imponibile += imponibile
        total_iva += iva
        total_totale += totale

        pdf.cell(col_w[0], 5, date_str, border=1, align="C")
        pdf.cell(col_w[1], 5, desc, border=1)
        pdf.cell(col_w[2], 5, _hide(imponibile, hide_amounts), border=1, align="R")
        pdf.cell(col_w[3], 5, _hide(iva, hide_amounts), border=1, align="R")
        pdf.cell(col_w[4], 5, _hide(totale, hide_amounts), border=1, align="R")
        pdf.ln()

    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(col_w[0], 6, "TOTALE", border=1, align="C")
    pdf.cell(col_w[1], 6, "", border=1)
    pdf.cell(col_w[2], 6, _hide(total_imponibile, hide_amounts), border=1, align="R")
    pdf.cell(col_w[3], 6, _hide(total_iva, hide_amounts), border=1, align="R")
    pdf.cell(col_w[4], 6, _hide(total_totale, hide_amounts), border=1, align="R")
    pdf.ln(8)


class TaxPdf(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 8, "LocazioneTuristica - Report Economico", align="C", new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Pagina {self.page_no()}/{{nb}}", align="C")


def generate_pdf(data, years, months, hide_amounts, include_chart):
    pdf = TaxPdf()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    year_label = ", ".join(str(y) for y in sorted(years))
    period = year_label
    if months and months != list(range(1, 13)):
        month_labels = [MONTH_NAMES[m] for m in sorted(months)]
        period = f"{year_label} - {', '.join(month_labels)}"

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, f"Report {' - ' if period else ''}{period}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generato il {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    monthly = data["monthly"]
    totals = data["totals"]

    col_w = [20, 38, 38, 38, 28, 28]
    headers = ["Mese", "Entrate", "Spese", "Netto", "N. Entrate", "N. Spese"]

    active_months = sorted(m for m in range(1, 13) if monthly[m]["income"] or monthly[m]["expense"])
    if not active_months:
        active_months = range(1, 13)

    pdf.set_font("Helvetica", "B", 8)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    total_income = 0.0
    total_expense = 0.0
    for m in active_months:
        inc = monthly[m]["income"]
        exp = monthly[m]["expense"]
        net = inc - exp
        total_income += inc
        total_expense += exp
        pdf.cell(col_w[0], 6, MONTH_NAMES[m][:3] + ".", border=1, align="C")
        pdf.cell(col_w[1], 6, _hide(inc, hide_amounts), border=1, align="R")
        pdf.cell(col_w[2], 6, _hide(exp, hide_amounts), border=1, align="R")
        pdf.cell(col_w[3], 6, _hide(net, hide_amounts), border=1, align="R")
        pdf.cell(col_w[4], 6, str(monthly[m]["income_count"]), border=1, align="C")
        pdf.cell(col_w[5], 6, str(monthly[m]["expense_count"]), border=1, align="C")
        pdf.ln()

    pdf.set_font("Helvetica", "B", 8)
    total_net = total_income - total_expense
    pdf.cell(col_w[0], 7, "TOTALE", border=1, align="C")
    pdf.cell(col_w[1], 7, _hide(total_income, hide_amounts), border=1, align="R")
    pdf.cell(col_w[2], 7, _hide(total_expense, hide_amounts), border=1, align="R")
    pdf.cell(col_w[3], 7, _hide(total_net, hide_amounts), border=1, align="R")
    pdf.cell(col_w[4], 7, "", border=1)
    pdf.cell(col_w[5], 7, "", border=1)
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, "Riepilogo Annuale", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 9)
    summary_items = [
        ("Imponibile entrate (al netto IVA e bollo)", totals["income_before_pm"]),
        ("Spese lorde", totals["expense_gross"]),
        ("Commissioni PM maturate", totals["pm_accrued"]),
        ("Versato ai PM", totals["pm_paid"]),
        ("PM da versare", totals["pm_due"]),
    ]
    for label, val in summary_items:
        pdf.cell(120, 6, label, border=0)
        pdf.cell(0, 6, _hide(val, hide_amounts), border=0, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(120, 7, "Saldo reale", border="T")
    pdf.cell(0, 7, _hide(totals["grand_total_real"], hide_amounts), border="T", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(120, 7, "Saldo virtuale", border="T")
    pdf.cell(0, 7, _hide(totals["grand_total_virtual"], hide_amounts), border="T", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, "Dettaglio Entrate", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    _write_detail_table(pdf, data["incomes"], True, data["apartments"], hide_amounts)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, "Dettaglio Spese", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    _write_detail_table(pdf, data["expenses"], False, data["apartments"], hide_amounts)

    if include_chart:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "Andamento Mensile", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)

        chart_left = 20
        chart_w = 170
        chart_h = 80
        chart_bottom = pdf.get_y() + chart_h
        chart_top = pdf.get_y()

        max_val = 0.0
        for m in active_months:
            max_val = max(max_val, monthly[m]["income"], monthly[m]["expense"])
        if max_val <= 0:
            max_val = 100

        bar_count = len(active_months)
        if bar_count == 0:
            bar_count = 1
        group_w = chart_w / bar_count
        bar_gap = 2
        bar_w = (group_w - bar_gap) / 2

        pdf.set_draw_color(200, 200, 200)
        for tick in range(0, 5):
            y_pos = chart_bottom - (chart_h * tick / 4)
            pdf.line(chart_left, y_pos, chart_left + chart_w, y_pos)
            pdf.set_font("Helvetica", "", 6)
            val = max_val * tick / 4
            pdf.set_xy(2, y_pos - 2)
            pdf.cell(16, 4, f"{val:,.0f}", align="R")

        for idx, m in enumerate(active_months):
            x = chart_left + idx * group_w
            inc_h = (monthly[m]["income"] / max_val) * chart_h if max_val > 0 else 0
            exp_h = (monthly[m]["expense"] / max_val) * chart_h if max_val > 0 else 0

            pdf.set_fill_color(31, 166, 74)
            pdf.rect(x + 1, chart_bottom - inc_h, bar_w, inc_h, style="F")

            pdf.set_fill_color(217, 83, 79)
            pdf.rect(x + 1 + bar_w + bar_gap, chart_bottom - exp_h, bar_w, exp_h, style="F")

            pdf.set_font("Helvetica", "", 6)
            pdf.set_xy(x, chart_bottom + 1)
            pdf.cell(group_w, 4, MONTH_NAMES[m][:3] + ".", align="C")

        pdf.set_font("Helvetica", "", 8)
        legend_y = chart_bottom + 12
        pdf.set_fill_color(31, 166, 74)
        pdf.rect(chart_left + 20, legend_y, 8, 5, style="F")
        pdf.set_xy(chart_left + 30, legend_y)
        pdf.cell(30, 5, "Entrate")
        pdf.set_fill_color(217, 83, 79)
        pdf.rect(chart_left + 80, legend_y, 8, 5, style="F")
        pdf.set_xy(chart_left + 90, legend_y)
        pdf.cell(30, 5, "Spese")

    return bytes(pdf.output())


def generate_730_pdf(data, years, deductions, hide_amounts):
    pdf = TaxPdf()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    year_label = ", ".join(str(y) for y in sorted(years))

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, f"Documentazione 730 - Anno {year_label}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generato il {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    monthly = data["monthly"]
    totals = data["totals"]
    active_months = sorted(m for m in range(1, 13) if monthly[m]["income"] or monthly[m]["expense"])
    if not active_months:
        active_months = range(1, 13)

    col_w = [20, 38, 38, 38, 28, 28]
    headers = ["Mese", "Entrate", "Spese", "Netto", "N. Entrate", "N. Spese"]

    pdf.set_font("Helvetica", "B", 8)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    total_income = 0.0
    total_expense = 0.0
    for m in active_months:
        inc = monthly[m]["income"]
        exp = monthly[m]["expense"]
        net = inc - exp
        total_income += inc
        total_expense += exp
        pdf.cell(col_w[0], 6, MONTH_NAMES[m][:3] + ".", border=1, align="C")
        pdf.cell(col_w[1], 6, _hide(inc, hide_amounts), border=1, align="R")
        pdf.cell(col_w[2], 6, _hide(exp, hide_amounts), border=1, align="R")
        pdf.cell(col_w[3], 6, _hide(net, hide_amounts), border=1, align="R")
        pdf.cell(col_w[4], 6, str(monthly[m]["income_count"]), border=1, align="C")
        pdf.cell(col_w[5], 6, str(monthly[m]["expense_count"]), border=1, align="C")
        pdf.ln()

    pdf.set_font("Helvetica", "B", 8)
    total_net = total_income - total_expense
    pdf.cell(col_w[0], 7, "TOTALE", border=1, align="C")
    pdf.cell(col_w[1], 7, _hide(total_income, hide_amounts), border=1, align="R")
    pdf.cell(col_w[2], 7, _hide(total_expense, hide_amounts), border=1, align="R")
    pdf.cell(col_w[3], 7, _hide(total_net, hide_amounts), border=1, align="R")
    pdf.cell(col_w[4], 7, "", border=1)
    pdf.cell(col_w[5], 7, "", border=1)
    pdf.ln(8)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, "Riepilogo", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    summary_items = [
        ("Imponibile entrate (al netto IVA e bollo)", totals["income_before_pm"]),
        ("Spese lorde", totals["expense_gross"]),
        ("Saldo reale", totals["grand_total_real"]),
        ("Saldo virtuale", totals["grand_total_virtual"]),
    ]
    for label, val in summary_items:
        pdf.cell(120, 6, label, border=0)
        pdf.cell(0, 6, _hide(val, hide_amounts), border=0, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, "Dettaglio Entrate", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    _write_detail_table(pdf, data["incomes"], True, data["apartments"], hide_amounts)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, "Dettaglio Spese", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    _write_detail_table(pdf, data["expenses"], False, data["apartments"], hide_amounts)

    if deductions:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "Documenti per Detrazioni", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.set_font("Helvetica", "B", 9)
        doc_headers = ["File", "Tipo", "Anno detrazione", "Note"]
        doc_cols = [60, 40, 30, 60]
        for i, h in enumerate(doc_headers):
            pdf.cell(doc_cols[i], 7, h, border=1, align="C")
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        for d in deductions:
            pdf.cell(doc_cols[0], 6, d.filename[:40], border=1)
            pdf.cell(doc_cols[1], 6, (d.document_type or "").capitalize(), border=1, align="C")
            pdf.cell(doc_cols[2], 6, str(d.deduction_year or ""), border=1, align="C")
            notes = (d.notes or "")[:50]
            pdf.cell(doc_cols[3], 6, notes, border=1, new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


@router.get("/export")
async def export_form(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login")
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        from datetime import datetime
        years = set()
        for inc in db.query(Income.date).all():
            try:
                years.add(datetime.strptime(inc[0], "%Y-%m-%d").year)
            except Exception:
                pass
        for exp in db.query(Expense.date).all():
            try:
                years.add(datetime.strptime(exp[0], "%Y-%m-%d").year)
            except Exception:
                pass
        available_years = sorted(years)
        return templates.TemplateResponse(request, "tax_export.html", {
            "available_years": available_years,
            "current_year": datetime.now().year,
        })
    finally:
        db.close()


@router.post("/export/pdf")
async def export_pdf(
    request: Request,
    years: list[str] = Form(default=[]),
    months: list[str] = Form(default=[]),
    include_chart: str = Form(''),
    hide_amounts: str = Form(''),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        selected_years = [int(y) for y in years] if years else [datetime.now().year]
        selected_months = [int(m) for m in months] if months else None
        show_chart = include_chart == 'on'
        hidden = hide_amounts == 'on'
        data = _collect_data(db, selected_years, selected_months)
        pdf_bytes = generate_pdf(data, selected_years, selected_months, hidden, show_chart)
        year_label = "_".join(str(y) for y in selected_years)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="report_{year_label}.pdf"'},
        )
    except Exception as e:
        print(f"TAX EXPORT PDF ERROR: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(url='/tax/export?error=export_failed', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get("/export/730")
async def export_730_form(request: Request, year: int = None):
    if not get_current_user(request):
        return RedirectResponse(url="/login")
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        years = set()
        for inc in db.query(Income.date).all():
            try:
                years.add(datetime.strptime(inc[0], "%Y-%m-%d").year)
            except Exception:
                pass
        for exp in db.query(Expense.date).all():
            try:
                years.add(datetime.strptime(exp[0], "%Y-%m-%d").year)
            except Exception:
                pass
        available_years = sorted(years)
        prev_year = datetime.now().year - 1 if not year else year
        deduction_count = db.query(Attachment).filter(Attachment.is_deduction == True).count()
        return templates.TemplateResponse(request, "tax_730.html", {
            "available_years": available_years,
            "current_year": datetime.now().year,
            "prev_year": prev_year,
            "deduction_count": deduction_count,
        })
    finally:
        db.close()


def _filter_deductions_by_year(db, year_int):
    from sqlalchemy.orm import joinedload
    deductions = db.query(Attachment).options(
        joinedload(Attachment.expense),
        joinedload(Attachment.income),
    ).filter(Attachment.is_deduction == True).all()
    year_deductions = []
    for d in deductions:
        attachment_year = None
        if d.deduction_year is not None:
            attachment_year = d.deduction_year
        if attachment_year is None and d.document_date:
            try:
                attachment_year = datetime.strptime(d.document_date, "%Y-%m-%d").year
            except Exception:
                pass
        if attachment_year is None:
            entry = d.expense or d.income
            if entry and entry.date:
                try:
                    attachment_year = datetime.strptime(entry.date, "%Y-%m-%d").year
                except Exception:
                    pass
        if attachment_year is None and d.created_at:
            attachment_year = d.created_at.year
        if attachment_year == year_int:
            year_deductions.append(d)
    return year_deductions


@router.post("/export/730/zip")
async def export_730_zip(
    request: Request,
    year: str = Form(...),
    hide_amounts: str = Form(''),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        year_int = int(year)
        hidden = hide_amounts == 'on'
        data = _collect_data(db, [year_int])
        year_deductions = _filter_deductions_by_year(db, year_int)

        pdf_bytes = generate_730_pdf(data, [year_int], year_deductions, hidden)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"730_{year_int}/report_{year_int}.pdf", pdf_bytes)
            for d in year_deductions:
                if d.disk_path and os.path.exists(d.disk_path):
                    arcname = f"730_{year_int}/documenti/{d.filename}"
                    zf.write(d.disk_path, arcname)
                    if d.notes:
                        zf.writestr(f"730_{year_int}/documenti/{d.filename}.txt", d.notes)

        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="730_{year_int}.zip"'},
        )
    except Exception as e:
        print(f"TAX 730 ZIP ERROR: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(url=f'/tax/export/730?error=export_failed', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/export/730/pdf")
async def export_730_pdf(
    request: Request,
    year: str = Form(...),
    hide_amounts: str = Form(''),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        expand_open_recurrences_to_current_year(db)
        year_int = int(year)
        hidden = hide_amounts == 'on'
        data = _collect_data(db, [year_int])
        year_deductions = _filter_deductions_by_year(db, year_int)

        pdf_bytes = generate_730_pdf(data, [year_int], year_deductions, hidden)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="730_{year_int}.pdf"'},
        )
    except Exception as e:
        print(f"TAX 730 PDF ERROR: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(url=f'/tax/export/730?error=export_failed', status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()
