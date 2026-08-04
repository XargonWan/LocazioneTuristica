import calendar as cal_mod
import csv
import io
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import re
import threading
import time
import urllib.request
from datetime import datetime as dt_mod
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from starlette.responses import RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER

from app.db import SessionLocal
from app.models import PlatformBooking, Apartment, Platform, Income, Settings, PropertyManager
from app.auth_utils import admin_required, get_current_user

router = APIRouter()
from app.main import templates


def _append_query_params(url: str, **params):
    target_url = url or "/calendar"
    parsed = urlsplit(target_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = str(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _build_calendar_grid(year, month, bookings):
    cal = cal_mod.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)
    day_bookings_map = {}
    for b in bookings:
        ci = b.get("check_in")
        co = b.get("check_out")
        if not ci or not co:
            continue
        for i in range((co - ci).days):
            d = ci + timedelta(days=i)
            key = d.strftime("%Y-%m-%d")
            if key not in day_bookings_map:
                day_bookings_map[key] = []
            day_bookings_map[key].append(b)
    weeks = []
    for week in month_days:
        days = []
        for day_num in week:
            if day_num == 0:
                days.append(None)
            else:
                date_str = f"{year}-{month:02d}-{day_num:02d}"
                days.append({
                    "day": day_num,
                    "date": date_str,
                    "bookings": day_bookings_map.get(date_str, []),
                })
        weeks.append(days)
    return weeks


@router.get("/calendar")
async def calendar_view(request: Request, year: int = None, month: int = None, apartment_id: int = None):
    if not get_current_user(request):
        return RedirectResponse(url="/login")
    db = SessionLocal()
    try:
        now = datetime.now()
        year = year or now.year
        month = month or now.month
        if month < 1:
            month = 1
            year -= 1
        if month > 12:
            month = 12
            year += 1

        apartments = db.query(Apartment).filter(Apartment.active == True).all()
        platforms = db.query(Platform).all()

        bookings_query = db.query(PlatformBooking).filter(PlatformBooking.status != "cancelled")
        if apartment_id:
            bookings_query = bookings_query.filter(PlatformBooking.apartment_id == apartment_id)
        platform_bookings = bookings_query.all()

        incomes = db.query(Income)
        if apartment_id:
            incomes = incomes.filter(Income.apartment_id == apartment_id)
        incomes = incomes.all()

        calendar_bookings = []
        booking_idx = 0
        for b in platform_bookings:
            try:
                ci = datetime.strptime(b.check_in, "%Y-%m-%d") if b.check_in else None
                co = datetime.strptime(b.check_out, "%Y-%m-%d") if b.check_out else None
            except Exception:
                continue
            if not ci or not co:
                continue
            apt_name = b.apartment.name if b.apartment else ""
            plat_name = b.platform.name if b.platform else ""
            calendar_bookings.append({
                "idx": booking_idx,
                "id": b.id,
                "guest_name": b.guest_name or "",
                "check_in": ci,
                "check_out": co,
                "apartment_name": apt_name,
                "platform_name": plat_name,
                "status": b.status or "confirmed",
                "gross_amount": float(b.gross_amount or 0.0),
                "net_amount": float(b.net_amount or 0.0),
                "platform_fee": float(b.platform_fee or 0.0),
                "income_id": b.income_id,
                "guests_count": b.guests_count,
            })
            booking_idx += 1

        linked_income_ids = {b.income_id for b in platform_bookings if b.income_id}
        for inc in incomes:
            if not inc.date:
                continue
            if inc.id in linked_income_ids:
                continue
            try:
                d = datetime.strptime(inc.date, "%Y-%m-%d")
            except Exception:
                continue
            co = None
            if inc.check_out:
                try:
                    co = datetime.strptime(inc.check_out, "%Y-%m-%d")
                except Exception:
                    pass
            if not co:
                co = d + timedelta(days=1)
            plat_name = inc.platform.name if inc.platform else ""
            apt_name = inc.apartment.name if inc.apartment else ""
            calendar_bookings.append({
                "idx": booking_idx,
                "id": None,
                "guest_name": inc.notes or "",
                "check_in": d,
                "check_out": co,
                "apartment_name": apt_name,
                "platform_name": plat_name,
                "status": "confirmed",
                "gross_amount": float(inc.gross_amount or 0.0),
                "net_amount": float(inc.net_amount or 0.0),
                "platform_fee": 0.0,
                "income_id": inc.id,
                "guests_count": None,
            })
            booking_idx += 1

        calendar_bookings.sort(key=lambda x: x["check_in"])
        grid = _build_calendar_grid(year, month, calendar_bookings)

        month_names = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

        ical_settings = db.query(Settings).filter(Settings.key.like("ical_url_%")).filter(~Settings.key.like("%_platform")).all()
        ical_urls = []
        for s in ical_settings:
            idx = s.key.split("_")[-1]
            platform_setting = db.query(Settings).filter(Settings.key == f"ical_url_{idx}_platform").first()
            platform_id = int(platform_setting.value) if platform_setting and platform_setting.value else None
            ical_urls.append({"idx": idx, "url": s.value, "platform_id": platform_id})

        prev_month = month - 1
        prev_year = year
        if prev_month < 1:
            prev_month = 12
            prev_year = year - 1
        next_month = month + 1
        next_year = year
        if next_month > 12:
            next_month = 1
            next_year = year + 1

        return templates.TemplateResponse(request, "calendar.html", {
            "bookings": calendar_bookings,
            "grid": grid,
            "year": year,
            "month": month,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "apartments": apartments,
            "platforms": platforms,
            "filter_apartment_id": apartment_id,
            "month_name": month_names[month],
            "now": now,
            "weekdays": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
            "ical_urls": ical_urls,
        })
    finally:
        db.close()


@router.post("/calendar/import/csv")
async def calendar_import_csv(
    request: Request,
    file: UploadFile = File(...),
    platform_id: int = Form(...),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        contents = await file.read()
        text = contents.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return RedirectResponse(
                url=_append_query_params("/calendar", import_status="error_no_columns"),
                status_code=HTTP_303_SEE_OTHER,
            )

        fn_lower = {k.lower().strip(): k for k in reader.fieldnames}
        total = 0
        for row in reader:
            booking_id = (
                row.get(fn_lower.get("confirmation code", ""))
                or row.get(fn_lower.get("reservation number", ""))
                or row.get(fn_lower.get("reservation_id", ""))
                or row.get(fn_lower.get("id", ""))
                or ""
            )
            guest = (
                row.get(fn_lower.get("guest", ""))
                or row.get(fn_lower.get("guest name", ""))
                or row.get(fn_lower.get("guest_name", ""))
                or row.get(fn_lower.get("name", ""))
                or ""
            )
            check_in = (
                row.get(fn_lower.get("start date", ""))
                or row.get(fn_lower.get("check in", ""))
                or row.get(fn_lower.get("check_in", ""))
                or row.get(fn_lower.get("arrival", ""))
                or ""
            )
            check_out = (
                row.get(fn_lower.get("end date", ""))
                or row.get(fn_lower.get("check out", ""))
                or row.get(fn_lower.get("check_out", ""))
                or row.get(fn_lower.get("departure", ""))
                or ""
            )
            status = (
                row.get(fn_lower.get("status", ""))
                or "confirmed"
            )
            gross = (
                row.get(fn_lower.get("gross earnings", ""))
                or row.get(fn_lower.get("total price", ""))
                or row.get(fn_lower.get("gross_amount", ""))
                or row.get(fn_lower.get("total", ""))
                or "0"
            )
            cleaning = (
                row.get(fn_lower.get("cleaning fee", ""))
                or row.get(fn_lower.get("cleaning_fee", ""))
                or "0"
            )
            fee = (
                row.get(fn_lower.get("airbnb fee", ""))
                or row.get(fn_lower.get("commission", ""))
                or row.get(fn_lower.get("platform_fee", ""))
                or row.get(fn_lower.get("service fee", ""))
                or "0"
            )
            net = (
                row.get(fn_lower.get("net earnings", ""))
                or row.get(fn_lower.get("net_amount", ""))
                or row.get(fn_lower.get("payout", ""))
                or "0"
            )
            guests = (
                row.get(fn_lower.get("guests", ""))
                or row.get(fn_lower.get("guest count", ""))
                or row.get(fn_lower.get("guests_count", ""))
                or ""
            )
            phone = (
                row.get(fn_lower.get("phone", ""))
                or row.get(fn_lower.get("phone number", ""))
                or ""
            )
            email = (
                row.get(fn_lower.get("email", ""))
                or row.get(fn_lower.get("email address", ""))
                or ""
            )
            listing = (
                row.get(fn_lower.get("listing", ""))
                or row.get(fn_lower.get("property", ""))
                or row.get(fn_lower.get("apartment", ""))
                or row.get(fn_lower.get("listing name", ""))
                or ""
            )

            def parse_currency(val):
                try:
                    return float(val.replace("€", "").replace(",", ".").strip())
                except Exception:
                    try:
                        return float(val)
                    except Exception:
                        return 0.0

            def parse_date(val):
                for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"]:
                    try:
                        return datetime.strptime(val.strip(), fmt).strftime("%Y-%m-%d")
                    except Exception:
                        continue
                return val.strip() if val else ""

            check_in = parse_date(check_in)
            check_out = parse_date(check_out)
            gross_amount = parse_currency(gross)
            cleaning_fee = parse_currency(cleaning)
            platform_fee = parse_currency(fee)
            net_amount = parse_currency(net) if net else (gross_amount - platform_fee)
            try:
                guests_count = int(float(guests)) if guests else None
            except Exception:
                guests_count = None

            apt_id = None
            if listing:
                apt = db.query(Apartment).filter(Apartment.name.ilike(f"%{listing}%")).first()
                if apt:
                    apt_id = apt.id

            exists = db.query(PlatformBooking).filter(
                PlatformBooking.platform_booking_id == booking_id,
                PlatformBooking.platform_id == platform_id,
            ).first() if booking_id else None
            if exists:
                exists.guest_name = guest
                exists.check_in = check_in
                exists.check_out = check_out
                exists.status = status
                exists.gross_amount = gross_amount
                exists.cleaning_fee = cleaning_fee
                exists.platform_fee = platform_fee
                exists.net_amount = net_amount
                exists.apartment_id = apt_id or exists.apartment_id
                exists.guests_count = guests_count or exists.guests_count
                exists.phone = phone or exists.phone
                exists.email = email or exists.email
                db.add(exists)
            else:
                pb = PlatformBooking(
                    platform_booking_id=booking_id,
                    apartment_id=apt_id,
                    platform_id=platform_id,
                    guest_name=guest,
                    check_in=check_in,
                    check_out=check_out,
                    status=status,
                    gross_amount=gross_amount,
                    cleaning_fee=cleaning_fee,
                    platform_fee=platform_fee,
                    net_amount=net_amount,
                    guests_count=guests_count,
                    phone=phone,
                    email=email,
                    import_source=file.filename or "csv",
                )
                db.add(pb)
            total += 1

        db.commit()
        return RedirectResponse(
            url=_append_query_params("/calendar", import_status="success", imported=str(total)),
            status_code=HTTP_303_SEE_OTHER,
        )
    except Exception as exc:
        db.rollback()
        return RedirectResponse(
            url=_append_query_params("/calendar", import_status="error", error=str(exc)),
            status_code=HTTP_303_SEE_OTHER,
        )
    finally:
        db.close()


@router.post("/calendar/booking/{booking_id}/link-income")
async def calendar_link_income(
    request: Request,
    booking_id: int,
    income_id: int = Form(...),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        booking = db.query(PlatformBooking).filter(PlatformBooking.id == booking_id).first()
        if booking:
            booking.income_id = income_id
            db.add(booking)
            db.commit()
        return RedirectResponse(url=_append_query_params("/calendar", linked="ok"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/calendar/booking/{booking_id}/unlink-income")
async def calendar_unlink_income(
    request: Request,
    booking_id: int,
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        booking = db.query(PlatformBooking).filter(PlatformBooking.id == booking_id).first()
        if booking:
            booking.income_id = None
            db.add(booking)
            db.commit()
        return RedirectResponse(url=_append_query_params("/calendar", linked="ok"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/calendar/booking/add")
async def calendar_booking_add(
    request: Request,
    guest_name: str = Form(...),
    check_in: str = Form(...),
    check_out: str = Form(...),
    apartment_id: int = Form(None),
    platform_id: int = Form(None),
    gross_amount: float = Form(0.0),
    platform_fee: float = Form(0.0),
    net_amount: float = Form(0.0),
    status: str = Form("confirmed"),
    guests_count: int = Form(None),
    phone: str = Form(None),
    email: str = Form(None),
    notes: str = Form(None),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        pb = PlatformBooking(
            apartment_id=apartment_id,
            platform_id=platform_id,
            guest_name=guest_name,
            check_in=check_in,
            check_out=check_out,
            gross_amount=gross_amount,
            platform_fee=platform_fee,
            net_amount=net_amount or (gross_amount - platform_fee),
            status=status,
            guests_count=guests_count,
            phone=phone,
            email=email,
            notes=notes,
        )
        db.add(pb)
        db.commit()
        return RedirectResponse(url=_append_query_params("/calendar", added="ok"), status_code=HTTP_303_SEE_OTHER)
    except Exception:
        db.rollback()
        return RedirectResponse(url=_append_query_params("/calendar", added="error"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/calendar/booking/{booking_id}/delete")
async def calendar_booking_delete(
    request: Request,
    booking_id: int,
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        booking = db.query(PlatformBooking).filter(PlatformBooking.id == booking_id).first()
        if booking:
            db.delete(booking)
            db.commit()
        return RedirectResponse(url=_append_query_params("/calendar", deleted="ok"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


def _parse_ical_fetch(url: str, platform_id: int = None, apartment_id: int = None) -> list[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LocazioneTuristica/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise ValueError(f"Errore scaricando iCal da {url}: {exc}")

    bookings = []
    current_event = {}
    in_event = False
    for line in data.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            current_event = {}
            in_event = True
        elif line == "END:VEVENT":
            in_event = False
            if current_event:
                ci = current_event.get("DTSTART", "").replace(";VALUE=DATE", "")
                co = current_event.get("DTEND", "").replace(";VALUE=DATE", "")
                summary = current_event.get("SUMMARY", "")
                desc = current_event.get("DESCRIPTION", "")
                uid = current_event.get("UID", "")
                if len(ci) == 8 and ci.isdigit():
                    ci = f"{ci[:4]}-{ci[4:6]}-{ci[6:8]}"
                if len(co) == 8 and co.isdigit():
                    co = f"{co[:4]}-{co[4:6]}-{co[6:8]}"
                guest_name = summary
                if " - " in summary:
                    guest_name = summary.split(" - ")[0].strip()
                bookings.append({
                    "platform_booking_id": uid,
                    "guest_name": guest_name[:200],
                    "check_in": ci,
                    "check_out": co,
                    "summary": summary,
                    "description": desc,
                    "platform_id": platform_id,
                    "apartment_id": apartment_id,
                })
        elif in_event:
            if ":" in line:
                key, val = line.split(":", 1)
                current_event[key.upper()] = val
    return bookings


@router.post("/calendar/ical/save-url")
async def calendar_ical_save_url(
    request: Request,
    ical_url: str = Form(...),
    platform_id: int = Form(None),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        idx = 1
        while True:
            existing = db.query(Settings).filter(Settings.key == f"ical_url_{idx}").first()
            if not existing:
                break
            idx += 1
        s = Settings(key=f"ical_url_{idx}", value=ical_url)
        db.add(s)
        s2 = Settings(key=f"ical_url_{idx}_platform", value=str(platform_id or ""))
        db.add(s2)
        db.commit()
        return RedirectResponse(url=_append_query_params("/calendar", ical="saved"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.post("/calendar/ical/delete-url")
async def calendar_ical_delete_url(
    request: Request,
    idx: int = Form(...),
    user=Depends(admin_required),
):
    db = SessionLocal()
    try:
        s = db.query(Settings).filter(Settings.key == f"ical_url_{idx}").first()
        if s:
            db.delete(s)
        s2 = db.query(Settings).filter(Settings.key == f"ical_url_{idx}_platform").first()
        if s2:
            db.delete(s2)
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url=_append_query_params("/calendar", ical="deleted"), status_code=HTTP_303_SEE_OTHER)


def _save_ical_booking_to_db(db, bd: dict):
    booking_id = bd.get("platform_booking_id") or ""
    guest = bd.get("guest_name", "")
    ci = bd.get("check_in", "")
    co = bd.get("check_out", "")
    platform_id = bd.get("platform_id")
    apartment_id = bd.get("apartment_id")

    pb = None
    if booking_id:
        pb = db.query(PlatformBooking).filter(
            PlatformBooking.platform_booking_id == booking_id
        ).first()

    if pb:
        pb.guest_name = guest
        pb.check_in = ci
        pb.check_out = co
        if apartment_id:
            pb.apartment_id = apartment_id
        if platform_id:
            pb.platform_id = platform_id
        db.add(pb)
    else:
        pb = PlatformBooking(
            platform_booking_id=booking_id,
            guest_name=guest,
            check_in=ci,
            check_out=co,
            apartment_id=apartment_id,
            platform_id=platform_id,
            status="confirmed",
            import_source="ical",
        )
        db.add(pb)

    if not pb.apartment_id:
        return pb

    income = None
    if pb.income_id:
        income = db.query(Income).filter(Income.id == pb.income_id).first()

    if not income:
        platform_id_val = pb.platform_id if pb.platform_id else (platform_id or None)
        income = Income(
            apartment_id=pb.apartment_id,
            platform_id=platform_id_val,
            date=ci if ci else None,
            check_out=co if co and co != ci else None,
            notes=guest[:500] if guest else None,
            gross_amount=0.0,
            net_amount=0.0,
            vat_percent=22.0,
        )
        db.add(income)
        db.flush()
        pb.income_id = income.id
        db.add(pb)
    else:
        if ci:
            income.date = ci
        if co and co != ci:
            income.check_out = co
        if guest:
            income.notes = guest[:500]
        if pb.platform_id:
            income.platform_id = pb.platform_id
        income.apartment_id = pb.apartment_id
        db.add(income)

    # Auto-assign PM from apartment
    apartment = db.query(Apartment).filter(Apartment.id == pb.apartment_id).first()
    if apartment and apartment.property_manager_id:
        income.associated_pm_id = apartment.property_manager_id
        pm = db.query(PropertyManager).filter(PropertyManager.id == apartment.property_manager_id).first()
        if pm:
            income.pm_percent = pm.percent
        db.add(income)

    return pb


@router.post("/calendar/ical/fetch-all")
async def calendar_ical_fetch_all(request: Request, user=Depends(admin_required)):
    db = SessionLocal()
    try:
        total = sync_all_ical_feeds(db=db)
        db.commit()
        return RedirectResponse(url=_append_query_params("/calendar", ical_fetch=f"ok:{total}"), status_code=HTTP_303_SEE_OTHER)
    except Exception as exc:
        db.rollback()
        return RedirectResponse(url=_append_query_params("/calendar", ical_fetch=f"error:{exc}"), status_code=HTTP_303_SEE_OTHER)
    finally:
        db.close()


@router.get("/calendar/ical")
async def calendar_ical_export(request: Request):
    db = SessionLocal()
    try:
        bookings = db.query(PlatformBooking).filter(PlatformBooking.status != "cancelled").all()
        incomes = db.query(Income).all()

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//LocazioneTuristica//Calendario Prenotazioni//IT",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "X-WR-CALNAME:Prenotazioni LocazioneTuristica",
        ]

        for b in bookings:
            if not b.check_in or not b.check_out:
                continue
            uid = f"booking-{b.id or 'imported'}@locazioneturistica"
            apt_name = b.apartment.name if b.apartment else "Sconosciuto"
            plat_name = b.platform.name if b.platform else ""
            summary = f"{b.guest_name or 'Ospite'} - {apt_name}"
            if plat_name:
                summary += f" ({plat_name})"
            ci = b.check_in.replace("-", "")
            co = b.check_out.replace("-", "")
            desc = f"Prenotazione #{b.id}\\nAppartamento: {apt_name}\\nPiattaforma: {plat_name}\\nOspiti: {b.guests_count or '?'}\\nStatus: {b.status}"
            lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTART;VALUE=DATE:{ci}",
                f"DTEND;VALUE=DATE:{co}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{desc}",
                "END:VEVENT",
            ])

        for inc in incomes:
            if not inc.date:
                continue
            uid = f"income-{inc.id}@locazioneturistica"
            apt_name = inc.apartment.name if inc.apartment else ""
            plat_name = inc.platform.name if inc.platform else ""
            summary = f"{inc.notes or 'Entrata'} - {apt_name}"
            if plat_name:
                summary += f" ({plat_name})"
            d = inc.date.replace("-", "")
            lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTART;VALUE=DATE:{d}",
                f"DTEND;VALUE=DATE:{d}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:Entrata #{inc.id}\\nImporto: €{float(inc.gross_amount or 0.0):.2f}",
                "END:VEVENT",
            ])

        lines.append("END:VCALENDAR")
        content = "\r\n".join(lines)
        return Response(
            content=content,
            media_type="text/calendar",
            headers={"Content-Disposition": "attachment; filename=calendario-prenotazioni.ics"},
        )
    finally:
        db.close()


def sync_all_ical_feeds(db=None):
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True
    try:
        settings = db.query(Settings).filter(Settings.key.like("ical_url_%")).filter(~Settings.key.like("%_platform")).all()
        total = 0
        for s in settings:
            idx = s.key.split("_")[-1]
            platform_setting = db.query(Settings).filter(Settings.key == f"ical_url_{idx}_platform").first()
            platform_id = int(platform_setting.value) if platform_setting and platform_setting.value else None
            try:
                bookings_data = _parse_ical_fetch(s.value, platform_id=platform_id)
            except ValueError:
                continue
            for bd in bookings_data:
                try:
                    _save_ical_booking_to_db(db, bd)
                except Exception:
                    continue
                total += 1
        if own_session:
            db.commit()
        return total
    finally:
        if own_session:
            db.close()


ICAL_SYNC_INTERVAL_DEFAULT = 360  # minutes (6 ore)
_ical_background_thread_started = False


def _ical_background_worker():
    while True:
        try:
            db = SessionLocal()
            try:
                interval_setting = db.query(Settings).filter(Settings.key == "ical_sync_interval_minutes").first()
                interval = int(interval_setting.value) if interval_setting and interval_setting.value else ICAL_SYNC_INTERVAL_DEFAULT
            except Exception:
                interval = ICAL_SYNC_INTERVAL_DEFAULT
            finally:
                db.close()

            try:
                sync_all_ical_feeds()
            except Exception:
                pass

            time.sleep(interval * 60)
        except Exception:
            time.sleep(3600)


def start_ical_background_sync():
    global _ical_background_thread_started
    if _ical_background_thread_started:
        return
    _ical_background_thread_started = True
    thread = threading.Thread(target=_ical_background_worker, daemon=True)
    thread.start()
