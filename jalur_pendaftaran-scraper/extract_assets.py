from __future__ import annotations

import re
from typing import List, Tuple
from bs4 import BeautifulSoup

from config import (
    JALUR_WORD_RE,
    NOISE_KEYWORDS,
    PDF_EXT,
    IMG_EXT,
    DATE_HINT_RE,
    DATE_RANGE_RE,
)

from utils import safe_join, normalize_url

"""
Extract links & embedded assets from HTML pages.

Tujuan: mendeteksi link jalur & jadwal pendaftaran di website kampus.
Bisa berupa:
- halaman HTML (jadwal)
- PDF (timeline / brosur)
- gambar (poster jadwal)

Return: (url, kind, hint, score)
"""

ASSET_EXT_RE = re.compile(r"(?i)\.(pdf|png|jpe?g|webp)(?:$|[?#])")


def _pick_from_srcset(srcset: str) -> List[str]:
    if not srcset:
        return []
    out: List[str] = []
    for part in srcset.split(","):
        p = part.strip().split(" ")[0].strip()
        if p:
            out.append(p)
    return out


def _urls_from_style(style: str) -> List[str]:
    if not style:
        return []
    return [m.group(1).strip("'\"") for m in re.finditer(r"url\(([^)]+)\)", style, flags=re.I)]


def _is_noise(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in NOISE_KEYWORDS)


def score_hint(text: str) -> float:
    t = (text or "").lower()
    score = 0.0

    # jalur / admission
    if JALUR_WORD_RE.search(t):
        score += 3.0

    # tanggal
    if DATE_HINT_RE.search(t):
        score += 2.0

    if DATE_RANGE_RE.search(t):
        score += 2.0

    # penalti noise
    for nk in NOISE_KEYWORDS:
        if nk in t:
            score -= 1.5

    return score


def extract_links_and_assets(page_url: str, html: str) -> List[Tuple[str, str, str, float]]:
    soup = BeautifulSoup(html, "lxml")
    out: List[Tuple[str, str, str, float]] = []

    # ---------------------------------
    # Page-level detection (dulunya fee-ish)
    # ---------------------------------
    page_text = soup.get_text(" ", strip=True).lower()
    page_jalurish = bool(
        JALUR_WORD_RE.search(page_text)
        or DATE_HINT_RE.search(page_text)
        or DATE_RANGE_RE.search(page_text)
    )

    # ---------------------------------
    # a[href]
    # ---------------------------------
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        text = (a.get_text(" ", strip=True) or "")[:200]
        u = safe_join(page_url, href)
        if not u:
            continue

        hint = f"{text} {href}".strip()

        if _is_noise(hint) and not JALUR_WORD_RE.search(hint):
            continue

        ul = u.lower()
        kind = "html"

        if ul.endswith(PDF_EXT) or (ASSET_EXT_RE.search(ul) and ".pdf" in ul):
            kind = "pdf"
        elif ul.endswith(IMG_EXT) or (
            ASSET_EXT_RE.search(ul)
            and any(x in ul for x in [".png", ".jpg", ".jpeg", ".webp"])
        ):
            kind = "image"

        sc = score_hint(hint)
        out.append((u, kind, hint, sc))

    # ---------------------------------
    # iframe/embed/object
    # ---------------------------------
    for tag, attr in [("iframe", "src"), ("embed", "src"), ("object", "data")]:
        for el in soup.find_all(tag):
            src = (el.get(attr) or "").strip()
            if not src:
                continue

            u = safe_join(page_url, src)
            if not u:
                continue

            hint = f"{tag}:{attr} {src}"
            low = u.lower()

            kind = "pdf" if (low.endswith(PDF_EXT) or ".pdf" in low) else "html"
            sc = score_hint(hint)
            out.append((u, kind, hint, sc))

    # ---------------------------------
    # source tag
    # ---------------------------------
    for s in soup.select("source"):
        src = (s.get("src") or "").strip()
        srcset = (s.get("srcset") or "").strip()

        for c in [src, *_pick_from_srcset(srcset)]:
            if not c:
                continue

            u = safe_join(page_url, c)
            if not u:
                continue

            low = u.lower()
            if not (ASSET_EXT_RE.search(low) or low.endswith(PDF_EXT) or low.endswith(IMG_EXT)):
                continue

            kind = "pdf" if ".pdf" in low else "image"
            hint = f"source {c}"[:200]
            sc = score_hint(hint) + 0.5

            out.append((u, kind, hint, sc))

    # ---------------------------------
    # Images (lazyload supported)
    # ---------------------------------
    def _looks_like_logo(u: str) -> bool:
        lu = (u or "").lower()
        return any(x in lu for x in ["logo", "favicon", "sprite", "icon", "brand", "avatar"]) and not JALUR_WORD_RE.search(lu)

    for img in soup.select("img"):
        attrs = img.attrs or {}
        cand = []

        for k in ["src", "data-src", "data-original", "data-lazy-src", "data-srcset", "srcset"]:
            v = (attrs.get(k) or "").strip() if isinstance(attrs.get(k), str) else ""
            if not v:
                continue
            if "srcset" in k:
                cand.extend(_pick_from_srcset(v))
            else:
                cand.append(v)

        alt = (img.get("alt") or "").strip()
        title = (img.get("title") or "").strip()
        hint = f"img {alt} {title}".strip()[:200]

        img_jalurish = page_jalurish or bool(JALUR_WORD_RE.search(hint))

        if _is_noise(hint) and not img_jalurish:
            continue

        for c in cand:
            u = safe_join(page_url, c)
            if not u:
                continue

            low = u.lower()

            if _looks_like_logo(low):
                continue

            if not (low.endswith(IMG_EXT) or ASSET_EXT_RE.search(low)):
                continue

            sc = score_hint(hint) + (1.0 if img_jalurish else 0.2)
            out.append((u, "image", hint, sc))

    # ---------------------------------
    # background-image style
    # ---------------------------------
    for el in soup.select("[style]"):
        style = (el.get("style") or "").strip()

        for raw_u in _urls_from_style(style):
            if not raw_u:
                continue

            u = safe_join(page_url, raw_u)
            if not u:
                continue

            low = u.lower()

            if not (ASSET_EXT_RE.search(low) or low.endswith(IMG_EXT) or low.endswith(PDF_EXT)):
                continue

            kind = "pdf" if ".pdf" in low else "image"
            hint = f"style background {raw_u}"[:200]
            sc = score_hint(hint) + (0.8 if page_jalurish else 0.2)

            out.append((u, kind, hint, sc))

    # ---------------------------------
    # data-* links + onclick
    # ---------------------------------
    for el in soup.find_all(True):
        attrs = el.attrs or {}

        for k in ["data-href", "data-url", "data-link", "data-src", "data-file"]:
            v = attrs.get(k)
            if isinstance(v, str):
                raw = v.strip()
                if not raw:
                    continue
                if not (JALUR_WORD_RE.search(raw) or ASSET_EXT_RE.search(raw)):
                    continue

                u = safe_join(page_url, raw)
                if not u:
                    continue

                low = u.lower()
                kind = "html"

                if ".pdf" in low:
                    kind = "pdf"
                elif any(ext in low for ext in [".png", ".jpg", ".jpeg", ".webp"]):
                    kind = "image"

                hint = f"{k} {raw}"[:200]
                sc = score_hint(hint) + 0.6

                out.append((u, kind, hint, sc))

        onclick = attrs.get("onclick")
        if isinstance(onclick, str) and onclick:
            m = re.search(r"(?:location\.href|window\.open)\s*\(?\s*['\"]([^'\"]+)['\"]", onclick, flags=re.I)
            if m:
                raw = m.group(1).strip()
                if raw and (JALUR_WORD_RE.search(raw) or ASSET_EXT_RE.search(raw)):
                    u = safe_join(page_url, raw)
                    if u:
                        low = u.lower()
                        kind = "pdf" if ".pdf" in low else ("image" if ASSET_EXT_RE.search(low) else "html")
                        hint = f"onclick {raw}"[:200]
                        sc = score_hint(hint) + 0.6
                        out.append((u, kind, hint, sc))

    # ---------------------------------
    # script URLs
    # ---------------------------------
    script_text = "\n".join(
        [s.get_text(" ", strip=True) for s in soup.find_all("script") if s.get_text(strip=True)]
    )

    if script_text:
        for m in re.finditer(r"https?://[^\s'\"<>]+", script_text):
            raw = m.group(0)
            if not (ASSET_EXT_RE.search(raw) or JALUR_WORD_RE.search(raw)):
                continue

            u = normalize_url(raw)
            if not u:
                continue

            low = u.lower()
            kind = "pdf" if ".pdf" in low else ("image" if ASSET_EXT_RE.search(low) else "html")
            hint = f"script {raw}"[:200]
            sc = score_hint(hint) + 0.4

            out.append((u, kind, hint, sc))

    # ---------------------------------
    # normalize + dedup
    # ---------------------------------
    seen = set()
    uniq = []

    for u, kind, hint, sc in out:
        u2 = normalize_url(u)
        if not u2:
            continue

        key = (u2, kind)
        if key in seen:
            continue

        seen.add(key)
        uniq.append((u2, kind, hint, sc))

    return uniq
