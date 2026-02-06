from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    p = urlparse(url)
    p = p._replace(fragment="")
    q = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True)
         if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid"}]
    p = p._replace(query=urlencode(q))
    return urlunparse(p)

def same_site(url: str, base: str) -> bool:
    try:
        u = urlparse(url)
        b = urlparse(base)
        uh = (u.netloc or "").lower()
        bh = (b.netloc or "").lower()
        if not uh or not bh:
            return False
        return uh == bh or uh.endswith("." + bh)
    except Exception:
        return False


def is_allowed_asset_url(url: str, official_base: str) -> bool:
    """Allow assets (pdf/image) outside base domain ONLY if host matches allowlist.
    This keeps 'no guessing': URL must still be discovered from official pages, but can live on CDN/S3/Drive."""
    try:
        from config import ALLOWED_ASSET_HOSTS
        host = (urlparse(url).netloc or "").lower()
        if not host:
            return False
        base_host = (urlparse(official_base).netloc or "").lower()
        if host == base_host or host.endswith("." + base_host):
            return True
        return any(host == h or host.endswith("." + h) or h in host for h in ALLOWED_ASSET_HOSTS)
    except Exception:
        return False

def safe_join(base: str, href: str) -> str:
    return normalize_url(urljoin(base, href))

def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "item"

@dataclass
class CandidateLink:
    campus_name: str
    official_website: str
    url: str
    kind: str        # html | pdf | image
    source_page: str
    context_hint: str = ""
    score: float = 0.0

@dataclass
class ValidatedLink:
    campus_name: str
    official_website: str
    url: str
    kind: str
    source_page: str
    verdict: str     # valid | invalid | uncertain
    reason: str = ""
    extracted_hint: str = ""
