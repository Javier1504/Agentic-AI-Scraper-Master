from __future__ import annotations
import re
import time
from dataclasses import dataclass
from typing import Dict, List
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from .config import HEADLESS, NAV_TIMEOUT_MS, WAIT_AFTER_LOAD_MS, MAX_TEXT_PER_PAGE

@dataclass
class FetchResult:
    ok: bool
    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    html: str
    links: List[Dict[str, str]]
    error: str

def _norm_space(s: str) -> str:
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _extract_links(base_url: str, soup: BeautifulSoup) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        absu = urljoin(base_url, href).split("#")[0]
        if absu in seen:
            continue
        seen.add(absu)
        txt = _norm_space(a.get_text(" ", strip=True))[:200]
        out.append({"href": absu, "text": txt})

    for a in soup.find_all("area", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        absu = urljoin(base_url, href).split("#")[0]
        if absu in seen:
            continue
        seen.add(absu)
        out.append({"href": absu, "text": ""})

    for tag, attr in [("object", "data"), ("embed", "src"), ("iframe", "src")]:
        for el in soup.find_all(tag):
            v = (el.get(attr) or "").strip()
            if not v:
                continue
            absu = urljoin(base_url, v).split("#")[0]
            if absu in seen:
                continue
            seen.add(absu)
            out.append({"href": absu, "text": f"[{tag}:{attr}]"})

    return out

def _clean_html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for sel in ["script", "style", "noscript", "svg", "canvas"]:
        for el in soup.select(sel):
            el.decompose()

    text = soup.get_text("\n", strip=True)
    text = _norm_space(text)
    if MAX_TEXT_PER_PAGE and len(text) > MAX_TEXT_PER_PAGE:
        text = text[:MAX_TEXT_PER_PAGE]
    return text

def _looks_cloudflare(html: str) -> bool:
    low = (html or "").lower()
    if "cloudflare" in low and ("just a moment" in low or "attention required" in low):
        return True
    if "cf-chl" in low or "challenge-platform" in low:
        return True
    return False

class PlaywrightFetcher:
    """
    Playwright fetcher yang lebih stabil:
    - pakai 1 browser, tapi context/page bisa di-reset kalau kena challenge (cloudflare)
    - mencegah challenge “nempel” dan mengganggu kampus berikutnya
    """

    def __init__(
        self,
        headless: bool = HEADLESS,
        nav_timeout_ms: int = NAV_TIMEOUT_MS,
        wait_after_load_ms: int = WAIT_AFTER_LOAD_MS,
    ):
        self.headless = headless
        self.nav_timeout_ms = nav_timeout_ms
        self.wait_after_load_ms = wait_after_load_ms

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> "PlaywrightFetcher":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._new_context()
        return self

    def _new_context(self) -> None:
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass

        assert self._browser is not None
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="id-ID",
            java_script_enabled=True,
            viewport={"width": 1280, "height": 720},
        )
        self._page = self._context.new_page()
        self._page.set_default_navigation_timeout(self.nav_timeout_ms)
        self._page.set_default_timeout(self.nav_timeout_ms)

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def fetch(self, url: str) -> FetchResult:
        url = (url or "").strip()
        if not url:
            return FetchResult(False, url, url, 0, "", "", "", [], "empty_url")

        assert self._page is not None

        def _do_fetch(wait_until: str) -> FetchResult:
            try:
                resp = self._page.goto(url, wait_until=wait_until)
                if self.wait_after_load_ms:
                    self._page.wait_for_timeout(self.wait_after_load_ms)

                final_url = self._page.url or url
                status = int(resp.status) if resp else 0
                headers = resp.headers if resp else {}
                content_type = (headers.get("content-type") or "").lower()

                html = self._page.content() or ""
                text = _clean_html_to_text(html)
                soup = BeautifulSoup(html, "html.parser")
                links = _extract_links(final_url, soup)

                ok = (status >= 200 and status < 400) and bool(text.strip())
                if _looks_cloudflare(html):
                    ok = False

                return FetchResult(ok, url, final_url, status, content_type, text, html, links, "")
            except PWTimeout as e:
                return FetchResult(False, url, url, 0, "", "", "", [], f"timeout:{e}")
            except Exception as e:
                return FetchResult(False, url, url, 0, "", "", "", [], f"playwright_err:{type(e).__name__}:{e}")
        # coba domcontentloaded
        r1 = _do_fetch("domcontentloaded")
        if r1.ok:
            return r1
        # kalau terlihat challenge, reset context dan coba networkidle (alternatif kalo ke blok)
        if _looks_cloudflare(r1.html) or ("cloudflare" in (r1.error or "").lower()):
            self._new_context()
            time.sleep(0.8)
            r2 = _do_fetch("networkidle")
            return r2

        return r1
