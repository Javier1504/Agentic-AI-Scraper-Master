from __future__ import annotations

from collections import deque
from typing import List, Set, Dict, Tuple

from config import FEE_WORD_RE, NOISE_KEYWORDS
from utils import CandidateLink, normalize_url, same_site, is_allowed_asset_url
from extract_assets import extract_links_and_assets

from logger import info, debug

def _is_noise_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in NOISE_KEYWORDS)

def _priority(url: str) -> float:
    u = (url or "").lower()
    if FEE_WORD_RE.search(u):
        return 10.0
    if any(x in u for x in ["pmb", "admission", "penerimaan", "biaya", "ukt", "spp", "spi", "ipi"]):
        return 3.0
    return 0.5

async def crawl_site(
    campus_name: str,
    official_website: str,
    fetch_html_async,
    max_pages: int = 80,
    min_candidate_score: float = 2.0,
) -> List[CandidateLink]:
    start = normalize_url(official_website)
    q = deque([start])
    visited: Set[str] = set()
    candidates: List[CandidateLink] = []

    while q and len(visited) < max_pages:
        url = q.popleft()
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

        html = fr.content.decode("utf-8", errors="ignore")
        found = extract_links_and_assets(fr.final_url, html)
        debug(f"crawl | univ='{campus_name}' found_links={len(found)} page={fr.final_url}")

        for u, kind, hint, sc in found:
            # HTML wajib satu domain (official). Asset (pdf/image) boleh lintas domain bila host allowlist.
            if kind == "html":
                if not same_site(u, start):
                    continue
            else:
                if not is_allowed_asset_url(u, start):
                    continue

            # stop noise pages unless fee-ish
            if _is_noise_url(u) and not FEE_WORD_RE.search(u) and sc < 4:
                continue

            is_feeish = bool(FEE_WORD_RE.search(u) or FEE_WORD_RE.search(hint) or sc >= min_candidate_score)

            if is_feeish:
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
                if _priority(u) >= 5:
                    q.appendleft(u)
                else:
                    q.append(u)

    # dedup by (url, kind) keep max score
    best: Dict[Tuple[str, str], CandidateLink] = {}
    for c in candidates:
        k = (c.url, c.kind)
        if k not in best or c.score > best[k].score:
            best[k] = c

    info(f"crawl_done | univ='{campus_name}' visited={len(visited)} candidates={len(best)}")
    return list(best.values())
