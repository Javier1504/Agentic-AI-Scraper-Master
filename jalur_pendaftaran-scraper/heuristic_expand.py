# heuristic_expand.py
from __future__ import annotations
from typing import List

EXPAND_KEYWORDS = [
    "jadwal",
    "jadwal seleksi",
    "jadwal pendaftaran",
    "snbp",
    "snbt",
    "mandiri",
    "pendaftaran",
    "seleksi",
]

EXPAND_SELECTORS = [
    "[aria-expanded='false']",
    "[data-bs-toggle='collapse']",
    ".accordion-button",
    ".accordion-title",
    ".elementor-accordion-title",
    ".elementor-tab-title",
    "button",
]

async def heuristic_expand_dom(page, max_clicks: int = 6) -> int:
    clicked = 0

    for selector in EXPAND_SELECTORS:
        if clicked >= max_clicks:
            break

        elements = await page.query_selector_all(selector)
        for el in elements:
            if clicked >= max_clicks:
                break

            try:
                text = (await el.inner_text() or "").lower()
            except Exception:
                continue

            if not any(k in text for k in EXPAND_KEYWORDS):
                continue

            try:
                await el.scroll_into_view_if_needed()
                await el.click()
                await page.wait_for_timeout(350)
                clicked += 1
            except Exception:
                continue

    return clicked
