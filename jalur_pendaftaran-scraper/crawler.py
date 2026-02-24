from __future__ import annotations

import heapq
from typing import List, Set, Dict, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from config import (
    JALUR_WORD_RE,
    NOISE_KEYWORDS,
    DATE_HINT_RE,
    DATE_RANGE_RE,
    LEVEL_HINT_RE,
)

from utils import CandidateLink, normalize_url, canonical_for_visit, same_site
from extract_assets import extract_links_and_assets
from logger import info, debug


# =========================================================
# HELPERS
# =========================================================

def _is_noise_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in NOISE_KEYWORDS)


def _priority(url: str, depth: int) -> float:
    """
    Hybrid priority:
    - URL signal
    - Depth penalty
    """
    u = (url or "").lower()
    score = 0.5

    if JALUR_WORD_RE.search(u):
        score += 10.0
    elif any(x in u for x in [
        "pmb", "ppmb", "admission", "penerimaan",
        "jalur", "seleksi", "registrasi",
        "daftar", "jadwal", "snpmb"
    ]):
        score += 4.0

    # depth penalty
    score -= depth * 0.7

    return score


def _page_signal_score(html: str) -> float:
    if not html:
        return 0.0

    try:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)[:20000]
        low = text.lower()

        score = 0.0

        if DATE_HINT_RE.search(text):
            score += 3.0

        if DATE_RANGE_RE.search(text):
            score += 2.0

        if JALUR_WORD_RE.search(text):
            score += 3.0

        if LEVEL_HINT_RE.search(text):
            score += 1.5

        if soup.find_all("table"):
            score += 1.0

        tr_count = len(soup.find_all("tr"))
        if tr_count >= 8:
            score += 1.5
        elif tr_count >= 4:
            score += 0.5

        if "datatable" in low or "tablepress" in low:
            score += 1.0

        if any(k in low for k in NOISE_KEYWORDS):
            score -= 0.5

        return score

    except Exception:
        return 0.0


# =========================================================
# ADMISSION ROOT DISCOVERY
# =========================================================

async def _discover_admission_root(start: str, fetch_html_async) -> str:
    """
    Try common admission subdomains first.
    Fallback to homepage if none valid.
    """
    parsed = urlparse(start)
    domain = parsed.netloc.replace("www.", "")

    guesses = [
        f"https://pmb.{domain}",
        f"https://ppmb.{domain}",
        f"https://admission.{domain}",
        f"https://penerimaan.{domain}",
        f"https://selma.{domain}",
        f"https://smup.{domain}",
    ]

    for g in guesses:
        try:
            fr = await fetch_html_async(g)
            if fr.ok and fr.status == 200:
                info(f"[DISCOVER] admission root found: {g}")
                return g
        except Exception:
            continue

    info(f"[DISCOVER] fallback to homepage")
    return start


# =========================================================
# MAIN CRAWLER (HYBRID + STABIL)
# =========================================================

async def crawl_site(
    campus_name: str,
    official_website: str,
    fetch_html_async,
    max_pages: int = 60,
    min_candidate_score: float = 2.0,
) -> List[CandidateLink]:

    start = canonical_for_visit(official_website)

    # 1️⃣ Discover admission root
    root = await _discover_admission_root(start, fetch_html_async)
    root = canonical_for_visit(root)

    # Priority queue: (-priority, counter, depth, url)
    q: List[Tuple[float, int, int, str]] = []
    counter = 0
    heapq.heappush(q, (-100.0, counter, 0, root))

    visited: Set[str] = set()
    candidates: List[CandidateLink] = []

    while q and len(visited) < max_pages:
        _, _, depth, url = heapq.heappop(q)
        url = canonical_for_visit(url)

        if not url:
            continue
        if url in visited:
            continue
        if not same_site(url, root):
            continue

        visited.add(url)

        info(
            f"crawl | univ='{campus_name}' "
            f"visit={len(visited)}/{max_pages} depth={depth} url={url}"
        )

        fr = await fetch_html_async(url)
        if not fr.ok or not fr.content:
            continue

        final_u = canonical_for_visit(fr.final_url or url)
        if final_u and final_u != url:
            visited.add(final_u)
            url = final_u

        html = fr.content.decode("utf-8", errors="ignore")

        # Content signal
        page_sc = _page_signal_score(html)

        if page_sc >= min_candidate_score:
            candidates.append(CandidateLink(
                campus_name=campus_name,
                official_website=root,
                url=url,
                kind="html",
                source_page=url,
                context_hint=f"page_signal_score={page_sc:.1f}",
                score=float(page_sc),
            ))

        # Extract links
        found = extract_links_and_assets(url, html)

        for u, kind, hint, sc in found:
            if kind != "html":
                continue

            u = canonical_for_visit(u)

            if not u:
                continue
            if u in visited:
                continue
            if not same_site(u, root):
                continue

            if _is_noise_url(u) and not JALUR_WORD_RE.search(u):
                continue

            is_related = bool(
                JALUR_WORD_RE.search(u)
                or JALUR_WORD_RE.search(hint)
                or DATE_HINT_RE.search(hint)
                or sc >= min_candidate_score
            )

            if not is_related:
                continue

            pr = _priority(u, depth + 1) + float(sc)

            counter += 1
            heapq.heappush(q, (-pr, counter, depth + 1, u))

    # Deduplicate
    best: Dict[Tuple[str, str], CandidateLink] = {}
    for c in candidates:
        k = (c.url, c.kind)
        if k not in best or c.score > best[k].score:
            best[k] = c

    info(
        f"crawl_done | univ='{campus_name}' "
        f"visited={len(visited)} candidates={len(best)}"
    )

    return list(best.values())
