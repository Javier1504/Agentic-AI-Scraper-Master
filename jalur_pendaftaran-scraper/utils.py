from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode

def normalize_url(url: str, keep_fragment: bool = False) -> str:
    url = (url or "").strip()
    if not url:
        return url
    p = urlparse(url)
    if not keep_fragment:
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
    
def is_related_domain(url: str, base: str) -> bool:
    u = urlparse(url).netloc.lower()
    b = urlparse(base).netloc.lower()
    return (
        u == b or
        u.endswith("." + b) or
        b.endswith("." + u)
    )

def safe_join(base: str, href: str) -> str:
    return normalize_url(urljoin(base, href))

def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "item"

def dedupe_candidates(candidates: list[CandidateLink]) -> list[CandidateLink]:
    """
    Hapus candidate duplikat berdasarkan (url, kind).
    Ambil yang score-nya paling tinggi.
    """
    seen: dict[tuple[str, str], CandidateLink] = {}

    for c in candidates:
        key = (c.url, c.kind)
        if key not in seen:
            seen[key] = c
        else:
            if c.score > seen[key].score:
                seen[key] = c

    return list(seen.values())


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
