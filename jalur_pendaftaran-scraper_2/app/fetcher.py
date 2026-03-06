import re
import requests
import tldextract

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


class PageFetcher:
    def __init__(self, timeout: int, user_agent: str, use_playwright: bool = True):
        self.timeout = timeout
        self.user_agent = user_agent
        self.use_playwright = use_playwright and (sync_playwright is not None)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    def get_registrable_domain(self, url: str) -> str:
        ext = tldextract.extract(url)
        if not ext.domain:
            return ""
        return ".".join([p for p in [ext.domain, ext.suffix] if p])

    def fetch(self, url: str) -> dict:
        """
        Return:
        {
          ok: bool,
          final_url: str,
          status: int,
          content_type: str,
          text: str,           # untuk HTML/text
          content_bytes: bytes # untuk PDF/binary
        }
        """
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            ct = (r.headers.get("content-type") or "").lower()

            # PDF/binary
            if "application/pdf" in ct or url.lower().endswith(".pdf"):
                return {
                    "ok": True,
                    "final_url": r.url,
                    "status": r.status_code,
                    "content_type": ct,
                    "text": "",
                    "content_bytes": r.content,
                }

            # HTML/text
            text = r.text if ("text" in ct or "html" in ct or ct == "") else ""
            return {
                "ok": True,
                "final_url": r.url,
                "status": r.status_code,
                "content_type": ct,
                "text": text,
                "content_bytes": b"",
            }
        except Exception:
            if self.use_playwright:
                return self.fetch_playwright(url)
            return {"ok": False, "final_url": url, "status": 0, "content_type": "", "text": "", "content_bytes": b""}

    def fetch_playwright(self, url: str) -> dict:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)
                page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                html = page.content()
                final_url = page.url
                browser.close()
            return {"ok": True, "final_url": final_url, "status": 200, "content_type": "text/html", "text": html, "content_bytes": b""}
        except Exception:
            return {"ok": False, "final_url": url, "status": 0, "content_type": "", "text": "", "content_bytes": b""}

    @staticmethod
    def html_to_text(html: str) -> str:
        html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        html = re.sub(r"<[^>]+>", " ", html)
        html = re.sub(r"\s+", " ", html).strip()
        return html