from __future__ import annotations

import time
import random
import os
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from config import JALUR_WORD_RE
from logger import info, warn

# Anti-bot strategies per domain
DOMAIN_STRATEGY = {
    'pmb.unpad.ac.id': {'delay': 3.0, 'timeout_ms': 35000},
    'admission.itb.ac.id': {'delay': 2.0, 'timeout_ms': 35000, 'extra_wait_after_ms': 3000},
    'undip.ac.id': {'delay': 1.5, 'timeout_ms': 30000},
}

# Rotated user agents untuk bypass detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

@dataclass
class FetchResult:
    ok: bool
    final_url: str
    status: int
    content_type: str
    content: bytes
    mode: str
    elapsed_ms: int
    meta: Optional[Dict[str, Any]] = None

class RequestsFetcher:
    def __init__(self, timeout_s: int = 25, headers: Optional[Dict[str, str]] = None):
        self.timeout_s = timeout_s
        self.sess = requests.Session()
        
        # Default anti-bot headers
        self.headers = headers or {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        }

    def _get_domain_strategy(self, url: str) -> Dict:
        """Get domain-specific strategy (timeout, delays, etc)"""
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '')
        return DOMAIN_STRATEGY.get(domain, {})

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=15))
    def fetch(self, url: str) -> FetchResult:
        # Apply domain-specific delay
        strategy = self._get_domain_strategy(url)
        delay = strategy.get('delay', 0.5)
        
        # Add random jitter (±20%) untuk lebih natural
        actual_delay = delay * random.uniform(0.8, 1.2)
        time.sleep(actual_delay)
        
        # Rotate user agent setiap request
        headers = self.headers.copy()
        headers['User-Agent'] = random.choice(USER_AGENTS)
        
        t0 = time.time()
        try:
            r = self.sess.get(url, timeout=self.timeout_s, headers=headers, allow_redirects=True)
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
        except requests.exceptions.Timeout:
            warn(f"fetch | mode=requests TIMEOUT url={url}")
            raise
        except requests.exceptions.ConnectionError as e:
            warn(f"fetch | mode=requests CONNECTION_ERROR url={url} err={type(e).__name__}")
            raise

class PlaywrightFetcher:
    def __init__(self, timeout_ms: int = 25000, headless: bool = True, save_dir: str | None = None, dump_network: bool = False):
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._pw = None
        self._browser = None
        self.save_dir = save_dir
        self.dump_network = dump_network

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            # Anti-detection: disable automation features
            args=['--disable-blink-features=AutomationControlled']
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    def _get_domain_strategy(self, url: str) -> Dict:
        """Get domain-specific strategy"""
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '')
        return DOMAIN_STRATEGY.get(domain, {})

    async def fetch_html(self, url: str, wait_after_ms: int = 1500) -> FetchResult:
        t0 = time.time()
        
        # Get domain strategy
        strategy = self._get_domain_strategy(url)
        timeout_ms = strategy.get('timeout_ms', self.timeout_ms)
        extra_wait = strategy.get('extra_wait_after_ms', 0)
        
        try:
            context = await self._browser.new_context(
                ignore_https_errors=True,
                # Simulate real browser
                user_agent=random.choice(USER_AGENTS),
                viewport={'width': 1920, 'height': 1080},
                locale='id-ID',
                timezone_id='Asia/Jakarta',
            )
            
            page = await context.new_page()
            network_requests = []
            if self.dump_network:
                # collect request URLs
                page.on("request", lambda req: network_requests.append(req.url))

            # Collect in-page anchor links (fully resolved)
            page_links = []
            try:
                page_links = await page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a=>a.href)")
            except Exception:
                page_links = []
            
            # Set default timeout untuk page
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
            
            # Navigate dengan waitForNavigation
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            
            info(f"fetch | mode=playwright goto success url={url}")
            
            # Wait untuk networkidle
            try:
                await page.wait_for_load_state("networkidle", timeout=min(8000, timeout_ms))
                info(f"fetch | mode=playwright networkidle reached url={url}")
            except Exception as e:
                info(f"fetch | mode=playwright networkidle timeout (ok) url={url}")
                pass
            
            # Auto-scroll untuk trigger lazy-load dan JS rendering
            try:
                # Scroll multiple times untuk ensure lazy content loaded
                for _ in range(3):
                    await page.evaluate("""() => { 
                        window.scrollTo(0, document.body.scrollHeight); 
                    }""")
                    await page.wait_for_timeout(200)
                
                await page.evaluate("""() => { 
                    window.scrollTo(0, 0); 
                }""")
                info(f"fetch | mode=playwright scrolling done url={url}")
            except Exception:
                pass
            
            # Extra wait untuk ITB dan sites dengan complex JS rendering
            total_wait = max(wait_after_ms, extra_wait)
            if total_wait > 0:
                info(f"fetch | mode=playwright extra wait {total_wait}ms url={url}")
                await page.wait_for_timeout(total_wait)

            # Interact with obvious admission-related elements to surface hidden SPA links or trigger XHRs
            clicked_links: list[str] = []
            try:
                # use the same regex from config to identify buttons/anchors worth clicking
                # this avoids a manual keyword list here
                js_re = JALUR_WORD_RE.pattern
                script = f"""
                () => {{
                    const re = new RegExp({js_re!r}, 'i');
                    const urls = [];
                    document.querySelectorAll('a').forEach(a=>{{
                        const txt = a.innerText || '';
                        if (re.test(txt)) {{
                            urls.push(a.href);
                            try {{ a.click(); }} catch(e){{}}
                        }}
                    }});
                    document.querySelectorAll('button').forEach(b=>{{
                        const txt = b.innerText || '';
                        if (re.test(txt)) {{
                            try {{ b.click(); }} catch(e){{}}
                        }}
                    }});
                    return urls;
                }}
                """
                candidate_urls = await page.evaluate(script)
                # dedupe and keep only fully qualified links
                clicked_links = list({u for u in candidate_urls if u})
                if clicked_links:
                    # give network time to fire after clicks
                    await page.wait_for_timeout(1500)
                    # refresh page_links because clicking may have modified DOM
                    try:
                        page_links = await page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a=>a.href)")
                    except Exception:
                        pass
            except Exception:
                pass

            # Get content
            html = await page.content()
            final_url = page.url
            # Optionally save debug artifacts
            if self.save_dir:
                try:
                    os.makedirs(self.save_dir, exist_ok=True)
                    parsed = urlparse(final_url)
                    host = parsed.netloc.replace(':', '_')
                    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
                    fname = f"debug_render_{host}_{ts}.html"
                    with open(os.path.join(self.save_dir, fname), 'w', encoding='utf-8') as f:
                        f.write(html)
                    if self.dump_network and network_requests:
                        nfile = f"debug_network_{host}_{ts}.txt"
                        with open(os.path.join(self.save_dir, nfile), 'w', encoding='utf-8') as f:
                            for r in network_requests:
                                f.write(r + '\n')
                except Exception:
                    pass

            await context.close()
            
            fr = FetchResult(
                ok=True,
                final_url=final_url,
                status=200,
                content_type="text/html",
                content=html.encode("utf-8"),
                mode="playwright",
                elapsed_ms=int((time.time() - t0) * 1000),
                meta={
                    "network_requests": network_requests,
                    "page_links": page_links,
                    "clicked_links": clicked_links,
                },
            )
            info(f"fetch | mode=playwright status=200 ms={fr.elapsed_ms} url={url}")
            return fr
            
        except PWTimeout:
            warn(f"fetch | mode=playwright TIMEOUT url={url}")
            return FetchResult(False, url, 0, "", b"", "playwright_timeout", int((time.time() - t0) * 1000))
        except Exception as e:
            warn(f"fetch | mode=playwright ERROR={type(e).__name__} msg={str(e)[:100]} url={url}")
            return FetchResult(False, url, 0, "", b"", f"playwright_err:{type(e).__name__}", int((time.time() - t0) * 1000))
