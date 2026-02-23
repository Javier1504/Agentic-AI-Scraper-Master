from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode

# Common non-navigation / non-http schemes we don't want to crawl
_BAD_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "blob:")

# WordPress/shortcode-ish junk that sometimes leaks into href/src attributes
_SHORTCODE_RE = re.compile(r"\[(?:wpdatatable|wp\s*datatable|tablepress|contact-form-7|vc_[^\]]+)\b", re.I)

def normalize_url(url: str) -> str:
    """Normalize a URL safely (never raise). Removes fragments and tracking params."""
    url = (url or "").strip()
    if not url:
        return ""

    try:
        p = urlparse(url)
    except ValueError:
        # e.g. invalid bracketed host: http://[wpdatatable%20id=21]
        return ""

    # Drop fragments
    p = p._replace(fragment="")

    # Strip common tracking query params, drop empty params,
    # and canonicalize param ordering to improve dedup.
    try:
        drop_keys = {
            # tracking
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid",
            # UI-only noise seen on many admission sites
            "menu", "label",
        }

        q = []
        for (k, v) in parse_qsl(p.query, keep_blank_values=True):
            kl = (k or "").lower()
            if not k:
                continue
            if kl in drop_keys:
                continue
            # drop empty values ("menu=&label=" etc.) to avoid URL explosion
            if v is None or str(v).strip() == "":
                continue
            q.append((k, v))

        # stable ordering for dedup (important when sites reorder params)
        q.sort(key=lambda kv: (kv[0].lower(), kv[1]))

        p = p._replace(query=urlencode(q, doseq=True))
    except Exception:
        # If query parsing fails for any reason, keep the original query
        pass

    try:
        return urlunparse(p)
    except Exception:
        return ""

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

def safe_join(base: str, href: str) -> str:
    """Safely join a possibly-broken href/src to a base URL.
    Returns empty string if href is not a valid crawl target.
    """
    href = (href or "").strip()
    if not href:
        return ""

    low = href.lower()

    # Skip anchors and obviously non-navigational links
    if low.startswith(_BAD_SCHEMES) or low.startswith("#"):
        return ""

    # Skip known shortcode garbage (can crash urlparse/urljoin due to bracketed host rules)
    if _SHORTCODE_RE.search(href):
        return ""

    # Some sites leak bracketed netlocs like http(s)://[wpdatatable id=21]
    if "//[" in href or low.startswith("http://[") or low.startswith("https://["):
        return ""

    try:
        joined = urljoin(base, href)
    except ValueError:
        return ""

    joined = normalize_url(joined)
    if not joined:
        return ""

    # Only allow http(s) absolute URLs (after joining)
    try:
        p = urlparse(joined)
    except ValueError:
        return ""

    if p.scheme not in ("http", "https") or not p.netloc:
        return ""

    return joined


def canonical_for_visit(url: str) -> str:
    """Canonical form used for the visited-set.

    Goal: prevent revisiting the *same* page through cosmetic URL variants:
    - query param order
    - empty UI params (menu/label)
    - redirects (caller should also canonicalize final_url)
    """
    u = normalize_url(url)
    if not u:
        return ""
    try:
        p = urlparse(u)
    except Exception:
        return ""

    # Normalize trailing slash: keep '/' for root, drop for non-root
    path = p.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    try:
        return urlunparse(p._replace(path=path))
    except Exception:
        return u

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
