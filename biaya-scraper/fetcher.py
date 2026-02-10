from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Dict

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from logger import info, warn

@dataclass
class FetchResult:
    ok: bool
    final_url: str
    status: int
    content_type: str
    content: bytes
    mode: str
    elapsed_ms: int

class RequestsFetcher:
    def __init__(self, timeout_s: int = 25, headers: Optional[Dict[str, str]] = None):
        self.timeout_s = timeout_s
        self.sess = requests.Session()
        self.headers = headers or {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
            )
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch(self, url: str) -> FetchResult:
        t0 = time.time()
        r = self.sess.get(url, timeout=self.timeout_s, headers=self.headers, allow_redirects=True)
        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        fr = FetchResult(
            ok=bool(r.ok),
            final_url=str(r.url),
            status=int(r.status_code),
            content_type=ct,
            content=r.content or b"",
            mode="requests",
            elapsed_ms=int((time.time() - t0) * 1000),
        )
        info(f"fetch | mode=requests status={fr.status} ct={fr.content_type or '-'} ms={fr.elapsed_ms} url={url}")
        return fr

class PlaywrightFetcher:
    def __init__(self, timeout_ms: int = 25000, headless: bool = True):
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._pw = None
        self._browser = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def fetch_html(self, url: str, wait_after_ms: int = 1500) -> FetchResult:
        t0 = time.time()
        try:
            context = await self._browser.new_context(ignore_https_errors=True)
            page = await context.new_page()
            await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            # Banyak situs kampus render tabel/menunya via JS (wpDataTables/DataTables).
            # Coba tunggu network idle sebentar (jika tidak tercapai, lanjut saja).
            try:
                await page.wait_for_load_state("networkidle", timeout=min(8000, self.timeout_ms))
            except Exception:
                pass
            # Auto-scroll ringan untuk memicu lazy-load.
            try:
                await page.evaluate("""() => { window.scrollTo(0, document.body.scrollHeight); }""")
                await page.wait_for_timeout(250)
                await page.evaluate("""() => { window.scrollTo(0, 0); }""")
            except Exception:
                pass
            if wait_after_ms and wait_after_ms > 0:
                await page.wait_for_timeout(wait_after_ms)
            html = await page.content()
            final_url = page.url
            await context.close()
            fr = FetchResult(
                ok=True,
                final_url=final_url,
                status=200,
                content_type="text/html",
                content=html.encode("utf-8"),
                mode="playwright",
                elapsed_ms=int((time.time() - t0) * 1000),
            )
            info(f"fetch | mode=playwright status=200 ms={fr.elapsed_ms} url={url}")
            return fr
        except PWTimeout:
            warn(f"fetch | mode=playwright TIMEOUT url={url}")
            return FetchResult(False, url, 0, "", b"", "playwright_timeout", int((time.time() - t0) * 1000))
        except Exception as e:
            warn(f"fetch | mode=playwright ERROR={type(e).__name__} url={url}")
            return FetchResult(False, url, 0, "", b"", f"playwright_err:{type(e).__name__}", int((time.time() - t0) * 1000))
