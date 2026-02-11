# crawler.py — ADMISSION CRAWLER v2 FINAL (FIXED)

from collections import deque
from typing import List, Set, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from config import JALUR_WORD_RE
from utils import (
    CandidateLink,
    normalize_url,
    same_site,
    is_related_domain,
    dedupe_candidates,      # 🔥 WAJIB
)

from extract_assets import (
    extract_links_and_assets,
)

from logger import info, warn

from section_extractor import extract_candidate_sections
from urllib.parse import urlparse



# =========================
# CONFIG
# =========================

ADMISSION_ENTRY_KEYWORDS = [
    "pmb", "ppmb", "admission", "penerimaan", "pendaftaran mahasiswa baru", "selma", "seleksi-masuk", "snbp", "snbt", "mandiri", "jadwal"
]

HARD_REJECT_KEYWORDS = [
    "daya-tampung", "kuota", "kapasitas", "program-studi", "prodi", "fakultas", "mbkm",
     "alumni", "berita", "news", "artikel", "biaya", "fee", "ukt", "beasiswa",
    "scholarship", "kontak", "contact", "lokasi", "location", "peta-situs", "bayar", 
    "dokumen", "animo", "ppds", "logo", "visi-misi", "sejarah", "tentang-kami",
    "pengumuman", "syarat-ketentuan", "terms-and-conditions", "privacy-policy",
    "inovasi", "research", "riset", "penelitian", "layanan", "service", 
]

MAX_ADMISSION_DEPTH = 5


# =========================
# HELPERS
# =========================

def is_admission_entry(url: str) -> bool:
    u = url.lower()
    return any(k in u for k in ADMISSION_ENTRY_KEYWORDS)


def hard_reject(url: str) -> bool:
    u = url.lower()

    # Jangan reject halaman jadwal
    if "jadwal" in u or "timeline" in u:
        return False

    return any(k in u for k in HARD_REJECT_KEYWORDS)

def _in_admission_subtree(url: str, prefixes: set[str], hosts: set[str]) -> bool:
    p = urlparse(url)
    path = p.path.rstrip("/") + "/"
    host = p.netloc

    if host in hosts:
        return True
    for pref in prefixes:
        if path.startswith(pref):
            return True
    return False

def admission_score(url: str, hint: str) -> int:
    blob = (url + " " + hint).lower()
    s = 0
    for k in ADMISSION_ENTRY_KEYWORDS:
        if k in blob:
            s += 2
    if any(k in blob for k in ["pmb", "admission", "um", "selma", "penerimaan", "seleksi"]):
        s += 5
    return s



# def _priority(url: str) -> int:
#     u = url.lower()
#     if "jadwal" in u or "timeline" in u:
#         return 100
#     if any(k in u for k in ["snbp", "snbt", "mandiri"]):
#         return 80
#     if is_admission_entry(u):
#         return 60
#     return 10


# =========================
# MAIN CRAWLER
# =========================

async def crawl_site(
    campus_name: str,
    official_website: str,
    fetcher,
    max_pages: int = 80,
) -> List[CandidateLink]:

    start = normalize_url(official_website)
    visited: Set[str] = set()
    candidates: List[CandidateLink] = []

    info(f"admission_discovery | {campus_name}")

    # --- STEP 1: ENTRY POINT FROM MENU ---
    fr, menu_links = await fetcher.fetch_with_menu(start)
    roots = [
        normalize_url(u)
        for u in menu_links
        if is_related_domain(u, start)
        and is_admission_entry(u)
    ]

    if not roots:
        warn(f"admission_fallback | {campus_name} | scanning homepage links")

        fr, _ = await fetcher.fetch_with_menu(start)
        if not fr.ok:
            return []

        html = fr.content.decode("utf-8", errors="ignore")
        found = extract_links_and_assets(fr.final_url, html)

        scored = []

        for u, kind, hint, score in found:
            s = admission_score(u, hint)
            if s > 0:
                scored.append((s, u))

            # ambil top kandidat
        scored.sort(reverse=True)
        top = [normalize_url(u) for s, u in scored[:3]]

        if top:
            roots = top
        else:
            # fallback terakhir: mulai dari homepage
            top = [start]


    admission_roots = roots[:3]
    q = deque([(u, 0) for u in admission_roots])
    
    from urllib.parse import urlparse
    admission_prefixes = set()
    admission_hosts = set()

    for r in admission_roots:
        p = urlparse(r)
        host = p.netloc
        path = p.path.rstrip("/")

        if host:
            admission_hosts.add(host)

        if path and path != "/":
            admission_prefixes.add(path + "/")
            
    lock_to_admission = len(admission_prefixes) > 0


    # --- STEP 2: BFS CRAWLING ---
    while q and len(visited) < max_pages:
        url, depth = q.popleft()

        if url in visited:
            continue
        if depth > MAX_ADMISSION_DEPTH:
            continue
        if not is_related_domain(url, start):
            continue
        if hard_reject(url):
            continue
        if lock_to_admission and not _in_admission_subtree(url, admission_prefixes, admission_hosts):
            continue

        visited.add(url)
        info(f"crawl | {campus_name} depth={depth} url={url}")

        fetch_url = normalize_url(url, keep_fragment=False)
        fr = (await fetcher.fetch_with_menu(url))[0]
        if not fr.ok:
            continue

        html = fr.content.decode("utf-8", errors="ignore")
        found = extract_links_and_assets(fr.final_url, html)
        
        sections = extract_candidate_sections(fr.final_url, html)

        for _, context in sections:
            candidates.append(
                CandidateLink(
                    campus_name=campus_name,
                    official_website=official_website,
                    url=fr.final_url,          # URL SAMA
                    kind="html",
                    source_page=fr.final_url,
                    context_hint=context,
                    score=90,                  # tinggi karena section-level
                )
            )

        # --- STEP 3: LINK ANALYSIS ---
        for u, kind, hint, score in found:
            u = normalize_url(u, keep_fragment=True)

            if not is_related_domain(u, start):
                continue
            if hard_reject(u):
                continue

            text_blob = (u + " " + hint).lower()

            # 🔥 PATCH UTAMA:
            # Semua halaman jadwal ATAU halaman yang mengandung kata jalur
            # dianggap kandidat. Pemecahan detail dilakukan di extractor.
            is_candidate = (
                "jadwal" in text_blob
                or JALUR_WORD_RE.search(text_blob)
            )

            if is_candidate:
                candidates.append(
                    CandidateLink(
                        campus_name=campus_name,
                        official_website=official_website,
                        url=u,
                        kind=kind,
                        source_page=fr.final_url,
                        context_hint=hint[:300],
                        score=score,
                    )
                )

            if kind == "html" and u not in visited:
                if (not lock_to_admission) or _in_admission_subtree(u, admission_prefixes, admission_hosts):
                    q.append((u, depth + 1))

    # --- STEP 4: DEDUP (NON-DESTRUCTIVE) ---
    # --- STEP 4: DEDUP (URL + KIND, AMBIL SCORE TERBAIK) ---
    best = dedupe_candidates(candidates)

    info(f"crawl_done | {campus_name} candidates={len(best)}")
    return best

