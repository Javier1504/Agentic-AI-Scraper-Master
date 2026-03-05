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
    HARD_NOISE_KEYWORDS,
)

from utils import CandidateLink, normalize_url, canonical_for_visit, same_site
from extract_assets import extract_links_and_assets
from logger import info, debug, warn, error


# =========================================================
# HELPERS
# =========================================================

def _is_noise_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in NOISE_KEYWORDS)

def _is_hard_noise_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in HARD_NOISE_KEYWORDS)

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
        "pmb", "ppmb", "spmb", "admission", "penerimaan",
        "jalur", "seleksi", "registrasi",
        "daftar", "jadwal", "snpmb", "selma", "smup",
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
        text = soup.get_text(" ", strip=True)[:25000]
        low = text.lower()

        score = 0.0

        # Keyword matching
        if DATE_HINT_RE.search(text):
            score += 3.5

        if DATE_RANGE_RE.search(text):
            score += 3.0

        if JALUR_WORD_RE.search(text):
            score += 4.0

        if LEVEL_HINT_RE.search(text):
            score += 2.0

        # Table detection (banyak universitas gunakan table untuk jadwal)
        tables = soup.find_all("table")
        tr_count = len(soup.find_all("tr"))
        if tables:
            score += 2.0
        if tr_count >= 8:
            score += 2.0
        elif tr_count >= 4:
            score += 1.0

        # Structured data detection
        if any(k in low for k in ["datatable", "tablepress", "datatables", "calendar", "schedule"]):
            score += 2.0

        # Content length heuristic (admission pages usually sizable)
        if len(text) > 2000:
            score += 1.0
        elif len(text) > 5000:
            score += 2.0

        # List/form elements detection
        forms = soup.find_all("form")
        if forms:
            score += 1.5
        
        inputs = soup.find_all(["input", "select"])
        if len(inputs) > 3:
            score += 1.0

        # Buttons dengan registration/application keywords
        buttons_text = " ".join([b.get_text() for b in soup.find_all("button")])
        if any(k in buttons_text.lower() for k in ["daftar", "register", "submit", "apply", "pendaftaran"]):
            score += 1.5

        # Penalti untuk noise
        noise_hits = 0
        for nk in NOISE_KEYWORDS:
            if nk in low:
                noise_hits += 1
                score -= 0.3

        # Additional harsh penalties for contextual noise
        from config import CONTEXT_NOISE_RE
        if CONTEXT_NOISE_RE.search(low):
            score -= 5.0

        # determine whether the jalur keyword should be trusted at all
        jalur_hit = bool(JALUR_WORD_RE.search(low))
        if jalur_hit and noise_hits >= 5:
            jalur_hit = False
            score -= 4.0

        # Minimum signal from structure only if we believe it is an admission page
        if (tr_count > 0 or forms) and jalur_hit:
            score = max(score, 1.5)

        # if nothing strongly suggests admissions, drop to zero
        if score < 2.0 and not (jalur_hit or DATE_HINT_RE.search(text) or DATE_RANGE_RE.search(text) or LEVEL_HINT_RE.search(text)):
            return 0.0

        return max(score, 0.0)

    except Exception as e:
        debug(f"page_signal_score error: {e}")
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
        f"https://spmb.{domain}",
        f"https://admissions.{domain}",
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
    max_pages: int = 80,
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
        
        print(f"Page Signal Score for {url}: {page_sc:.1f}")  # Debug print untuk page signal score

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

        # If Playwright returned meta URLs (network requests / in-page links), include them
        try:
            meta = getattr(fr, 'meta', None) or {}
            if isinstance(meta, dict):
                # network_requests may contain API endpoints; treat as html candidates with low score
                for nr in meta.get('network_requests', []) or []:
                    try:
                        u = canonical_for_visit(nr)
                        if u and same_site(u, root):
                            found.append((u, 'html', 'network_request', 0.5))
                    except Exception:
                        continue

                for pl in meta.get('page_links', []) or []:
                    try:
                        u = canonical_for_visit(pl)
                        if u and same_site(u, root):
                            found.append((u, 'html', 'page_link', 1.0))
                    except Exception:
                        continue

                # include links that our fetcher clicked/interacted with
                for cl in meta.get('clicked_links', []) or []:
                    try:
                        u = canonical_for_visit(cl)
                        if u and same_site(u, root):
                            found.append((u, 'html', 'clicked', 1.5))
                    except Exception:
                        continue
        except Exception:
            pass

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
                or "jadwal" in hint.lower()
                or "seleksi" in hint.lower()
                or sc >= 1.0
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