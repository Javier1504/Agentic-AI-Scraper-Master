# fetcher.py
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

import requests
from heuristic_expand import heuristic_expand_dom
from tenacity import retry, stop_after_attempt, wait_exponential
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from logger import info, warn
from menu_crawler import extract_menu_links


@dataclass
class FetchResult:
    ok: bool
    final_url: str
    status: int
    content_type: str
    content: bytes
    mode: str
    elapsed_ms: int


# ======================
# Requests Fetcher
# ======================
class RequestsFetcher:
    def __init__(self, timeout_s: int = 25, headers: Optional[Dict[str, str]] = None):
        self.timeout_s = timeout_s
        self.sess = requests.Session()
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 Chrome/121 Safari/537.36"
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(1, 2, 10))
    def fetch(self, url: str) -> FetchResult:
        t0 = time.time()
        r = self.sess.get(url, timeout=self.timeout_s, headers=self.headers)
        ct = (r.headers.get("content-type") or "").split(";")[0]
        return FetchResult(
            ok=r.ok,
            final_url=str(r.url),
            status=r.status_code,
            content_type=ct,
            content=r.content or b"",
            mode="requests",
            elapsed_ms=int((time.time() - t0) * 1000),
        )


# ======================
# Playwright Fetcher
# ======================
class PlaywrightFetcher:
    def __init__(self, timeout_ms: int = 25000, headless: bool = True):
        self.timeout_ms = timeout_ms
        self.headless = headless

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._browser.close()
        await self._pw.stop()

    async def fetch_html(self, url: str, wait_after_ms: int = 500) -> FetchResult:
        t0 = time.time()
        try:
            ctx = await self._browser.new_context(ignore_https_errors=True)
            page = await ctx.new_page()
            await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            if wait_after_ms:
                await page.wait_for_timeout(wait_after_ms)
            html = await page.content()
            final_url = page.url
            await ctx.close()

            return FetchResult(
                ok=True,
                final_url=final_url,
                status=200,
                content_type="text/html",
                content=html.encode("utf-8"),
                mode="playwright",
                elapsed_ms=int((time.time() - t0) * 1000),
            )

        except PWTimeout:
            warn(f"playwright timeout {url}")
            return FetchResult(False, url, 0, "", b"", "timeout", 0)

    async def fetch_with_menu(self, url: str) -> Tuple[FetchResult, List[str]]:
        t0 = time.time()
        ctx = await self._browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()

        try:
            await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            await page.wait_for_timeout(800)

            menu_links = await extract_menu_links(page)

            # 🔥 HYBRID SCRAPING STEP
            clicked = await heuristic_expand_dom(page)

            html = await page.content()
            final_url = page.url

            fr = FetchResult(
                ok=True,
                final_url=final_url,
                status=200,
                content_type="text/html",
                content=html.encode("utf-8"),
                mode="playwright_menu",
                elapsed_ms=int((time.time() - t0) * 1000),
            )
            return fr, menu_links

        except Exception as e:
            warn(f"menu_fetch_failed | url={url} err={type(e).__name__}")
            return FetchResult(False, url, 0, "", b"", "menu_failed", 0), []

        finally:
            await ctx.close()
