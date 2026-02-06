from __future__ import annotations

import re
from typing import List, Tuple, Iterable
from bs4 import BeautifulSoup

from config import FEE_WORD_RE, FEE_KEYWORDS, NOISE_KEYWORDS, PDF_EXT, IMG_EXT, MONEY_HINT_RE
from utils import safe_join, normalize_url

"""Extract links & embedded assets from HTML pages.

Tujuan: mendeteksi link biaya/UKT di website kampus yang sering berupa:
- halaman HTML (tabel)
- PDF ter-embed (<embed>/<object>/<iframe>)
- gambar hasil scan (lazyload img / background-image)

Return: (url, kind, hint, score)
"""

# match URL that contains an asset extension anywhere (handles querystring: file.pdf?download=1)
ASSET_EXT_RE = re.compile(r"(?i)\.(pdf|png|jpe?g|webp)(?:$|[?#])")

def _pick_from_srcset(srcset: str, cap_w: int = 2200) -> List[str]:
    """Pick a srcset URL that is large enough but not too huge.

    If width descriptors (e.g. '1200w') exist, choose the largest <= cap_w.
    Otherwise fallback to the first URL.
    Returns a list (0 or 1 url) to keep asset harvesting tight.
    """
    if not srcset:
        return []
    best_under = ("", -1)
    best_any = ("", -1)

    for part in srcset.split(","):
        p = part.strip()
        if not p:
            continue
        toks = p.split()
        url = toks[0].strip()
        w = -1
        if len(toks) >= 2 and toks[1].lower().endswith("w"):
            try:
                w = int(re.sub(r"[^0-9]", "", toks[1]))
            except Exception:
                w = -1

        if w > best_any[1]:
            best_any = (url, w)
        if 0 < w <= cap_w and w > best_under[1]:
            best_under = (url, w)

    chosen = best_under[0] or best_any[0]
    return [chosen] if chosen else []


def _urls_from_style(style: str) -> List[str]:
    """Extract url(...) from inline style="..."""
    if not style:
        return []
    return [m.group(1).strip("'\"") for m in re.finditer(r"url\(([^)]+)\)", style, flags=re.I)]

def _is_noise(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in NOISE_KEYWORDS)

def score_hint(text: str) -> float:
    t = (text or "").lower()
    # normalisasi separator agar 'biaya-kuliah' ~ 'biaya kuliah'
    t = re.sub(r"[^a-z0-9]+", " ", t)
    score = 0.0
    for kw in FEE_KEYWORDS:
        if kw in t:
            score += 2.0
    for nk in NOISE_KEYWORDS:
        if nk in t:
            score -= 1.5
    return score

# NOTE: helper lama (_looks_like_asset/_kind_from_url/...) sengaja dihapus agar tidak membingungkan.

def extract_links_and_assets(page_url: str, html: str) -> List[Tuple[str, str, str, float]]:
    """
    Return (url, kind, hint, score)
    kind: html | pdf | image
    """
    soup = BeautifulSoup(html, "lxml")
    out: List[Tuple[str, str, str, float]] = []

    # a[href]
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        text = (a.get_text(" ", strip=True) or "")[:200]
        u = safe_join(page_url, href)
        hint = f"{text} {href}".strip()

        # anti-noise: skip kalau jelas noise dan tidak ada fee word
        if _is_noise(hint) and not FEE_WORD_RE.search(hint):
            continue

        ul = u.lower()
        kind = "html"
        # handle querystring cases too
        if ul.endswith(PDF_EXT) or (ASSET_EXT_RE.search(ul) and ".pdf" in ul):
            kind = "pdf"
        elif ul.endswith(IMG_EXT) or (ASSET_EXT_RE.search(ul) and any(x in ul for x in [".png", ".jpg", ".jpeg", ".webp"])):
            kind = "image"

        sc = score_hint(hint)
        out.append((u, kind, hint, sc))

    # iframe/embed/object for pdf
    for tag, attr in [("iframe", "src"), ("embed", "src"), ("object", "data")]:
        for el in soup.find_all(tag):
            src = (el.get(attr) or "").strip()
            if not src:
                continue
            u = safe_join(page_url, src)
            hint = f"{tag}:{attr} {src}"
            low = u.lower()
            kind = "pdf" if (low.endswith(PDF_EXT) or (ASSET_EXT_RE.search(low) and ".pdf" in low)) else "html"
            sc = score_hint(hint)
            out.append((u, kind, hint, sc))

    # source tags (picture/video) for images/pdf
    for s in soup.select("source"):
        src = (s.get("src") or "").strip()
        srcset = (s.get("srcset") or "").strip()
        for c in [src, *list(_pick_from_srcset(srcset))]:
            if not c:
                continue
            u = safe_join(page_url, c)
            low = u.lower()
            if not (ASSET_EXT_RE.search(low) or low.endswith(PDF_EXT) or low.endswith(IMG_EXT)):
                continue
            kind = "pdf" if ".pdf" in low else "image"
            hint = f"source {c}"[:200]
            sc = score_hint(hint) + 0.5
            out.append((u, kind, hint, sc))

    # Images: allow if page is fee-ish OR the image hint is fee-ish.
    # Also support lazyload attrs: data-src, data-original, data-lazy-src, data-srcset, etc.
    page_text = soup.get_text(" ", strip=True).lower()
    page_feeish = bool(FEE_WORD_RE.search(page_text) or MONEY_HINT_RE.search(page_text))

    for img in soup.select("img"):
        attrs = img.attrs or {}
        cand = []
        for k in ["src", "data-src", "data-original", "data-lazy-src", "data-srcset", "srcset"]:
            v = (attrs.get(k) or "").strip() if isinstance(attrs.get(k), str) else ""
            if not v:
                continue
            if "srcset" in k:
                cand.extend(list(_pick_from_srcset(v)))
            else:
                cand.append(v)

        alt = (img.get("alt") or "").strip()
        title = (img.get("title") or "").strip()
        hint = f"img {alt} {title} {attrs.get('class','')}".strip()[:200]

        # filter obvious non-content images
        def _looks_like_logo(u: str) -> bool:
            lu = (u or "").lower()
            return any(x in lu for x in ["logo", "favicon", "sprite", "icon", "brand", "avatar"]) and not FEE_WORD_RE.search(lu)

        img_feeish = page_feeish or bool(FEE_WORD_RE.search(hint))
        if _is_noise(hint) and not img_feeish:
            continue

        for c in cand:
            u = safe_join(page_url, c)
            low = u.lower()
            if _looks_like_logo(low):
                continue
            if not (low.endswith(IMG_EXT) or (ASSET_EXT_RE.search(low) and any(x in low for x in [".png", ".jpg", ".jpeg", ".webp"]))):
                continue
            sc = score_hint(hint) + (1.0 if img_feeish else 0.2)
            out.append((u, "image", hint, sc))

    # inline style background-image urls (often used for scanned fee tables)
    for el in soup.select("[style]"):
        style = (el.get("style") or "").strip()
        for raw_u in _urls_from_style(style):
            if not raw_u:
                continue
            u = safe_join(page_url, raw_u)
            low = u.lower()
            if not (ASSET_EXT_RE.search(low) or low.endswith(IMG_EXT) or low.endswith(PDF_EXT)):
                continue
            kind = "pdf" if ".pdf" in low else "image"
            hint = f"style background {raw_u}"[:200]
            sc = score_hint(hint) + (0.8 if page_feeish else 0.2)
            out.append((u, kind, hint, sc))

    # normalize + dedup
    seen = set()
    uniq = []
    for u, kind, hint, sc in out:
        u2 = normalize_url(u)
        key = (u2, kind)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((u2, kind, hint, sc))
    return uniq
