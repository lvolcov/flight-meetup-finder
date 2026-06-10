"""Playwright e2e tests for the front-end (F-21..F-26).

Covers: the form renders, dark-mode persists across reloads, the mobile
layout collapses filters at 380px, a mocked search streams results to
completion, and client-side re-sort reorders the DOM. The scraper is the
offline deterministic source — no Google Flights calls. Created 2026-06-09.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


def _start_narrow_search(page: Page, base_url: str) -> None:
    """Open the form, restrict to two destinations and launch the search."""
    page.goto(base_url + "/")
    # Wait for the async destination checklist to populate.
    page.wait_for_selector("#dest-list input[type=checkbox]")
    # Keep only the first two candidate destinations to keep the run quick.
    page.evaluate(
        """() => {
            const boxes = Array.from(
                document.querySelectorAll('#dest-list input[type=checkbox]'));
            boxes.forEach((b, i) => { b.checked = i < 2; });
        }"""
    )
    page.click("#launch")
    page.wait_for_url("**/search/**")


def test_form_renders(page: Page, base_url: str) -> None:
    page.goto(base_url + "/")
    expect(page.locator("#search-form")).to_be_visible()
    expect(page.locator(".tab", has_text="Meetup")).to_have_class(
        "tab is-active"
    )
    page.wait_for_selector("#dest-list input[type=checkbox]")
    # Plain-language explanations are present for the date options...
    expect(page.locator("#search-form .hint").first).to_contain_text(
        "Outbound"
    )
    # ...and the fields carry tooltips.
    assert "Earliest day" in (
        page.locator('label:has(input[name="outbound_start"])').get_attribute(
            "title"
        )
        or ""
    )
    # The browser tab has an icon.
    assert page.locator('link[rel="icon"]').count() == 1


def test_dark_mode_persists(page: Page, base_url: str) -> None:
    page.goto(base_url + "/")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    page.click("#theme-toggle")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.reload()
    # The pre-paint script must restore the persisted choice.
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"


def test_mobile_collapses_filters(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 380, "height": 740})
    page.goto(base_url + "/")
    # The traveller-A accordion is closed by default: its controls are hidden.
    traveller = page.locator('[data-traveller="a"] select[data-leg="outbound"]')
    expect(traveller).to_be_hidden()
    page.locator(".accordion", has_text="time, duration, stops").first.locator(
        "summary"
    ).click()
    expect(traveller).to_be_visible()


def test_search_streams_results(page: Page, base_url: str) -> None:
    _start_narrow_search(page, base_url)
    # Results appear as they are matched...
    page.wait_for_selector(".result")
    # ...and the job reaches completion.
    expect(page.locator("#status-text")).to_contain_text("done", timeout=15000)
    assert page.locator(".result").count() >= 1
    # Every leg shows its own price in pounds (EUR legs also show the
    # original euro amount).
    first = page.locator(".result").first
    assert first.locator(".leg-price").count() == 4  # meetup = 4 legs
    expect(first.locator(".leg-price").first).to_contain_text("£")
    expect(first.locator(".leg-orig").first).to_contain_text("€")


def test_dates_use_british_format(page: Page, base_url: str) -> None:
    page.goto(base_url + "/")
    field = page.locator('input[name="outbound_start"]')
    assert field.get_attribute("placeholder") == "dd/mm/yyyy"
    # The JS defaults are written in dd/mm/yyyy.
    assert field.input_value().count("/") == 2
    day = field.input_value().split("/")[0]
    assert 1 <= int(day) <= 31

    # Entering explicit dd/mm/yyyy dates must survive the round trip to the
    # API (which speaks ISO) and come back on the result card.
    page.wait_for_selector("#dest-list input[type=checkbox]")
    page.fill('input[name="outbound_start"]', "15/07/2026")
    page.fill('input[name="outbound_end"]', "15/07/2026")
    page.fill('input[name="return_start"]', "18/07/2026")
    page.fill('input[name="return_end"]', "18/07/2026")
    page.fill('input[name="min_nights"]', "3")
    page.fill('input[name="max_nights"]', "3")
    page.evaluate(
        """() => {
            const boxes = Array.from(
                document.querySelectorAll('#dest-list input[type=checkbox]'));
            boxes.forEach((b, i) => { b.checked = i < 1; });
        }"""
    )
    # 15/07 must be parsed as 15 July, not the 7th of month 15.
    expect(page.locator("#estimate")).to_contain_text("scrape", timeout=5000)
    page.click("#launch")
    page.wait_for_url("**/search/**")
    expect(page.locator("#status-text")).to_contain_text("done", timeout=15000)
    expect(page.locator(".result .badge").first).to_contain_text("15 Jul → 18 Jul")


def test_schengen_toggle_deselects_passport_destinations(
    page: Page, base_url: str
) -> None:
    page.goto(base_url + "/")
    page.wait_for_selector("#dest-list input[type=checkbox]")
    edi = page.locator('#dest-list input[value="EDI"]')
    bcn = page.locator('#dest-list input[value="BCN"]')
    assert edi.is_checked() and bcn.is_checked()

    # One tap deselects everything with passport control from Lisbon...
    page.check("#schengen-only")
    assert not edi.is_checked()
    assert bcn.is_checked()
    # Dublin is EU but not Schengen — must be deselected too.
    assert not page.locator('#dest-list input[value="DUB"]').is_checked()

    # ...and unticking brings them back.
    page.uncheck("#schengen-only")
    assert edi.is_checked()

    # The choice persists across reloads (localStorage).
    page.check("#schengen-only")
    page.reload()
    page.wait_for_selector("#dest-list input[type=checkbox]")
    assert page.locator("#schengen-only").is_checked()
    assert not page.locator('#dest-list input[value="EDI"]').is_checked()


def test_calendar_button_fills_british_date(page: Page, base_url: str) -> None:
    page.goto(base_url + "/")
    wrap = page.locator(".date-field").first
    expect(wrap.locator(".date-btn")).to_be_visible()
    # Playwright cannot drive the OS calendar popup, so simulate the pick on
    # the hidden native input and assert it lands as dd/mm/yyyy in the box.
    page.evaluate(
        """() => {
            const wrap = document.querySelector('.date-field');
            const pick = wrap.querySelector('.date-pick');
            pick.value = '2026-07-15';
            pick.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    assert (
        page.locator('input[name="outbound_start"]').input_value()
        == "15/07/2026"
    )
    # The sync must also refresh the live query estimate.
    expect(page.locator("#estimate")).to_contain_text("scrape", timeout=5000)


def test_running_search_visible_after_navigating_away(
    page: Page, base_url: str, browser: object
) -> None:
    _start_narrow_search(page, base_url)
    job_url = page.url
    expect(page.locator("#status-text")).to_contain_text("done", timeout=15000)

    # Leaving the results page must not lose the search: the home page lists
    # it with a link back to the live view.
    page.goto(base_url + "/")
    page.wait_for_selector("#recent-jobs .job-row")
    row = page.locator("#recent-jobs .job-row").first
    expect(row).to_contain_text("meetup")
    href = row.locator(".job-link").get_attribute("href")
    assert href and job_url.endswith(href)

    # A completely separate browser context (e.g. the phone) sees it too —
    # the list comes from the server, not this browser's storage.
    other = browser.new_context()  # type: ignore[attr-defined]
    try:
        phone = other.new_page()
        phone.goto(base_url + "/")
        phone.wait_for_selector("#recent-jobs .job-row")
        assert phone.locator("#recent-jobs .job-row").count() >= 1
        phone.locator("#recent-jobs .job-link").first.click()
        phone.wait_for_url("**/search/**")
        expect(phone.locator("#status-text")).to_contain_text(
            "done", timeout=15000
        )
    finally:
        other.close()


def test_rerun_and_delete_from_searches_list(
    page: Page, base_url: str
) -> None:
    _start_narrow_search(page, base_url)
    expect(page.locator("#status-text")).to_contain_text("done", timeout=15000)

    page.goto(base_url + "/")
    page.wait_for_selector("#recent-jobs .job-row")
    initial = page.locator("#recent-jobs .job-row").count()

    # Rerun launches a fresh job with the same filters and opens it.
    original_url = page.url
    page.locator("#recent-jobs [data-rerun]").first.click()
    page.wait_for_url("**/search/**")
    assert page.url != original_url
    expect(page.locator("#status-text")).to_contain_text("done", timeout=15000)

    page.goto(base_url + "/")
    page.wait_for_selector("#recent-jobs .job-row")
    expect(page.locator("#recent-jobs .job-row")).to_have_count(initial + 1)

    # Delete (accepting the confirmation) removes the search from the list.
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#recent-jobs [data-delete]").first.click()
    expect(page.locator("#recent-jobs .job-row")).to_have_count(
        initial, timeout=5000
    )


def test_client_side_resort(page: Page, base_url: str) -> None:
    _start_narrow_search(page, base_url)
    expect(page.locator("#status-text")).to_contain_text("done", timeout=15000)
    assert page.locator(".result").count() >= 2

    def prices() -> list[float]:
        return [
            float(v)
            for v in page.locator(".result").evaluate_all(
                "els => els.map(e => parseFloat(e.dataset.price))"
            )
        ]

    def durations() -> list[float]:
        return [
            float(v)
            for v in page.locator(".result").evaluate_all(
                "els => els.map(e => parseFloat(e.dataset.duration))"
            )
        ]

    page.select_option("#sort-by", "combined_gbp")
    by_price = prices()
    assert by_price == sorted(by_price)

    page.select_option("#sort-by", "total_duration")
    by_duration = durations()
    assert by_duration == sorted(by_duration)
