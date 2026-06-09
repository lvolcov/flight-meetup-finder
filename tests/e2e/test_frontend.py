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
