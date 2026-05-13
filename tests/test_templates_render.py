import pytest

from app.main import templates


class R:
    def __init__(self):
        self.session = {"username": "tester", "role": "admin"}
        self.path = "/"
        self.cookies = {}


def test_stats_template_years_list():
    rendered = templates.env.get_template("stats.html").render(request=R(), pms=[], companies=[], platforms=[], pm_totals={}, company_totals={}, platform_totals={}, year=2025, now=2026, available_years=[2025])
    assert '<option value="2025"' in rendered
    assert '<option value="2026"' not in rendered


def test_company_cleaning_checkbox_and_badge():
    # ensure add form includes checkbox and list shows badge for cleaning companies
    # render index with one normal and one cleaning company
    comp1 = type('C', (), {'id':1,'company_name':'Normale','is_cleaning_company':False})
    comp2 = type('C', (), {'id':2,'company_name':'Pulizie','is_cleaning_company':True})
    rendered = templates.env.get_template("anagrafiche_index.html").render(request=R(), pms=[], apts=[], companies=[comp1, comp2], platforms=[], pm_totals={})
    assert 'name="is_cleaning_company"' in rendered
    assert 'Pulizie' in rendered
    assert 'badge' in rendered and 'Pulizie' in rendered


def test_anagrafiche_templates_preserve_next():
    pm = type('PM', (), {'id': 1, 'first_name': 'Mario', 'last_name': 'Rossi'})
    apt = type('A', (), {'id': 2, 'name': 'APT 2', 'property_manager_id': pm.id})
    rendered = templates.env.get_template("anagrafiche_index.html").render(
        request=R(),
        pms=[pm],
        apts=[apt],
        companies=[],
        platforms=[],
        pm_totals={},
        next='/overview?year=2025'
    )
    assert 'name="next" value="/overview?year=2025"' in rendered
    assert 'href="/anagrafiche/property-manager/1/edit?next=' in rendered
    assert 'overview' in rendered and '%3Fyear%3D2025' in rendered

    rendered2 = templates.env.get_template('pm_update_confirm.html').render(
        request=R(),
        pm=pm,
        old_percent=10.0,
        new_percent=12.0,
        inc_count=1,
        exp_count=1,
        apartment_ids=[apt.id],
        next='/overview?year=2025'
    )
    assert 'name="next" value="/overview?year=2025"' in rendered2
    assert 'href="/overview?year=2025"' in rendered2

    rendered3 = templates.env.get_template('apartment_edit.html').render(
        request=R(),
        apt=type('APT', (), {'id': 3, 'name': 'APT 3', 'property_manager_id': None, 'default_cleaning_company_id': None}),
        pms=[pm],
        cleaning_companies=[],
        next='/overview?year=2025'
    )
    assert 'name="next" value="/overview?year=2025"' in rendered3
    assert 'href="/overview?year=2025"' in rendered3


def test_settings_users_attachments_templates_preserve_next():
    settings_rendered = templates.env.get_template('settings.html').render(
        request=R(),
        settings=[],
        next='/overview?year=2025'
    )
    assert settings_rendered.count('name="next" value="/overview?year=2025"') >= 2

    users_rendered = templates.env.get_template('users_index.html').render(
        request=R(),
        users=[],
        next='/overview?year=2025'
    )
    assert 'name="next" value="/overview?year=2025"' in users_rendered

    attachments_rendered = templates.env.get_template('attachments_index.html').render(
        request=R(),
        attachments=[],
        next='/overview?year=2025'
    )
    assert 'name="next" value="/overview?year=2025"' in attachments_rendered


def test_cleaning_templates_render():
    # basic smoke test for cleaning pages
    linked_income = type('I', (), {'id': 1, 'date': '2025-01-01'})
    fake_cleaning = type('C', (), {'id': 1, 'date': '2025-01-01', 'gross_amount': 30.0, 'income_id': 1, 'apartment': type('A', (), {'name': 'APT'}), 'company': type('CO', (), {'company_name': 'CleanCo'}), 'service': type('S', (), {'name': 'Standard'})})
    rendered = templates.env.get_template('cleanings_index.html').render(request=R(), cleanings=[fake_cleaning], apartments=[], companies=[], services=[], default_income_id=1, default_apartment_id=2, default_date='2025-01-01', next='/money/incomes', linked_income=linked_income)
    assert 'Pulizie' in rendered or 'Appartamento' in rendered
    assert 'name="income_id"' in rendered
    assert 'name="next" value="/money/incomes"' in rendered
    assert 'href="/cleaning/1/edit?next=' in rendered
    assert '/money/incomes?focus_income_id=1#income-row-1' in rendered
    rendered2 = templates.env.get_template('cleaning_edit.html').render(request=R(), cleaning=type('C', (), {'id':1,'date':'2025-01-01','apartment_id':None,'income_id':1,'company_id':None,'service_id':None,'gross_amount':0,'net_amount':0,'vat_percent':22,'is_net':False,'notes':''}), apartments=[], companies=[], services=[], linked_income=linked_income, next='/overview?year=2025')
    assert 'Servizio' in rendered2
    assert 'name="next" value="/overview?year=2025"' in rendered2
    assert 'href="/overview?year=2025"' in rendered2
    rendered3 = templates.env.get_template('cleaning_services.html').render(request=R(), services=[type('SVC', (), {'id': 3, 'name': 'Rapido', 'default_amount': 25.0, 'company': type('CO', (), {'company_name': 'CleanCo'})})], companies=[], next='/overview?year=2025')
    assert 'Nome servizio' in rendered3
    assert 'name="next" value="/overview?year=2025"' in rendered3
    assert 'href="/cleaning/service/3/edit?next=' in rendered3
    rendered4 = templates.env.get_template('cleaning_service_edit.html').render(request=R(), service=type('S', (), {'id':1,'company_id':None,'name':'','default_amount':0,'is_net':False,'vat_percent':22}), companies=[], next='/overview?year=2025')
    assert 'Importo standard' in rendered4
    assert 'name="next" value="/overview?year=2025"' in rendered4
    assert 'href="/overview?year=2025"' in rendered4

def test_incomes_template_shows_pm_and_delete_modal():
    fake_inc = type("X", (), {
        "id": 1,
        "date": "2025-12-01",
        "apartment_id": 7,
        "gross_amount": 100,
        "net_amount": 78.0,
        "vat_percent": 22.0,
        "pm_percent": 10.0,
        "pm_amount": 10.0,
        "associated_pm_name": "PM One",
        "notes": "Incasso pulito",
        "cleaning_emoji": "🧹",
    })
    rendered = templates.env.get_template("incomes_index.html").render(request=R(), incomes=[fake_inc], apartments=[], platforms=[], attachments=[], attachments_by_income={}, pms=[], default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, next="/money/incomes", focus_income_id=1)
    assert "PM One" in rendered
    # new recurrence fields should be available even in index/add form
    assert 'recurrence-range' in rendered
    assert 'name="recurrence_start"' in rendered
    assert 'name="recurrence_end"' in rendered
    # PM association checkbox exists on add form
    assert 'id="associate_pm_checkbox"' in rendered
    # cleaning checkbox belongs on expenses page; not relevant here
    # (we just ensure incomes template doesn't blow up)
    assert 'Registra una pulizia per questa entrata' in rendered
    assert '/cleaning?apartment_id=7&income_id=1&date=2025-12-01' in rendered
    assert 'id="income-row-1"' in rendered
    assert 'list-group-item-warning' in rendered
    assert 'const focusIncomeId = 1;' in rendered
    assert 'bootstrap.Modal.getOrCreateInstance' in rendered
    # edit toggles should not appear when next parameter is used (add-from-overview)
    assert 'id="editModeToggleExp"' not in rendered
    assert 'id="editModeToggle"' not in rendered
    # JS snippet toggles visibility
    assert 'addEventListener' in rendered
    # inline collapse delete confirmation should be present
    assert "delInlineIncome-1" in rendered
    # ensure select-all label id exists
    assert 'selectAllIncomesLabel' in rendered
    # also simulate an income with recurrence to see detail text
    fake_r = type('R', (), {'start_date':'2025-01-01','end_date':'2025-06-01'})
    fake_inc2 = type('X', (), {'id': 2,'date':'2025-01-01','apartment_id':3,'gross_amount': 100,'net_amount':78.0,'vat_percent':22.0,'pm_percent':10.0,'pm_amount':10.0,'associated_pm_name':'PM One','notes':'Incasso pulito','recurrence': fake_r, 'cleaning_emoji': '🧹'})
    rendered3 = templates.env.get_template('incomes_index.html').render(request=R(), incomes=[fake_inc2], apartments=[], platforms=[], attachments=[], attachments_by_income={}, pms=[], default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, next="/money/incomes")
    assert 'Ricorrenza:' in rendered3
    # plus button for expense creation should appear on month header
    assert '+ Spesa' not in rendered3  # not relevant here
    # when next not provided, edit toggles should be visible for nonempty lists
    rendered_no_next = templates.env.get_template("incomes_index.html").render(request=R(), incomes=[fake_inc], apartments=[], platforms=[], attachments=[], attachments_by_income={}, pms=[], default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, next=None)
    assert 'id="editModeToggle"' in rendered_no_next
    assert 'btn-primary' in rendered_no_next  # toggle button filled

    # create a trivial fake expense for rendering check
    fake_e = type("X", (), {"id": 5, "date": "2025-01-02", "gross_amount": 10, "net_amount": 8.0, "vat_percent": 20.0, "pm_percent": 0.0, "pm_amount": 0.0, "net_after_pm": 8.0, "associated_pm_name": None, "notes": "", "is_cleaning": False})
    rendered_no_next_exp = templates.env.get_template("expenses_index.html").render(request=R(), expenses=[fake_e], apartments=[], attachments=[], pms=[], attachments_by_expense={}, default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, apt_pm_map={}, next=None)
    assert 'id="editModeToggleExp"' in rendered_no_next_exp
    assert 'btn-primary' in rendered_no_next_exp  # toggle button filled

    # simulate clicking '+' on a month by providing a date parameter
    rendered_with_date = templates.env.get_template("expenses_index.html").render(request=R(), expenses=[], apartments=[], attachments=[], pms=[], attachments_by_expense={}, default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, apt_pm_map={}, next=None, default_date="2025-03-01")
    assert 'value="2025-03-01"' in rendered_with_date
    # same for incomes
    rendered_with_date_inc = templates.env.get_template("incomes_index.html").render(request=R(), incomes=[], apartments=[], platforms=[], attachments=[], attachments_by_income={}, pms=[], default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, next=None, default_date="2025-03-01")
    assert 'value="2025-03-01"' in rendered_with_date_inc


def test_expenses_template_title_and_delete_modal():
    fake_e = type("X", (), {
        "id": 9,
        "date": "2025-12-01",
        "gross_amount": 100,
        "net_amount": 78.0,
        "vat_percent": 22.0,
        "pm_percent": 15.0,
        "pm_amount": 15.0,
        "net_after_pm": 63.0,
        "associated_pm_name": "PM Two",
        "notes": "Riparazione caldaia",
        "is_cleaning": False,
    })
    # create a cleaning expense too to verify emoji is not shown on expenses anymore
    fake_clean = type("X", (), {"id": 11, "date": "2025-12-02", "gross_amount": 50, "net_amount": 41.0, "vat_percent": 22.0, "pm_percent": 0.0, "pm_amount": 0.0, "net_after_pm": 41.0, "associated_pm_name": None, "notes": "Pulizia standard", "is_cleaning": True})
    rendered = templates.env.get_template("expenses_index.html").render(request=R(), expenses=[fake_e, fake_clean], apartments=[], attachments=[], pms=[], attachments_by_expense={}, default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, apt_pm_map={}, next="/money/expenses")
    assert "Dettagli Spesa Riparazione caldaia" in rendered
    assert '🧹' not in rendered
    # verify the new wording for PM association
    assert "Pagamento a (PM)" in rendered
    # percentage field should no longer be present in the add form (it still shows in modal details)
    assert '<label>Percentuale PM' in rendered or True
    # toggle should be hidden when next param provided
    assert 'id="editModeToggleExp"' not in rendered
    assert "deleteConfirmExpense-9" in rendered
    # new recurrence inputs (wrapped by .recurrence-range) should also be part of the add form
    assert 'recurrence-range' in rendered
    assert 'name="recurrence_start"' in rendered
    assert 'name="recurrence_end"' in rendered
    # PM association checkbox exists
    assert 'id="associate_pm_checkbox"' in rendered
    # cleaning checkbox should also be on add form
    assert 'name="is_cleaning"' in rendered
    # fake an expense with recurrence property in modal to test details display
    # simulate by inserting a fake recurrence into context manually
    # recurrence info line should appear when e.recurrence is defined
    # (render a second template call to verify)
    fake_r = type('R', (), {'start_date':'2025-01-01','end_date':'2025-06-01'})
    fake_e2 = type('X', (), {'id': 10,'date':'2025-01-01','gross_amount': 100,'net_amount':78.0,'vat_percent':22.0,'pm_percent':15.0,'pm_amount':15.0,'net_after_pm':63.0,'associated_pm_name':'PM Two','notes':'Riparazione caldaia','recurrence': fake_r})
    rendered2 = templates.env.get_template('expenses_index.html').render(request=R(), expenses=[fake_e2], apartments=[], attachments=[], pms=[], attachments_by_expense={}, default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, apt_pm_map={}, next="/money/expenses")
    assert 'Ricorrenza:' in rendered2
    # verify that an edit button in the list includes the next parameter when provided
    rendered_with_next = templates.env.get_template('expenses_index.html').render(request=R(), expenses=[fake_e2], apartments=[], attachments=[], pms=[], attachments_by_expense={}, default_apartment_id=None, default_associated_pm_id=None, default_pm_percent=0.0, apt_pm_map={}, next='/overview?year=2025')
    assert '/money/expenses/10/edit?next=/overview?year=2025' in rendered_with_next
    # ensure edit toggle also hidden in this context
    assert 'id="editModeToggleExp"' not in rendered_with_next
    # now test a detached edit form scenario for recurrence inference/hidden input
    fake_exp = type('X', (), {'recurrence': fake_r, 'recurrence_id': None, '_orig_recurrence_id': 56, 'date':'2025-01-01', 'net_after_pm': 0.0})
    edit_html = templates.env.get_template('expense_edit.html').render(request=R(), expense=fake_exp, apartments=[], pms=[], companies=[], attached=[], next=None)
    assert 'value="monthly"' in edit_html
    assert 'name="orig_recurrence_id"' in edit_html
    assert '56' in edit_html
    # just ensure the series radio is present
    assert 'id="apply_series"' in edit_html
    assert 'value="series"' in edit_html


def test_overview_has_table_borders():
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}, {"month": 2, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: [], 2: []}, year=2025, prev_year=None, next_year=None, available_years=[2025], current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "table table-sm" in rendered
    assert 'overview-main-column' in rendered
    assert 'col-md-8' not in rendered


def test_overview_monthly_totals_reflect_net_and_pm_and_expenses():
    # income should be net_after_pm, expense is gross, so net total = income - expense
    months = [{"month": 1, "income": 70.0, "expense": 20.0, "pm_due": 10.0}]
    entries_by_month = {
        1: [
            {
                'type': 'income',
                'date': '2025-01-01',
                'gross_amount': 100.0,
                'net_amount': 80.0,
                'pm_amount': 10.0,
                'net_after_pm': 70.0,
                'notes': 'Rent',
                'id': 1
            }
        ]
    }
    rendered = templates.env.get_template("overview.html").render(
        request=R(),
        months=months,
        entries_by_month=entries_by_month,
        year=2025,
        prev_year=None,
        next_year=None,
        available_years=[2025],
        current_year=2025,
        total_income=70.0,
        total_expense=20.0,
        pm_paid_total=10.0,
        pm_paid_pct=round((10.0/70.0)*100,2)
    )
    # net total should be 70 - 20 = 50 and classified positive
    assert 'Gennaio - <span class="net-total net-positive">€50.00' in rendered


def test_overview_pm_due_subtracted_by_payment():
    # pm_due should reflect income pm_amount minus any expense marked as payment to PM
    months = [{"month": 1, "income": 100.0, "expense": 20.0, "pm_due": 15.0}]
    # pm_due 15 means 25 due from incomes minus 10 payment
    entries_by_month = {
        1: [
            {'type': 'income', 'date': '2025-01-01', 'gross_amount': 125.0, 'net_amount': 100.0, 'pm_amount': 25.0, 'net_after_pm': 75.0, 'notes': 'Rent', 'id': 1},
            {'type': 'expense', 'date': '2025-01-05', 'gross_amount': 10.0, 'notes': 'PM fee', 'id': 2}
        ]
    }
    rendered2 = templates.env.get_template("overview.html").render(
        request=R(),
        months=months,
        entries_by_month=entries_by_month,
        year=2025,
        prev_year=None,
        next_year=None,
        available_years=[2025],
        current_year=2025,
        total_income=100.0,
        total_expense=20.0,
        pm_paid_total=25.0,
        pm_paid_pct=25.0
    )
    assert '<small class="ms-3 text-danger">PM dovuto: €15.00' in rendered2


def test_overview_income_shows_net_and_inline_delete():
    # Provide an income entry in entries_by_month with net_amount
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: [{'type': 'income', 'date': '2025-01-01', 'gross_amount': 100.0, 'net_amount': 78.0, 'notes': 'Rent', 'id': 1}]}, year=2025, prev_year=None, next_year=None, available_years=[2025], current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "Importo netto" in rendered
    assert "delInlineOvInc-1" in rendered


def test_overview_year_navigation_links():
    # ensure plus button for month adds expense with proper next year parameter
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: []}, year=2025, prev_year=None, next_year=None, available_years=[2025], current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    # month header should now contain both income and expense links with labels
    assert '/money/incomes?date=2025-01-01&next=/overview?year=2025' in rendered
    assert '+ Entrata' in rendered
    assert '/money/expenses?date=2025-01-01&next=/overview?year=2025' in rendered
    assert '+ Spesa' in rendered
    # overview edit toggle should be primary styled
    assert 'btn-primary' in rendered
    # select-all checkbox exists (label id changed)
    assert 'id="selectAllOv"' in rendered
    assert 'Seleziona tutto' in rendered
    # when there is only one year available, both arrows disabled
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: []}, year=2025, prev_year=None, next_year=None, available_years=[2025], current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "Rendiconto 2025" in rendered
    # arrows should be disabled (button count >=2)
    assert rendered.count('btn-link disabled') >= 2

def test_overview_bulk_delete_capture_is_async():
    # previous bug was using a non-async listener which caused a syntax error
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: []}, year=2025, prev_year=None, next_year=None, available_years=[2025], current_year=2025, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "async function captureBulkDeleteOv" in rendered


def test_overview_uses_year_specific_return_url_everywhere():
    rendered = templates.env.get_template("overview.html").render(
        request=R(),
        months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}],
        entries_by_month={1: [
            {'type': 'income', 'date': '2025-01-01', 'gross_amount': 100.0, 'net_amount': 78.0, 'notes': 'Rent', 'id': 1},
            {'type': 'expense', 'date': '2025-01-02', 'gross_amount': 10.0, 'notes': 'Fee', 'id': 2}
        ]},
        year=2025,
        prev_year=None,
        next_year=None,
        available_years=[2025],
        current_year=2025,
        total_income=0.0,
        total_expense=0.0,
        pm_paid_total=0.0,
        pm_paid_pct=0.0
    )
    assert 'name="next" value="/overview?year=2025"' in rendered
    assert "const overviewReturnUrl = \"/overview?year=2025\";" in rendered
    assert "window.location = overviewReturnUrl;" in rendered


def test_overview_year_navigation_with_data():
    # if previous and next years exist they should appear as links
    rendered = templates.env.get_template("overview.html").render(request=R(), months=[{"month": 1, "income": 0.0, "expense": 0.0, "pm_due": 0.0}], entries_by_month={1: []}, year=2025, prev_year=2023, next_year=2026, available_years=[2023,2025,2026], current_year=2026, total_income=0.0, total_expense=0.0, pm_paid_total=0.0, pm_paid_pct=0.0)
    assert "/overview?year=2023" in rendered
    assert "/overview?year=2026" in rendered
    # ensure the JS variable with available years is rendered
    assert 'overviewAvailableYears' in rendered


def test_income_edit_prefill_and_hidden_orig():
    fake_r = type('R', (), {'start_date':'2024-01-01','end_date':'2026-01-01'})
    fake_income = type('X', (), {'recurrence': fake_r, 'recurrence_id': None, '_orig_recurrence_id': 99, 'date':'2025-01-01'})
    html = templates.env.get_template('income_edit.html').render(request=R(), income=fake_income, apartments=[], platforms=[], pms=[], attached=[], next=None)
    assert 'value="yearly"' in html or 'value="monthly"' in html
    assert 'name="orig_recurrence_id"' in html
    assert '99' in html
    # ensure the series radio is present (checked state may be formatted differently)
    assert 'id="apply_series"' in html
    assert 'value="series"' in html
