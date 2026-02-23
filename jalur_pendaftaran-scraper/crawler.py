from __future__ import annotations

import heapq
from typing import List, Set, Dict, Tuple, Optional

from bs4 import BeautifulSoup

from config import (
    JALUR_WORD_RE,
    NOISE_KEYWORDS,
    DATE_HINT_RE,
    DATE_RANGE_RE,
    LEVEL_HINT_RE,
    JALUR_DATE_ROW_RE
)

from utils import CandidateLink, normalize_url, canonical_for_visit, same_site
from extract_assets import extract_links_and_assets
from logger import info, debug


def _is_noise_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in NOISE_KEYWORDS)


def _priority(url: str) -> float:
    u = (url or "").lower()

    # URL yang sangat kuat mengandung jalur/admission
    if JALUR_WORD_RE.search(u):
        return 10.0

    # URL admission umum
    if any(x in u for x in [
        "pmb", "ppmb", "admission", "penerimaan", "jalur",
        "seleksi", "registrasi", "daftar", "jadwal", "snpmb",
    ]):
        return 4.0

    return 0.5


def _page_signal_score(html: str) -> float:
    """
    Skor sinyal halaman berdasarkan konten (bukan URL saja).

    Target: menangkap halaman jadwal/jalur walaupun URL tidak eksplisit.
    """
    if not html:
        return 0.0

    try:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)[:20000]
        low = text.lower()

        score = 0.0

        # Ada tanggal → sangat penting untuk jadwal
        if DATE_HINT_RE.search(text):
            score += 3.0

        # Ada rentang tanggal
        if DATE_RANGE_RE.search(text):
            score += 2.0

        # Ada kata jalur/seleksi
        if JALUR_WORD_RE.search(text):
            score += 3.0

        # Ada jenjang pendidikan
        if LEVEL_HINT_RE.search(text):
            score += 1.5

        # Banyak tabel → biasanya jadwal dalam tabel
        tables = soup.find_all("table")
        if tables:
            score += 1.0

        tr_count = len(soup.find_all("tr"))
        if tr_count >= 8:
            score += 1.5
        elif tr_count >= 4:
            score += 0.5

        # Hint tabel populer CMS
        if "datatable" in low or "tablepress" in low:
            score += 1.0

        # Penalti ringan untuk noise
        if any(k in low for k in NOISE_KEYWORDS):
            score -= 0.5

        return score

    except Exception:
        return 0.0


async def crawl_site(
    campus_name: str,
    official_website: str,
    fetch_html_async,
    max_pages: int = 200,
    min_candidate_score: float = 2.0,
) -> List[CandidateLink]:

    start = canonical_for_visit(official_website)

    # Priority queue: pop highest priority first.
    # item = (-prio, counter, url)
    q: List[Tuple[float, int, str]] = []
    counter = 0
    heapq.heappush(q, (-100.0, counter, start))

    visited: Set[str] = set()
    candidates: List[CandidateLink] = []

    while q and len(visited) < max_pages:
        _, _, url = heapq.heappop(q)
        url = canonical_for_visit(url)

        if not url:
            continue
        if url in visited:
            continue
        if not same_site(url, start):
            continue

        visited.add(url)

        info(f"crawl | univ='{campus_name}' visit={len(visited)}/{max_pages} queue={len(q)} url={url}")

        fr = await fetch_html_async(url)
        if not fr.ok or not fr.content:
            debug(f"crawl | univ='{campus_name}' fetch_failed mode={fr.mode} status={fr.status} url={url}")
            continue

        # Hindari duplikasi akibat redirect
        final_u = canonical_for_visit(fr.final_url or url)
        if final_u and final_u != url:
            visited.add(final_u)
            url = final_u

        html = fr.content.decode("utf-8", errors="ignore")

        # ✅ Content-based signal untuk jalur/jadwal
        page_sc = _page_signal_score(html)

        if page_sc >= min_candidate_score:
            candidates.append(CandidateLink(
                campus_name=campus_name,
                official_website=start,
                url=fr.final_url,
                kind="html",
                source_page=fr.final_url,
                context_hint=f"page_signal_score={page_sc:.1f}",
                score=float(page_sc),
            ))

        found = extract_links_and_assets(url, html)
        debug(f"crawl | univ='{campus_name}' found_links={len(found)} page={fr.final_url}")

        for u, kind, hint, sc in found:
            u = canonical_for_visit(u)

            if not u:
                continue
            if not same_site(u, start):
                continue

            # stop noise pages kecuali sangat kuat jalur
            if _is_noise_url(u) and not JALUR_WORD_RE.search(u) and sc < 4:
                continue

            is_jalur_related = bool(
                JALUR_WORD_RE.search(u)
                or JALUR_WORD_RE.search(hint)
                or DATE_HINT_RE.search(hint)
                or sc >= min_candidate_score
            )

            if is_jalur_related:
                candidates.append(CandidateLink(
                    campus_name=campus_name,
                    official_website=start,
                    url=u,
                    kind=kind,
                    source_page=fr.final_url,
                    context_hint=hint[:300],
                    score=float(sc),
                ))
                debug(f"candidate | univ='{campus_name}' kind={kind} score={sc:.1f} url={u}")

            if kind == "html" and u not in visited:
                pr = _priority(u) + float(sc)

                # Jika halaman ini sudah kuat sinyal jalur
                if page_sc >= 5.0:
                    pr += 1.5

                counter += 1
                heapq.heappush(q, (-pr, counter, u))

    # dedup by (url, kind) keep max score
    best: Dict[Tuple[str, str], CandidateLink] = {}
    for c in candidates:
        k = (c.url, c.kind)
        if k not in best or c.score > best[k].score:
            best[k] = c

    info(f"crawl_done | univ='{campus_name}' visited={len(visited)} candidates={len(best)}")
    return list(best.values())
